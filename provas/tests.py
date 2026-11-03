import base64
import logging
import tempfile
import uuid
from datetime import date
from decimal import Decimal
from importlib import import_module
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest import skipUnless

import cv2
import numpy as np
from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.staticfiles import finders
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, OperationalError, connection, transaction
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.urls import resolve, reverse
from PIL import Image

from academico.models import Aluno, Matricula, Turma
from banco_questoes.models import Alternativa, Disciplina, Questao, Topico

from .forms import MascaraCriterioTopicoFormSet, MascaraForm
from .models import (
    AtribuicaoVersao,
    FolhaDigitalizada,
    FolhaResposta,
    Mascara,
    MascaraCriterioDificuldade,
    MascaraCriterioTopico,
    MascaraProva,
    Prova,
    ProvaAplicacao,
    ProvaQuestao,
    ProvaVersao,
    ProvaVersaoItem,
)
from .presets import construir_distribuicao_topicos_enade, preset_enade_initial
from .services.answer_sheet_layout import (
    MARKER_IDS,
    MARKER_INSET,
    PAGE_H,
    PAGE_W,
    SCHEMA_VERSION,
    build_layout,
    geometry_from_dict,
    max_questoes_por_pagina,
)
from .services.correcao import (
    corrigir_folha_digitalizada,
    corrigir_questao_manual,
    lancar_nota_discursiva,
    lancar_objetiva_manual,
)
from .services.fiducial import gerar_qr_data_uri, montar_payload_folha
from .services.folha_service import (
    contar_questoes_objetivas,
    gerar_folhas_da_aplicacao,
    render_folha_pdf,
)
from .services.omr import (
    PX_POR_MM,
    OmrError,
    corrigir_perspectiva,
    decodificar_qr,
    detectar_marcadores_com_largura,
    extrair_qr_token,
    identificar_folha,
    ler_qr_da_folha,
    ler_respostas,
    localizar_folha,
)
from .services.pdf_generator import PDFGenerationError, PDFGeneratorService, resolve_static_media_url
from .services.version_assigner import (
    atribuir_versoes,
    incluir_alunos_novos,
    indices_em_blocos,
    resumo_distribuicao,
)
from .views import prova_list


@skipUnless(connection.vendor == 'mysql', 'Regressão da estrutura legada específica do MySQL.')
class RepararSchemaVersoesTest(TransactionTestCase):
    def test_repara_schema_legado_preservando_dados_e_permite_novas_versoes(self):
        from provas.models import Prova, ProvaQuestao, ProvaVersao, ProvaVersaoItem
        from provas.services.version_generator import gerar_versoes_prova

        reparar = import_module('provas.migrations.0008_reparar_schema_versoes').reparar_schema_versoes
        disciplina = Disciplina.objects.create(nome='Disciplina da regressão de schema')
        topico = Topico.objects.create(disciplina=disciplina, nome='Tópico de teste')
        questao = Questao.objects.create(
            disciplina=disciplina, topico=topico, tipo='OBJ', dificuldade='F', enunciado='Questão preservada'
        )
        prova = Prova.objects.create(titulo='Prova preservada', disciplina=disciplina)
        alternativas = [{'letra': 'A', 'texto': 'Resposta preservada', 'correta': True}]
        base = ProvaQuestao.objects.create(
            prova=prova, questao=questao, snapshot_questao={'tipo': 'OBJ', 'alternativas': alternativas}
        )
        versao = ProvaVersao.objects.create(prova=prova, identificador='A', numero_versao=1)
        item = ProvaVersaoItem.objects.create(versao=versao, prova_questao=base, alternativas_embaralhadas=alternativas)

        # Simula o banco observado: 0006 aplicada, mas colunas ainda legadas.
        with connection.schema_editor() as editor:
            editor.alter_unique_together(ProvaVersao, ProvaVersao._meta.unique_together, (('prova', 'identificador'),))
            editor.alter_unique_together(
                ProvaVersaoItem, ProvaVersaoItem._meta.unique_together, (('versao', 'numero'),)
            )
        with connection.cursor() as cursor:
            cursor.execute(
                'ALTER TABLE provas_provaversao '
                'CHANGE identificador versao varchar(5) NOT NULL, '
                'DROP COLUMN atualizado_em, '
                "ADD COLUMN identificador_qrcode varchar(100) NOT NULL DEFAULT 'qr-preservado'"
            )
            cursor.execute(
                'ALTER TABLE provas_provaversaoitem '
                'CHANGE alternativas_embaralhadas snapshot_alternativas json NOT NULL, '
                "ADD COLUMN gabarito_letra varchar(2) NOT NULL DEFAULT 'A', "
                'ADD COLUMN criado_em datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)'
            )
            cursor.execute('ALTER TABLE provas_provaversao ALTER COLUMN identificador_qrcode DROP DEFAULT')
            cursor.execute(
                'ALTER TABLE provas_provaversaoitem '
                'ALTER COLUMN gabarito_letra DROP DEFAULT, ALTER COLUMN criado_em DROP DEFAULT'
            )

        try:
            with self.assertRaises(OperationalError):
                list(prova.versoes.all())

            with connection.schema_editor() as editor:
                reparar(apps, editor)
            versao.refresh_from_db()
            item.refresh_from_db()
            self.assertEqual(versao.identificador, 'A')
            self.assertEqual(versao.atualizado_em, versao.criado_em)
            self.assertEqual(item.alternativas_embaralhadas, alternativas)
            with connection.cursor() as cursor:
                cursor.execute('SELECT identificador_qrcode FROM provas_provaversao WHERE id = %s', [versao.pk])
                self.assertEqual(cursor.fetchone()[0], 'qr-preservado')
                cursor.execute('SELECT gabarito_letra FROM provas_provaversaoitem WHERE id = %s', [item.pk])
                self.assertEqual(cursor.fetchone()[0], 'A')

            # Uma segunda execução deve ser inócua, inclusive após DDL parcial.
            with connection.schema_editor() as editor:
                reparar(apps, editor)
            nova_prova = Prova.objects.create(titulo='Prova após reparo', disciplina=disciplina)
            ProvaQuestao.objects.create(
                prova=nova_prova, questao=questao, snapshot_questao={'tipo': 'OBJ', 'alternativas': alternativas}
            )
            novas = gerar_versoes_prova(nova_prova)
            self.assertEqual([nova.identificador for nova in novas], ['A', 'B', 'C'])
            self.assertEqual(novas[0].itens.get().alternativas_embaralhadas[0]['texto'], 'Resposta preservada')
            self.assertTrue(ProvaVersao.objects.filter(pk=versao.pk).exists())
            for model in (ProvaVersao, ProvaVersaoItem):
                with connection.cursor() as cursor:
                    constraints = connection.introspection.get_constraints(cursor, model._meta.db_table)
                uniques = {tuple(c['columns']) for c in constraints.values() if c['unique']}
                for campos in model._meta.unique_together:
                    self.assertIn(tuple(model._meta.get_field(nome).column for nome in campos), uniques)
        finally:
            # Restaura a estrutura para os outros testes, mesmo se uma asserção falhar.
            with connection.schema_editor() as editor:
                reparar(apps, editor)
            with connection.cursor() as cursor:
                cursor.execute('ALTER TABLE provas_provaversao DROP COLUMN identificador_qrcode')
                cursor.execute('ALTER TABLE provas_provaversaoitem DROP COLUMN gabarito_letra, DROP COLUMN criado_em')


class ProvasAppBaseTest(TestCase):
    """Testes base de rotas e integridade do app provas (Módulo 2)."""

    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username='docente_provas', password='secret_password')  # noqa: S106
        self.user.groups.add(Group.objects.get(name='Docente'))

    def test_provas_index_url_resolves(self):
        """O painel principal dá acesso às provas persistidas."""
        found = resolve('/provas/')
        self.assertEqual(found.func, prova_list)

    def test_provas_index_requires_login(self):
        """Verifica que /provas/ redireciona usuários anônimos para o login."""
        url = reverse('provas:index')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_provas_index_authenticated_returns_200(self):
        """Verifica renderização da tela inicial do módulo de provas para usuários logados."""
        self.client.login(username='docente_provas', password='secret_password')  # noqa: S106
        url = reverse('provas:index')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Provas salvas')
        self.assertContains(response, reverse('provas:mascara_list'))


class ProvaListTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        from provas.models import Prova

        cls.user = get_user_model().objects.create_user(username='docente_painel')
        cls.user.groups.add(Group.objects.get_or_create(name='Administrador')[0])
        cls.disciplina = Disciplina.objects.create(nome='Disciplina arquivada', ativo=False)
        cls.outra = Disciplina.objects.create(nome='Outra disciplina')
        cls.manual = Prova.objects.create(
            titulo='Avaliação manual salva', tipo_geracao='MANUAL', disciplina=cls.disciplina
        )
        cls.mascara = MascaraProva.objects.create(titulo='Máscara de referência', disciplina=cls.outra)
        cls.automatica = Prova.objects.create(titulo='Avaliação por máscara', mascara=cls.mascara)

    def setUp(self):
        self.client.force_login(self.user)

    def test_painel_inclui_provas_sem_mascara_e_metricas(self):
        response = self.client.get(reverse('provas:index'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['metricas'], {'total': 2, 'manuais': 1, 'automaticas': 1})
        self.assertContains(response, self.manual.titulo)
        self.assertContains(response, self.automatica.titulo)
        self.assertContains(response, reverse('provas:prova_revisao', kwargs={'pk': self.manual.pk}))

    def test_filtros_busca_tipo_e_disciplina_inativa(self):
        response = self.client.get(
            reverse('provas:prova_list'), {'q': 'manual salva', 'tipo': 'MANUAL', 'disciplina': self.disciplina.pk}
        )
        self.assertEqual(list(response.context['provas']), [self.manual])
        self.assertContains(response, 'Disciplina arquivada')
        self.assertNotContains(response, self.automatica.titulo)

    def test_filtro_considera_disciplina_herdada_da_mascara(self):
        response = self.client.get(reverse('provas:index'), {'disciplina': self.outra.pk, 'tipo': 'AUTOMATICA'})
        self.assertEqual(list(response.context['provas']), [self.automatica])

    def test_filtros_invalidos_e_estado_sem_resultados(self):
        response = self.client.get(reverse('provas:index'), {'disciplina': 'invalida', 'tipo': 'invalido'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['page_obj'].paginator.count, 2)
        response = self.client.get(reverse('provas:index'), {'q': 'titulo inexistente'})
        self.assertContains(response, 'Nenhuma prova encontrada')

    def test_paginacao_mantem_filtros_e_ordem_das_provas(self):
        from provas.models import Prova

        Prova.objects.bulk_create(
            [Prova(titulo=f'Lote {numero}', tipo_geracao='MANUAL', disciplina=self.disciplina) for numero in range(13)]
        )
        response = self.client.get(reverse('provas:index'), {'q': 'Lote', 'tipo': 'MANUAL', 'page': '2'})
        self.assertEqual(len(response.context['provas']), 1)
        self.assertEqual(response.context['provas'][0].titulo, 'Lote 0')
        self.assertEqual(response.context['querystring'], 'q=Lote&tipo=MANUAL')


class MascaraProvaModelTest(TestCase):
    """Testes unitários de regras de negócio para o modelo MascaraProva."""

    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username='prof_mascara', password='secret_password')  # noqa: S106
        self.user.groups.add(Group.objects.get(name='Docente'))
        self.disc = Disciplina.objects.create(nome='Estrutura de Dados')
        self.top1 = Topico.objects.create(disciplina=self.disc, nome='Árvores')
        self.top2 = Topico.objects.create(disciplina=self.disc, nome='Filas')

    def test_mascara_valid_clean(self):
        """Verifica que uma máscara com 40/40/20 (soma 100%) é válida."""
        mascara = MascaraProva(
            titulo='AV1 Estrutura de Dados',
            disciplina=self.disc,
            quantidade_questoes=10,
            modalidade=MascaraProva.Modalidade.OBJETIVA,
            percentual_facil=40,
            percentual_medio=40,
            percentual_dificil=20,
            autor=self.user,
        )
        mascara.full_clean()
        mascara.save()
        self.assertIsNotNone(mascara.pk)

    def test_mascara_invalid_difficulty_sum_raises_validation_error(self):
        """Verifica que percentuais de dificuldade cuja soma difere de 100% levantam erro."""
        mascara = MascaraProva(
            titulo='Máscara Inválida',
            disciplina=self.disc,
            quantidade_questoes=10,
            percentual_facil=50,
            percentual_medio=50,
            percentual_dificil=10,  # Soma = 110%
        )
        with self.assertRaises(ValidationError) as ctx:
            mascara.full_clean()

        self.assertIn('percentual_facil', ctx.exception.message_dict)

    def test_mascara_mixed_modality_count_validation(self):
        """Verifica que modalidade mista valida a soma de objetivas e discursivas."""
        mascara = MascaraProva(
            titulo='Máscara Mista',
            disciplina=self.disc,
            quantidade_questoes=10,
            modalidade=MascaraProva.Modalidade.MISTA,
            quantidade_objetivas=7,
            quantidade_discursivas=2,  # Soma = 9 != 10
            percentual_facil=40,
            percentual_medio=40,
            percentual_dificil=20,
        )
        with self.assertRaises(ValidationError) as ctx:
            mascara.full_clean()

        self.assertIn('quantidade_questoes', ctx.exception.message_dict)

    def test_calcular_distribuicao_dificuldade_precision(self):
        """Verifica cálculo inteiro de questões por dificuldade com ajuste de arredondamento."""
        mascara = MascaraProva(
            titulo='Máscara 10 Questões',
            disciplina=self.disc,
            quantidade_questoes=10,
            percentual_facil=40,
            percentual_medio=40,
            percentual_dificil=20,
        )
        dist = mascara.calcular_distribuicao_dificuldade()
        self.assertEqual(dist['F'], 4)
        self.assertEqual(dist['M'], 4)
        self.assertEqual(dist['D'], 2)
        self.assertEqual(sum(dist.values()), 10)

    def test_mascara_auto_syncs_criterios_dificuldade_on_save(self):
        """Verifica que ao salvar uma máscara os critérios normalizados de dificuldade são sincronizados."""
        mascara = Mascara(
            titulo='AV1 Estrutura de Dados Sync',
            disciplina=self.disc,
            quantidade_questoes=10,
            percentual_facil=40,
            percentual_medio=40,
            percentual_dificil=20,
            autor=self.user,
        )
        mascara.save()

        criterios = MascaraCriterioDificuldade.objects.filter(mascara=mascara)
        self.assertEqual(criterios.count(), 3)

        crit_f = criterios.get(dificuldade='F')
        crit_m = criterios.get(dificuldade='M')
        crit_d = criterios.get(dificuldade='D')

        self.assertEqual(crit_f.percentual, 40)
        self.assertEqual(crit_f.quantidade_questoes, 4)
        self.assertEqual(crit_m.percentual, 40)
        self.assertEqual(crit_m.quantidade_questoes, 4)
        self.assertEqual(crit_d.percentual, 20)
        self.assertEqual(crit_d.quantidade_questoes, 2)
        self.assertIn('Fácil (40%)', str(crit_f))

    def test_mascara_criterio_topico_validation_other_discipline(self):
        """Verifica que associar um tópico de outra disciplina levanta ValidationError no clean()."""
        outra_disc = Disciplina.objects.create(nome='Cálculo I')
        topico_outra_disc = Topico.objects.create(disciplina=outra_disc, nome='Derivadas')

        mascara = Mascara.objects.create(
            titulo='AV1 Estrutura de Dados',
            disciplina=self.disc,
            quantidade_questoes=10,
            percentual_facil=40,
            percentual_medio=40,
            percentual_dificil=20,
            autor=self.user,
        )

        crit_topico = MascaraCriterioTopico(
            mascara=mascara,
            topico=topico_outra_disc,
            percentual=50,
            quantidade_questoes=5,
        )

        with self.assertRaises(ValidationError) as ctx:
            crit_topico.clean()

        self.assertIn('não pertence à disciplina da máscara', str(ctx.exception))

    def test_mascara_criterio_topico_valid_clean_and_str(self):
        """Verifica criação e representação textual válida de MascaraCriterioTopico."""
        mascara = Mascara.objects.create(
            titulo='AV1 Estrutura de Dados',
            disciplina=self.disc,
            quantidade_questoes=10,
            percentual_facil=40,
            percentual_medio=40,
            percentual_dificil=20,
            autor=self.user,
        )

        crit_topico = MascaraCriterioTopico(
            mascara=mascara,
            topico=self.top1,
            percentual=50,
            quantidade_questoes=5,
        )
        crit_topico.clean()
        crit_topico.save()

        self.assertIn('Árvores (50%)', str(crit_topico))
        self.assertEqual(mascara.criterios_topico.count(), 1)


class MascaraViewsTest(TestCase):
    """Testes de integração das views de Máscara de Prova."""

    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username='prof_views', password='secret_password')  # noqa: S106
        self.user.groups.add(Group.objects.get_or_create(name='Administrador')[0])
        self.client.login(username='prof_views', password='secret_password')  # noqa: S106

        self.disc = Disciplina.objects.create(nome='Banco de Dados (UBD)')
        self.top1 = Topico.objects.create(disciplina=self.disc, nome='SQL')
        self.top2 = Topico.objects.create(disciplina=self.disc, nome='Normalização')

        self.mascara = MascaraProva.objects.create(
            titulo='AV2 Banco de Dados',
            disciplina=self.disc,
            quantidade_questoes=10,
            modalidade=MascaraProva.Modalidade.OBJETIVA,
            percentual_facil=30,
            percentual_medio=50,
            percentual_dificil=20,
            autor=self.user,
        )

    def test_mascara_list_view(self):
        """Verifica listagem de máscaras."""
        url = reverse('provas:mascara_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'AV2 Banco de Dados')
        self.assertContains(response, 'Banco de Dados (UBD)')

    def test_mascara_create_post_success(self):
        """Verifica criação de nova máscara com formset de tópicos."""
        url = reverse('provas:mascara_create')
        post_data = {
            'titulo': 'Nova Máscara Criada via Teste',
            'descricao': 'Observações de teste',
            'disciplina': self.disc.pk,
            'quantidade_questoes': 10,
            'modalidade': 'OBJ',
            'quantidade_objetivas': 10,
            'quantidade_discursivas': 0,
            'percentual_facil': 40,
            'percentual_medio': 40,
            'percentual_dificil': 20,
            'valor_total': '10.00',
            'tempo_estimado_minutos': 90,
            'embaralhar_questoes': 'on',
            'embaralhar_alternativas': 'on',
            'ativo': 'on',
            # Formset tópicos
            'topicos_distribuicao-TOTAL_FORMS': '2',
            'topicos_distribuicao-INITIAL_FORMS': '0',
            'topicos_distribuicao-MIN_NUM_FORMS': '0',
            'topicos_distribuicao-MAX_NUM_FORMS': '1000',
            'topicos_distribuicao-0-topico': str(self.top1.pk),
            'topicos_distribuicao-0-percentual': '60',
            'topicos_distribuicao-0-quantidade_questoes': '6',
            'topicos_distribuicao-1-topico': str(self.top2.pk),
            'topicos_distribuicao-1-percentual': '40',
            'topicos_distribuicao-1-quantidade_questoes': '4',
        }
        response = self.client.post(url, post_data)
        self.assertEqual(response.status_code, 302)

        nova = MascaraProva.objects.get(titulo='Nova Máscara Criada via Teste')
        self.assertEqual(nova.autor, self.user)
        self.assertEqual(nova.topicos_distribuicao.count(), 2)

    def test_mascara_detail_view_with_diagnostico(self):
        """Verifica tela de detalhes com diagnóstico de estoque."""
        # Cria questões no acervo para teste de diagnóstico
        Questao.objects.create(
            disciplina=self.disc,
            topico=self.top1,
            tipo=Questao.Tipo.OBJETIVA,
            dificuldade=Questao.Dificuldade.FACIL,
            enunciado='Questão fácil de SQL',
            ativo=True,
        )

        url = reverse('provas:mascara_detail', kwargs={'pk': self.mascara.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'AV2 Banco de Dados')
        self.assertContains(response, 'Diagnóstico de Estoque no Banco de Questões')

    def test_mascara_toggle_ativo(self):
        """Verifica alternância de status ativo/inativo."""
        url = reverse('provas:mascara_toggle_ativo', kwargs={'pk': self.mascara.pk})
        response = self.client.post(url)
        self.assertRedirects(response, reverse('provas:mascara_list'))

        self.mascara.refresh_from_db()
        self.assertFalse(self.mascara.ativo)

    def test_api_topicos_e_estoque_json(self):
        """Verifica endpoint AJAX de consulta de tópicos e estoque em tempo real."""
        url = f'{reverse("provas:api_topicos_estoque")}?disciplina_id={self.disc.pk}'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['disciplina_id'], self.disc.pk)
        self.assertEqual(len(data['topicos']), 2)
        self.assertIn('estoque', data)


class MascaraInterdisciplinarTest(TestCase):
    """Garante a geração automática de simulados com todas as disciplinas."""

    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username='coord_integrado', password='secret_password')  # noqa: S106
        self.user.groups.add(Group.objects.get(name='Docente'))
        self.disciplina_1 = Disciplina.objects.create(nome='Algoritmos')
        self.disciplina_2 = Disciplina.objects.create(nome='Banco de Dados')
        self.topico_1 = Topico.objects.create(disciplina=self.disciplina_1, nome='Estruturas de Controle')
        self.topico_2 = Topico.objects.create(disciplina=self.disciplina_2, nome='Modelagem Relacional')

        Questao.objects.create(
            disciplina=self.disciplina_1,
            topico=self.topico_1,
            tipo=Questao.Tipo.OBJETIVA,
            dificuldade=Questao.Dificuldade.FACIL,
            enunciado='Questão integrada fácil.',
        )
        Questao.objects.create(
            disciplina=self.disciplina_2,
            topico=self.topico_2,
            tipo=Questao.Tipo.OBJETIVA,
            dificuldade=Questao.Dificuldade.MEDIO,
            enunciado='Questão integrada média.',
        )

    def test_gera_prova_com_questoes_de_disciplinas_distintas(self):
        mascara = Mascara.objects.create(
            titulo='Simulado ENADE 2026 — ADS',
            quantidade_questoes=2,
            modalidade=Mascara.Modalidade.OBJETIVA,
            quantidade_objetivas=2,
            quantidade_discursivas=0,
            percentual_facil=50,
            percentual_medio=50,
            percentual_dificil=0,
            autor=self.user,
        )

        self.assertIsNone(mascara.disciplina)
        self.assertTrue(mascara.validar_estoque().esta_valido)
        prova = mascara.gerar_prova(autor=self.user)

        self.assertIsNone(prova.disciplina)
        self.assertEqual(prova.itens.count(), 2)
        self.assertEqual(
            {item.questao.disciplina_id for item in prova.itens.all()},
            {self.disciplina_1.pk, self.disciplina_2.pk},
        )


class WeasyPrintPDFGenerationTest(TestCase):
    """Testes de compilação de PDF com WeasyPrint e validação de layout."""

    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username='prof_weasy', password='secret_password')  # noqa: S106
        self.user.groups.add(Group.objects.get(name='Docente'))

        self.disc = Disciplina.objects.create(nome='Estruturas de Dados')

        # Questão Objetiva com alternativas
        self.q_obj = Questao.objects.create(
            disciplina=self.disc,
            tipo=Questao.Tipo.OBJETIVA,
            dificuldade=Questao.Dificuldade.FACIL,
            origem='ENADE ADS 2021',
            numero_questao='10',
            enunciado='Qual a complexidade temporal de busca em uma Hash Table com colisão nula?',
            criterios_correcao='O acesso direto via função de espalhamento ocorre em tempo constante O(1).',
            ativo=True,
            autor=self.user,
        )
        Alternativa.objects.create(questao=self.q_obj, letra='A', texto='O(1)', correta=True)
        Alternativa.objects.create(questao=self.q_obj, letra='B', texto='O(n)', correta=False)
        Alternativa.objects.create(questao=self.q_obj, letra='C', texto='O(log n)', correta=False)

        # Questão Discursiva
        self.q_dis = Questao.objects.create(
            disciplina=self.disc,
            tipo=Questao.Tipo.DISCURSIVA,
            dificuldade=Questao.Dificuldade.MEDIO,
            origem='POSCOMP 2025',
            numero_questao='25',
            enunciado='Explique o conceito de lista encadeada simples e compare com um vetor estático.',
            criterios_correcao='Abordar alocação dinâmica, nós com ponteiros e complexidade de inserção no início.',
            ativo=True,
            autor=self.user,
        )

    def test_pdf_service_generates_valid_pdf_bytes(self):
        """Verifica se o PDFGeneratorService compila o template e produz bytes de PDF válidos."""
        context = {
            'prova': SimpleNamespace(titulo='Avaliação de Teste Unitário', numero_caderno=1, pk=1),
            'disciplina_nome': 'Estruturas de Dados',
            'docente_nome': 'Prof. Teste',
            'identificador_display': 'Versão A',
            'total_questoes': 2,
        }

        pdf_bytes = PDFGeneratorService.render_pdf_from_template(
            'provas/pdf_capa_prova.html',
            context=context,
        )

        self.assertIsInstance(pdf_bytes, bytes)
        self.assertTrue(len(pdf_bytes) > 1000)
        self.assertTrue(pdf_bytes.startswith(b'%PDF-'))

    def test_pdf_service_raises_on_invalid_template(self):
        """Verifica se erro no template levanta PDFGenerationError apropriado."""
        with self.assertRaises(PDFGenerationError):
            PDFGeneratorService.render_pdf_from_template('provas/template_inexistente_xyz.html', {})

    def test_resolve_static_media_url_maps_static_to_local_file(self):
        """A URL raiz-relativa de {% static %} é reescrita para o CSS local no disco."""
        resolvido = resolve_static_media_url('file:///static/provas/css/print_prova.css')

        caminho_css = finders.find('provas/css/print_prova.css')
        self.assertIsNotNone(caminho_css, 'print_prova.css deveria ser encontrado pelos finders.')
        self.assertEqual(resolvido, Path(caminho_css).as_uri())
        self.assertIn('@page', Path(caminho_css).read_text(encoding='utf-8'))

    def test_resolve_static_media_url_maps_media_to_media_root(self):
        """A URL raiz-relativa de mídia (FieldFile.url) é reescrita para MEDIA_ROOT."""
        with tempfile.TemporaryDirectory() as tmp:
            arquivo = Path(tmp) / 'questoes' / 'figura.png'
            arquivo.parent.mkdir(parents=True)
            arquivo.write_bytes(b'\x89PNG\r\n')
            with override_settings(MEDIA_ROOT=tmp):
                resolvido = resolve_static_media_url('file:///media/questoes/figura.png')
            self.assertEqual(resolvido, arquivo.as_uri())

    def test_resolve_static_media_url_passes_through_external_urls(self):
        """URLs externas/data não são interceptadas e seguem para o fetcher padrão."""
        for url in ('https://cdn.example.com/app.css', 'data:text/css,body{color:red}'):
            self.assertEqual(resolve_static_media_url(url), url)

    def test_prova_completa_pdf_loads_static_stylesheet(self):
        """Regressão: o PDF da prova completa carrega o CSS estático (não sai sem estilo)."""
        context = {
            'prova': SimpleNamespace(numero_caderno=1, pk=1, titulo='Avaliação de Regressão'),
            'itens_render': [],
            'total_questoes': 0,
            'incluir_capa': False,
            'incluir_gabarito': False,
        }

        registros: list[str] = []
        handler = logging.Handler()
        handler.emit = lambda record: registros.append(record.getMessage())
        logger_wp = logging.getLogger('weasyprint')
        nivel_original = logger_wp.level
        logger_wp.addHandler(handler)
        logger_wp.setLevel(logging.WARNING)
        try:
            pdf_bytes = PDFGeneratorService.render_pdf_from_template('provas/pdf_prova_completa.html', context)
        finally:
            logger_wp.removeHandler(handler)
            logger_wp.setLevel(nivel_original)

        self.assertTrue(pdf_bytes.startswith(b'%PDF-'))
        self.assertFalse(
            any('Failed to load' in msg and 'print_prova.css' in msg for msg in registros),
            msg=f'WeasyPrint não carregou o CSS estático do PDF: {registros}',
        )


class MascaraListPaginationTest(TestCase):
    """Testes unitários para a paginação de Máscaras de Prova."""

    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username='docente_masc_pag', password='secret_password')  # noqa: S106
        self.user.groups.add(Group.objects.get(name='Docente'))
        self.client.login(username='docente_masc_pag', password='secret_password')  # noqa: S106
        self.url = reverse('provas:mascara_list')

        self.disc = Disciplina.objects.create(nome='Arquitetura de Computadores')

        # Cria 18 máscaras (limite de 10 por página)
        self.mascaras = [
            MascaraProva.objects.create(
                titulo=f'Máscara de Teste #{i}',
                disciplina=self.disc,
                quantidade_questoes=10,
                modalidade=MascaraProva.Modalidade.OBJETIVA,
                percentual_facil=40,
                percentual_medio=40,
                percentual_dificil=20,
                autor=self.user,
                ativo=True,
            )
            for i in range(1, 19)
        ]

    def test_mascara_pagination_page_1(self):
        """Verifica se a página 1 traz 10 máscaras e indica existência da página 2."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

        page_obj = response.context['page_obj']
        self.assertEqual(len(page_obj), 10)
        self.assertEqual(page_obj.number, 1)
        self.assertEqual(page_obj.paginator.num_pages, 2)
        self.assertTrue(page_obj.has_next())
        self.assertContains(response, 'Página 1 de 2')

    def test_mascara_pagination_page_2(self):
        """Verifica se a página 2 traz as 8 máscaras restantes."""
        response = self.client.get(f'{self.url}?page=2')
        self.assertEqual(response.status_code, 200)

        page_obj = response.context['page_obj']
        self.assertEqual(len(page_obj), 8)
        self.assertEqual(page_obj.number, 2)
        self.assertFalse(page_obj.has_next())
        self.assertTrue(page_obj.has_previous())
        self.assertContains(response, 'Página 2 de 2')

    def test_mascara_pagination_preserves_disciplina_filter(self):
        """Verifica se o filtro por disciplina é preservado ao paginar."""
        response = self.client.get(f'{self.url}?disciplina={self.disc.pk}&page=2')
        self.assertEqual(response.status_code, 200)
        self.assertIn('disciplina=', response.context['querystring'])
        self.assertNotIn('page=', response.context['querystring'])


class MascaraValidacaoPercentuaisTest(TestCase):
    """Testes unitários e de integração para validação da soma dos percentuais."""

    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username='prof_valida', password='secret_password')  # noqa: S106
        self.user.groups.add(Group.objects.get(name='Docente'))
        self.disc = Disciplina.objects.create(nome='Engenharia de Software')
        self.top1 = Topico.objects.create(disciplina=self.disc, nome='Requisitos')
        self.top2 = Topico.objects.create(disciplina=self.disc, nome='Arquitetura')
        self.top3 = Topico.objects.create(disciplina=self.disc, nome='Testes')

    def test_dificuldade_sum_under_100_fails_in_model_and_form(self):
        """Verifica que soma de dificuldade inferior a 100% (ex: 90%) é rejeitada no Model e no Form."""
        mascara = Mascara(
            titulo='Prova Falha 90%',
            disciplina=self.disc,
            quantidade_questoes=10,
            percentual_facil=30,
            percentual_medio=40,
            percentual_dificil=20,  # 30+40+20 = 90%
            autor=self.user,
        )
        with self.assertRaises(ValidationError) as ctx:
            mascara.full_clean()
        self.assertIn('faltam 10%', str(ctx.exception))

        form_data = {
            'titulo': 'Form Falha 90%',
            'disciplina': self.disc.pk,
            'quantidade_questoes': 10,
            'modalidade': 'OBJ',
            'percentual_facil': 30,
            'percentual_medio': 40,
            'percentual_dificil': 20,
            'valor_total': '10.00',
            'tempo_estimado_minutos': 60,
        }
        form = MascaraForm(data=form_data)
        self.assertFalse(form.is_valid())
        self.assertIn('percentual_facil', form.errors)
        self.assertIn('faltam 10%', form.errors['percentual_facil'][0])

    def test_dificuldade_sum_over_100_fails_in_model_and_form(self):
        """Verifica que soma de dificuldade superior a 100% (ex: 120%) é rejeitada no Model e no Form."""
        mascara = Mascara(
            titulo='Prova Falha 120%',
            disciplina=self.disc,
            quantidade_questoes=10,
            percentual_facil=50,
            percentual_medio=40,
            percentual_dificil=30,  # 50+40+30 = 120%
            autor=self.user,
        )
        with self.assertRaises(ValidationError) as ctx:
            mascara.full_clean()
        self.assertIn('excede em 20%', str(ctx.exception))

        form_data = {
            'titulo': 'Form Falha 120%',
            'disciplina': self.disc.pk,
            'quantidade_questoes': 10,
            'modalidade': 'OBJ',
            'percentual_facil': 50,
            'percentual_medio': 40,
            'percentual_dificil': 30,
            'valor_total': '10.00',
            'tempo_estimado_minutos': 60,
        }
        form = MascaraForm(data=form_data)
        self.assertFalse(form.is_valid())
        self.assertIn('percentual_facil', form.errors)
        self.assertIn('excede em 20%', form.errors['percentual_facil'][0])

    def test_dificuldade_sum_exact_100_passes(self):
        """Verifica que soma de dificuldade exatamente 100% é válida no Model e no Form."""
        mascara = Mascara(
            titulo='Prova Válida 100%',
            disciplina=self.disc,
            quantidade_questoes=10,
            modalidade='OBJ',
            quantidade_objetivas=10,
            quantidade_discursivas=0,
            percentual_facil=40,
            percentual_medio=40,
            percentual_dificil=20,
            autor=self.user,
        )
        mascara.full_clean()
        mascara.save()
        self.assertIsNotNone(mascara.pk)

        form_data = {
            'titulo': 'Form Válido 100%',
            'disciplina': self.disc.pk,
            'quantidade_questoes': 10,
            'modalidade': 'OBJ',
            'quantidade_objetivas': 10,
            'quantidade_discursivas': 0,
            'percentual_facil': 40,
            'percentual_medio': 40,
            'percentual_dificil': 20,
            'valor_total': '10.00',
            'tempo_estimado_minutos': 60,
        }
        form = MascaraForm(data=form_data)
        self.assertTrue(form.is_valid())

    def test_topicos_sum_under_100_fails_in_formset(self):
        """Verifica que formset de tópicos somando menos de 100% (ex: 90%) é inválido."""
        mascara = Mascara.objects.create(
            titulo='Mascara Base',
            disciplina=self.disc,
            quantidade_questoes=10,
            percentual_facil=40,
            percentual_medio=40,
            percentual_dificil=20,
            autor=self.user,
        )
        formset_data = {
            'topicos_distribuicao-TOTAL_FORMS': '2',
            'topicos_distribuicao-INITIAL_FORMS': '0',
            'topicos_distribuicao-MIN_NUM_FORMS': '0',
            'topicos_distribuicao-MAX_NUM_FORMS': '1000',
            'topicos_distribuicao-0-topico': str(self.top1.pk),
            'topicos_distribuicao-0-percentual': '50',
            'topicos_distribuicao-0-quantidade_questoes': '5',
            'topicos_distribuicao-1-topico': str(self.top2.pk),
            'topicos_distribuicao-1-percentual': '40',  # 50 + 40 = 90%
            'topicos_distribuicao-1-quantidade_questoes': '4',
        }
        formset = MascaraCriterioTopicoFormSet(data=formset_data, instance=mascara)
        self.assertFalse(formset.is_valid())
        self.assertIn('faltam 10%', formset.non_form_errors()[0])

    def test_topicos_sum_over_100_fails_in_formset(self):
        """Verifica que formset de tópicos somando mais de 100% (ex: 110%) é inválido."""
        mascara = Mascara.objects.create(
            titulo='Mascara Base 2',
            disciplina=self.disc,
            quantidade_questoes=10,
            percentual_facil=40,
            percentual_medio=40,
            percentual_dificil=20,
            autor=self.user,
        )
        formset_data = {
            'topicos_distribuicao-TOTAL_FORMS': '2',
            'topicos_distribuicao-INITIAL_FORMS': '0',
            'topicos_distribuicao-MIN_NUM_FORMS': '0',
            'topicos_distribuicao-MAX_NUM_FORMS': '1000',
            'topicos_distribuicao-0-topico': str(self.top1.pk),
            'topicos_distribuicao-0-percentual': '60',
            'topicos_distribuicao-0-quantidade_questoes': '6',
            'topicos_distribuicao-1-topico': str(self.top2.pk),
            'topicos_distribuicao-1-percentual': '50',  # 60 + 50 = 110%
            'topicos_distribuicao-1-quantidade_questoes': '5',
        }
        formset = MascaraCriterioTopicoFormSet(data=formset_data, instance=mascara)
        self.assertFalse(formset.is_valid())
        self.assertIn('excede em 10%', formset.non_form_errors()[0])

    def test_topicos_sum_exact_100_passes_in_formset(self):
        """Verifica que formset de tópicos somando exatamente 100% é válido."""
        mascara = Mascara.objects.create(
            titulo='Mascara Base 3',
            disciplina=self.disc,
            quantidade_questoes=10,
            percentual_facil=40,
            percentual_medio=40,
            percentual_dificil=20,
            autor=self.user,
        )
        formset_data = {
            'topicos_distribuicao-TOTAL_FORMS': '2',
            'topicos_distribuicao-INITIAL_FORMS': '0',
            'topicos_distribuicao-MIN_NUM_FORMS': '0',
            'topicos_distribuicao-MAX_NUM_FORMS': '1000',
            'topicos_distribuicao-0-topico': str(self.top1.pk),
            'topicos_distribuicao-0-percentual': '60',
            'topicos_distribuicao-0-quantidade_questoes': '6',
            'topicos_distribuicao-1-topico': str(self.top2.pk),
            'topicos_distribuicao-1-percentual': '40',  # 60 + 40 = 100%
            'topicos_distribuicao-1-quantidade_questoes': '4',
        }
        formset = MascaraCriterioTopicoFormSet(data=formset_data, instance=mascara)
        self.assertTrue(formset.is_valid())

    def test_topico_com_percentual_zero_fails(self):
        """Verifica que selecionar um tópico com percentual zero levanta erro."""
        mascara = Mascara.objects.create(
            titulo='Mascara Base 4',
            disciplina=self.disc,
            quantidade_questoes=10,
            percentual_facil=40,
            percentual_medio=40,
            percentual_dificil=20,
            autor=self.user,
        )
        formset_data = {
            'topicos_distribuicao-TOTAL_FORMS': '2',
            'topicos_distribuicao-INITIAL_FORMS': '0',
            'topicos_distribuicao-MIN_NUM_FORMS': '0',
            'topicos_distribuicao-MAX_NUM_FORMS': '1000',
            'topicos_distribuicao-0-topico': str(self.top1.pk),
            'topicos_distribuicao-0-percentual': '100',
            'topicos_distribuicao-0-quantidade_questoes': '10',
            'topicos_distribuicao-1-topico': str(self.top2.pk),
            'topicos_distribuicao-1-percentual': '0',
            'topicos_distribuicao-1-quantidade_questoes': '0',
        }
        formset = MascaraCriterioTopicoFormSet(data=formset_data, instance=mascara)
        self.assertFalse(formset.is_valid())
        self.assertIn('maior que 0%', str(formset.errors))

    def test_percentual_sem_topico_fails(self):
        """Verifica que informar percentual sem selecionar tópico correspondente levanta erro."""
        mascara = Mascara.objects.create(
            titulo='Mascara Base 5',
            disciplina=self.disc,
            quantidade_questoes=10,
            percentual_facil=40,
            percentual_medio=40,
            percentual_dificil=20,
            autor=self.user,
        )
        formset_data = {
            'topicos_distribuicao-TOTAL_FORMS': '1',
            'topicos_distribuicao-INITIAL_FORMS': '0',
            'topicos_distribuicao-MIN_NUM_FORMS': '0',
            'topicos_distribuicao-MAX_NUM_FORMS': '1000',
            'topicos_distribuicao-0-topico': '',
            'topicos_distribuicao-0-percentual': '100',
            'topicos_distribuicao-0-quantidade_questoes': '10',
        }
        formset = MascaraCriterioTopicoFormSet(data=formset_data, instance=mascara)
        self.assertFalse(formset.is_valid())
        self.assertIn('topico', str(formset.errors))

    def test_topico_duplicado_fails(self):
        """Verifica que selecionar o mesmo tópico duas vezes levanta erro de duplicidade."""
        mascara = Mascara.objects.create(
            titulo='Mascara Base 6',
            disciplina=self.disc,
            quantidade_questoes=10,
            percentual_facil=40,
            percentual_medio=40,
            percentual_dificil=20,
            autor=self.user,
        )
        formset_data = {
            'topicos_distribuicao-TOTAL_FORMS': '2',
            'topicos_distribuicao-INITIAL_FORMS': '0',
            'topicos_distribuicao-MIN_NUM_FORMS': '0',
            'topicos_distribuicao-MAX_NUM_FORMS': '1000',
            'topicos_distribuicao-0-topico': str(self.top1.pk),
            'topicos_distribuicao-0-percentual': '50',
            'topicos_distribuicao-0-quantidade_questoes': '5',
            'topicos_distribuicao-1-topico': str(self.top1.pk),
            'topicos_distribuicao-1-percentual': '50',
            'topicos_distribuicao-1-quantidade_questoes': '5',
        }
        formset = MascaraCriterioTopicoFormSet(data=formset_data, instance=mascara)
        self.assertFalse(formset.is_valid())
        self.assertTrue(any(formset.errors))

    def test_distribuicao_livre_sem_topicos_passes(self):
        """Verifica que formulário sem tópicos (distribuição livre) é válido."""
        mascara = Mascara.objects.create(
            titulo='Mascara Base 7',
            disciplina=self.disc,
            quantidade_questoes=10,
            percentual_facil=40,
            percentual_medio=40,
            percentual_dificil=20,
            autor=self.user,
        )
        formset_data = {
            'topicos_distribuicao-TOTAL_FORMS': '0',
            'topicos_distribuicao-INITIAL_FORMS': '0',
            'topicos_distribuicao-MIN_NUM_FORMS': '0',
            'topicos_distribuicao-MAX_NUM_FORMS': '1000',
        }
        formset = MascaraCriterioTopicoFormSet(data=formset_data, instance=mascara)
        self.assertTrue(formset.is_valid())

    def test_topico_outra_disciplina_rejeitado_no_formset(self):
        """Verifica que associar um tópico de outra disciplina no formset é rejeitado."""
        outra_disc = Disciplina.objects.create(nome='Cálculo Vetorial')
        topico_invalido = Topico.objects.create(disciplina=outra_disc, nome='Gradiente e Rotacional')

        mascara = Mascara.objects.create(
            titulo='Mascara Filtro Disciplina',
            disciplina=self.disc,
            quantidade_questoes=10,
            percentual_facil=40,
            percentual_medio=40,
            percentual_dificil=20,
            autor=self.user,
        )
        formset_data = {
            'topicos_distribuicao-TOTAL_FORMS': '2',
            'topicos_distribuicao-INITIAL_FORMS': '0',
            'topicos_distribuicao-MIN_NUM_FORMS': '0',
            'topicos_distribuicao-MAX_NUM_FORMS': '1000',
            'topicos_distribuicao-0-topico': str(self.top1.pk),
            'topicos_distribuicao-0-percentual': '50',
            'topicos_distribuicao-0-quantidade_questoes': '5',
            'topicos_distribuicao-1-topico': str(topico_invalido.pk),
            'topicos_distribuicao-1-percentual': '50',
            'topicos_distribuicao-1-quantidade_questoes': '5',
        }
        formset = MascaraCriterioTopicoFormSet(data=formset_data, instance=mascara, disciplina=self.disc)
        self.assertFalse(formset.is_valid())
        self.assertIn('topico', formset.errors[1])

    def test_formset_inicializa_com_queryset_filtrado_por_disciplina(self):
        """Verifica que ao instanciar o formset com disciplina os campos select contêm apenas tópicos da disciplina."""
        outra_disc = Disciplina.objects.create(nome='Física Teórica')
        topico_outra = Topico.objects.create(disciplina=outra_disc, nome='Termodinâmica')

        formset = MascaraCriterioTopicoFormSet(disciplina=self.disc)
        for form in formset.forms:
            qs = form.fields['topico'].queryset
            self.assertIn(self.top1, qs)
            self.assertIn(self.top2, qs)
            self.assertNotIn(topico_outra, qs)


class MatrizMetasCalculoTest(TestCase):
    """Testes unitários e de integração para o cálculo de metas por célula (Maior Resto)."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        user_model = get_user_model()
        self.user = user_model.objects.create_user(username='docente_matriz', password='secret_password')  # noqa: S106
        self.user.groups.add(Group.objects.get(name='Docente'))
        self.client.login(username='docente_matriz', password='secret_password')  # noqa: S106
        self.disc = Disciplina.objects.create(nome='Engenharia de Software')
        self.top1 = Topico.objects.create(disciplina=self.disc, nome='Arquitetura de Software')
        self.top2 = Topico.objects.create(disciplina=self.disc, nome='Padrões de Projeto')
        self.top3 = Topico.objects.create(disciplina=self.disc, nome='Testes Automatizados')

    def test_calcular_metas_celulas_caso_padrao_soma_exata(self):
        """Verifica cálculo de cotas e distribuição do maior resto em caso com 10 questões e 3 tópicos."""
        from provas.services.matrix_calculator import calcular_metas_celulas

        topicos = [
            {'id': 1, 'nome': 'Tópico 1', 'percentual': 50},
            {'id': 2, 'nome': 'Tópico 2', 'percentual': 30},
            {'id': 3, 'nome': 'Tópico 3', 'percentual': 20},
        ]
        res = calcular_metas_celulas(
            quantidade_total=10,
            percentual_facil=40,
            percentual_medio=40,
            percentual_dificil=20,
            topicos=topicos,
        )

        self.assertEqual(res.total_questoes, 10)
        self.assertEqual(len(res.linhas), 3)
        self.assertEqual(len(res.celulas), 9)

        # Soma total de todas as células deve ser estritamente igual a 10
        total_alocado = sum(c.quantidade for c in res.celulas)
        self.assertEqual(total_alocado, 10)

        # Verificação das cotas individuais
        # T1 (50%): F=2, M=2, D=1 (soma 5)
        self.assertEqual(res.get_quantidade(1, 'F'), 2)
        self.assertEqual(res.get_quantidade(1, 'M'), 2)
        self.assertEqual(res.get_quantidade(1, 'D'), 1)

        # T2 (30%): F=1, M=1, D=1 (soma 3)
        self.assertEqual(res.get_quantidade(2, 'F'), 1)
        self.assertEqual(res.get_quantidade(2, 'M'), 1)
        self.assertEqual(res.get_quantidade(2, 'D'), 1)

        # T3 (20%): F=1, M=1, D=0 (soma 2)
        self.assertEqual(res.get_quantidade(3, 'F'), 1)
        self.assertEqual(res.get_quantidade(3, 'M'), 1)
        self.assertEqual(res.get_quantidade(3, 'D'), 0)

        # Totais por dificuldade
        self.assertEqual(res.totais_por_dificuldade['F'], 4)
        self.assertEqual(res.totais_por_dificuldade['M'], 4)
        self.assertEqual(res.totais_por_dificuldade['D'], 2)

    def test_calcular_metas_celulas_distribuicao_livre(self):
        """Verifica que quando não há tópicos específicos, a matriz é gerada em modo livre somando o total."""
        from provas.services.matrix_calculator import calcular_metas_celulas

        res = calcular_metas_celulas(
            quantidade_total=10,
            percentual_facil=33,
            percentual_medio=34,
            percentual_dificil=33,
            topicos=None,
        )
        self.assertTrue(res.eh_distribuicao_livre)
        self.assertEqual(res.total_questoes, 10)
        self.assertEqual(sum(c.quantidade for c in res.celulas), 10)
        self.assertEqual(sum(res.totais_por_dificuldade.values()), 10)

    def test_invariante_soma_diversos_tamanhos_prova(self):
        """Garante a invariante de soma = total para múltiplos tamanhos de prova e distribuições assimétricas."""
        from provas.services.matrix_calculator import calcular_metas_celulas

        topicos = [
            {'id': 1, 'nome': 'T1', 'percentual': 45},
            {'id': 2, 'nome': 'T2', 'percentual': 35},
            {'id': 3, 'nome': 'T3', 'percentual': 20},
        ]

        for n in [1, 3, 5, 7, 8, 10, 13, 15, 20, 33, 50, 100]:
            res = calcular_metas_celulas(
                quantidade_total=n,
                percentual_facil=30,
                percentual_medio=50,
                percentual_dificil=20,
                topicos=topicos,
            )
            soma_celulas = sum(c.quantidade for c in res.celulas)
            soma_linhas = sum(linha.total_questoes for linha in res.linhas)
            soma_cols = sum(res.totais_por_dificuldade.values())

            self.assertEqual(soma_celulas, n, f'Falha na soma de células para N={n}')
            self.assertEqual(soma_linhas, n, f'Falha na soma de linhas para N={n}')
            self.assertEqual(soma_cols, n, f'Falha na soma de colunas para N={n}')
            self.assertTrue(all(c.quantidade >= 0 for c in res.celulas))

    def test_mascara_model_calcular_matriz_metas_integracao(self):
        """Verifica o método calcular_matriz_metas() no modelo Mascara persistido no banco."""
        mascara = Mascara.objects.create(
            titulo='Prova Final de ES',
            disciplina=self.disc,
            quantidade_questoes=10,
            percentual_facil=40,
            percentual_medio=40,
            percentual_dificil=20,
            autor=self.user,
        )
        MascaraCriterioTopico.objects.create(mascara=mascara, topico=self.top1, percentual=60)
        MascaraCriterioTopico.objects.create(mascara=mascara, topico=self.top2, percentual=40)

        resultado = mascara.calcular_matriz_metas()
        self.assertEqual(resultado.total_questoes, 10)
        self.assertEqual(len(resultado.linhas), 2)
        self.assertEqual(sum(c.quantidade for c in resultado.celulas), 10)
        self.assertEqual(resultado.get_quantidade(self.top1.pk, 'F'), 2)
        self.assertEqual(resultado.get_quantidade(self.top1.pk, 'M'), 2)

    def test_mascara_detail_view_contem_matriz_metas(self):
        """Verifica que a view mascara_detail injeta e renderiza a matriz de metas com sucesso."""
        from django.urls import reverse

        mascara = Mascara.objects.create(
            titulo='Prova View Matriz',
            disciplina=self.disc,
            quantidade_questoes=10,
            percentual_facil=40,
            percentual_medio=40,
            percentual_dificil=20,
            autor=self.user,
        )
        MascaraCriterioTopico.objects.create(mascara=mascara, topico=self.top1, percentual=50)
        MascaraCriterioTopico.objects.create(mascara=mascara, topico=self.top2, percentual=50)

        url = reverse('provas:mascara_detail', kwargs={'pk': mascara.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('matriz_metas', response.context)
        self.assertEqual(response.context['matriz_metas'].total_questoes, 10)
        self.assertContains(response, 'Matriz de Metas por Célula (Tópico × Dificuldade)')


class SelecaoQuestoesMenosUsadasTest(TestCase):
    """Testes unitários e de integração para a consulta priorizando questões menos usadas (RF07)."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        user_model = get_user_model()
        self.user = user_model.objects.create_user(username='docente_rf07', password='secret_password')  # noqa: S106
        self.user.groups.add(Group.objects.get(name='Docente'))
        self.disc = Disciplina.objects.create(nome='Algoritmos e Estrutura de Dados')
        self.topico = Topico.objects.create(disciplina=self.disc, nome='Árvores Binárias')

        # Criar 6 questões no banco para o mesmo tópico e dificuldade
        self.questoes = [
            Questao.objects.create(
                disciplina=self.disc,
                topico=self.topico,
                tipo=Questao.Tipo.OBJETIVA,
                dificuldade=Questao.Dificuldade.FACIL,
                enunciado=f'Questão de teste {i}',
                autor=self.user,
                ativo=True,
            )
            for i in range(1, 7)
        ]

        self.mascara = Mascara.objects.create(
            titulo='AV1 Algoritmos',
            disciplina=self.disc,
            quantidade_questoes=2,
            percentual_facil=100,
            percentual_medio=0,
            percentual_dificil=0,
            autor=self.user,
        )

    def test_questoes_nunca_usadas_tem_prioridade_maxima(self):
        """Verifica que questões não usadas nas últimas gerações têm prioridade máxima sobre as usadas."""
        from provas.models import Prova, ProvaQuestao

        # Gera Prova 1 usando Q1 e Q2
        p1 = Prova.objects.create(mascara=self.mascara, titulo='Caderno 1', autor=self.user)
        ProvaQuestao.objects.create(prova=p1, questao=self.questoes[0], ordem=1, numero=1)
        ProvaQuestao.objects.create(prova=p1, questao=self.questoes[1], ordem=2, numero=2)

        # Consulta candidatas priorizadas
        candidatas = list(self.mascara.consultar_questoes_priorizadas())

        # Q3..Q6 devem ter 0 usos e vir antes de Q1 e Q2 (que têm 1 uso)
        nao_usadas = [q for q in candidatas if q.vezes_usada_recentemente == 0]
        usadas = [q for q in candidatas if q.vezes_usada_recentemente == 1]

        self.assertEqual(len(nao_usadas), 4)
        self.assertEqual(len(usadas), 2)
        self.assertEqual({q.id for q in usadas}, {self.questoes[0].id, self.questoes[1].id})

        # Os 4 primeiros itens do queryset devem ser as não usadas
        primeiras_4_ids = {q.id for q in candidatas[:4]}
        self.assertEqual(
            primeiras_4_ids, {self.questoes[2].id, self.questoes[3].id, self.questoes[4].id, self.questoes[5].id}
        )

    def test_janela_deslizante_ultimas_5_geracoes(self):
        """Verifica que apenas as 5 provas mais recentes da máscara são consideradas na contagem."""
        import datetime

        from django.utils import timezone

        from provas.models import Prova, ProvaQuestao

        agora = timezone.now()

        # Cria 6 provas com timestamps progressivos
        # Prova 1 (mais antiga, fora da janela de 5): usa Q1
        p1 = Prova.objects.create(mascara=self.mascara, titulo='Prova 1', autor=self.user)
        Prova.objects.filter(pk=p1.pk).update(criado_em=agora - datetime.timedelta(days=10))
        ProvaQuestao.objects.create(prova=p1, questao=self.questoes[0], ordem=1, numero=1)

        # Provas 2..6 (as 5 mais recentes): usam Q2 e Q3
        for i in range(2, 7):
            p = Prova.objects.create(mascara=self.mascara, titulo=f'Prova {i}', autor=self.user)
            Prova.objects.filter(pk=p.pk).update(criado_em=agora - datetime.timedelta(days=7 - i))
            ProvaQuestao.objects.create(prova=p, questao=self.questoes[1], ordem=1, numero=1)
            ProvaQuestao.objects.create(prova=p, questao=self.questoes[2], ordem=2, numero=2)

        candidatas = list(self.mascara.consultar_questoes_priorizadas())
        q1 = next(q for q in candidatas if q.id == self.questoes[0].id)
        q2 = next(q for q in candidatas if q.id == self.questoes[1].id)

        # Q1 foi usada apenas na Prova 1 (6ª mais recente, fora da janela de 5), então vezes_usada_recentemente == 0
        self.assertEqual(q1.vezes_usada_recentemente, 0)
        # Q2 foi usada em todas as 5 provas mais recentes, então vezes_usada_recentemente == 5
        self.assertEqual(q2.vezes_usada_recentemente, 5)

    def test_isolamento_historico_por_mascara(self):
        """Verifica que o histórico de uso não vaza entre máscaras diferentes."""
        from provas.models import Prova, ProvaQuestao

        outra_mascara = Mascara.objects.create(
            titulo='Outra Turma de Algoritmos',
            disciplina=self.disc,
            quantidade_questoes=2,
            percentual_facil=100,
            percentual_medio=0,
            percentual_dificil=0,
            autor=self.user,
        )

        # Gera prova na outra_mascara usando Q1
        p_outra = Prova.objects.create(mascara=outra_mascara, titulo='Caderno Outra', autor=self.user)
        ProvaQuestao.objects.create(prova=p_outra, questao=self.questoes[0], ordem=1, numero=1)

        # Na máscara original, Q1 deve continuar com 0 utilizações
        candidatas_orig = list(self.mascara.consultar_questoes_priorizadas())
        q1_orig = next(q for q in candidatas_orig if q.id == self.questoes[0].id)
        self.assertEqual(q1_orig.vezes_usada_recentemente, 0)

        # Na outra máscara, Q1 tem 1 utilização
        candidatas_outra = list(outra_mascara.consultar_questoes_priorizadas())
        q1_outra = next(q for q in candidatas_outra if q.id == self.questoes[0].id)
        self.assertEqual(q1_outra.vezes_usada_recentemente, 1)

    def test_priorizacao_enade_com_menos_usadas(self):
        """Verifica que priorizar_enade=True pontua questões ENADE mantendo desempate por menor uso."""
        self.mascara.priorizar_enade = True
        self.mascara.save()

        # Marca Q4 como ENADE
        self.questoes[3].origem = 'ENADE ADS 2021'
        self.questoes[3].save()

        candidatas = list(self.mascara.consultar_questoes_priorizadas())
        # Q4 deve ser a primeira questão devido a priorizar_enade
        self.assertEqual(candidatas[0].id, self.questoes[3].id)

    def test_selecionar_questoes_por_matriz_fluxo_completo(self):
        """Verifica a seleção automatizada completa por matriz de metas sem duplicações."""
        selecionadas = self.mascara.selecionar_questoes_automatizada()
        self.assertEqual(len(selecionadas), 2)
        # IDs devem ser únicos
        ids = [q.id for q in selecionadas]
        self.assertEqual(len(ids), len(set(ids)))


class BloqueioEstoqueInsuficienteTest(TestCase):
    """Testes para validação de disponibilidade e bloqueio de geração por insuficiência de estoque."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        user_model = get_user_model()
        self.user = user_model.objects.create_user(username='docente_bloqueio', password='secret_password')  # noqa: S106
        self.user.groups.add(Group.objects.get(name='Docente'))
        self.client.login(username='docente_bloqueio', password='secret_password')  # noqa: S106
        self.disc = Disciplina.objects.create(nome='Banco de Dados II')
        self.top1 = Topico.objects.create(disciplina=self.disc, nome='Indexação e Árvores B+')
        self.top2 = Topico.objects.create(disciplina=self.disc, nome='Processamento de Consultas')

        # Máscara que exige 4 questões:
        # T1 (50% = 2 questões): 1 Fácil (50%), 1 Difícil (50%)
        # T2 (50% = 2 questões): 1 Fácil (50%), 1 Difícil (50%)
        self.mascara = Mascara.objects.create(
            titulo='AV2 Banco de Dados',
            disciplina=self.disc,
            quantidade_questoes=4,
            percentual_facil=50,
            percentual_medio=0,
            percentual_dificil=50,
            autor=self.user,
        )
        MascaraCriterioTopico.objects.create(mascara=self.mascara, topico=self.top1, percentual=50)
        MascaraCriterioTopico.objects.create(mascara=self.mascara, topico=self.top2, percentual=50)

    def test_diagnostico_identifica_deficits_exatos_por_celula(self):
        """Verifica que o relatório identifica com precisão as células com déficit e calcula itens faltantes."""
        # Criamos apenas 1 questão Fácil no T1 (demanda é 1 Fácil e 1 Difícil para T1 e para T2)
        Questao.objects.create(
            disciplina=self.disc,
            topico=self.top1,
            tipo=Questao.Tipo.OBJETIVA,
            dificuldade=Questao.Dificuldade.FACIL,
            enunciado='Questão T1 Fácil',
            autor=self.user,
            ativo=True,
        )

        relatorio = self.mascara.validar_estoque()
        self.assertFalse(relatorio.esta_valido)
        # Faltam: T1 Difícil (1), T2 Fácil (1), T2 Difícil (1) -> 3 déficits
        self.assertEqual(len(relatorio.deficits), 3)
        self.assertEqual(relatorio.total_faltante, 3)

        # Verifica dados de um déficit específico
        deficit_t1_d = next(d for d in relatorio.deficits if d.topico_id == self.top1.id and d.dificuldade == 'D')
        self.assertEqual(deficit_t1_d.quantidade_necessaria, 1)
        self.assertEqual(deficit_t1_d.quantidade_disponivel, 0)
        self.assertEqual(deficit_t1_d.quantidade_faltante, 1)

    def test_bloqueio_gerar_prova_lanca_excecao_e_nao_comita(self):
        """Garante que a tentativa de gerar prova com estoque insuficiente é abortada sem salvar nada."""
        from provas.models import Prova, ProvaQuestao
        from provas.services.stock_validator import EstoqueInsuficienteError

        with self.assertRaises(EstoqueInsuficienteError) as ctx:
            self.mascara.gerar_prova(autor=self.user)

        self.assertIn('Déficits identificados', str(ctx.exception))
        # Nenhuma prova deve ter sido persistida
        self.assertEqual(Prova.objects.count(), 0)
        self.assertEqual(ProvaQuestao.objects.count(), 0)

    def test_gerar_prova_sucesso_quando_estoque_suficiente(self):
        """Garante que com estoque completo a avaliação é criada e todas as questões são associadas."""
        # Popula exatamente o estoque necessário:
        # T1: 1 Fácil, 1 Difícil
        Questao.objects.create(
            disciplina=self.disc,
            topico=self.top1,
            tipo=Questao.Tipo.OBJETIVA,
            dificuldade=Questao.Dificuldade.FACIL,
            enunciado='Q T1 F',
            autor=self.user,
            ativo=True,
        )
        Questao.objects.create(
            disciplina=self.disc,
            topico=self.top1,
            tipo=Questao.Tipo.OBJETIVA,
            dificuldade=Questao.Dificuldade.DIFICIL,
            enunciado='Q T1 D',
            autor=self.user,
            ativo=True,
        )
        # T2: 1 Fácil, 1 Difícil
        Questao.objects.create(
            disciplina=self.disc,
            topico=self.top2,
            tipo=Questao.Tipo.OBJETIVA,
            dificuldade=Questao.Dificuldade.FACIL,
            enunciado='Q T2 F',
            autor=self.user,
            ativo=True,
        )
        Questao.objects.create(
            disciplina=self.disc,
            topico=self.top2,
            tipo=Questao.Tipo.OBJETIVA,
            dificuldade=Questao.Dificuldade.DIFICIL,
            enunciado='Q T2 D',
            autor=self.user,
            ativo=True,
        )

        relatorio = self.mascara.validar_estoque()
        self.assertTrue(relatorio.esta_valido)
        self.assertEqual(len(relatorio.deficits), 0)

        prova = self.mascara.gerar_prova(autor=self.user)
        self.assertIsNotNone(prova.pk)
        self.assertEqual(prova.itens.count(), 4)
        self.assertEqual(prova.mascara, self.mascara)

    def test_view_mascara_gerar_prova_com_estoque_insuficiente_redireciona_com_erro(self):
        """Testa o endpoint POST mascara_gerar_prova quando o estoque está insuficiente."""
        from django.urls import reverse

        from provas.models import Prova

        url = reverse('provas:mascara_gerar_prova', kwargs={'pk': self.mascara.pk})
        response = self.client.post(url, follow=True)

        self.assertRedirects(response, reverse('provas:mascara_detail', kwargs={'pk': self.mascara.pk}))
        self.assertContains(response, 'Geração bloqueada')
        self.assertEqual(Prova.objects.count(), 0)

    def test_view_mascara_gerar_prova_com_sucesso(self):
        """Testa o endpoint POST mascara_gerar_prova gerando com sucesso e notificando o usuário."""
        from django.urls import reverse

        from provas.models import Prova

        # Criando o estoque completo
        for t in [self.top1, self.top2]:
            for d in [Questao.Dificuldade.FACIL, Questao.Dificuldade.DIFICIL]:
                Questao.objects.create(
                    disciplina=self.disc,
                    topico=t,
                    tipo=Questao.Tipo.OBJETIVA,
                    dificuldade=d,
                    enunciado=f'Q {t.nome} {d}',
                    autor=self.user,
                    ativo=True,
                )

        url = reverse('provas:mascara_gerar_prova', kwargs={'pk': self.mascara.pk})
        response = self.client.post(url, follow=True)

        self.assertEqual(Prova.objects.count(), 1)
        prova = Prova.objects.first()
        self.assertRedirects(response, reverse('provas:prova_revisao', kwargs={'pk': prova.pk}))
        self.assertContains(response, 'gerada com sucesso')


class SnapshotProvaTest(TestCase):
    """
    Testes de integridade e imutabilidade dos snapshots JSON
    registrados nos models Prova e ProvaQuestao.
    """

    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username='prof_snapshot', password='secret_password')  # noqa: S106
        self.user.groups.add(Group.objects.get(name='Docente'))
        self.disc = Disciplina.objects.create(nome='Algoritmos Snapshot')
        self.top1 = Topico.objects.create(disciplina=self.disc, nome='Grafos')
        self.top2 = Topico.objects.create(disciplina=self.disc, nome='Ordenação')

        self.mascara = Mascara.objects.create(
            titulo='AV Snapshot Test',
            disciplina=self.disc,
            quantidade_questoes=4,
            modalidade=Mascara.Modalidade.OBJETIVA,
            percentual_facil=50,
            percentual_medio=50,
            percentual_dificil=0,
            autor=self.user,
        )

        from provas.models import MascaraCriterioTopico

        MascaraCriterioTopico.objects.create(mascara=self.mascara, topico=self.top1, percentual=50)
        MascaraCriterioTopico.objects.create(mascara=self.mascara, topico=self.top2, percentual=50)

        # Estoque: 1 questão por célula (4 no total) — exatamente suficiente
        for t in [self.top1, self.top2]:
            for d in [Questao.Dificuldade.FACIL, Questao.Dificuldade.MEDIO]:
                q = Questao.objects.create(
                    disciplina=self.disc,
                    topico=t,
                    tipo=Questao.Tipo.OBJETIVA,
                    dificuldade=d,
                    enunciado=f'Enunciado snapshot {t.nome} {d}',
                    origem='ENADE 2025',
                    numero_questao='Q1',
                    autor=self.user,
                    ativo=True,
                )
                Alternativa.objects.create(questao=q, letra='A', texto='Opção A', correta=True)
                Alternativa.objects.create(questao=q, letra='B', texto='Opção B', correta=False)

        self.prova = self.mascara.gerar_prova(autor=self.user)

    def test_snapshot_mascara_contem_campos_esperados(self):
        """Verifica que snapshot_mascara contém todos os parâmetros da máscara."""
        snap = self.prova.snapshot_mascara
        campos_obrigatorios = [
            'mascara_id',
            'titulo',
            'disciplina_id',
            'disciplina_nome',
            'quantidade_questoes',
            'modalidade',
            'modalidade_display',
            'quantidade_objetivas',
            'quantidade_discursivas',
            'percentual_facil',
            'percentual_medio',
            'percentual_dificil',
            'valor_total',
            'tempo_estimado_minutos',
            'embaralhar_questoes',
            'embaralhar_alternativas',
            'priorizar_enade',
        ]
        for campo in campos_obrigatorios:
            self.assertIn(campo, snap, f'Campo "{campo}" ausente no snapshot_mascara')

        self.assertEqual(snap['mascara_id'], self.mascara.pk)
        self.assertEqual(snap['titulo'], 'AV Snapshot Test')
        self.assertEqual(snap['quantidade_questoes'], 4)
        self.assertEqual(snap['percentual_facil'], 50)

    def test_snapshot_criterios_dificuldade_contem_3_itens(self):
        """Verifica que snapshot_criterios_dificuldade tem exatamente 3 critérios (F, M, D)."""
        snap = self.prova.snapshot_criterios_dificuldade
        self.assertEqual(len(snap), 3)

        dificuldades_presentes = {item['dificuldade'] for item in snap}
        self.assertEqual(dificuldades_presentes, {'F', 'M', 'D'})

        for item in snap:
            self.assertIn('percentual', item)
            self.assertIn('quantidade', item)
            self.assertIn('dificuldade_display', item)

    def test_snapshot_criterios_topico_contem_topicos_configurados(self):
        """Verifica que snapshot_criterios_topico contém os tópicos da máscara."""
        snap = self.prova.snapshot_criterios_topico
        self.assertEqual(len(snap), 2)

        nomes = {item['topico_nome'] for item in snap}
        self.assertIn('Grafos', nomes)
        self.assertIn('Ordenação', nomes)

        for item in snap:
            self.assertIn('topico_id', item)
            self.assertIn('percentual', item)
            self.assertIn('quantidade', item)

    def test_snapshot_matriz_metas_soma_igual_total(self):
        """Verifica que snapshot_matriz_metas preserva a soma correta de questões."""
        snap = self.prova.snapshot_matriz_metas
        self.assertIn('total_questoes', snap)
        self.assertIn('celulas', snap)
        self.assertEqual(snap['total_questoes'], 4)

        soma_celulas = sum(cel['quantidade'] for cel in snap['celulas'])
        self.assertEqual(soma_celulas, 4)

    def test_snapshot_questao_em_cada_prova_questao(self):
        """Verifica que cada ProvaQuestao possui snapshot com enunciado, alternativas e metadados."""
        itens = self.prova.itens.all()
        self.assertEqual(itens.count(), 4)

        for item in itens:
            snap = item.snapshot_questao
            self.assertIn('questao_id', snap)
            self.assertIn('tipo', snap)
            self.assertIn('dificuldade', snap)
            self.assertIn('enunciado', snap)
            self.assertIn('alternativas', snap)
            self.assertIn('disciplina_nome', snap)
            self.assertIn('topico_nome', snap)

            # Questões objetivas devem ter alternativas
            self.assertEqual(snap['tipo'], 'OBJ')
            self.assertGreater(len(snap['alternativas']), 0)
            self.assertTrue(any(alt['correta'] for alt in snap['alternativas']))

    def test_snapshot_imutavel_apos_alteracao_mascara(self):
        """Garante que o snapshot não é afetado por alterações posteriores na máscara."""
        titulo_original = self.prova.snapshot_mascara['titulo']
        percentual_original = self.prova.snapshot_mascara['percentual_facil']

        # Altera a máscara
        self.mascara.titulo = 'AV Alterada Depois'
        self.mascara.percentual_facil = 30
        self.mascara.percentual_medio = 50
        self.mascara.percentual_dificil = 20
        self.mascara.save()

        # Recarrega a prova do banco
        self.prova.refresh_from_db()

        self.assertEqual(self.prova.snapshot_mascara['titulo'], titulo_original)
        self.assertEqual(self.prova.snapshot_mascara['percentual_facil'], percentual_original)

    def test_snapshot_imutavel_apos_alteracao_questao(self):
        """Garante que o snapshot da questão não muda se a questão for editada depois."""
        item = self.prova.itens.first()
        enunciado_original = item.snapshot_questao['enunciado']

        # Altera o enunciado da questão original
        questao = item.questao
        questao.enunciado = 'Enunciado completamente diferente após edição'
        questao.save()

        # Recarrega o item do banco
        item.refresh_from_db()

        self.assertEqual(item.snapshot_questao['enunciado'], enunciado_original)
        self.assertNotEqual(item.questao.enunciado, enunciado_original)


class ProvaRevisaoViewTest(TestCase):
    """
    Testes da tela de revisão da seleção (meta vs. atual) para provas geradas.
    """

    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username='prof_revisao', password='secret_password')  # noqa: S106
        self.user.groups.add(Group.objects.get(name='Docente'))
        self.client.login(username='prof_revisao', password='secret_password')  # noqa: S106
        self.disc = Disciplina.objects.create(nome='Revisão Disciplina')
        self.top1 = Topico.objects.create(disciplina=self.disc, nome='Tópico Rev A')
        self.top2 = Topico.objects.create(disciplina=self.disc, nome='Tópico Rev B')

        self.mascara = Mascara.objects.create(
            titulo='AV Revisão Test',
            disciplina=self.disc,
            quantidade_questoes=4,
            modalidade=Mascara.Modalidade.OBJETIVA,
            percentual_facil=50,
            percentual_medio=50,
            percentual_dificil=0,
            autor=self.user,
        )

        from provas.models import MascaraCriterioTopico

        MascaraCriterioTopico.objects.create(mascara=self.mascara, topico=self.top1, percentual=50)
        MascaraCriterioTopico.objects.create(mascara=self.mascara, topico=self.top2, percentual=50)

        # Estoque completo
        for t in [self.top1, self.top2]:
            for d in [Questao.Dificuldade.FACIL, Questao.Dificuldade.MEDIO]:
                q = Questao.objects.create(
                    disciplina=self.disc,
                    topico=t,
                    tipo=Questao.Tipo.OBJETIVA,
                    dificuldade=d,
                    enunciado=f'Enunciado revisão {t.nome} {d}',
                    autor=self.user,
                    ativo=True,
                )
                Alternativa.objects.create(questao=q, letra='A', texto='Alt A', correta=True)
                Alternativa.objects.create(questao=q, letra='B', texto='Alt B', correta=False)

        self.prova = self.mascara.gerar_prova(autor=self.user)

    def test_prova_revisao_requer_login(self):
        """Verifica que a tela de revisão redireciona usuários anônimos."""
        self.client.logout()
        url = reverse('provas:prova_revisao', kwargs={'pk': self.prova.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_prova_revisao_retorna_200_com_contexto(self):
        """Verifica renderização da tela de revisão com contexto completo."""
        url = reverse('provas:prova_revisao', kwargs={'pk': self.prova.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Comparativo Meta vs. Atual')
        self.assertContains(response, 'Questões Selecionadas')
        self.assertIn('comparativo_celulas', response.context)
        self.assertIn('linhas_comparativo', response.context)
        self.assertIn('resumo_dificuldade', response.context)
        self.assertIn('total_meta', response.context)
        self.assertIn('total_atual', response.context)

    def test_comparativo_meta_vs_atual_correto(self):
        """Verifica que meta vs. atual está correto e conforme."""
        url = reverse('provas:prova_revisao', kwargs={'pk': self.prova.pk})
        response = self.client.get(url)

        self.assertEqual(response.context['total_meta'], 4)
        self.assertEqual(response.context['total_atual'], 4)

        # Todas as células devem ter status 'ok' pois o estoque era exatamente suficiente
        for cel in response.context['comparativo_celulas']:
            self.assertEqual(cel['status'], 'ok', f'Célula {cel["topico_nome"]}/{cel["dificuldade"]} divergente')

    def test_mascara_gerar_prova_redireciona_para_revisao(self):
        """Verifica que após gerar a prova, o redirect vai para a tela de revisão."""
        from provas.models import Prova as ProvaModel

        # Criar mais estoque para uma segunda geração
        for t in [self.top1, self.top2]:
            for d in [Questao.Dificuldade.FACIL, Questao.Dificuldade.MEDIO]:
                q = Questao.objects.create(
                    disciplina=self.disc,
                    topico=t,
                    tipo=Questao.Tipo.OBJETIVA,
                    dificuldade=d,
                    enunciado=f'Extra revisão {t.nome} {d}',
                    autor=self.user,
                    ativo=True,
                )
                Alternativa.objects.create(questao=q, letra='A', texto='Alt A', correta=True)

        url = reverse('provas:mascara_gerar_prova', kwargs={'pk': self.mascara.pk})
        response = self.client.post(url)

        self.assertEqual(response.status_code, 302)
        nova_prova = ProvaModel.objects.latest('criado_em')
        expected_url = reverse('provas:prova_revisao', kwargs={'pk': nova_prova.pk})
        self.assertEqual(response.url, expected_url)


class TrocaIndividualQuestaoTest(TestCase):
    """
    Testes unitários e de integração para a funcionalidade de troca individual de questões
    em avaliações geradas (ajuste fino preservando a matriz pedagógica).
    """

    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username='prof_troca', password='secret_password')  # noqa: S106
        self.user.groups.add(Group.objects.get(name='Docente'))
        self.client.login(username='prof_troca', password='secret_password')  # noqa: S106

        self.disc = Disciplina.objects.create(nome='Banco de Dados II')
        self.top1 = Topico.objects.create(disciplina=self.disc, nome='Indexação B-Tree')
        self.top2 = Topico.objects.create(disciplina=self.disc, nome='Transações ACID')

        self.mascara = Mascara.objects.create(
            titulo='AV Troca Test',
            disciplina=self.disc,
            quantidade_questoes=2,
            modalidade=Mascara.Modalidade.OBJETIVA,
            percentual_facil=100,
            percentual_medio=0,
            percentual_dificil=0,
            autor=self.user,
        )

        from provas.models import MascaraCriterioTopico

        MascaraCriterioTopico.objects.create(mascara=self.mascara, topico=self.top1, percentual=50)
        MascaraCriterioTopico.objects.create(mascara=self.mascara, topico=self.top2, percentual=50)

        # Questões da prova original (1 Fácil do top1, 1 Fácil do top2)
        self.q1_original = Questao.objects.create(
            disciplina=self.disc,
            topico=self.top1,
            tipo=Questao.Tipo.OBJETIVA,
            dificuldade=Questao.Dificuldade.FACIL,
            enunciado='Questão 1 Original B-Tree Fácil',
            autor=self.user,
            ativo=True,
        )
        Alternativa.objects.create(questao=self.q1_original, letra='A', texto='Alt 1', correta=True)

        self.q2_original = Questao.objects.create(
            disciplina=self.disc,
            topico=self.top2,
            tipo=Questao.Tipo.OBJETIVA,
            dificuldade=Questao.Dificuldade.FACIL,
            enunciado='Questão 2 Original ACID Fácil',
            autor=self.user,
            ativo=True,
        )
        Alternativa.objects.create(questao=self.q2_original, letra='A', texto='Alt 2', correta=True)

        # Gera a prova apenas com q1_original e q2_original disponíveis
        self.prova = self.mascara.gerar_prova(autor=self.user)
        self.item1 = self.prova.itens.get(questao=self.q1_original)
        self.item2 = self.prova.itens.get(questao=self.q2_original)

        # Candidata elegível para substituir Q1 (mesmo top1 e mesma dificuldade Fácil) criada após a prova
        self.q1_candidata = Questao.objects.create(
            disciplina=self.disc,
            topico=self.top1,
            tipo=Questao.Tipo.OBJETIVA,
            dificuldade=Questao.Dificuldade.FACIL,
            enunciado='Questão Candidata B-Tree Fácil Reserva',
            origem='ENADE 2023',
            autor=self.user,
            ativo=True,
        )
        Alternativa.objects.create(questao=self.q1_candidata, letra='A', texto='Alt Res', correta=True)

        # Questão inelegível para Q1 (outro tópico ou outra dificuldade)
        self.q_outro_topico = Questao.objects.create(
            disciplina=self.disc,
            topico=self.top2,
            tipo=Questao.Tipo.OBJETIVA,
            dificuldade=Questao.Dificuldade.FACIL,
            enunciado='Questão Outro Tópico',
            autor=self.user,
            ativo=True,
        )

        self.q_outra_dificuldade = Questao.objects.create(
            disciplina=self.disc,
            topico=self.top1,
            tipo=Questao.Tipo.OBJETIVA,
            dificuldade=Questao.Dificuldade.DIFICIL,
            enunciado='Questão B-Tree Difícil',
            autor=self.user,
            ativo=True,
        )

    def test_obter_candidatas_mesmo_topico_e_dificuldade(self):
        """Verifica que obter_candidatas_substituicao retorna estritamente questões da mesma célula."""
        from provas.services.question_replacer import obter_candidatas_substituicao

        candidatas = list(obter_candidatas_substituicao(self.item1))

        self.assertIn(self.q1_candidata, candidatas)
        self.assertNotIn(
            self.q1_original, candidatas, 'Questão atual não deve ser sugerida como substituta de si mesma'
        )
        self.assertNotIn(self.q_outro_topico, candidatas, 'Questão de outro tópico não deve ser sugerida')
        self.assertNotIn(self.q_outra_dificuldade, candidatas, 'Questão de outra dificuldade não deve ser sugerida')

    def test_obter_candidatas_exclui_questoes_ja_presentes_na_prova(self):
        """Garante que questões já presentes na prova atual nunca aparecem como candidatas."""
        from provas.services.question_replacer import obter_candidatas_substituicao

        candidatas_item1 = list(obter_candidatas_substituicao(self.item1))
        candidatas_item2 = list(obter_candidatas_substituicao(self.item2))

        # Questões já na prova não devem constar
        self.assertNotIn(self.q1_original, candidatas_item1)
        self.assertNotIn(self.q2_original, candidatas_item1)
        self.assertNotIn(self.q1_original, candidatas_item2)
        self.assertNotIn(self.q2_original, candidatas_item2)

    def test_substituir_questao_atualiza_model_e_snapshot(self):
        """Verifica que a substituição atualiza a FK e regenera o snapshot_questao."""
        from provas.services.question_replacer import substituir_questao_prova

        item_atualizado = substituir_questao_prova(
            item=self.item1,
            nova_questao=self.q1_candidata,
            autor=self.user,
        )

        self.assertEqual(item_atualizado.questao, self.q1_candidata)
        self.assertEqual(item_atualizado.snapshot_questao['questao_id'], self.q1_candidata.pk)
        self.assertEqual(item_atualizado.snapshot_questao['enunciado'], 'Questão Candidata B-Tree Fácil Reserva')

        # Recarrega do banco
        self.item1.refresh_from_db()
        self.assertEqual(self.item1.questao_id, self.q1_candidata.pk)
        self.assertEqual(self.item1.snapshot_questao['questao_id'], self.q1_candidata.pk)

    def test_substituir_questao_violando_celula_levanta_erro(self):
        """Garante que tentar substituir por questão de tópico ou dificuldade diferente é rejeitado."""
        from provas.services.question_replacer import SubstituicaoQuestaoError, substituir_questao_prova

        # Tenta substituir com outro tópico
        with self.assertRaises(SubstituicaoQuestaoError):
            substituir_questao_prova(item=self.item1, nova_questao=self.q_outro_topico)

        # Tenta substituir com outra dificuldade
        with self.assertRaises(SubstituicaoQuestaoError):
            substituir_questao_prova(item=self.item1, nova_questao=self.q_outra_dificuldade)

    def test_substituir_por_questao_ja_na_prova_rejeitada(self):
        """Garante que não é permitido duplicar uma questão já existente na prova."""
        from provas.services.question_replacer import SubstituicaoQuestaoError, substituir_questao_prova

        with self.assertRaises(SubstituicaoQuestaoError):
            substituir_questao_prova(item=self.item1, nova_questao=self.q2_original)

    def test_view_candidatas_json_endpoint(self):
        """Verifica o endpoint AJAX de consulta de candidatas."""
        url = reverse('provas:item_candidatas_substituicao', kwargs={'item_pk': self.item1.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['item_id'], self.item1.pk)
        self.assertEqual(data['questao_atual']['id'], self.q1_original.pk)
        self.assertGreaterEqual(data['total_candidatas'], 1)

        ids_candidatas = [c['id'] for c in data['candidatas']]
        self.assertIn(self.q1_candidata.pk, ids_candidatas)

    def test_view_substituir_post_sucesso(self):
        """Verifica o endpoint POST de substituição de questão."""
        url = reverse('provas:item_substituir_questao', kwargs={'item_pk': self.item1.pk})
        response = self.client.post(
            url,
            {'nova_questao_id': str(self.q1_candidata.pk)},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])

        self.item1.refresh_from_db()
        self.assertEqual(self.item1.questao_id, self.q1_candidata.pk)


class MontagemManualProvaTest(TestCase):
    """
    Testes da funcionalidade de montagem 100% manual de provas (seleção livre no acervo).
    """

    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username='prof_manual', password='secret_password')  # noqa: S106
        self.user.groups.add(Group.objects.get_or_create(name='Administrador')[0])
        self.client.login(username='prof_manual', password='secret_password')  # noqa: S106

        self.disc = Disciplina.objects.create(nome='Sistemas Distribuídos')
        self.top1 = Topico.objects.create(disciplina=self.disc, nome='Consenso Raft e Paxos')
        self.top2 = Topico.objects.create(disciplina=self.disc, nome='Comunicação RPC')

        self.outra_disc = Disciplina.objects.create(nome='Inteligência Artificial')
        self.top_outra = Topico.objects.create(disciplina=self.outra_disc, nome='Redes Neurais')

        # Questões da disciplina
        self.q1 = Questao.objects.create(
            disciplina=self.disc,
            topico=self.top1,
            tipo=Questao.Tipo.OBJETIVA,
            dificuldade=Questao.Dificuldade.FACIL,
            enunciado='Questão 1 Consenso Fácil',
            autor=self.user,
            ativo=True,
        )
        Alternativa.objects.create(questao=self.q1, letra='A', texto='Alt 1', correta=True)

        self.q2 = Questao.objects.create(
            disciplina=self.disc,
            topico=self.top1,
            tipo=Questao.Tipo.OBJETIVA,
            dificuldade=Questao.Dificuldade.MEDIO,
            enunciado='Questão 2 Consenso Médio',
            autor=self.user,
            ativo=True,
        )
        Alternativa.objects.create(questao=self.q2, letra='A', texto='Alt 2', correta=True)

        self.q3 = Questao.objects.create(
            disciplina=self.disc,
            topico=self.top2,
            tipo=Questao.Tipo.OBJETIVA,
            dificuldade=Questao.Dificuldade.DIFICIL,
            enunciado='Questão 3 RPC Difícil',
            autor=self.user,
            ativo=True,
        )
        Alternativa.objects.create(questao=self.q3, letra='A', texto='Alt 3', correta=True)

        # Questão de outra disciplina
        self.q_outra_disc = Questao.objects.create(
            disciplina=self.outra_disc,
            topico=self.top_outra,
            tipo=Questao.Tipo.OBJETIVA,
            dificuldade=Questao.Dificuldade.FACIL,
            enunciado='Questão de IA',
            autor=self.user,
            ativo=True,
        )

    def test_criar_prova_manual_sucesso_com_snapshots(self):
        """Verifica criação de prova manual com persistência de itens e snapshots consolidados."""
        from provas.models import Prova
        from provas.services.manual_generator import criar_prova_manual

        prova = criar_prova_manual(
            titulo='AV1 Manual — SD 2026.2',
            disciplina=self.disc,
            questoes_ids=[self.q1.pk, self.q2.pk, self.q3.pk],
            autor=self.user,
            numero_caderno=1,
        )

        self.assertEqual(prova.titulo, 'AV1 Manual — SD 2026.2')
        self.assertEqual(prova.tipo_geracao, Prova.TipoGeracao.MANUAL)
        self.assertIsNone(prova.mascara)
        self.assertEqual(prova.disciplina, self.disc)
        self.assertEqual(prova.disciplina_efetiva, self.disc)
        self.assertEqual(prova.itens.count(), 3)

        # Snapshots consolidados
        self.assertEqual(prova.snapshot_mascara['tipo_geracao'], 'MANUAL')
        self.assertEqual(prova.snapshot_mascara['quantidade_questoes'], 3)
        self.assertEqual(prova.snapshot_matriz_metas['total_questoes'], 3)
        self.assertEqual(prova.snapshot_matriz_metas['totais_por_dificuldade'], {'F': 1, 'M': 1, 'D': 1})

        # Snapshots individuais por item
        for item in prova.itens.all():
            self.assertIn('enunciado', item.snapshot_questao)
            self.assertIn('dificuldade', item.snapshot_questao)
            self.assertIn('alternativas', item.snapshot_questao)

    def test_criar_prova_manual_sem_questoes_rejeitada(self):
        """Garante que tentar criar uma prova manual sem questões levanta erro."""
        from provas.services.manual_generator import ProvaManualError, criar_prova_manual

        with self.assertRaises(ProvaManualError):
            criar_prova_manual(
                titulo='Prova Vazia',
                disciplina=self.disc,
                questoes_ids=[],
                autor=self.user,
            )

    def test_criar_prova_manual_questao_outra_disciplina_rejeitada(self):
        """Garante que selecionar questão de outra disciplina levanta erro de integridade."""
        from provas.services.manual_generator import ProvaManualError, criar_prova_manual

        with self.assertRaises(ProvaManualError):
            criar_prova_manual(
                titulo='Prova Incompatível',
                disciplina=self.disc,
                questoes_ids=[self.q1.pk, self.q_outra_disc.pk],
                autor=self.user,
            )

    def test_criar_prova_manual_com_pontuacao_por_questao(self):
        """Cada questão pode ter sua pontuação; sem valor informado, vale 1,00."""
        from provas.services.manual_generator import criar_prova_manual

        prova = criar_prova_manual(
            titulo='Manual com pesos',
            disciplina=self.disc,
            questoes_ids=[self.q1.pk, self.q2.pk, self.q3.pk],
            pontuacoes={self.q1.pk: Decimal('2.5'), self.q2.pk: '1,75'},
        )
        pontos = dict(prova.itens.values_list('questao_id', 'pontuacao'))
        self.assertEqual(
            pontos, {self.q1.pk: Decimal('2.50'), self.q2.pk: Decimal('1.75'), self.q3.pk: Decimal('1.00')}
        )
        for versao in prova.versoes.all():  # as versões herdam a pontuação do item da prova
            with self.subTest(versao=versao.identificador):
                self.assertEqual({iv.prova_questao.questao_id: iv.pontuacao for iv in versao.itens.all()}, pontos)

    def test_criar_prova_manual_rejeita_pontuacao_invalida(self):
        from provas.models import Prova
        from provas.services.manual_generator import ProvaManualError, criar_prova_manual

        for valor in ('0', '-1', 'abc', '1000', '', 'NaN'):
            with self.subTest(valor=valor), self.assertRaises(ProvaManualError):
                criar_prova_manual(
                    titulo=f'Inválida {valor}',
                    disciplina=self.disc,
                    questoes_ids=[self.q1.pk],
                    pontuacoes={self.q1.pk: valor},
                )
        self.assertFalse(Prova.objects.filter(titulo__startswith='Inválida').exists())

    def test_view_manual_salva_pontos_e_preserva_selecao_quando_ha_erro(self):
        import re

        from provas.models import Prova

        url = reverse('provas:prova_manual_create')
        resp = self.client.get(url, {'disciplina': self.disc.pk})
        self.assertContains(resp, f'name="pontuacao_{self.q1.pk}"')
        self.assertContains(resp, 'id="total-pontos"')

        dados = {
            'titulo': 'Manual pontuada',
            'disciplina': str(self.disc.pk),
            'questoes_selecionadas': [str(self.q1.pk), str(self.q2.pk)],
            f'pontuacao_{self.q1.pk}': '3,5',
            f'pontuacao_{self.q2.pk}': '',  # em branco = padrão 1,00
            f'pontuacao_{self.q3.pk}': '9',  # questão não marcada: ignorado
        }
        resp = self.client.post(url, dados)
        prova = Prova.objects.get(titulo='Manual pontuada')
        self.assertRedirects(resp, reverse('provas:prova_revisao', kwargs={'pk': prova.pk}))
        self.assertEqual(
            dict(prova.itens.values_list('questao_id', 'pontuacao')),
            {self.q1.pk: Decimal('3.50'), self.q2.pk: Decimal('1.00')},
        )

        dados.update(titulo='Manual inválida', **{f'pontuacao_{self.q1.pk}': '0'})
        resp = self.client.post(url, dados)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'maior que zero')
        self.assertFalse(Prova.objects.filter(titulo='Manual inválida').exists())
        # A seleção e os pontos digitados voltam preenchidos.
        self.assertContains(resp, f'value="{self.q1.pk}" class="form-checkbox checkbox-questao"')
        q1_marcada = re.search(
            rf'value="{self.q1.pk}" class="form-checkbox checkbox-questao"[^>]*>', resp.content.decode()
        )
        self.assertIn('checked', q1_marcada.group(0))
        self.assertContains(resp, f'name="pontuacao_{self.q1.pk}" value="0"')

    def test_view_prova_manual_create_get(self):
        """Verifica renderização da tela de montagem manual."""
        url = reverse('provas:prova_manual_create')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Montagem Manual de Avaliação')

        # GET filtrando por disciplina carrega o acervo
        response_disc = self.client.get(f'{url}?disciplina={self.disc.pk}')
        self.assertEqual(response_disc.status_code, 200)
        self.assertContains(response_disc, 'Questão 1 Consenso Fácil')
        self.assertContains(response_disc, 'Questão 2 Consenso Médio')
        self.assertNotContains(response_disc, 'Questão de IA')

    def test_view_prova_manual_create_post_sucesso(self):
        """Verifica submissão do formulário criando a prova e redirecionando para revisão."""
        from provas.models import Prova

        url = reverse('provas:prova_manual_create')
        post_data = {
            'titulo': 'Exame Final Manual',
            'disciplina': str(self.disc.pk),
            'numero_caderno': '1',
            'questoes_selecionadas': [str(self.q1.pk), str(self.q2.pk)],
        }
        response = self.client.post(url, post_data)

        self.assertEqual(response.status_code, 302)
        prova_criada = Prova.objects.get(titulo='Exame Final Manual')
        self.assertRedirects(response, reverse('provas:prova_revisao', kwargs={'pk': prova_criada.pk}))
        self.assertEqual(prova_criada.itens.count(), 2)
        versoes = list(prova_criada.versoes.order_by('numero_versao'))
        self.assertEqual([versao.identificador for versao in versoes], ['A', 'B', 'C'])
        for versao in versoes:
            self.assertEqual(versao.itens.count(), 2)
            for item in versao.itens.all():
                self.assertTrue(item.alternativas_embaralhadas)

    def test_prova_revisao_renderiza_prova_manual(self):
        """Verifica que a tela de revisão exibe perfeitamente avaliações manuais (sem máscara)."""
        from provas.services.manual_generator import criar_prova_manual

        prova = criar_prova_manual(
            titulo='AV Revisão Manual Test',
            disciplina=self.disc,
            questoes_ids=[self.q1.pk, self.q3.pk],
            autor=self.user,
        )

        url = reverse('provas:prova_revisao', kwargs={'pk': prova.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'AV Revisão Manual Test')
        self.assertContains(response, 'Montagem Manual')
        self.assertContains(response, 'Sistemas Distribuídos')
        self.assertEqual(response.context['total_meta'], 2)
        self.assertEqual(response.context['total_atual'], 2)
        self.assertEqual([v.identificador for v in response.context['versoes']], ['A', 'B', 'C'])
        self.assertContains(response, 'Prova salva no painel')
        self.assertNotContains(response, 'algoritmo RF07')
        self.assertContains(response, 'versao=B&incluir_capa=1&incluir_gabarito=0')

    def test_prova_manual_pode_ser_reaberta_pelo_painel_em_nova_sessao(self):
        from provas.models import Prova

        response = self.client.post(
            reverse('provas:prova_manual_create'),
            {
                'titulo': 'Prova para usar depois',
                'disciplina': self.disc.pk,
                'numero_caderno': 1,
                'questoes_selecionadas': [self.q2.pk, self.q1.pk],
            },
        )
        self.assertEqual(response.status_code, 302)
        prova = Prova.objects.get(titulo='Prova para usar depois')
        self.client.logout()
        self.client.force_login(self.user)
        painel = self.client.get(reverse('provas:index'))
        self.assertContains(painel, prova.titulo)
        self.assertEqual(painel.context['provas'][0].total_itens, 2)
        self.assertEqual(painel.context['provas'][0].total_versoes, 3)
        revisao = self.client.get(reverse('provas:prova_revisao', kwargs={'pk': prova.pk}))
        self.assertEqual(revisao.status_code, 200)
        self.assertEqual([item.questao_id for item in revisao.context['itens']], [self.q2.pk, self.q1.pk])
        self.assertEqual([v.identificador for v in revisao.context['versoes']], ['A', 'B', 'C'])

    def test_troca_questao_em_prova_manual(self):
        """Garante que a troca individual de questão funciona em avaliações manuais."""
        from provas.services.manual_generator import criar_prova_manual
        from provas.services.question_replacer import obter_candidatas_substituicao, substituir_questao_prova

        # Candidata reserva Fácil do top1
        q_reserva = Questao.objects.create(
            disciplina=self.disc,
            topico=self.top1,
            tipo=Questao.Tipo.OBJETIVA,
            dificuldade=Questao.Dificuldade.FACIL,
            enunciado='Questão Reserva Consenso Fácil',
            autor=self.user,
            ativo=True,
        )

        prova = criar_prova_manual(
            titulo='Prova Troca Manual',
            disciplina=self.disc,
            questoes_ids=[self.q1.pk, self.q2.pk],
            autor=self.user,
        )
        item1 = prova.itens.get(questao=self.q1)

        candidatas = list(obter_candidatas_substituicao(item1))
        self.assertIn(q_reserva, candidatas)

        item_trocado = substituir_questao_prova(item=item1, nova_questao=q_reserva, autor=self.user)
        self.assertEqual(item_trocado.questao, q_reserva)
        self.assertEqual(item_trocado.snapshot_questao['questao_id'], q_reserva.pk)

    def test_criar_prova_manual_com_mascara_quantidade_exata_sucesso(self):
        """Verifica criação manual vinculada a uma máscara guia com quantidade exata de questões."""
        from provas.models import Mascara
        from provas.services.manual_generator import criar_prova_manual

        mascara = Mascara.objects.create(
            titulo='Máscara Guia SD',
            disciplina=self.disc,
            quantidade_questoes=2,
            percentual_facil=50,
            percentual_medio=50,
            percentual_dificil=0,
            autor=self.user,
        )

        prova = criar_prova_manual(
            titulo='AV Guia Manual',
            mascara=mascara,
            questoes_ids=[self.q1.pk, self.q2.pk],
            autor=self.user,
        )

        self.assertEqual(prova.mascara, mascara)
        self.assertEqual(prova.disciplina, self.disc)
        self.assertEqual(prova.itens.count(), 2)
        self.assertEqual(prova.snapshot_mascara['mascara_id'], mascara.pk)

    def test_criar_prova_manual_com_mascara_quantidade_divergente_rejeitada(self):
        """Garante que selecionar quantidade diferente da meta da máscara levanta erro de validação."""
        from provas.models import Mascara
        from provas.services.manual_generator import ProvaManualError, criar_prova_manual

        mascara = Mascara.objects.create(
            titulo='Máscara Guia 3 Itens',
            disciplina=self.disc,
            quantidade_questoes=3,
            percentual_facil=34,
            percentual_medio=33,
            percentual_dificil=33,
            autor=self.user,
        )

        # Faltando questões (2 em vez de 3)
        with self.assertRaises(ProvaManualError) as ctx:
            criar_prova_manual(
                titulo='AV Incompleta',
                mascara=mascara,
                questoes_ids=[self.q1.pk, self.q2.pk],
                autor=self.user,
            )
        self.assertIn('exige exatamente 3 questão(ões)', str(ctx.exception))

        # Excesso de questões (1 em vez de 3)
        with self.assertRaises(ProvaManualError):
            criar_prova_manual(
                titulo='AV 1 Item',
                mascara=mascara,
                questoes_ids=[self.q1.pk],
                autor=self.user,
            )

    def test_view_prova_manual_create_com_mascara_post_divergente_exibe_erro(self):
        """Verifica que submeter formulário com quantidade divergente da máscara exibe erro amigável."""
        from provas.models import Mascara

        mascara = Mascara.objects.create(
            titulo='Máscara 3 Questões',
            disciplina=self.disc,
            quantidade_questoes=3,
            percentual_facil=34,
            percentual_medio=33,
            percentual_dificil=33,
            autor=self.user,
        )

        url = reverse('provas:prova_manual_create')
        post_data = {
            'titulo': 'Exame Divergente',
            'disciplina': str(self.disc.pk),
            'mascara': str(mascara.pk),
            'numero_caderno': '1',
            'questoes_selecionadas': [str(self.q1.pk), str(self.q2.pk)],  # Apenas 2 de 3
        }
        response = self.client.post(url, post_data, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'exige exatamente 3 questão(ões)')

    def test_criar_prova_manual_com_mascara_sem_disciplina_rejeitada(self):
        """Máscara sem disciplina (ex.: Simulado ENADE) não guia a montagem manual: erro de validação."""
        from provas.models import Mascara
        from provas.services.manual_generator import ProvaManualError, criar_prova_manual

        mascara = Mascara.objects.create(
            titulo='Simulado ENADE Sem Disciplina',
            disciplina=None,
            quantidade_questoes=2,
            percentual_facil=50,
            percentual_medio=50,
            percentual_dificil=0,
            autor=self.user,
        )

        with self.assertRaises(ProvaManualError) as ctx:
            criar_prova_manual(
                titulo='AV Sem Disciplina',
                disciplina=self.disc,
                mascara=mascara,
                questoes_ids=[self.q1.pk, self.q2.pk],
                autor=self.user,
            )
        self.assertIn('não está vinculada a uma disciplina', str(ctx.exception))
        self.assertFalse(Prova.objects.filter(titulo='AV Sem Disciplina').exists())

    def test_view_prova_manual_create_com_mascara_sem_disciplina_exibe_erro(self):
        """POST com máscara sem disciplina mostra a mensagem de validação em vez de erro 500."""
        from provas.models import Mascara

        mascara = Mascara.objects.create(
            titulo='Simulado ENADE Sem Disciplina',
            disciplina=None,
            quantidade_questoes=2,
            percentual_facil=50,
            percentual_medio=50,
            percentual_dificil=0,
            autor=self.user,
        )

        response = self.client.post(
            reverse('provas:prova_manual_create'),
            {
                'titulo': 'Exame Sem Disciplina',
                'disciplina': str(self.disc.pk),
                'mascara': str(mascara.pk),
                'questoes_selecionadas': [str(self.q1.pk), str(self.q2.pk)],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'não está vinculada a uma disciplina')
        self.assertFalse(Prova.objects.filter(titulo='Exame Sem Disciplina').exists())

    def test_view_prova_manual_create_vazio_post_exibe_erro(self):
        """Verifica que tentar salvar uma prova sem questões exibe mensagem de erro."""
        url = reverse('provas:prova_manual_create')
        post_data = {
            'titulo': 'Exame Sem Questões',
            'disciplina': str(self.disc.pk),
            'numero_caderno': '1',
            'questoes_selecionadas': [],
        }
        response = self.client.post(url, post_data, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'A avaliação não pode ficar vazia')


class ProvaVersaoModelAndGenerationTest(TestCase):
    """Testes para o sistema de Versões de Prova (Prova A/B/C) e embaralhamento."""

    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username='prof_versoes', password='secret_password')  # noqa: S106
        self.user.groups.add(Group.objects.get(name='Docente'))
        self.client.login(username='prof_versoes', password='secret_password')  # noqa: S106

        self.disc = Disciplina.objects.create(nome='Algoritmos e Estruturas de Dados')
        self.topico = Topico.objects.create(disciplina=self.disc, nome='Recursão e Grafos')

        # Cria 3 questões objetivas com alternativas A-E
        self.questoes = []
        for i in range(1, 4):
            q = Questao.objects.create(
                tipo=Questao.Tipo.OBJETIVA,
                dificuldade=Questao.Dificuldade.MEDIO,
                disciplina=self.disc,
                topico=self.topico,
                enunciado=f'Enunciado de teste da questão #{i}',
                autor=self.user,
            )
            Alternativa.objects.create(questao=q, letra='A', texto=f'Opção A da Q{i}', correta=(i == 1))
            Alternativa.objects.create(questao=q, letra='B', texto=f'Opção B da Q{i}', correta=(i == 2))
            Alternativa.objects.create(questao=q, letra='C', texto=f'Opção C da Q{i}', correta=(i == 3))
            Alternativa.objects.create(questao=q, letra='D', texto=f'Opção D da Q{i}', correta=False)
            Alternativa.objects.create(questao=q, letra='E', texto=f'Opção E da Q{i}', correta=False)
            self.questoes.append(q)

        # Máscara configurada com embaralhamento
        self.mascara = Mascara.objects.create(
            titulo='AV Oficial Algoritmos 2026.2',
            disciplina=self.disc,
            quantidade_questoes=3,
            modalidade=Mascara.Modalidade.OBJETIVA,
            percentual_facil=0,
            percentual_medio=100,
            percentual_dificil=0,
            embaralhar_questoes=True,
            embaralhar_alternativas=True,
            autor=self.user,
        )

        # Gera prova base
        self.prova = self.mascara.gerar_prova(autor=self.user)

    def test_geracao_automatica_cria_versoes_a_b_c(self):
        """Verifica que a geração da prova cria automaticamente as versões A, B e C."""
        versoes = self.prova.versoes.all().order_by('numero_versao')
        self.assertEqual(versoes.count(), 3)
        self.assertEqual([v.identificador for v in versoes], ['A', 'B', 'C'])

    def test_cada_versao_possui_todas_as_questoes(self):
        """Verifica que cada versão A, B e C possui exatamente o número total de questões."""
        for versao in self.prova.versoes.all():
            self.assertEqual(versao.itens.count(), 3)
            # Cada item da versão deve possuir 5 alternativas embaralhadas
            for item in versao.itens.all():
                alts = item.alternativas_embaralhadas
                self.assertEqual(len(alts), 5)
                # Verifica que exatamente uma alternativa é correta
                corretas = [a for a in alts if a.get('correta')]
                self.assertEqual(len(corretas), 1)
                # Verifica que as letras visuais são A, B, C, D, E
                letras = [a.get('letra') for a in alts]
                self.assertEqual(letras, ['A', 'B', 'C', 'D', 'E'])

    def test_servico_gerar_versoes_personalizadas(self):
        """Verifica que o serviço aceita quantidade customizada de versões (ex.: A, B, C, D)."""
        from provas.services.version_generator import gerar_versoes_prova

        versoes = gerar_versoes_prova(prova=self.prova, quantidade_versoes=4)
        self.assertEqual(len(versoes), 4)
        self.assertEqual([v.identificador for v in versoes], ['A', 'B', 'C', 'D'])

    def test_view_prova_gerar_versoes_post(self):
        """Verifica endpoint POST de regeneração de versões."""
        url = reverse('provas:prova_gerar_versoes', kwargs={'pk': self.prova.pk})
        response = self.client.post(url, {'quantidade_versoes': '3'}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Versão A, Versão B, Versão C')

    def test_view_prova_pdf_versao_especifica_retorna_200(self):
        """Verifica que o endpoint prova_pdf com ?versao=B renderiza PDF normalmente."""
        url = reverse('provas:prova_pdf', kwargs={'pk': self.prova.pk})
        response = self.client.get(f'{url}?versao=B')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(len(response.content) > 1000)

    def test_view_prova_gabarito_pdf_versao_especifica_retorna_200(self):
        """Verifica que o endpoint prova_gabarito_pdf com ?versao=C renderiza PDF com sucesso."""
        url = reverse('provas:prova_gabarito_pdf', kwargs={'pk': self.prova.pk})
        response = self.client.get(f'{url}?versao=C')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(len(response.content) > 1000)

    def test_view_prova_pdf_completa_com_gabarito_retorna_200(self):
        """Verifica que o endpoint prova_pdf com incluir_gabarito=1 renderiza pacote completo."""
        url = reverse('provas:prova_pdf', kwargs={'pk': self.prova.pk})
        response = self.client.get(f'{url}?versao=A&incluir_gabarito=1&incluir_capa=1')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(len(response.content) > 1000)

    def test_view_prova_pacote_zip_retorna_zip_com_todas_as_versoes(self):
        """Verifica que o endpoint prova_pacote_zip entrega arquivo ZIP com todos os PDFs das versões."""
        import io
        import zipfile

        url = reverse('provas:prova_pacote_zip', kwargs={'pk': self.prova.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/zip')

        # Lê conteúdo do ZIP
        zip_file = zipfile.ZipFile(io.BytesIO(response.content))
        file_names = zip_file.namelist()

        # Verifica presença de arquivos de todas as versões
        self.assertIn('Caderno_Versao_A.pdf', file_names)
        self.assertIn('Gabarito_Oficial_Versao_A.pdf', file_names)
        self.assertIn('Caderno_Versao_B.pdf', file_names)
        self.assertIn('Gabarito_Oficial_Versao_B.pdf', file_names)
        self.assertIn('Caderno_Versao_C.pdf', file_names)
        self.assertIn('Gabarito_Oficial_Versao_C.pdf', file_names)


class PresetEnadeTest(TestCase):
    """Testes do preset de Simulado ENADE — Componente Específico (30 = 27 objetivas + 3 discursivas)."""

    def setUp(self):
        self.disciplina = Disciplina.objects.create(nome='Análise e Desenvolvimento de Sistemas')
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username='coord_enade', password='secret_password')  # noqa: S106
        self.user.groups.add(Group.objects.get_or_create(name='Administrador')[0])

    def test_preset_initial_tem_estrutura_do_componente_especifico(self):
        """O preset propõe 30 questões (27 objetivas + 3 discursivas) em modalidade mista."""
        initial = preset_enade_initial(self.disciplina)
        self.assertEqual(initial['modalidade'], MascaraProva.Modalidade.MISTA)
        self.assertEqual(initial['quantidade_questoes'], 30)
        self.assertEqual(initial['quantidade_objetivas'], 27)
        self.assertEqual(initial['quantidade_discursivas'], 3)
        self.assertTrue(initial['priorizar_enade'])
        self.assertEqual(
            initial['percentual_facil'] + initial['percentual_medio'] + initial['percentual_dificil'],
            100,
        )
        self.assertEqual(initial['disciplina'], self.disciplina)
        self.assertIn(self.disciplina.nome, initial['titulo'])

    def test_preset_sem_disciplina_nao_quebra(self):
        """Sem disciplina, o preset ainda retorna valores válidos e título genérico."""
        initial = preset_enade_initial()
        self.assertNotIn('disciplina', initial)
        self.assertIn('ENADE', initial['titulo'].upper())
        self.assertIn('Componente Específico', initial['titulo'])

    def test_mascara_montada_pelo_preset_passa_na_validacao(self):
        """Uma máscara construída com os valores do preset deve passar no full_clean()."""
        initial = preset_enade_initial(self.disciplina)
        mascara = MascaraProva(autor=self.user, **initial)
        # Não deve levantar ValidationError: soma de dificuldade = 100 e objetivas + discursivas = total.
        mascara.full_clean()

    def test_create_view_com_preset_enade_pre_preenche_formulario(self):
        """GET em mascara_create?preset=enade retorna o formulário com os valores do CE."""
        self.client.login(username='coord_enade', password='secret_password')  # noqa: S106
        url = reverse('provas:mascara_create')
        response = self.client.get(f'{url}?preset=enade&disciplina={self.disciplina.pk}')
        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        self.assertEqual(form.initial['quantidade_questoes'], 30)
        self.assertEqual(form.initial['modalidade'], MascaraProva.Modalidade.MISTA)
        self.assertEqual(form.initial['quantidade_objetivas'], 27)
        self.assertEqual(form.initial['disciplina'], self.disciplina)


class MistaSplitSelecaoTest(TestCase):
    """Garante que a modalidade MISTA respeita o split objetivas/discursivas na seleção e no estoque."""

    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username='doc_mista', password='secret_password')  # noqa: S106
        self.user.groups.add(Group.objects.get(name='Docente'))
        self.disc = Disciplina.objects.create(nome='Algoritmos e Programação')
        self.mascara = Mascara.objects.create(
            titulo='AV Mista',
            disciplina=self.disc,
            quantidade_questoes=5,
            modalidade=Mascara.Modalidade.MISTA,
            quantidade_objetivas=3,
            quantidade_discursivas=2,
            percentual_facil=100,
            percentual_medio=0,
            percentual_dificil=0,
            autor=self.user,
        )

    def _criar_questoes(self, tipo, quantidade, dificuldade=Questao.Dificuldade.FACIL):
        for i in range(quantidade):
            Questao.objects.create(
                disciplina=self.disc,
                tipo=tipo,
                dificuldade=dificuldade,
                enunciado=f'{tipo} {i}',
                autor=self.user,
                ativo=True,
            )

    def test_plano_selecao_mista_separa_tipos(self):
        """O plano de seleção soma 3 objetivas + 2 discursivas, sem corte de dificuldade nas discursivas."""
        plano = self.mascara.montar_plano_selecao()
        total_obj = sum(b.quantidade for b in plano if b.tipo == Questao.Tipo.OBJETIVA)
        total_dis = sum(b.quantidade for b in plano if b.tipo == Questao.Tipo.DISCURSIVA)
        self.assertEqual(total_obj, 3)
        self.assertEqual(total_dis, 2)
        baldes_dis = [b for b in plano if b.tipo == Questao.Tipo.DISCURSIVA]
        self.assertTrue(all(b.dificuldade is None for b in baldes_dis))

    def test_geracao_mista_poe_discursivas_primeiro_na_prova_e_nos_cadernos(self):
        """Discursivas no início (D1..k) e objetivas numeradas 1..N (como no cartão), em todo caderno."""
        self._criar_questoes(Questao.Tipo.OBJETIVA, 5)
        self._criar_questoes(Questao.Tipo.DISCURSIVA, 4, dificuldade=Questao.Dificuldade.MEDIO)
        prova = self.mascara.gerar_prova(autor=self.user)

        esperado = ['DIS', 'DIS', 'OBJ', 'OBJ', 'OBJ']
        self.assertEqual([item.questao.tipo for item in prova.itens.order_by('ordem')], esperado)
        self.assertEqual([item.numero for item in prova.itens.order_by('ordem')], [1, 2, 1, 2, 3])
        for versao in prova.versoes.all():
            with self.subTest(versao=versao.identificador):
                itens = list(versao.itens.select_related('prova_questao__questao').order_by('ordem'))
                self.assertEqual([item.prova_questao.questao.tipo for item in itens], esperado)
                self.assertEqual([item.numero for item in itens], [1, 2, 1, 2, 3])

    def test_geracao_mista_respeita_split(self):
        """A prova gerada tem exatamente 3 objetivas e 2 discursivas."""
        self._criar_questoes(Questao.Tipo.OBJETIVA, 5)
        self._criar_questoes(Questao.Tipo.DISCURSIVA, 4, dificuldade=Questao.Dificuldade.MEDIO)

        prova = self.mascara.gerar_prova(autor=self.user)

        self.assertEqual(prova.itens.count(), 5)
        self.assertEqual(prova.itens.filter(questao__tipo=Questao.Tipo.OBJETIVA).count(), 3)
        self.assertEqual(prova.itens.filter(questao__tipo=Questao.Tipo.DISCURSIVA).count(), 2)

    def test_estoque_mista_bloqueia_por_falta_de_discursivas(self):
        """Sem discursivas suficientes, a geração é bloqueada mesmo com objetivas de sobra."""
        from provas.services.stock_validator import EstoqueInsuficienteError

        self._criar_questoes(Questao.Tipo.OBJETIVA, 5)
        self._criar_questoes(Questao.Tipo.DISCURSIVA, 1, dificuldade=Questao.Dificuldade.MEDIO)

        relatorio = self.mascara.validar_estoque()
        self.assertFalse(relatorio.esta_valido)

        with self.assertRaises(EstoqueInsuficienteError):
            self.mascara.gerar_prova(autor=self.user)


class SimuladoEnadeAreasTest(TestCase):
    """Testes do Simulado ENADE distribuído nas 5 áreas do Componente Específico."""

    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username='coord_areas', password='secret_password')  # noqa: S106
        self.user.groups.add(Group.objects.get_or_create(name='Administrador')[0])

        # Acervo cobrindo 3 áreas do CE (Banco de Dados, Segurança e Desenvolvimento).
        acervo = {
            'Utilização de Banco de Dados e SQL': ['Consultas SQL', 'Junções (JOINs)'],
            'Segurança da Informação': ['Criptografia'],
            'Estrutura de Dados': ['Árvores'],
        }
        self.topicos = []
        questoes = []
        for disc_nome, topicos_nomes in acervo.items():
            disc = Disciplina.objects.create(nome=disc_nome)
            for tnome in topicos_nomes:
                topico = Topico.objects.create(disciplina=disc, nome=tnome)
                self.topicos.append(topico)
                for dif in ('F', 'M', 'D'):
                    for i in range(10):
                        questoes.append(
                            Questao(
                                disciplina=disc,
                                topico=topico,
                                tipo=Questao.Tipo.OBJETIVA,
                                dificuldade=dif,
                                enunciado=f'{disc_nome}/{tnome} OBJ {dif}{i}',
                                autor=self.user,
                                ativo=True,
                            )
                        )
                for i in range(3):
                    questoes.append(
                        Questao(
                            disciplina=disc,
                            topico=topico,
                            tipo=Questao.Tipo.DISCURSIVA,
                            dificuldade='M',
                            enunciado=f'{disc_nome}/{tnome} DIS {i}',
                            autor=self.user,
                            ativo=True,
                        )
                    )
        Questao.objects.bulk_create(questoes)

    def test_distribuicao_topicos_cobre_areas_e_soma_100(self):
        distribuicao = construir_distribuicao_topicos_enade()
        self.assertTrue(distribuicao)
        self.assertEqual(sum(d['percentual'] for d in distribuicao), 100)
        self.assertTrue(all(d['percentual'] >= 1 for d in distribuicao))
        # Todos os tópicos com questões nas áreas mapeadas devem ser contemplados.
        ids_distribuidos = {d['topico_id'] for d in distribuicao}
        self.assertEqual(ids_distribuidos, {t.pk for t in self.topicos})

    def test_view_mascara_enade_create_persiste_mascara_e_criterios(self):
        self.client.login(username='coord_areas', password='secret_password')  # noqa: S106
        url = reverse('provas:mascara_enade_create')
        response = self.client.post(url)

        mascara = Mascara.objects.filter(priorizar_enade=True).order_by('-pk').first()
        self.assertIsNotNone(mascara)
        self.assertRedirects(response, reverse('provas:mascara_detail', kwargs={'pk': mascara.pk}))
        self.assertEqual(mascara.modalidade, Mascara.Modalidade.MISTA)
        self.assertEqual(mascara.quantidade_questoes, 30)
        self.assertEqual(mascara.quantidade_objetivas, 27)
        self.assertEqual(mascara.quantidade_discursivas, 3)
        self.assertIsNone(mascara.disciplina)
        criterios = mascara.topicos_distribuicao.all()
        self.assertTrue(criterios.exists())
        self.assertEqual(sum(c.percentual for c in criterios), 100)

    def test_geracao_enade_produz_27_objetivas_e_3_discursivas(self):
        self.client.login(username='coord_areas', password='secret_password')  # noqa: S106
        self.client.post(reverse('provas:mascara_enade_create'))
        mascara = Mascara.objects.filter(priorizar_enade=True).order_by('-pk').first()

        relatorio = mascara.validar_estoque()
        self.assertTrue(relatorio.esta_valido, relatorio.mensagem_resumo)

        prova = mascara.gerar_prova(autor=self.user)
        self.assertEqual(prova.itens.count(), 30)
        self.assertEqual(prova.itens.filter(questao__tipo=Questao.Tipo.OBJETIVA).count(), 27)
        self.assertEqual(prova.itens.filter(questao__tipo=Questao.Tipo.DISCURSIVA).count(), 3)

    def test_pdf_enade_layout_agrupa_discursivas_antes_das_objetivas(self):
        from django.template.loader import render_to_string

        def _item(numero, tipo, enunciado):
            questao = SimpleNamespace(
                tipo=tipo,
                enunciado=enunciado,
                texto_auxiliar='',
                imagem=None,
                criterios_correcao='',
            )
            return {
                'numero': numero,
                'ordem': numero,
                'pontuacao': 1,
                'questao': questao,
                'alternativas_versao': [
                    {'letra': 'A', 'texto': 'alt a', 'correta': True},
                    {'letra': 'B', 'texto': 'alt b', 'correta': False},
                ],
            }

        from provas.services.caderno_layout import organizar_caderno

        discursivas = [_item(1, Questao.Tipo.DISCURSIVA, 'Discursiva de teste')]
        objetivas = [_item(2, Questao.Tipo.OBJETIVA, 'Objetiva de teste')]
        _, blocos = organizar_caderno(discursivas + objetivas)
        contexto = {
            'prova': SimpleNamespace(titulo='Simulado ENADE', numero_caderno=1, pk=1),
            'identificador_display': 'Caderno 1',
            'itens_discursivas': discursivas,
            'itens_objetivas': objetivas,
            'blocos_objetivas': [bloco for bloco in blocos if bloco['formato'] != 'discursivas'],
            'total_questoes': 2,
            'total_objetivas': 1,
            'total_discursivas': 1,
            'incluir_capa': True,
            'incluir_gabarito': False,
        }
        html = render_to_string('provas/pdf_prova_enade.html', contexto)

        self.assertIn('Componente Específico', html)
        self.assertIn('Questão Discursiva 01', html)
        self.assertIn('Área livre', html)
        # A discursiva deve aparecer antes da primeira objetiva no caderno, e a objetiva é a
        # Questão 01 (numeração do cartão), num bloco de duas colunas por ser curta.
        self.assertLess(html.index('Questão Discursiva 01'), html.index('Objetiva de teste'))
        self.assertIn('Questão 01', html)
        self.assertIn('class="enade-colunas"', html)
        # Sem a faixa "Componente Específico" nem o subtítulo das discursivas no caderno (a faixa já
        # está no cabeçalho de toda página); o subtítulo das objetivas continua.
        self.assertNotIn('class="enade-secao"', html)
        self.assertNotIn('>Questões Discursivas<', html)
        self.assertIn('>Questões Objetivas<', html)
        # Rascunho com 15 linhas numeradas.
        self.assertIn('>15<', html.replace(' ', '').replace('\n', ''))


class FolhaRespostaGeometryTest(SimpleTestCase):
    """
    Testes da geometria da folha de resposta (OMR).

    Garantem que o layout é "fixo e conhecido": contagem/rotulagem correta das
    bolhas, ausência de sobreposição, todos os elementos dentro do quadro dos
    marcadores e serialização estável (contrato consumido pelo futuro leitor OMR).
    """

    def _todas_bolhas(self, geo):
        return list(geo.answer_bubbles) + list(geo.id_bubbles)

    def test_contagem_e_rotulos_das_respostas(self):
        for nq in (1, 10, 30, 50):
            geo = build_layout(num_questoes=nq)
            self.assertEqual(len(geo.answer_bubbles), nq * 5)
            self.assertEqual({b.question for b in geo.answer_bubbles}, set(range(1, nq + 1)))
            # Cada questão possui exatamente as alternativas A–E.
            for q in range(1, nq + 1):
                letras = sorted(b.option for b in geo.answer_bubbles if b.question == q)
                self.assertEqual(letras, ['A', 'B', 'C', 'D', 'E'])

    def test_disco_lido_cabe_nas_bolhas_dos_cartoes_ja_impressos(self):
        """
        As bolhas cresceram de 1,9 para 2,4 mm sem mudar os centros. O leitor mede só o disco
        interno (INNER_R_FRAC × raio atual): ele precisa caber dentro da bolha antiga, sem
        tocar a borda impressa (0,3 mm), para os cartões já impressos continuarem legíveis.
        """
        from .services.omr import INNER_R_FRAC

        raio_antigo, borda = 1.9, 0.3
        for bolha in build_layout(num_questoes=57).answer_bubbles:
            self.assertLess(bolha.r * INNER_R_FRAC, raio_antigo - borda / 2)

    def test_numero_de_alternativas_variavel(self):
        geo = build_layout(num_questoes=10, num_alternativas=4)
        self.assertEqual(geo.option_labels, ['A', 'B', 'C', 'D'])
        self.assertEqual(len(geo.answer_bubbles), 10 * 4)

    def test_matriz_de_matricula(self):
        geo = build_layout(num_questoes=10, id_digits=8)
        self.assertEqual(len(geo.id_bubbles), 8 * 10)
        self.assertEqual({b.digit for b in geo.id_bubbles}, set(range(8)))
        self.assertEqual({b.value for b in geo.id_bubbles}, set(range(10)))

    def test_sem_matricula_quando_id_digits_zero(self):
        geo = build_layout(num_questoes=10, id_digits=0)
        self.assertEqual(geo.id_bubbles, [])
        self.assertIsNone(geo.id_matrix_title)

    def test_bolhas_de_resposta_nao_dependem_da_matriz_de_matricula(self):
        """Remover a matriz não move nenhuma bolha: cartões impressos com a matriz seguem legíveis."""
        for num_questoes in (1, 10, 27, 57):
            with self.subTest(num_questoes=num_questoes):
                com_matriz = build_layout(num_questoes=num_questoes, id_digits=8)
                sem_matriz = build_layout(num_questoes=num_questoes, id_digits=0)
                self.assertEqual(
                    [b.to_dict() for b in com_matriz.answer_bubbles],
                    [b.to_dict() for b in sem_matriz.answer_bubbles],
                )
                self.assertEqual(com_matriz.markers, sem_matriz.markers)
                self.assertEqual(com_matriz.qr, sem_matriz.qr)

    def test_cartao_nominal_sai_sem_matriz_de_matricula(self):
        from django.template.loader import render_to_string

        from provas.services.folha_service import FOLHA_ALTERNATIVAS, FOLHA_COLUMNS, FOLHA_ID_DIGITS

        geo = build_layout(
            num_questoes=10, num_alternativas=FOLHA_ALTERNATIVAS, id_digits=FOLHA_ID_DIGITS, columns=FOLHA_COLUMNS
        )
        html = render_to_string('provas/pdf_folha_resposta.html', {'geo': geo, 'aluno_matricula': '2026010123'})
        self.assertEqual(geo.id_bubbles, [])
        self.assertNotIn('fr-bubble fr-bubble-id', html)
        self.assertIn('2026010123', html)  # a matrícula segue impressa no campo de identificação

    def test_marcadores_nos_quatro_cantos(self):
        geo = build_layout(num_questoes=10)
        self.assertEqual(len(geo.markers), 4)
        self.assertEqual({m.id for m in geo.markers}, set(MARKER_IDS.values()))
        self.assertEqual({m.corner for m in geo.markers}, {'TL', 'TR', 'BR', 'BL'})
        cantos = {m.corner: (m.cx, m.cy) for m in geo.markers}
        self.assertEqual(cantos['TL'], (MARKER_INSET, MARKER_INSET))
        self.assertEqual(cantos['TR'], (PAGE_W - MARKER_INSET, MARKER_INSET))
        self.assertEqual(cantos['BR'], (PAGE_W - MARKER_INSET, PAGE_H - MARKER_INSET))
        self.assertEqual(cantos['BL'], (MARKER_INSET, PAGE_H - MARKER_INSET))

    def test_bolhas_dentro_do_quadro_dos_marcadores(self):
        geo = build_layout(num_questoes=50, id_digits=8)
        for b in self._todas_bolhas(geo):
            self.assertGreaterEqual(b.cx - b.r, MARKER_INSET)
            self.assertLessEqual(b.cx + b.r, PAGE_W - MARKER_INSET)
            self.assertGreaterEqual(b.cy - b.r, MARKER_INSET)
            self.assertLessEqual(b.cy + b.r, PAGE_H - MARKER_INSET)

    def test_qr_dentro_da_pagina(self):
        geo = build_layout(num_questoes=10)
        self.assertGreaterEqual(geo.qr.x, 0)
        self.assertGreaterEqual(geo.qr.y, 0)
        self.assertLessEqual(geo.qr.x + geo.qr.w, PAGE_W)
        self.assertLessEqual(geo.qr.y + geo.qr.h, PAGE_H)

    def test_bolhas_nao_se_sobrepoem(self):
        import math

        geo = build_layout(num_questoes=50, id_digits=8)
        bolhas = self._todas_bolhas(geo)
        for i in range(len(bolhas)):
            a = bolhas[i]
            for j in range(i + 1, len(bolhas)):
                b = bolhas[j]
                dist = math.hypot(a.cx - b.cx, a.cy - b.cy)
                self.assertGreaterEqual(
                    dist,
                    a.r + b.r,
                    msg=f'Sobreposição entre {a} e {b} (dist={dist:.2f}).',
                )

    def test_serializacao_roundtrip_preserva_geometria(self):
        geo = build_layout(num_questoes=50, num_alternativas=5, id_digits=8, columns=3)
        data = geo.to_dict()
        rt = geometry_from_dict(data)

        self.assertEqual(rt['schema_version'], SCHEMA_VERSION)
        self.assertEqual(
            [m.to_dict() for m in rt['markers']],
            [m.to_dict() for m in geo.markers],
        )
        self.assertEqual(rt['qr'].to_dict(), geo.qr.to_dict())
        self.assertEqual(
            [b.to_dict() for b in rt['answer_bubbles']],
            [b.to_dict() for b in geo.answer_bubbles],
        )
        self.assertEqual(
            [b.to_dict() for b in rt['id_bubbles']],
            [b.to_dict() for b in geo.id_bubbles],
        )

    def test_capacidade_excedida_levanta_valueerror(self):
        capacidade = max_questoes_por_pagina(3)
        # No limite: ok. Acima do limite: erro.
        build_layout(num_questoes=capacidade, columns=3)
        with self.assertRaises(ValueError):
            build_layout(num_questoes=capacidade + 1, columns=3)

    def test_parametros_invalidos_levantam_valueerror(self):
        with self.assertRaises(ValueError):
            build_layout(num_questoes=0)
        with self.assertRaises(ValueError):
            build_layout(num_questoes=10, num_alternativas=1)
        with self.assertRaises(ValueError):
            build_layout(num_questoes=10, num_alternativas=6)
        with self.assertRaises(ValueError):
            build_layout(num_questoes=10, columns=0)
        with self.assertRaises(ValueError):
            build_layout(num_questoes=10, columns=5)


class ProvaAplicacaoModelTest(TestCase):
    def setUp(self):
        self.prova = Prova.objects.create(titulo='Prova de Teste')
        self.turma = Turma.objects.create(periodo='2026.2', nome='MAC Noite')

    def test_str_e_unicidade(self):
        ap = ProvaAplicacao.objects.create(prova=self.prova, turma=self.turma, data_aplicacao=date(2026, 11, 10))
        self.assertIn('MAC Noite', str(ap))
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProvaAplicacao.objects.create(prova=self.prova, turma=self.turma, data_aplicacao=date(2026, 11, 10))


class AtribuicaoVersaoServiceTest(TestCase):
    def setUp(self):
        self.prova = Prova.objects.create(titulo='Prova RF')
        self.versoes = [
            ProvaVersao.objects.create(prova=self.prova, identificador=ident, numero_versao=i)
            for i, ident in enumerate(['A', 'B', 'C'], start=1)
        ]
        self.turma = Turma.objects.create(periodo='2026.2', nome='MAC Noite')
        self.aplicacao = ProvaAplicacao.objects.create(
            prova=self.prova, turma=self.turma, data_aplicacao=date(2026, 11, 10)
        )

    def _matricular(self, nome, matricula, situacao=None, ativo=True):
        aluno = Aluno.objects.create(nome=nome, matricula=matricula, ativo=ativo)
        Matricula.objects.create(
            aluno=aluno,
            turma=self.turma,
            situacao=situacao or Matricula.Situacao.MATRICULADO,
        )
        return aluno

    def test_distribui_em_blocos_continuos_e_equilibrados(self):
        # 7 alunos, nomes em ordem controlada (A..G); cadastrados fora de ordem.
        for i, letra in enumerate('GCAFBED'):
            self._matricular(f'{letra} Aluno', f'M{i:02d}')

        atribuicoes = atribuir_versoes(self.aplicacao)
        self.assertEqual(len(atribuicoes), 7)

        # Na ordem alfabética, cada versão é uma faixa contínua; a sobra vai para a primeira.
        ordenadas = sorted(atribuicoes, key=lambda a: a.sequencia)
        self.assertEqual([a.aluno.nome[0] for a in ordenadas], list('ABCDEFG'))
        self.assertEqual([a.versao.identificador for a in ordenadas], list('AAABBCC'))

    def test_indices_em_blocos_equilibra_as_faixas(self):
        self.assertEqual(indices_em_blocos(7, 3), [0, 0, 0, 1, 1, 2, 2])
        self.assertEqual([indices_em_blocos(31, 3).count(v) for v in range(3)], [11, 10, 10])
        self.assertEqual(indices_em_blocos(6, 3), [0, 0, 1, 1, 2, 2])
        self.assertEqual(indices_em_blocos(2, 3), [0, 1])  # menos alunos que versões
        self.assertEqual(indices_em_blocos(4, 1), [0, 0, 0, 0])  # caderno único
        self.assertEqual(indices_em_blocos(0, 3), [])

    def test_resumo_mostra_a_faixa_de_cada_versao(self):
        for i, letra in enumerate('ABCDEFG'):
            self._matricular(f'{letra} Aluno', f'M{i:02d}')
        atribuir_versoes(self.aplicacao)

        self.assertEqual(
            resumo_distribuicao(self.aplicacao),
            [
                {'versao': 'A', 'total': 3, 'primeiro': 'A Aluno', 'ultimo': 'C Aluno'},
                {'versao': 'B', 'total': 2, 'primeiro': 'D Aluno', 'ultimo': 'E Aluno'},
                {'versao': 'C', 'total': 2, 'primeiro': 'F Aluno', 'ultimo': 'G Aluno'},
            ],
        )

    def test_resumo_sem_faixa_quando_a_distribuicao_e_alternada(self):
        # Aplicação distribuída antes da regra em blocos (A, B, A): a versão A não é contínua.
        alunos = [self._matricular(nome, f'M{i}') for i, nome in enumerate(['Ana', 'Bruno', 'Carla'])]
        for sequencia, (aluno, versao) in enumerate(zip(alunos, [0, 1, 0], strict=True), start=1):
            AtribuicaoVersao.objects.create(
                aplicacao=self.aplicacao, aluno=aluno, versao=self.versoes[versao], sequencia=sequencia
            )

        resumo = {item['versao']: item for item in resumo_distribuicao(self.aplicacao)}
        self.assertEqual((resumo['A']['total'], resumo['A']['primeiro']), (2, ''))
        self.assertEqual((resumo['B']['total'], resumo['B']['primeiro']), (1, 'Bruno'))
        self.assertEqual((resumo['C']['total'], resumo['C']['primeiro']), (0, ''))

    def test_aluno_novo_entra_no_bloco_alfabetico_sem_mexer_nos_outros(self):
        for i, nome in enumerate(['Ana', 'Bruno', 'Diego', 'Eva', 'Gil', 'Hugo']):
            self._matricular(nome, f'M{i}')
        atribuir_versoes(self.aplicacao)  # A: Ana, Bruno | B: Diego, Eva | C: Gil, Hugo
        antes = {a.aluno_id: (a.versao_id, a.sequencia) for a in self.aplicacao.atribuicoes.all()}

        self._matricular('Zeca', 'N3')  # depois do último bloco (C)
        self._matricular('Carla', 'N2')  # entre Bruno (A) e Diego (B)
        self._matricular('Bia', 'N1')  # dentro do bloco A
        criadas = incluir_alunos_novos(self.aplicacao)

        # Em ordem alfabética: Bia → A (vizinhos A); Carla → B (A já tem 3, B tem 2); Zeca → C.
        letras = {v.pk: v.identificador for v in self.versoes}
        self.assertEqual(
            [(a.aluno.nome, letras[a.versao_id], a.sequencia) for a in criadas],
            [('Bia', 'A', 7), ('Carla', 'B', 8), ('Zeca', 'C', 9)],
        )
        depois = {a.aluno_id: (a.versao_id, a.sequencia) for a in self.aplicacao.atribuicoes.all()}
        self.assertTrue(all(depois[aluno_id] == valor for aluno_id, valor in antes.items()))

        # As faixas continuam contínuas na ordem alfabética.
        faixas = [(r['versao'], r['total'], r['primeiro'], r['ultimo']) for r in resumo_distribuicao(self.aplicacao)]
        self.assertEqual(faixas, [('A', 3, 'Ana', 'Bruno'), ('B', 3, 'Carla', 'Eva'), ('C', 3, 'Gil', 'Zeca')])
        self.assertEqual(incluir_alunos_novos(self.aplicacao), [])  # ninguém mais sem versão

    def test_aluno_novo_em_distribuicao_alternada_vai_para_a_versao_com_menos_alunos(self):
        alunos = [self._matricular(nome, f'M{i}') for i, nome in enumerate(['Ana', 'Bruno', 'Carla', 'Davi'])]
        for sequencia, (aluno, versao) in enumerate(zip(alunos, [0, 1, 2, 0], strict=True), start=1):
            AtribuicaoVersao.objects.create(
                aplicacao=self.aplicacao, aluno=aluno, versao=self.versoes[versao], sequencia=sequencia
            )
        self._matricular('Beto', 'N1')

        criadas = incluir_alunos_novos(self.aplicacao)

        # A tem 2 alunos; B e C têm 1 — empate resolvido pela menor versão (B).
        self.assertEqual([(a.aluno.nome, a.versao_id) for a in criadas], [('Beto', self.versoes[1].pk)])

    def test_ignora_nao_matriculados_e_inativos(self):
        self._matricular('Ativo Um', 'A1')
        self._matricular('Trancado', 'T1', situacao=Matricula.Situacao.TRANCADO)
        self._matricular('Inativo', 'I1', ativo=False)

        atribuicoes = atribuir_versoes(self.aplicacao)
        self.assertEqual(len(atribuicoes), 1)
        self.assertEqual(atribuicoes[0].aluno.nome, 'Ativo Um')

    def test_idempotente(self):
        self._matricular('Ana', 'A1')
        self._matricular('Bruno', 'B1')
        atribuir_versoes(self.aplicacao)
        atribuir_versoes(self.aplicacao)
        self.assertEqual(self.aplicacao.atribuicoes.count(), 2)

    def test_sem_versoes_levanta_valueerror(self):
        self.prova.versoes.all().delete()
        self._matricular('Ana', 'A1')
        with self.assertRaises(ValueError):
            atribuir_versoes(self.aplicacao)

    def test_bloqueia_redistribuicao_apos_gerar_folhas(self):
        ana = self._matricular('Ana', 'A1')
        self._matricular('Bruno', 'B1')
        atribuir_versoes(self.aplicacao)
        versao_ana = self.aplicacao.atribuicoes.get(aluno=ana).versao
        FolhaResposta.objects.create(prova_aplicacao=self.aplicacao, aluno=ana, prova_versao=versao_ana)

        with self.assertRaisesMessage(ValueError, 'cartões-resposta desta aplicação já foram gerados'):
            atribuir_versoes(self.aplicacao)
        self.assertEqual(self.aplicacao.atribuicoes.get(aluno=ana).versao, versao_ana)


class FolhaRespostaTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='coord_f', password='segredo123')  # noqa: S106
        self.user.groups.add(Group.objects.get_or_create(name='Administrador')[0])
        self.disciplina = Disciplina.objects.create(nome='Disc Folha')
        self.topico = Topico.objects.create(disciplina=self.disciplina, nome='Top Folha')
        self.questao = Questao.objects.create(
            disciplina=self.disciplina, topico=self.topico, tipo='OBJ', dificuldade='F', enunciado='Q obj'
        )
        self.prova = Prova.objects.create(titulo='Prova Folha', disciplina=self.disciplina)
        self.pq = ProvaQuestao.objects.create(
            prova=self.prova, questao=self.questao, numero=1, ordem=1, snapshot_questao={'tipo': 'OBJ'}
        )
        self.versoes = []
        for i, ident in enumerate(['A', 'B'], start=1):
            v = ProvaVersao.objects.create(prova=self.prova, identificador=ident, numero_versao=i)
            ProvaVersaoItem.objects.create(
                versao=v,
                prova_questao=self.pq,
                numero=1,
                ordem=1,
                alternativas_embaralhadas=[{'letra': 'A', 'correta': True}],
            )
            self.versoes.append(v)
        self.turma = Turma.objects.create(periodo='2026.2', nome='MAC Noite')
        self.ana = Aluno.objects.create(nome='Ana', matricula='A1')
        self.bruno = Aluno.objects.create(nome='Bruno', matricula='B1')
        Matricula.objects.create(aluno=self.ana, turma=self.turma)
        Matricula.objects.create(aluno=self.bruno, turma=self.turma)
        self.aplicacao = ProvaAplicacao.objects.create(
            prova=self.prova, turma=self.turma, data_aplicacao=date(2026, 11, 10)
        )

    def test_qr_token_unico_e_status_default(self):
        f1 = FolhaResposta.objects.create(prova_aplicacao=self.aplicacao, aluno=self.ana, prova_versao=self.versoes[0])
        f2 = FolhaResposta.objects.create(
            prova_aplicacao=self.aplicacao, aluno=self.bruno, prova_versao=self.versoes[1]
        )
        self.assertIsNotNone(f1.qr_token)
        self.assertNotEqual(f1.qr_token, f2.qr_token)
        self.assertEqual(f1.status, FolhaResposta.Status.GERADA)

    def test_unique_aplicacao_aluno(self):
        FolhaResposta.objects.create(prova_aplicacao=self.aplicacao, aluno=self.ana, prova_versao=self.versoes[0])
        with self.assertRaises(IntegrityError), transaction.atomic():
            FolhaResposta.objects.create(prova_aplicacao=self.aplicacao, aluno=self.ana, prova_versao=self.versoes[1])

    def test_contar_questoes_objetivas(self):
        self.assertEqual(contar_questoes_objetivas(self.versoes[0]), 1)

    def test_gerar_folhas_auto_atribui_e_idempotente(self):
        folhas = gerar_folhas_da_aplicacao(self.aplicacao)
        self.assertEqual(len(folhas), 2)
        self.assertEqual(FolhaResposta.objects.filter(prova_aplicacao=self.aplicacao).count(), 2)
        # cada folha usa a versão atribuída ao aluno
        for folha in folhas:
            atrib = self.aplicacao.atribuicoes.get(aluno=folha.aluno)
            self.assertEqual(folha.prova_versao_id, atrib.versao_id)
        # idempotente: rodar de novo mantém a contagem e os tokens
        tokens = {f.aluno_id: f.qr_token for f in folhas}
        folhas2 = gerar_folhas_da_aplicacao(self.aplicacao)
        self.assertEqual(len(folhas2), 2)
        self.assertEqual(FolhaResposta.objects.filter(prova_aplicacao=self.aplicacao).count(), 2)
        for f in folhas2:
            self.assertEqual(f.qr_token, tokens[f.aluno_id])

    def test_regerar_folhas_nao_troca_versao_de_folha_corrigida(self):
        folhas = {f.aluno_id: f for f in gerar_folhas_da_aplicacao(self.aplicacao)}
        lida, gerada = folhas[self.ana.pk], folhas[self.bruno.pk]
        lida.status = FolhaResposta.Status.LIDA
        lida.save(update_fields=['status'])
        versao_lida, versao_gerada = lida.prova_versao_id, gerada.prova_versao_id
        # Atribuições trocadas por fora (ex.: edição no admin): cada aluno passa à versão do outro.
        self.aplicacao.atribuicoes.filter(aluno=self.ana).update(versao_id=versao_gerada)
        self.aplicacao.atribuicoes.filter(aluno=self.bruno).update(versao_id=versao_lida)

        gerar_folhas_da_aplicacao(self.aplicacao)

        lida.refresh_from_db()
        gerada.refresh_from_db()
        self.assertEqual(lida.prova_versao_id, versao_lida)  # corrigida: mantém a versão impressa
        self.assertEqual(gerada.prova_versao_id, versao_lida)  # ainda não corrigida: acompanha a atribuição

    def test_render_folha_pdf_gera_bytes(self):
        folhas = gerar_folhas_da_aplicacao(self.aplicacao)
        pdf = render_folha_pdf(folhas[0])
        self.assertTrue(pdf.startswith(b'%PDF'))

    def test_export_zip_uma_folha_por_aluno(self):
        import io
        import zipfile

        self.client.login(username='coord_f', password='segredo123')  # noqa: S106
        url = reverse('provas:aplicacao_folhas_zip', args=[self.aplicacao.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'application/zip')
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            self.assertEqual(len(zf.namelist()), 2)

    def test_export_zip_login_obrigatorio(self):
        url = reverse('provas:aplicacao_folhas_zip', args=[self.aplicacao.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)

    def _baixar_cadernos(self, **params):
        """GET do ZIP de cadernos com o PDF simulado; devolve a resposta e os contextos renderizados."""
        from unittest import mock

        self.client.login(username='coord_f', password='segredo123')  # noqa: S106
        url = reverse('provas:aplicacao_cadernos_zip', args=[self.aplicacao.pk])
        with mock.patch.object(PDFGeneratorService, 'render_pdf_from_template', return_value=b'%PDF-teste') as render:
            resp = self.client.get(url, params)
        return resp, [chamada.args[1] for chamada in render.call_args_list]

    def test_export_cadernos_zip_um_caderno_nominal_por_aluno(self):
        import io
        import zipfile

        resp, contextos = self._baixar_cadernos()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'application/zip')
        self.assertIn('cadernos_aplicacao_', resp['Content-Disposition'])
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            self.assertEqual(zf.namelist(), ['01_versao_A_a1_ana.pdf', '02_versao_B_b1_bruno.pdf'])

        # Capa nominal: aluno, turma, turno e data da aplicação; sem gabarito.
        ana = contextos[0]
        self.assertEqual((ana['aluno_nome'], ana['aluno_matricula']), ('Ana', 'A1'))
        self.assertEqual((ana['turma'], ana['data_aplicacao']), ('MAC Noite / 2026.2', '10/11/2026'))
        self.assertTrue(ana['incluir_capa'])
        self.assertFalse(ana['incluir_gabarito'])
        self.assertEqual(ana['versao'], self.versoes[0])
        self.assertEqual(contextos[1]['versao'], self.versoes[1])

        # A versão do caderno é a do cartão: os cartões foram gerados e a redistribuição travou.
        self.assertEqual(FolhaResposta.objects.filter(prova_aplicacao=self.aplicacao).count(), 2)
        with self.assertRaises(ValueError):
            atribuir_versoes(self.aplicacao)

    def test_export_cadernos_zip_de_uma_versao(self):
        import io
        import zipfile

        resp, contextos = self._baixar_cadernos(versao='b')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('_versao_B.zip', resp['Content-Disposition'])
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            self.assertEqual(zf.namelist(), ['02_versao_B_b1_bruno.pdf'])  # mantém o nº da ordem alfabética
        self.assertEqual([c['aluno_nome'] for c in contextos], ['Bruno'])

    def test_export_cadernos_zip_versao_sem_alunos_retorna_400(self):
        resp, contextos = self._baixar_cadernos(versao='Z')
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(contextos, [])

    def test_export_cadernos_zip_login_obrigatorio(self):
        resp = self.client.get(reverse('provas:aplicacao_cadernos_zip', args=[self.aplicacao.pk]))
        self.assertEqual(resp.status_code, 302)

    def test_caderno_nominal_preenche_a_capa_nos_dois_layouts(self):
        from django.template.loader import render_to_string

        from .services.caderno_service import contexto_caderno

        for layout in ('', 'enade'):
            template_name, contexto = contexto_caderno(self.prova, self.versoes[0], layout=layout)
            html = render_to_string(template_name, {**contexto, 'aluno_nome': 'Ana Teste', 'aluno_matricula': 'MAT-77'})
            self.assertIn('<strong>Ana Teste</strong>', html)
            self.assertIn('<strong>MAT-77</strong>', html)
            # Sem aluno (caderno genérico), os campos continuam em branco.
            self.assertNotIn('<strong></strong>', render_to_string(template_name, contexto))

    def test_capa_do_aluno_sem_assinatura_e_sem_modalidade(self):
        from django.template.loader import render_to_string

        from .services.caderno_service import contexto_caderno

        for layout in ('', 'enade'):
            template_name, contexto = contexto_caderno(self.prova, self.versoes[0], layout=layout)
            for template in (template_name, 'provas/pdf_capa_prova.html'):
                html = render_to_string(template, {**contexto, 'modalidade': 'Somente Objetivas'})
                self.assertIn('Matrícula:', html)
                self.assertNotIn('Assinatura:', html)
                self.assertNotIn('Modalidade:', html)
                self.assertNotIn('Somente Objetivas', html)

    def test_gerar_folhas_inclui_aluno_que_entrou_depois_sem_mexer_nos_outros(self):
        antes = {f.aluno_id: (f.qr_token, f.prova_versao_id) for f in gerar_folhas_da_aplicacao(self.aplicacao)}
        carlos = Aluno.objects.create(nome='Carlos', matricula='C1')
        Matricula.objects.create(aluno=carlos, turma=self.turma)

        folhas = gerar_folhas_da_aplicacao(self.aplicacao)

        self.assertEqual(len(folhas), 3)
        for folha in folhas:
            if folha.aluno_id in antes:
                self.assertEqual((folha.qr_token, folha.prova_versao_id), antes[folha.aluno_id])
        folha_carlos = next(folha for folha in folhas if folha.aluno_id == carlos.pk)
        self.assertEqual(folha_carlos.prova_versao, self.versoes[1])  # depois de Bruno (bloco B)

    def test_cartao_e_caderno_individuais_em_pdf(self):
        from unittest import mock

        folha = next(f for f in gerar_folhas_da_aplicacao(self.aplicacao) if f.aluno == self.ana)
        self.client.login(username='coord_f', password='segredo123')  # noqa: S106
        with mock.patch.object(PDFGeneratorService, 'render_pdf_from_template', return_value=b'%PDF-teste') as render:
            cartao = self.client.get(reverse('provas:folha_cartao_pdf', args=[folha.pk]))
            caderno = self.client.get(reverse('provas:folha_caderno_pdf', args=[folha.pk]))

        self.assertEqual((cartao.status_code, cartao['Content-Type']), (200, 'application/pdf'))
        self.assertIn('cartao-a1-ana-versao-a.pdf', cartao['Content-Disposition'])
        self.assertEqual((caderno.status_code, caderno['Content-Type']), (200, 'application/pdf'))
        self.assertIn('caderno-a1-ana-versao-a.pdf', caderno['Content-Disposition'])
        templates = [chamada.args[0] for chamada in render.call_args_list]
        self.assertEqual(templates, ['provas/pdf_folha_resposta.html', 'provas/pdf_prova_completa.html'])
        self.assertEqual(render.call_args_list[1].args[1]['aluno_nome'], 'Ana')

        # A lista de alunos leva aos PDFs individuais.
        lista = self.client.get(reverse('provas:aplicacao_folhas', args=[self.aplicacao.pk]))
        self.assertContains(lista, reverse('provas:folha_cartao_pdf', args=[folha.pk]))
        self.assertContains(lista, reverse('provas:folha_caderno_pdf', args=[folha.pk]))

    def test_pdfs_individuais_exigem_login(self):
        folha = gerar_folhas_da_aplicacao(self.aplicacao)[0]
        for rota in ('provas:folha_cartao_pdf', 'provas:folha_caderno_pdf'):
            self.assertEqual(self.client.get(reverse(rota, args=[folha.pk])).status_code, 302)

    def test_render_cadernos_nominais_gera_pdf(self):
        from .services.caderno_service import render_cadernos_nominais

        folhas = gerar_folhas_da_aplicacao(self.aplicacao)
        gerados = list(render_cadernos_nominais(self.aplicacao, folhas[:1]))
        self.assertEqual(len(gerados), 1)
        self.assertTrue(gerados[0][1].startswith(b'%PDF'))


def _imagem_png(nome='folha.png'):
    """Gera um arquivo PNG válido em memória para testes de upload."""
    buffer = BytesIO()
    Image.new('RGB', (12, 12), 'white').save(buffer, format='PNG')
    return SimpleUploadedFile(nome, buffer.getvalue(), content_type='image/png')


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class UploadFolhasViewTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='coord_up', password='segredo123')  # noqa: S106
        self.user.groups.add(Group.objects.get_or_create(name='Administrador')[0])
        self.prova = Prova.objects.create(titulo='Prova Upload')
        self.turma = Turma.objects.create(periodo='2026.2', nome='MAC Noite')
        self.aplicacao = ProvaAplicacao.objects.create(
            prova=self.prova, turma=self.turma, data_aplicacao=date(2026, 11, 10)
        )
        self.url = reverse('provas:aplicacao_upload_folhas', args=[self.aplicacao.pk])

    def test_login_obrigatorio(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)

    def test_get_ok(self):
        self.client.login(username='coord_up', password='segredo123')  # noqa: S106
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)

    def test_upload_multiplas_imagens(self):
        self.client.login(username='coord_up', password='segredo123')  # noqa: S106
        resp = self.client.post(
            self.url,
            {'imagens': [_imagem_png('f1.png'), _imagem_png('f2.png')]},
        )
        self.assertRedirects(resp, self.url)
        self.assertEqual(FolhaDigitalizada.objects.filter(prova_aplicacao=self.aplicacao).count(), 2)
        d = FolhaDigitalizada.objects.filter(prova_aplicacao=self.aplicacao).first()
        self.assertEqual(d.status, FolhaDigitalizada.Status.PENDENTE)
        self.assertEqual(d.enviado_por, self.user)

    def test_rejeita_arquivo_nao_imagem(self):
        self.client.login(username='coord_up', password='segredo123')  # noqa: S106
        arquivo = SimpleUploadedFile('nota.txt', b'isto nao e imagem', content_type='text/plain')
        resp = self.client.post(self.url, {'imagens': [arquivo]})
        self.assertEqual(resp.status_code, 200)  # reexibe o form com erro
        self.assertEqual(FolhaDigitalizada.objects.count(), 0)

    def test_foto_heic_recusada_com_orientacao(self):
        self.client.login(username='coord_up', password='segredo123')  # noqa: S106
        cabecalho_heic = b'\x00\x00\x00\x18ftypheic' + b'\x00' * 64
        # Pela extensão e, renomeada para .jpg, pela assinatura do arquivo.
        for nome in ('20261007_113607.heic', 'foto.jpg'):
            arquivo = SimpleUploadedFile(nome, cabecalho_heic, content_type='image/heic')
            resp = self.client.post(self.url, {'imagens': [arquivo]})
            self.assertEqual(resp.status_code, 200)
            self.assertContains(resp, 'formato HEIC/HEIF')
            self.assertContains(resp, 'câmera do próprio sistema')
        self.assertEqual(FolhaDigitalizada.objects.count(), 0)

    def test_tela_reduz_fotos_grandes_antes_do_envio(self):
        # O navegador reduz as fotos (lado maior 3200 px, JPEG 0,88) — ver OmrLerRespostasTest
        # .test_foto_reduzida_le_igual, que garante que a leitura não muda com essa redução.
        self.client.login(username='coord_up', password='segredo123')  # noqa: S106
        resp = self.client.get(self.url)
        self.assertContains(resp, 'data-reduzir-lado="3200"')
        self.assertContains(resp, 'data-reduzir-qualidade="0.88"')
        self.assertContains(resp, "canvas.toBlob(ok, 'image/jpeg', QUALIDADE)")


class FolhaDigitalizadaModelTest(TestCase):
    def test_status_default_pendente(self):
        prova = Prova.objects.create(titulo='P')
        turma = Turma.objects.create(periodo='2026.2', nome='MAC Noite')
        aplicacao = ProvaAplicacao.objects.create(prova=prova, turma=turma, data_aplicacao=date(2026, 11, 10))
        d = FolhaDigitalizada(prova_aplicacao=aplicacao)
        self.assertEqual(d.status, FolhaDigitalizada.Status.PENDENTE)


# Tokens fixos para os testes que leem o QR: a imagem gerada é sempre a mesma, então a
# leitura é determinística (com uuid4 aleatório, cada execução testava um QR diferente).
TOKEN_QR_OMR = uuid.UUID('3f0c0a52-6d0e-4b1a-9f55-0f8f6c1d2e3a')
TOKEN_QR_CORRECAO = uuid.UUID('9b2d7e41-5c3a-4f86-8d17-2a6e4c9b0f58')


def _canvas_omr(payload, px=PX_POR_MM):
    """Gera uma imagem sintética de folha: 4 marcadores ArUco + QR nas posições da geometria."""
    geo = build_layout(num_questoes=10)
    largura = int(round(PAGE_W * px))
    altura = int(round(PAGE_H * px))
    canvas = np.full((altura, largura, 3), 255, np.uint8)

    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    for m in geo.markers:
        size_px = int(round(m.size * px))
        marcador = cv2.cvtColor(cv2.aruco.generateImageMarker(dictionary, m.id, size_px), cv2.COLOR_GRAY2BGR)
        x0 = int(round(m.left * px))
        y0 = int(round(m.top * px))
        canvas[y0 : y0 + size_px, x0 : x0 + size_px] = marcador

    _colar_qr_producao(canvas, geo, payload, px)
    return canvas


def _colar_qr_producao(canvas, geo, payload, px=PX_POR_MM):
    """
    Desenha no canvas o QR **exatamente como é impresso** na folha: gerado por
    ``fiducial.gerar_qr_data_uri`` (margem 2, correção M) e escalado para a caixa do QR
    da geometria. Antes, os helpers usavam um QR próprio com margem 1 — fora do que vai
    para o papel —, o que tornava alguns testes intermitentes (~1–3% dos tokens).
    """
    png = base64.b64decode(gerar_qr_data_uri(payload).split(',', 1)[1])
    qr_arr = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    qsize = int(round(geo.qr.w * px))
    qr_arr = cv2.resize(qr_arr, (qsize, qsize), interpolation=cv2.INTER_NEAREST)
    qx = int(round(geo.qr.x * px))
    qy = int(round(geo.qr.y * px))
    canvas[qy : qy + qsize, qx : qx + qsize] = qr_arr


class OmrReaderTest(TestCase):
    def setUp(self):
        disc = Disciplina.objects.create(nome='Disc OMR')
        topico = Topico.objects.create(disciplina=disc, nome='Top OMR')
        questao = Questao.objects.create(disciplina=disc, topico=topico, tipo='OBJ', dificuldade='F', enunciado='Q')
        self.prova = Prova.objects.create(titulo='Prova OMR', disciplina=disc)
        self.pq = ProvaQuestao.objects.create(
            prova=self.prova, questao=questao, numero=1, ordem=1, snapshot_questao={'tipo': 'OBJ'}
        )
        self.versao = ProvaVersao.objects.create(prova=self.prova, identificador='A', numero_versao=1)
        self.turma = Turma.objects.create(periodo='2026.2', nome='MAC Noite')
        self.aluno = Aluno.objects.create(nome='Ana', matricula='A1')
        self.aplicacao = ProvaAplicacao.objects.create(
            prova=self.prova, turma=self.turma, data_aplicacao=date(2026, 11, 10)
        )
        self.folha = FolhaResposta.objects.create(
            prova_aplicacao=self.aplicacao, aluno=self.aluno, prova_versao=self.versao, qr_token=TOKEN_QR_OMR
        )
        self.payload = montar_payload_folha(self.folha.qr_token)

    def test_extrair_qr_token(self):
        self.assertEqual(extrair_qr_token(self.payload), str(self.folha.qr_token))
        self.assertIsNone(extrair_qr_token('lixo'))
        self.assertIsNone(extrair_qr_token('EXM1F|nao-e-uuid'))
        self.assertIsNone(extrair_qr_token(''))

    def test_localizar_folha_inexistente(self):
        self.assertIsNone(localizar_folha(str(uuid.uuid4())))

    def test_decodificar_e_identificar(self):
        canvas = _canvas_omr(self.payload)
        self.assertEqual(decodificar_qr(canvas), self.payload)
        self.assertEqual(identificar_folha(canvas), self.folha)

    def test_corrigir_perspectiva_tamanho_e_qr(self):
        canvas = _canvas_omr(self.payload)
        corrigida = corrigir_perspectiva(canvas)
        self.assertEqual(corrigida.shape[0], int(round(PAGE_H * PX_POR_MM)))
        self.assertEqual(corrigida.shape[1], int(round(PAGE_W * PX_POR_MM)))
        self.assertEqual(decodificar_qr(corrigida), self.payload)

    def test_corrigir_perspectiva_de_foto_torta(self):
        canvas = _canvas_omr(self.payload)
        h, w = canvas.shape[:2]
        origem = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
        destino = np.float32([[w * 0.04, h * 0.03], [w * 0.98, h * 0.01], [w * 0.99, h * 0.98], [w * 0.02, h * 0.97]])
        matriz = cv2.getPerspectiveTransform(origem, destino)
        torta = cv2.warpPerspective(canvas, matriz, (w, h), borderValue=(255, 255, 255))
        corrigida = corrigir_perspectiva(torta)
        self.assertEqual(identificar_folha(corrigida), self.folha)

    def test_marcadores_faltando_levanta_erro(self):
        branco = np.full((int(PAGE_H * PX_POR_MM), int(PAGE_W * PX_POR_MM), 3), 255, np.uint8)
        with self.assertRaises(OmrError):
            corrigir_perspectiva(branco)

    def test_marcadores_em_foto_de_celular_ruidosa_em_resolucao_cheia(self):
        """
        Foto real 3000×4000 (teste de campo de 2026-10-07): o detector com os parâmetros padrão
        não achava os marcadores. Aqui, 14 px/mm com ruído forte de sensor reproduz a falha:
        a detecção na imagem original perde um canto e a reduzida (1500 px) acha os 4.
        """
        canvas = _canvas_omr(self.payload, px=14)
        ruido = np.random.default_rng(5).normal(0, 70, canvas.shape[:2]).astype(np.float32)[:, :, None]
        foto = np.clip(canvas.astype(np.float32) * 0.8 + 20 + ruido, 0, 255).astype(np.uint8)

        centros, largura = detectar_marcadores_com_largura(cv2.cvtColor(foto, cv2.COLOR_BGR2GRAY))
        self.assertEqual(sorted(centros), [0, 1, 2, 3])
        self.assertEqual(largura, 1500)  # fechou só com a imagem reduzida
        self.assertEqual(
            corrigir_perspectiva(foto).shape[:2], (int(round(PAGE_H * PX_POR_MM)), int(round(PAGE_W * PX_POR_MM)))
        )

    def test_qr_lido_no_recorte_endireitado_quando_a_foto_inteira_falha(self):
        from unittest import mock

        from .services import omr

        canvas = _canvas_omr(self.payload)
        original = omr.decodificar_qr
        chamadas = []

        def falha_na_foto_inteira(imagem):
            chamadas.append(imagem.shape)
            return None if len(chamadas) == 1 else original(imagem)

        with mock.patch.object(omr, 'decodificar_qr', side_effect=falha_na_foto_inteira):
            self.assertEqual(ler_qr_da_folha(canvas), (self.payload, 'recorte'))
            self.assertEqual(identificar_folha(canvas), self.folha)
        self.assertEqual(ler_qr_da_folha(canvas), (self.payload, 'imagem'))


def _canvas_bolhas(pares, num_questoes=6, qr_payload=None, px=PX_POR_MM):
    """
    Canvas canônico com marcadores ArUco (+ QR opcional) e bolhas de resposta preenchidas.

    ``pares``: iterável de tuplas ``(questao, opção)`` a preencher, ex.: ``{(1, 'A'), (2, 'C')}``.
    Retorna ``(canvas, geometry)``.

    Desenha de propósito o layout **antigo, com matriz de matrícula** (``id_digits=8``),
    enquanto a correção lê com a geometria atual do cartão (sem matriz): os testes de
    correção provam que cartões impressos antes da remoção da matriz seguem legíveis.
    """
    geo = build_layout(num_questoes=num_questoes, num_alternativas=5, id_digits=8, columns=3)
    largura = int(round(PAGE_W * px))
    altura = int(round(PAGE_H * px))
    canvas = np.full((altura, largura, 3), 255, np.uint8)

    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    for m in geo.markers:
        size_px = int(round(m.size * px))
        marcador = cv2.cvtColor(cv2.aruco.generateImageMarker(dictionary, m.id, size_px), cv2.COLOR_GRAY2BGR)
        x0 = int(round(m.left * px))
        y0 = int(round(m.top * px))
        canvas[y0 : y0 + size_px, x0 : x0 + size_px] = marcador

    if qr_payload:
        _colar_qr_producao(canvas, geo, qr_payload, px)

    alvo = set(pares)
    for b in geo.answer_bubbles:
        if (b.question, b.option) in alvo:
            cv2.circle(canvas, (int(round(b.cx * px)), int(round(b.cy * px))), int(round(b.r * px)), (0, 0, 0), -1)

    return canvas, geo


class OmrLerRespostasTest(SimpleTestCase):
    """Amostragem das bolhas: leitura da alternativa marcada, branco e ambígua."""

    def test_le_marcadas_e_branco(self):
        canvas, geo = _canvas_bolhas({(1, 'A'), (2, 'C'), (3, 'E')}, num_questoes=4)
        leituras = ler_respostas(canvas, geo.answer_bubbles)
        self.assertEqual(leituras[1].status, 'ok')
        self.assertEqual(leituras[1].marcada, 'A')
        self.assertEqual(leituras[2].marcada, 'C')
        self.assertEqual(leituras[3].marcada, 'E')
        self.assertEqual(leituras[4].status, 'branco')
        self.assertIsNone(leituras[4].marcada)

    def test_foto_reduzida_le_igual(self):
        """
        A tela de envio reduz as fotos no navegador (lado maior 3200 px, JPEG 0,88). Um cartão
        desenhado a 14 px/mm (foto de ~12 MP) e reduzido para 3200 e até 1600 px continua com o
        mesmo QR e as mesmas respostas — a imagem canônica do OMR tem 10 px/mm (2100 × 2970).
        """
        marcadas = {(1, 'B'), (2, 'E'), (3, 'A'), (5, 'C')}
        payload = montar_payload_folha(TOKEN_QR_CORRECAO)
        canvas, geo = _canvas_bolhas(marcadas, num_questoes=6, qr_payload=payload, px=14)
        canvas = cv2.GaussianBlur(canvas, (0, 0), 1.0)
        esperado = {q: le.marcada for q, le in ler_respostas(corrigir_perspectiva(canvas), geo.answer_bubbles).items()}
        self.assertEqual(sum(1 for letra in esperado.values() if letra), 4)
        for lado in (3200, 1600):
            with self.subTest(lado=lado):
                fator = lado / max(canvas.shape[:2])
                menor = cv2.resize(canvas, None, fx=fator, fy=fator, interpolation=cv2.INTER_AREA)
                _, jpg = cv2.imencode('.jpg', menor, [cv2.IMWRITE_JPEG_QUALITY, 88])
                foto = cv2.imdecode(jpg, cv2.IMREAD_COLOR)
                canonica = corrigir_perspectiva(foto)
                self.assertEqual(decodificar_qr(canonica), payload)
                lidas = ler_respostas(canonica, geo.answer_bubbles)
                self.assertEqual({q: le.marcada for q, le in lidas.items()}, esperado)

    def test_ambigua_com_duas_marcadas(self):
        canvas, geo = _canvas_bolhas({(1, 'A'), (1, 'B')}, num_questoes=2)
        leituras = ler_respostas(canvas, geo.answer_bubbles)
        self.assertEqual(leituras[1].status, 'ambigua')
        self.assertIsNone(leituras[1].marcada)

    def test_sem_aneis_impressos_nao_ha_ajuste(self):
        canvas, geo = _canvas_bolhas({(1, 'A'), (2, 'C')}, num_questoes=3)
        leituras = ler_respostas(canvas, geo.answer_bubbles)
        self.assertTrue(all(leitura.deslocamento_mm == (0.0, 0.0) for leitura in leituras.values()))

    @staticmethod
    def _cartao_curvado(raio_impresso_mm, marcadas, deslocamento_por_coluna_px):
        """
        Imagem canônica com o anel impresso de cada bolha e as marcadas preenchidas, todas
        deslocadas por coluna — como uma folha curvada fotografada na mão (na foto real de
        2026-10-07: +0,5/+2,2 mm, −0,4/+2,1 mm e −2,0/+1,6 mm).
        """
        geo = build_layout(num_questoes=15, num_alternativas=5, id_digits=0, columns=3)
        canvas = np.full((int(round(PAGE_H * PX_POR_MM)), int(round(PAGE_W * PX_POR_MM)), 3), 255, np.uint8)
        r = int(round(raio_impresso_mm * PX_POR_MM))
        for b in geo.answer_bubbles:
            coluna = (b.question - 1) // 5
            dx, dy = deslocamento_por_coluna_px[coluna]
            centro = (int(round(b.cx * PX_POR_MM + dx)), int(round(b.cy * PX_POR_MM + dy)))
            cv2.circle(canvas, centro, r, (65, 85, 51), 3)  # borda impressa (#334155)
            if (b.question, b.option) in marcadas:
                cv2.circle(canvas, centro, int(r * 0.85), (90, 40, 20), -1)  # caneta azul
        return canvas, geo

    def test_ajuste_fino_le_folha_curvada(self):
        from unittest import mock

        from .services import omr

        marcadas = {(q, 'ABCDE'[q % 5]) for q in range(1, 16)}
        deslocamentos = [(5, 22), (-4, 21), (-20, 16)]
        for raio in (2.4, 1.9):  # cartão atual e cartões impressos antes de 2026-10-06
            canvas, geo = self._cartao_curvado(raio, marcadas, deslocamentos)
            leituras = ler_respostas(canvas, geo.answer_bubbles)
            self.assertEqual({q: leitura.marcada for q, leitura in leituras.items()}, dict(marcadas), raio)
            self.assertEqual(leituras[1].deslocamento_mm, (0.5, 2.2))
            self.assertEqual(leituras[11].deslocamento_mm, (-2.0, 1.6))

            # Sem o ajuste fino, a marcação fica com menos margem; na bolha antiga (1,9 mm, a da
            # foto real) a folha nem é lida. A bolha atual, maior, tolera mais deslocamento.
            with mock.patch.object(omr, '_deslocamento_da_questao', return_value=(0.0, 0.0)):
                sem_ajuste = ler_respostas(canvas, geo.answer_bubbles)

            def menor_marcada(lidas):
                return min(lidas[q].preenchimentos[letra] for q, letra in marcadas)

            self.assertGreater(menor_marcada(leituras), menor_marcada(sem_ajuste))
            if raio == 1.9:
                self.assertNotEqual({q: leitura.marcada for q, leitura in sem_ajuste.items()}, dict(marcadas))


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class CorrecaoFolhaTest(TestCase):
    """RF11: correção automática end-to-end gravando Desempenho."""

    GABARITO = {1: 'B', 2: 'A', 3: 'D'}

    def setUp(self):
        self.user = get_user_model().objects.create_user(username='corretor', password='segredo123')  # noqa: S106
        self.user.groups.add(Group.objects.get_or_create(name='Administrador')[0])
        self.disc = Disciplina.objects.create(nome='Disc Correcao')
        self.prova = Prova.objects.create(titulo='Prova Correcao', disciplina=self.disc)
        self.versao = ProvaVersao.objects.create(prova=self.prova, identificador='A', numero_versao=1)
        self.questoes = {}
        for ordem, correta in self.GABARITO.items():
            questao = Questao.objects.create(disciplina=self.disc, tipo='OBJ', dificuldade='F', enunciado=f'Q{ordem}')
            self.questoes[ordem] = questao
            pq = ProvaQuestao.objects.create(
                prova=self.prova, questao=questao, numero=ordem, ordem=ordem, snapshot_questao={'tipo': 'OBJ'}
            )
            alts = [{'letra': letra, 'correta': (letra == correta)} for letra in ['A', 'B', 'C', 'D', 'E']]
            ProvaVersaoItem.objects.create(
                versao=self.versao, prova_questao=pq, numero=ordem, ordem=ordem, alternativas_embaralhadas=alts
            )
        self.turma = Turma.objects.create(periodo='2026.2', nome='T Correcao')
        self.aluno = Aluno.objects.create(nome='Ana', matricula='A1')
        self.aplicacao = ProvaAplicacao.objects.create(
            prova=self.prova, turma=self.turma, data_aplicacao=date(2026, 11, 10)
        )
        self.folha = FolhaResposta.objects.create(
            prova_aplicacao=self.aplicacao, aluno=self.aluno, prova_versao=self.versao, qr_token=TOKEN_QR_CORRECAO
        )

    def _digitalizacao(self, pares, qr_payload=None, folha=None, torta=False):
        canvas, _ = _canvas_bolhas(pares, num_questoes=3, qr_payload=qr_payload)
        if torta:
            altura, largura = canvas.shape[:2]
            origem = np.float32([[0, 0], [largura, 0], [largura, altura], [0, altura]])
            destino = np.float32(
                [
                    [largura * 0.04, altura * 0.03],
                    [largura * 0.98, altura * 0.01],
                    [largura * 0.99, altura * 0.98],
                    [largura * 0.02, altura * 0.97],
                ]
            )
            matriz = cv2.getPerspectiveTransform(origem, destino)
            canvas = cv2.warpPerspective(canvas, matriz, (largura, altura), borderValue=(255, 255, 255))
        _ok, buffer = cv2.imencode('.png', canvas)
        arquivo = SimpleUploadedFile('folha.png', buffer.tobytes(), content_type='image/png')
        return FolhaDigitalizada.objects.create(
            prova_aplicacao=self.aplicacao, imagem=arquivo, folha_resposta=folha, enviado_por=self.user
        )

    def test_correcao_grava_desempenho(self):
        from desempenho.models import Desempenho

        # Q1 marca B (correta), Q2 marca C (errada), Q3 em branco.
        dig = self._digitalizacao({(1, 'B'), (2, 'C')}, folha=self.folha)
        resultado = corrigir_folha_digitalizada(dig, usuario=self.user)

        self.assertTrue(resultado.sucesso, resultado.mensagem)
        self.assertEqual(resultado.total_questoes, 3)
        self.assertEqual(resultado.acertos, 1)
        self.assertEqual(resultado.brancos, 1)

        d1 = Desempenho.objects.get(folha_resposta=self.folha, questao=self.questoes[1])
        self.assertEqual(d1.resposta_marcada, 'B')
        self.assertTrue(d1.acertou)
        self.assertEqual(d1.tipo_correcao, Desempenho.TipoCorrecao.AUTOMATICA)

        d2 = Desempenho.objects.get(folha_resposta=self.folha, questao=self.questoes[2])
        self.assertEqual(d2.resposta_marcada, 'C')
        self.assertFalse(d2.acertou)

        d3 = Desempenho.objects.get(folha_resposta=self.folha, questao=self.questoes[3])
        self.assertEqual(d3.resposta_marcada, '')
        self.assertFalse(d3.acertou)

        self.folha.refresh_from_db()
        dig.refresh_from_db()
        self.assertEqual(self.folha.status, FolhaResposta.Status.LIDA)
        self.assertEqual(dig.status, FolhaDigitalizada.Status.PROCESSADA)

    def test_identifica_folha_pelo_qr(self):
        dig = self._digitalizacao({(1, 'B')}, qr_payload=montar_payload_folha(self.folha.qr_token))
        resultado = corrigir_folha_digitalizada(dig, usuario=self.user)
        self.assertTrue(resultado.sucesso, resultado.mensagem)
        dig.refresh_from_db()
        self.assertEqual(dig.folha_resposta_id, self.folha.pk)

    def test_qr_ausente_marca_erro(self):
        dig = self._digitalizacao(set())  # sem QR e sem folha pré-identificada
        resultado = corrigir_folha_digitalizada(dig, usuario=self.user)
        self.assertFalse(resultado.sucesso)
        dig.refresh_from_db()
        self.assertEqual(dig.status, FolhaDigitalizada.Status.ERRO)

    def test_view_corrige_pendentes(self):
        from desempenho.models import Desempenho

        self.client.login(username='corretor', password='segredo123')  # noqa: S106
        dig = self._digitalizacao({(1, 'B'), (2, 'A'), (3, 'D')}, folha=self.folha)  # todas corretas
        url = reverse('provas:aplicacao_corrigir_folhas', args=[self.aplicacao.pk])
        resp = self.client.post(url)

        self.assertRedirects(resp, reverse('provas:aplicacao_upload_folhas', args=[self.aplicacao.pk]))
        dig.refresh_from_db()
        self.assertEqual(dig.status, FolhaDigitalizada.Status.PROCESSADA)
        self.assertEqual(Desempenho.objects.filter(folha_resposta=self.folha, acertou=True).count(), 3)

    def test_dupla_marcacao_end_to_end(self):
        from desempenho.models import Desempenho

        dig = self._digitalizacao({(1, 'A'), (1, 'B')}, folha=self.folha)  # Q1 com duas marcações
        resultado = corrigir_folha_digitalizada(dig, usuario=self.user)
        self.assertTrue(resultado.sucesso, resultado.mensagem)
        self.assertEqual(resultado.ambiguas, 1)
        d1 = Desempenho.objects.get(folha_resposta=self.folha, questao=self.questoes[1])
        self.assertEqual(d1.resposta_marcada, '*')
        self.assertFalse(d1.acertou)

    def test_folha_torta_end_to_end(self):
        from desempenho.models import Desempenho

        # Todas corretas, mas a imagem chega distorcida (perspectiva): o deskew deve resolver.
        dig = self._digitalizacao({(1, 'B'), (2, 'A'), (3, 'D')}, folha=self.folha, torta=True)
        resultado = corrigir_folha_digitalizada(dig, usuario=self.user)
        self.assertTrue(resultado.sucesso, resultado.mensagem)
        self.assertEqual(resultado.acertos, 3)
        self.assertEqual(Desempenho.objects.filter(folha_resposta=self.folha, acertou=True).count(), 3)

    def test_rejeita_folha_de_outra_aplicacao(self):
        from desempenho.models import Desempenho

        # Mesma prova/versão aplicada a outra turma: a folha lida não pertence a esta aplicação.
        # (A folha é vinculada diretamente para exercitar a salvaguarda sem depender da leitura do QR.)
        outra_aplicacao = ProvaAplicacao.objects.create(
            prova=self.prova,
            turma=Turma.objects.create(periodo='2026.2', nome='T Outra'),
            data_aplicacao=date(2026, 11, 11),
        )
        folha_outra = FolhaResposta.objects.create(
            prova_aplicacao=outra_aplicacao,
            aluno=Aluno.objects.create(nome='Bia', matricula='B1'),
            prova_versao=self.versao,
        )
        dig = self._digitalizacao({(1, 'B')}, folha=folha_outra)

        resultado = corrigir_folha_digitalizada(dig, usuario=self.user)

        self.assertFalse(resultado.sucesso)
        self.assertIn('outra aplicação', resultado.mensagem)
        dig.refresh_from_db()
        self.assertEqual(dig.status, FolhaDigitalizada.Status.ERRO)
        self.assertFalse(Desempenho.objects.filter(folha_resposta=folha_outra).exists())

    def test_rejeita_folha_anulada(self):
        from desempenho.models import Desempenho

        self.folha.status = FolhaResposta.Status.ANULADA
        self.folha.save(update_fields=['status'])
        dig = self._digitalizacao({(1, 'B')}, folha=self.folha)

        resultado = corrigir_folha_digitalizada(dig, usuario=self.user)

        self.assertFalse(resultado.sucesso)
        self.assertIn('anulado', resultado.mensagem)
        self.assertFalse(Desempenho.objects.filter(folha_resposta=self.folha).exists())

    def test_reprocessamento_preserva_correcao_manual(self):
        from desempenho.models import Desempenho

        # 1ª leitura: tudo em branco → Q1 vai para a fila e é corrigida manualmente como B (correta).
        corrigir_folha_digitalizada(self._digitalizacao(set(), folha=self.folha), usuario=self.user)
        d1 = Desempenho.objects.get(folha_resposta=self.folha, questao=self.questoes[1])
        corrigir_questao_manual(d1, 'B', usuario=self.user)

        # Reenvio da mesma folha, agora com Q1 lida como C e Q2 como A (correta).
        resultado = corrigir_folha_digitalizada(
            self._digitalizacao({(1, 'C'), (2, 'A')}, folha=self.folha), usuario=self.user
        )

        self.assertTrue(resultado.sucesso, resultado.mensagem)
        self.assertEqual(resultado.preservadas, 1)
        self.assertEqual(resultado.acertos, 2)  # Q1 (manual) + Q2 (automática)
        d1.refresh_from_db()
        self.assertEqual(d1.tipo_correcao, Desempenho.TipoCorrecao.MANUAL)
        self.assertEqual(d1.resposta_marcada, 'B')
        self.assertTrue(d1.acertou)
        d2 = Desempenho.objects.get(folha_resposta=self.folha, questao=self.questoes[2])
        self.assertEqual(d2.tipo_correcao, Desempenho.TipoCorrecao.AUTOMATICA)
        self.assertTrue(d2.acertou)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class RevisaoManualTest(TestCase):
    """Fila de revisão manual: listagem, correção pontual e revínculo de folha."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username='rev', password='segredo123')  # noqa: S106
        self.user.groups.add(Group.objects.get_or_create(name='Administrador')[0])
        self.disc = Disciplina.objects.create(nome='Disc Rev')
        self.prova = Prova.objects.create(titulo='Prova Rev', disciplina=self.disc)
        self.versao = ProvaVersao.objects.create(prova=self.prova, identificador='A', numero_versao=1)
        self.questao = Questao.objects.create(
            disciplina=self.disc, tipo='OBJ', dificuldade='F', enunciado='Questão de revisão'
        )
        pq = ProvaQuestao.objects.create(
            prova=self.prova, questao=self.questao, numero=1, ordem=1, snapshot_questao={'tipo': 'OBJ'}
        )
        alts = [{'letra': letra, 'correta': (letra == 'B')} for letra in ['A', 'B', 'C', 'D', 'E']]
        ProvaVersaoItem.objects.create(
            versao=self.versao, prova_questao=pq, numero=1, ordem=1, alternativas_embaralhadas=alts
        )
        self.turma = Turma.objects.create(periodo='2026.2', nome='T Rev')
        self.aluno = Aluno.objects.create(nome='Ana', matricula='A1')
        self.aplicacao = ProvaAplicacao.objects.create(
            prova=self.prova, turma=self.turma, data_aplicacao=date(2026, 11, 10)
        )
        self.folha = FolhaResposta.objects.create(
            prova_aplicacao=self.aplicacao, aluno=self.aluno, prova_versao=self.versao
        )
        self.client.login(username='rev', password='segredo123')  # noqa: S106

    def _desempenho(self, marcada):
        from desempenho.models import Desempenho

        return Desempenho.objects.create(
            folha_resposta=self.folha,
            questao=self.questao,
            tipo_correcao=Desempenho.TipoCorrecao.AUTOMATICA,
            resposta_marcada=marcada,
            acertou=False,
        )

    def _digitalizacao_erro(self, pares):
        canvas, _ = _canvas_bolhas(pares, num_questoes=1)
        _ok, buffer = cv2.imencode('.png', canvas)
        return FolhaDigitalizada.objects.create(
            prova_aplicacao=self.aplicacao,
            imagem=SimpleUploadedFile('erro.png', buffer.tobytes(), content_type='image/png'),
            status=FolhaDigitalizada.Status.ERRO,
            mensagem='QR não identificado',
        )

    def test_pagina_lista_pendencias(self):
        self._desempenho('*')
        self._digitalizacao_erro(set())
        resp = self.client.get(reverse('provas:aplicacao_revisao_manual', args=[self.aplicacao.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Dupla marcação')
        self.assertContains(resp, 'QR não identificado')

    def test_corrigir_questao_manual_letra_correta(self):
        from desempenho.models import Desempenho

        dsp = self._desempenho('*')
        resp = self.client.post(reverse('provas:desempenho_revisao_manual', args=[dsp.pk]), {'resposta': 'B'})
        self.assertRedirects(resp, reverse('provas:aplicacao_revisao_manual', args=[self.aplicacao.pk]))
        dsp.refresh_from_db()
        self.assertEqual(dsp.resposta_marcada, 'B')
        self.assertTrue(dsp.acertou)
        self.assertEqual(dsp.tipo_correcao, Desempenho.TipoCorrecao.MANUAL)

    def test_corrigir_questao_manual_em_branco_recalcula(self):
        dsp = self._desempenho('*')
        corrigir_questao_manual(dsp, '', usuario=self.user)
        dsp.refresh_from_db()
        self.assertEqual(dsp.resposta_marcada, '')
        self.assertFalse(dsp.acertou)

    def test_corrigir_questao_manual_resposta_invalida(self):
        dsp = self._desempenho('')
        with self.assertRaises(ValueError):
            corrigir_questao_manual(dsp, 'Z', usuario=self.user)

    def test_vincular_folha_reprocessa(self):
        from desempenho.models import Desempenho

        dig = self._digitalizacao_erro({(1, 'B')})  # marca a alternativa correta
        resp = self.client.post(
            reverse('provas:digitalizacao_vincular_folha', args=[dig.pk]), {'folha_id': self.folha.pk}
        )
        self.assertRedirects(resp, reverse('provas:aplicacao_revisao_manual', args=[self.aplicacao.pk]))
        dig.refresh_from_db()
        self.assertEqual(dig.status, FolhaDigitalizada.Status.PROCESSADA)
        self.assertEqual(dig.folha_resposta_id, self.folha.pk)
        self.assertTrue(
            Desempenho.objects.filter(folha_resposta=self.folha, questao=self.questao, acertou=True).exists()
        )


class LancamentoManualTest(TestCase):
    """Lançamento manual de discursivas (nota/corte) e fallback objetivo sem scan."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username='prof', password='segredo123')  # noqa: S106
        self.user.groups.add(Group.objects.get_or_create(name='Administrador')[0])
        self.disc = Disciplina.objects.create(nome='Disc Lanc')
        self.prova = Prova.objects.create(titulo='Prova Lanc', disciplina=self.disc)
        self.versao = ProvaVersao.objects.create(prova=self.prova, identificador='A', numero_versao=1)

        self.obj = Questao.objects.create(disciplina=self.disc, tipo='OBJ', dificuldade='F', enunciado='Objetiva')
        pq_obj = ProvaQuestao.objects.create(
            prova=self.prova, questao=self.obj, numero=1, ordem=1, snapshot_questao={'tipo': 'OBJ'}
        )
        ProvaVersaoItem.objects.create(
            versao=self.versao,
            prova_questao=pq_obj,
            numero=1,
            ordem=1,
            alternativas_embaralhadas=[
                {'letra': letra, 'correta': (letra == 'B')} for letra in ['A', 'B', 'C', 'D', 'E']
            ],
        )

        self.dis = Questao.objects.create(disciplina=self.disc, tipo='DIS', dificuldade='M', enunciado='Discursiva')
        pq_dis = ProvaQuestao.objects.create(
            prova=self.prova, questao=self.dis, numero=2, ordem=2, snapshot_questao={'tipo': 'DIS'}
        )
        ProvaVersaoItem.objects.create(
            versao=self.versao, prova_questao=pq_dis, numero=2, ordem=2, alternativas_embaralhadas=[]
        )

        self.turma = Turma.objects.create(periodo='2026.2', nome='T Lanc')
        self.aluno = Aluno.objects.create(nome='Ana', matricula='A1')
        self.aplicacao = ProvaAplicacao.objects.create(
            prova=self.prova, turma=self.turma, data_aplicacao=date(2026, 11, 10)
        )
        self.folha = FolhaResposta.objects.create(
            prova_aplicacao=self.aplicacao, aluno=self.aluno, prova_versao=self.versao
        )

    def test_tela_nao_tem_mais_nota_de_corte(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('provas:folha_lancamento_manual', args=[self.folha.pk]))
        self.assertNotContains(resp, 'nota_corte')
        self.assertNotContains(resp, 'Nota de corte')
        self.assertFalse(hasattr(self.aplicacao, 'nota_corte'))

    def test_campos_numericos_mostram_os_valores_atuais(self):
        # <input type="number"> só aceita ponto: o valor localizado ("7,50") apareceria vazio.
        lancar_nota_discursiva(self.folha, self.dis, Decimal('7.5'), usuario=self.user)
        self.client.force_login(self.user)
        resp = self.client.get(reverse('provas:folha_lancamento_manual', args=[self.folha.pk]))
        self.assertContains(resp, 'value="7.50"')
        self.assertContains(resp, 'Corrigida')  # discursiva: sem "Acertou/Errou"

    def test_tela_de_lancamento_usa_os_rotulos_do_caderno(self):
        # Versão antiga (discursiva gravada com número 2): a tela mostra "D01", como no caderno.
        self.client.force_login(self.user)
        resp = self.client.get(reverse('provas:folha_lancamento_manual', args=[self.folha.pk]))
        self.assertEqual([linha['rotulo_curto'] for linha in resp.context['linhas']], ['Q01', 'D01'])
        self.assertContains(resp, '>D01<')

    def test_lancar_discursiva_grava_a_nota_sem_acertou(self):
        from desempenho.models import Desempenho

        dsp = lancar_nota_discursiva(self.folha, self.dis, 7, usuario=self.user)
        self.assertEqual(dsp.nota, Decimal('7'))
        self.assertIsNone(dsp.acertou)  # a discursiva conta pela nota, não por acertou/errou
        self.assertEqual(dsp.tipo_correcao, Desempenho.TipoCorrecao.MANUAL)

    def test_nota_fora_do_intervalo_levanta_erro(self):
        with self.assertRaises(ValueError):
            lancar_nota_discursiva(self.folha, self.dis, 11, usuario=self.user)

    def test_fallback_objetiva_por_gabarito(self):
        acerto = lancar_objetiva_manual(self.folha, self.obj, 'B', usuario=self.user)
        self.assertTrue(acerto.acertou)
        self.assertEqual(acerto.resposta_marcada, 'B')
        erro = lancar_objetiva_manual(self.folha, self.obj, 'C', usuario=self.user)
        self.assertFalse(erro.acertou)

    def test_view_post_lanca_discursiva_e_objetiva(self):
        from desempenho.models import Desempenho

        self.client.login(username='prof', password='segredo123')  # noqa: S106
        url = reverse('provas:folha_lancamento_manual', args=[self.folha.pk])
        resp = self.client.post(
            url,
            {f'nota_{self.dis.id}': '8', f'resp_{self.obj.id}': 'B'},
        )
        self.assertRedirects(resp, url)
        d_dis = Desempenho.objects.get(folha_resposta=self.folha, questao=self.dis)
        self.assertEqual(d_dis.nota, Decimal('8'))
        self.assertIsNone(d_dis.acertou)
        d_obj = Desempenho.objects.get(folha_resposta=self.folha, questao=self.obj)
        self.assertEqual(d_obj.resposta_marcada, 'B')
        self.assertTrue(d_obj.acertou)

    def test_pagina_folhas_lista_aluno(self):
        self.client.login(username='prof', password='segredo123')  # noqa: S106
        resp = self.client.get(reverse('provas:aplicacao_folhas', args=[self.aplicacao.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Ana')

    def test_pagina_folhas_separa_objetivas_e_discursivas(self):
        lancar_objetiva_manual(self.folha, self.obj, 'B', usuario=self.user)
        lancar_nota_discursiva(self.folha, self.dis, 6, usuario=self.user)
        self.client.login(username='prof', password='segredo123')  # noqa: S106
        resp = self.client.get(reverse('provas:aplicacao_folhas', args=[self.aplicacao.pk]))
        self.assertContains(resp, '1 de 1 objetiva(s) certa(s)')
        self.assertContains(resp, '1 discursiva(s) corrigida(s)')
        self.assertNotContains(resp, 'Nota de corte')

    def test_model_valida_intervalo_da_nota(self):
        from desempenho.models import Desempenho

        registro = Desempenho(folha_resposta=self.folha, questao=self.dis, nota=Decimal('11'))
        with self.assertRaises(ValidationError):
            registro.full_clean()
        registro.nota = Decimal('-1')
        with self.assertRaises(ValidationError):
            registro.full_clean()
        registro.nota = Decimal('10')
        registro.full_clean()  # dentro do intervalo: não levanta

    def test_duplicado_folha_questao_bloqueado(self):
        from desempenho.models import Desempenho

        Desempenho.objects.create(folha_resposta=self.folha, questao=self.dis, nota=Decimal('5'))
        with self.assertRaises(IntegrityError), transaction.atomic():
            Desempenho.objects.create(folha_resposta=self.folha, questao=self.dis, nota=Decimal('6'))

    def test_relancamento_atualiza_sem_duplicar(self):
        from desempenho.models import Desempenho

        lancar_nota_discursiva(self.folha, self.dis, 5, usuario=self.user)
        lancar_nota_discursiva(self.folha, self.dis, 9, usuario=self.user)
        self.assertEqual(Desempenho.objects.filter(folha_resposta=self.folha, questao=self.dis).count(), 1)
        registro = Desempenho.objects.get(folha_resposta=self.folha, questao=self.dis)
        self.assertEqual(registro.nota, Decimal('9'))


class TravaProvaAplicadaTest(TestCase):
    """Prova com versões distribuídas a alunos fica travada; antes disso, a troca sincroniza os cadernos."""

    def setUp(self):
        from provas.services.snapshot_builder import construir_snapshot_questao
        from provas.services.version_generator import gerar_versoes_prova

        self.user = get_user_model().objects.create_user(username='prof_trava', password='segredo123')  # noqa: S106
        self.user.groups.add(Group.objects.get(name='Docente'))
        self.client.login(username='prof_trava', password='segredo123')  # noqa: S106

        self.disc = Disciplina.objects.create(nome='Disc Trava')
        self.topico = Topico.objects.create(disciplina=self.disc, nome='Tópico Trava')
        self.questoes = [self._questao(f'Q{i}') for i in range(1, 4)]
        self.prova = Prova.objects.create(titulo='Prova Trava', disciplina=self.disc, autor=self.user)
        for ordem, questao in enumerate(self.questoes, start=1):
            ProvaQuestao.objects.create(
                prova=self.prova,
                questao=questao,
                numero=ordem,
                ordem=ordem,
                snapshot_questao=construir_snapshot_questao(questao),
            )
        self.versoes = gerar_versoes_prova(self.prova, quantidade_versoes=2)
        self.item = self.prova.itens.get(ordem=1)
        self.nova = self._questao('NOVA', correta='C')

        self.aluno = Aluno.objects.create(nome='Ana', matricula='A1')
        self.aplicacao = ProvaAplicacao.objects.create(
            prova=self.prova,
            turma=Turma.objects.create(periodo='2026.2', nome='T Trava'),
            data_aplicacao=date(2026, 11, 10),
        )

    def _questao(self, enunciado, correta='A'):
        questao = Questao.objects.create(
            disciplina=self.disc, topico=self.topico, tipo='OBJ', dificuldade='F', enunciado=enunciado
        )
        for letra in 'ABCDE':
            Alternativa.objects.create(
                questao=questao, letra=letra, texto=f'{enunciado}-{letra}', correta=(letra == correta)
            )
        return questao

    def _distribuir_por_folha(self):
        return FolhaResposta.objects.create(
            prova_aplicacao=self.aplicacao, aluno=self.aluno, prova_versao=self.versoes[0]
        )

    def test_nao_travada_sem_folhas_nem_atribuicoes(self):
        # Ter uma aplicação cadastrada não trava: nada foi distribuído a alunos ainda.
        self.assertFalse(self.prova.versoes_distribuidas)

    def test_travada_por_atribuicao_ou_folha(self):
        AtribuicaoVersao.objects.create(aplicacao=self.aplicacao, aluno=self.aluno, versao=self.versoes[1])
        self.assertTrue(self.prova.versoes_distribuidas)
        AtribuicaoVersao.objects.all().delete()
        self._distribuir_por_folha()
        self.assertTrue(self.prova.versoes_distribuidas)

    def test_regerar_versoes_bloqueado_apos_distribuicao(self):
        from provas.services.version_generator import VersoesDistribuidasError, gerar_versoes_prova

        self._distribuir_por_folha()
        pks_antes = set(self.prova.versoes.values_list('pk', flat=True))

        with self.assertRaises(VersoesDistribuidasError):
            gerar_versoes_prova(self.prova, quantidade_versoes=3)
        self.assertEqual(set(self.prova.versoes.values_list('pk', flat=True)), pks_antes)

    def test_view_regerar_bloqueado_redireciona_com_mensagem(self):
        self._distribuir_por_folha()
        pks_antes = set(self.prova.versoes.values_list('pk', flat=True))

        resp = self.client.post(
            reverse('provas:prova_gerar_versoes', args=[self.prova.pk]), {'quantidade_versoes': 3}, follow=True
        )

        self.assertRedirects(resp, reverse('provas:prova_revisao', args=[self.prova.pk]))
        self.assertContains(resp, 'Não foi possível regerar os cadernos')
        self.assertEqual(set(self.prova.versoes.values_list('pk', flat=True)), pks_antes)

    def test_troca_bloqueada_apos_distribuicao(self):
        from provas.services.question_replacer import SubstituicaoQuestaoError, substituir_questao_prova

        self._distribuir_por_folha()
        with self.assertRaises(SubstituicaoQuestaoError):
            substituir_questao_prova(self.item, self.nova)
        self.item.refresh_from_db()
        self.assertEqual(self.item.questao, self.questoes[0])

    def test_troca_antes_da_distribuicao_sincroniza_cadernos(self):
        from provas.services.question_replacer import substituir_questao_prova

        substituir_questao_prova(self.item, self.nova)

        textos_nova = {f'NOVA-{letra}' for letra in 'ABCDE'}
        for versao in self.versoes:
            item_versao = versao.itens.get(prova_questao=self.item)
            alternativas = item_versao.alternativas_embaralhadas
            self.assertEqual({a['texto'] for a in alternativas}, textos_nova)
            self.assertEqual([a['letra'] for a in alternativas], list('ABCDE'))
            corretas = [a for a in alternativas if a['correta']]
            self.assertEqual(len(corretas), 1)
            self.assertEqual(corretas[0]['texto'], 'NOVA-C')
        # A versão A (1ª) não embaralha: gabarito igual ao da questão original.
        item_a = self.versoes[0].itens.get(prova_questao=self.item)
        self.assertEqual([a['letra_original'] for a in item_a.alternativas_embaralhadas], list('ABCDE'))

    def test_revisao_exibe_trava_e_desabilita_troca(self):
        self._distribuir_por_folha()
        resp = self.client.get(reverse('provas:prova_revisao', args=[self.prova.pk]))
        self.assertContains(resp, 'versões travadas')
        self.assertContains(resp, 'Troca bloqueada')
        self.assertNotContains(resp, reverse('provas:prova_gerar_versoes', args=[self.prova.pk]))


class ControleAcessoPerfilProvasTest(TestCase):
    """Views de provas/correção exigem perfil Docente ou Coordenador (não basta estar logado)."""

    def setUp(self):
        self.prova = Prova.objects.create(titulo='Prova Acesso')
        self.aplicacao = ProvaAplicacao.objects.create(
            prova=self.prova,
            turma=Turma.objects.create(periodo='2026.2', nome='T Acesso'),
            data_aplicacao=date(2026, 11, 10),
        )
        self.sem_grupo = get_user_model().objects.create_user(username='sem_grupo', password='segredo123')  # noqa: S106
        self.coordenador = get_user_model().objects.create_user(username='coord_ac', password='segredo123')  # noqa: S106
        self.coordenador.groups.add(Group.objects.get(name='Coordenador'))

    def _urls(self):
        return [
            reverse('provas:prova_list'),
            reverse('provas:mascara_list'),
            reverse('provas:prova_gabarito_pdf', args=[self.prova.pk]),
            reverse('provas:aplicacao_folhas', args=[self.aplicacao.pk]),
            reverse('provas:aplicacao_revisao_manual', args=[self.aplicacao.pk]),
            reverse('provas:folha_resposta_demo'),
            reverse('provas:aplicacao_list'),
            reverse('provas:aplicacao_detail', args=[self.aplicacao.pk]),
            reverse('provas:aplicacao_notas', args=[self.aplicacao.pk]),
            reverse('provas:aplicacao_ata_pdf', args=[self.aplicacao.pk]),
        ]

    def test_usuario_sem_grupo_recebe_403(self):
        self.client.force_login(self.sem_grupo)
        for url in self._urls():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_anonimo_redirecionado_ao_login_inclusive_demo(self):
        for url in (reverse('provas:prova_list'), reverse('provas:folha_resposta_demo')):
            with self.subTest(url=url):
                resp = self.client.get(url)
                self.assertEqual(resp.status_code, 302)
                self.assertIn(reverse('login'), resp['Location'])

    def test_coordenador_acessa(self):
        self.client.force_login(self.coordenador)
        for url in (reverse('provas:prova_list'), reverse('provas:aplicacao_folhas', args=[self.aplicacao.pk])):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)


class AplicacaoTelasTest(TestCase):
    """Fluxo de aplicações fora do admin: listagem, cadastro, painel e distribuição de versões."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username='doc_aplic', password='segredo123')  # noqa: S106
        self.user.groups.add(Group.objects.get_or_create(name='Administrador')[0])
        self.client.force_login(self.user)

        self.prova = Prova.objects.create(titulo='Prova Aplicável')
        self.versoes = [
            ProvaVersao.objects.create(prova=self.prova, identificador=ident, numero_versao=i)
            for i, ident in enumerate(['A', 'B'], start=1)
        ]
        self.prova_sem_versao = Prova.objects.create(titulo='Prova Sem Versão')
        self.turma = Turma.objects.create(periodo='2026.2', nome='T Aplic')
        self.alunos = []
        for nome, matricula in (('Ana', 'A1'), ('Bruno', 'B1')):
            aluno = Aluno.objects.create(nome=nome, matricula=matricula)
            Matricula.objects.create(aluno=aluno, turma=self.turma)
            self.alunos.append(aluno)

    def _aplicacao(self):
        return ProvaAplicacao.objects.create(prova=self.prova, turma=self.turma, data_aplicacao=date(2026, 11, 10))

    def _dados(self, **extra):
        dados = {
            'prova': self.prova.pk,
            'turma': self.turma.pk,
            'data_aplicacao': '2026-11-20',
            'nota_corte': '6',
            'observacao': '',
        }
        dados.update(extra)
        return dados

    def test_lista_exibe_aplicacoes_e_ignora_filtro_invalido(self):
        aplicacao = self._aplicacao()
        resp = self.client.get(reverse('provas:aplicacao_list'), {'turma': 'abc'})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, reverse('provas:aplicacao_detail', args=[aplicacao.pk]))

    def test_form_pre_seleciona_prova_e_lista_so_provas_com_versoes(self):
        resp = self.client.get(reverse('provas:aplicacao_create'), {'prova': self.prova.pk})
        self.assertEqual(resp.status_code, 200)
        form = resp.context['form']
        self.assertEqual(str(form.initial['prova']), str(self.prova.pk))
        self.assertIn(self.prova, form.fields['prova'].queryset)
        self.assertNotIn(self.prova_sem_versao, form.fields['prova'].queryset)

    def test_criar_aplicacao_redireciona_para_o_painel(self):
        resp = self.client.post(reverse('provas:aplicacao_create'), self._dados())
        aplicacao = ProvaAplicacao.objects.get(prova=self.prova, turma=self.turma)
        self.assertRedirects(resp, reverse('provas:aplicacao_detail', args=[aplicacao.pk]))
        self.assertEqual(aplicacao.data_aplicacao, date(2026, 11, 20))

    def test_criar_rejeita_prova_sem_versoes_e_duplicada(self):
        resp = self.client.post(reverse('provas:aplicacao_create'), self._dados(prova=self.prova_sem_versao.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['form'].errors.get('prova'))

        self._aplicacao()
        resp = self.client.post(reverse('provas:aplicacao_create'), self._dados(data_aplicacao='2026-11-10'))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['form'].non_field_errors())
        self.assertEqual(ProvaAplicacao.objects.count(), 1)

    def test_painel_mostra_etapas_e_distribui_versoes(self):
        aplicacao = self._aplicacao()
        url_painel = reverse('provas:aplicacao_detail', args=[aplicacao.pk])
        resp = self.client.get(url_painel)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['matriculados'], 2)
        for rota in (
            'aplicacao_atribuir_versoes',
            'aplicacao_folhas_zip',
            'aplicacao_cadernos_zip',
            'aplicacao_upload_folhas',
            'aplicacao_notas',
        ):
            self.assertContains(resp, reverse(f'provas:{rota}', args=[aplicacao.pk]))

        resp = self.client.post(reverse('provas:aplicacao_atribuir_versoes', args=[aplicacao.pk]), follow=True)
        self.assertRedirects(resp, url_painel)
        self.assertEqual(aplicacao.atribuicoes.count(), 2)
        self.assertContains(resp, '2 aluno(s) receberam versão')
        # Resumo por versão (blocos na ordem alfabética: Ana → A, Bruno → B).
        self.assertContains(resp, '<strong>Versão A</strong> — 1 aluno(s): Ana')
        self.assertContains(resp, '<strong>Versão B</strong> — 1 aluno(s): Bruno')

    def test_redistribuicao_bloqueada_apos_folhas(self):
        aplicacao = self._aplicacao()
        atribuir_versoes(aplicacao)
        FolhaResposta.objects.create(prova_aplicacao=aplicacao, aluno=self.alunos[0], prova_versao=self.versoes[0])

        resp = self.client.get(reverse('provas:aplicacao_detail', args=[aplicacao.pk]))
        self.assertTrue(resp.context['redistribuicao_bloqueada'])
        self.assertNotContains(resp, reverse('provas:aplicacao_atribuir_versoes', args=[aplicacao.pk]))

        resp = self.client.post(reverse('provas:aplicacao_atribuir_versoes', args=[aplicacao.pk]), follow=True)
        self.assertContains(resp, 'Não foi possível distribuir as versões')

    def test_painel_avisa_quem_entrou_depois_dos_cartoes(self):
        aplicacao = self._aplicacao()
        atribuir_versoes(aplicacao)
        for aluno in self.alunos:
            FolhaResposta.objects.create(prova_aplicacao=aplicacao, aluno=aluno, prova_versao=self.versoes[0])
        url = reverse('provas:aplicacao_detail', args=[aplicacao.pk])
        self.assertEqual(self.client.get(url).context['alunos_sem_cartao'], 0)

        novo = Aluno.objects.create(nome='Caio', matricula='C1')
        Matricula.objects.create(aluno=novo, turma=self.turma)
        resp = self.client.get(url)
        self.assertEqual(resp.context['alunos_sem_cartao'], 1)
        self.assertContains(resp, 'entraram na turma depois')

    def test_admin_trava_prova_e_turma_de_aplicacao_com_cartoes(self):
        from django.contrib import admin as django_admin
        from django.test import RequestFactory

        model_admin = django_admin.site._registry[ProvaAplicacao]
        request = RequestFactory().get('/admin/')
        aplicacao = self._aplicacao()
        self.assertNotIn('prova', model_admin.get_readonly_fields(request, aplicacao))
        self.assertNotIn('prova', model_admin.get_readonly_fields(request, None))

        FolhaResposta.objects.create(prova_aplicacao=aplicacao, aluno=self.alunos[0], prova_versao=self.versoes[0])
        campos = model_admin.get_readonly_fields(request, aplicacao)
        self.assertIn('prova', campos)
        self.assertIn('turma', campos)

    def test_menu_e_revisao_da_prova_levam_as_aplicacoes(self):
        resp = self.client.get(reverse('provas:prova_revisao', args=[self.prova.pk]))
        self.assertContains(resp, f'{reverse("provas:aplicacao_create")}?prova={self.prova.pk}')
        self.assertContains(resp, f'href="{reverse("provas:aplicacao_list")}"')


class NotasAplicacaoViewTest(TestCase):
    """Página de notas da turma e exportação XLSX/CSV (com proteção contra injeção de fórmula)."""

    def setUp(self):
        from desempenho.models import Desempenho

        self.user = get_user_model().objects.create_user(username='doc_notas', password='segredo123')  # noqa: S106
        self.user.groups.add(Group.objects.get_or_create(name='Administrador')[0])
        self.client.force_login(self.user)

        disc = Disciplina.objects.create(nome='Disc Export')
        self.prova = Prova.objects.create(titulo='Prova Export', disciplina=disc)
        versao = ProvaVersao.objects.create(prova=self.prova, identificador='A', numero_versao=1)
        questao = Questao.objects.create(disciplina=disc, tipo='OBJ', dificuldade='F', enunciado='Q1')
        pq = ProvaQuestao.objects.create(
            prova=self.prova, questao=questao, numero=1, ordem=1, snapshot_questao={'tipo': 'OBJ'}
        )
        ProvaVersaoItem.objects.create(versao=versao, prova_questao=pq, numero=1, ordem=1)
        self.aplicacao = ProvaAplicacao.objects.create(
            prova=self.prova,
            turma=Turma.objects.create(periodo='2026.2', nome='T Export'),
            data_aplicacao=date(2026, 11, 10),
        )
        # Nome malicioso: começaria uma fórmula numa planilha.
        aluno = Aluno.objects.create(nome='=HYPERLINK("http://x")', matricula='M1')
        folha = FolhaResposta.objects.create(prova_aplicacao=self.aplicacao, aluno=aluno, prova_versao=versao)
        Desempenho.objects.create(folha_resposta=folha, questao=questao, acertou=True)
        self.url = reverse('provas:aplicacao_notas', args=[self.aplicacao.pk])

    def test_pagina_de_notas(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['linhas'][0]['nota'], Decimal('10.00'))
        self.assertContains(resp, 'Completa')

    def test_exporta_xlsx_com_texto_seguro(self):
        from openpyxl import load_workbook

        resp = self.client.get(self.url, {'formato': 'xlsx'})
        self.assertEqual(resp.status_code, 200)
        self.assertIn('spreadsheetml', resp['Content-Type'])
        self.assertIn('.xlsx', resp['Content-Disposition'])

        planilha = load_workbook(BytesIO(resp.content)).active
        self.assertEqual(planilha['A1'].value, 'Matrícula')
        self.assertEqual(planilha['J1'].value, 'Nota final (0–10)')
        self.assertEqual(planilha['A2'].value, 'M1')
        self.assertEqual(planilha['B2'].data_type, 's')  # texto, não fórmula
        self.assertEqual(planilha['B2'].value, '=HYPERLINK("http://x")')
        self.assertEqual(float(planilha['J2'].value), 10.0)

    def test_exporta_csv_pt_br_com_texto_seguro(self):
        resp = self.client.get(self.url, {'formato': 'csv'})
        self.assertEqual(resp.status_code, 200)
        conteudo = resp.content.decode('utf-8')
        self.assertTrue(conteudo.startswith('\N{BYTE ORDER MARK}'))
        linhas = conteudo.lstrip('\N{BYTE ORDER MARK}').splitlines()
        self.assertTrue(linhas[0].startswith('Matrícula;Aluno;'))
        self.assertIn(';"\'=HYPERLINK(""http://x"")";', linhas[1])  # prefixo ' neutraliza a fórmula
        self.assertIn(';10,00;', linhas[1])  # decimal com vírgula


class DiagnosticarOmrCommandTest(TestCase):
    """Comando de calibração do teste de campo: diagnostica QR, marcadores e bolhas sem gravar nada."""

    def setUp(self):
        disc = Disciplina.objects.create(nome='Disc Diag')
        prova = Prova.objects.create(titulo='Prova Diag', disciplina=disc)
        versao = ProvaVersao.objects.create(prova=prova, identificador='A', numero_versao=1)
        for ordem in range(1, 4):
            questao = Questao.objects.create(disciplina=disc, tipo='OBJ', dificuldade='F', enunciado=f'Q{ordem}')
            pq = ProvaQuestao.objects.create(
                prova=prova, questao=questao, numero=ordem, ordem=ordem, snapshot_questao={'tipo': 'OBJ'}
            )
            ProvaVersaoItem.objects.create(versao=versao, prova_questao=pq, numero=ordem, ordem=ordem)
        aplicacao = ProvaAplicacao.objects.create(
            prova=prova, turma=Turma.objects.create(periodo='2026.2', nome='T Diag'), data_aplicacao=date(2026, 11, 10)
        )
        self.folha = FolhaResposta.objects.create(
            prova_aplicacao=aplicacao,
            aluno=Aluno.objects.create(nome='Ana Diag', matricula='D1'),
            prova_versao=versao,
            qr_token=TOKEN_QR_CORRECAO,
        )
        self.pasta = Path(tempfile.mkdtemp())

    def _imagem(self, nome, pares, com_qr=True):
        payload = montar_payload_folha(self.folha.qr_token) if com_qr else None
        canvas, _ = _canvas_bolhas(pares, num_questoes=3, qr_payload=payload)
        caminho = self.pasta / nome
        cv2.imwrite(str(caminho), canvas)
        return str(caminho)

    def _executar(self, *args):
        from io import StringIO

        from django.core.management import call_command

        saida = StringIO()
        call_command('diagnosticar_omr', *args, stdout=saida)
        return saida.getvalue()

    def test_folha_identificada_pelo_qr_mostra_bolhas_sem_gravar(self):
        from desempenho.models import Desempenho

        saida = self._executar(self._imagem('folha.png', {(1, 'B'), (2, 'A'), (2, 'C')}))

        self.assertIn('QR: ok — Ana Diag (versão A)', saida)
        self.assertRegex(saida, r'Q01 .*→ ok \(B\)')
        self.assertRegex(saida, r'Q02 .*→ ambigua')
        self.assertRegex(saida, r'Q03 .*→ branco')
        self.assertIn('folha identificada pelo QR em 1', saida)
        self.assertFalse(Desempenho.objects.exists())
        self.assertFalse(FolhaDigitalizada.objects.exists())

    def test_sem_qr_usa_questoes_e_salva_imagem_endireitada(self):
        saida_dir = self.pasta / 'canonicas'
        saida = self._executar(
            self._imagem('sem_qr.png', {(3, 'E')}, com_qr=False),
            '--questoes',
            '3',
            '--salvar-canonica',
            str(saida_dir),
        )

        self.assertIn('QR: não lido', saida)
        self.assertRegex(saida, r'Q03 .*→ ok \(E\)')
        self.assertTrue((saida_dir / 'sem_qr_canonica.png').is_file())

    def test_imagem_ilegivel_nao_interrompe(self):
        invalida = self.pasta / 'nao_e_imagem.jpg'
        invalida.write_text('texto', encoding='utf-8')
        saida = self._executar(str(invalida), self._imagem('ok.png', {(1, 'A')}))
        self.assertIn('Imagem ilegível', saida)
        self.assertIn('2 imagem(ns)', saida)


class QuantidadeCadernosTest(TestCase):
    """Quantidade de cadernos (versões) por máscara: padrão 3; Simulado ENADE com caderno único."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username='prof_cadernos', password='segredo123')  # noqa: S106
        self.user.groups.add(Group.objects.get_or_create(name='Administrador')[0])
        self.client.force_login(self.user)
        self.disc = Disciplina.objects.create(nome='Disc Cadernos')
        self.topicos = [Topico.objects.create(disciplina=self.disc, nome=f'Tópico {i}') for i in (1, 2)]
        self.questoes = []
        for topico in self.topicos:
            questao = Questao.objects.create(
                disciplina=self.disc, topico=topico, tipo='OBJ', dificuldade='F', enunciado=f'Q {topico.nome}'
            )
            for letra in 'ABCDE':
                Alternativa.objects.create(questao=questao, letra=letra, texto=letra, correta=(letra == 'A'))
            self.questoes.append(questao)

    def _mascara(self, **extra):
        dados = {
            'titulo': 'AV Cadernos',
            'disciplina': self.disc,
            'quantidade_questoes': 2,
            'modalidade': Mascara.Modalidade.OBJETIVA,
            'percentual_facil': 100,
            'percentual_medio': 0,
            'percentual_dificil': 0,
            'autor': self.user,
        }
        dados.update(extra)
        mascara = Mascara.objects.create(**dados)
        for topico in self.topicos:
            MascaraCriterioTopico.objects.create(mascara=mascara, topico=topico, percentual=50)
        return mascara

    def test_preset_e_simulado_enade_um_clique_usam_caderno_unico(self):
        self.assertEqual(preset_enade_initial()['quantidade_versoes'], 1)
        self.client.post(reverse('provas:mascara_enade_create'))
        self.assertEqual(Mascara.objects.get(titulo__startswith='Simulado ENADE').quantidade_versoes, 1)

    def test_mascara_padrao_continua_com_tres_cadernos(self):
        prova = self._mascara().gerar_prova(autor=self.user)
        self.assertEqual(list(prova.versoes.values_list('identificador', flat=True)), ['A', 'B', 'C'])

    def test_mascara_de_caderno_unico_gera_so_a_versao_a(self):
        prova = self._mascara(quantidade_versoes=1).gerar_prova(autor=self.user)
        self.assertEqual(list(prova.versoes.values_list('identificador', flat=True)), ['A'])
        self.assertEqual(prova.snapshot_mascara['quantidade_versoes'], 1)

    def test_montagem_manual_segue_a_mascara_guia(self):
        from provas.services.manual_generator import criar_prova_manual

        ids = [q.pk for q in self.questoes]
        com_guia = criar_prova_manual(
            titulo='Manual guia', questoes_ids=ids, mascara=self._mascara(quantidade_versoes=1)
        )
        sem_guia = criar_prova_manual(titulo='Manual livre', disciplina=self.disc, questoes_ids=ids)
        self.assertEqual(com_guia.versoes.count(), 1)
        self.assertEqual(sem_guia.versoes.count(), 3)

    def test_revisao_e_regeracao_respeitam_caderno_unico(self):
        prova = self._mascara(quantidade_versoes=1).gerar_prova(autor=self.user)
        url_revisao = reverse('provas:prova_revisao', args=[prova.pk])

        resp = self.client.get(url_revisao)
        self.assertContains(resp, 'name="quantidade_versoes" value="1"')
        self.assertContains(resp, 'caderno único')

        resp = self.client.post(reverse('provas:prova_gerar_versoes', args=[prova.pk]), follow=True)
        self.assertRedirects(resp, url_revisao)
        self.assertContains(resp, 'Caderno único gerado com sucesso')
        self.assertEqual(prova.versoes.count(), 1)

    def test_prioridade_snapshot_mascara_padrao(self):
        from provas.services.version_generator import quantidade_versoes_da_prova

        mascara = self._mascara(quantidade_versoes=4)
        self.assertEqual(quantidade_versoes_da_prova(Prova(titulo='Livre')), 3)
        self.assertEqual(quantidade_versoes_da_prova(Prova(titulo='Antiga', mascara=mascara)), 4)
        congelada = Prova(titulo='Nova', mascara=mascara, snapshot_mascara={'quantidade_versoes': 2})
        self.assertEqual(quantidade_versoes_da_prova(congelada), 2)
        invalida = Prova(titulo='Inválida', mascara=mascara, snapshot_mascara={'quantidade_versoes': 'x'})
        self.assertEqual(quantidade_versoes_da_prova(invalida), 4)

    def test_formulario_valida_intervalo_e_aceita_omissao(self):
        base = {
            'titulo': 'Form Cadernos',
            'disciplina': self.disc.pk,
            'quantidade_questoes': 10,
            'modalidade': 'OBJ',
            'quantidade_objetivas': 10,
            'quantidade_discursivas': 0,
            'percentual_facil': 30,
            'percentual_medio': 50,
            'percentual_dificil': 20,
            'valor_total': '10.00',
            'tempo_estimado_minutos': 60,
        }
        for invalido in (0, 9):
            with self.subTest(quantidade_versoes=invalido):
                self.assertIn('quantidade_versoes', MascaraForm(data={**base, 'quantidade_versoes': invalido}).errors)
        form = MascaraForm(data=base)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['quantidade_versoes'], 3)

    def test_simulado_enade_e_sempre_caderno_unico(self):
        from provas.services.version_generator import gerar_versoes_prova, quantidade_versoes_da_prova

        # Máscara ENADE "editada" para 3 cadernos (direto no banco, sem passar pelo formulário).
        mascara = self._mascara(titulo='Simulado ENADE — teste', priorizar_enade=True, quantidade_versoes=3)
        prova = mascara.gerar_prova(autor=self.user)
        self.assertEqual(list(prova.versoes.values_list('identificador', flat=True)), ['A'])

        prova.snapshot_mascara = {**prova.snapshot_mascara, 'quantidade_versoes': 3}
        self.assertEqual(quantidade_versoes_da_prova(prova), 1)
        for kwargs in ({'quantidade_versoes': 3}, {'identificadores': ['A', 'B', 'C']}):
            with self.subTest(**kwargs):
                self.assertEqual([v.identificador for v in gerar_versoes_prova(prova, **kwargs)], ['A'])

        # Nem um POST pedindo 3 versões gera mais de uma.
        self.client.post(reverse('provas:prova_gerar_versoes', args=[prova.pk]), {'quantidade_versoes': '3'})
        self.assertEqual(list(prova.versoes.values_list('identificador', flat=True)), ['A'])

        # Fora do ENADE, a quantidade da máscara continua valendo.
        comum = self._mascara(titulo='AV1 ENADE-like sem prioridade', quantidade_versoes=3).gerar_prova(autor=self.user)
        self.assertEqual(comum.versoes.count(), 3)

    def test_formulario_recusa_mais_de_um_caderno_no_simulado_enade(self):
        base = {
            'titulo': 'Simulado ENADE — Form',
            'priorizar_enade': 'on',
            'disciplina': self.disc.pk,
            'quantidade_questoes': 10,
            'modalidade': 'OBJ',
            'quantidade_objetivas': 10,
            'quantidade_discursivas': 0,
            'percentual_facil': 30,
            'percentual_medio': 50,
            'percentual_dificil': 20,
            'valor_total': '10.00',
            'tempo_estimado_minutos': 60,
        }
        form = MascaraForm(data={**base, 'quantidade_versoes': 3})
        self.assertIn('caderno único', form.errors['quantidade_versoes'][0])

        form = MascaraForm(data=base)  # campo omitido: assume 1, sem erro
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['quantidade_versoes'], 1)

        form = MascaraForm(data={**base, 'priorizar_enade': '', 'quantidade_versoes': 3})  # sem prioridade ENADE
        self.assertTrue(form.is_valid(), form.errors)

    def test_migracao_ajusta_so_mascaras_enade_existentes(self):
        migracao = import_module('provas.migrations.0013_mascara_quantidade_versoes')
        enade = self._mascara(titulo='Simulado ENADE — Componente Específico (ADS)')
        comum = self._mascara(titulo='AV1 Oficial')

        migracao.enade_caderno_unico(apps, None)

        enade.refresh_from_db()
        comum.refresh_from_db()
        self.assertEqual(enade.quantidade_versoes, 1)
        self.assertEqual(comum.quantidade_versoes, 3)


class CartaoRespostaCadernoTest(TestCase):
    """Cartão-resposta × caderno: numeração coerente, aviso das discursivas e capa sem QR falso."""

    def setUp(self):
        from provas.services.snapshot_builder import construir_snapshot_questao

        self.disc = Disciplina.objects.create(nome='Disc Cartão')
        self.prova = Prova.objects.create(titulo='Prova Cartão', disciplina=self.disc)
        # Ordem base propositalmente misturada: OBJ, DIS, OBJ, DIS, OBJ.
        self.questoes = []
        for ordem, tipo in enumerate(['OBJ', 'DIS', 'OBJ', 'DIS', 'OBJ'], start=1):
            questao = Questao.objects.create(
                disciplina=self.disc, tipo=tipo, dificuldade='F', enunciado=f'{tipo}{ordem}'
            )
            if tipo == 'OBJ':
                for letra in 'ABCDE':
                    Alternativa.objects.create(questao=questao, letra=letra, texto=letra, correta=(letra == 'A'))
            ProvaQuestao.objects.create(
                prova=self.prova,
                questao=questao,
                numero=ordem,
                ordem=ordem,
                snapshot_questao=construir_snapshot_questao(questao),
            )
            self.questoes.append(questao)

    def _enunciados(self, versao):
        return [
            item.prova_questao.questao.enunciado
            for item in versao.itens.select_related('prova_questao__questao').order_by('ordem')
        ]

    def test_cadernos_em_blocos_discursivas_primeiro_sem_misturar_tipos(self):
        from provas.services.version_generator import gerar_versoes_prova

        versoes = gerar_versoes_prova(self.prova, quantidade_versoes=3)

        # Versão A: sem embaralhar — discursivas no início, cada tipo na ordem original.
        self.assertEqual(self._enunciados(versoes[0]), ['DIS2', 'DIS4', 'OBJ1', 'OBJ3', 'OBJ5'])
        for versao in versoes:
            with self.subTest(versao=versao.identificador):
                enunciados = self._enunciados(versao)
                self.assertEqual(sorted(enunciados[:2]), ['DIS2', 'DIS4'])
                self.assertTrue(all(e.startswith('OBJ') for e in enunciados[2:]))
                # Número dentro do tipo: discursivas 1..2, objetivas 1..3 (= bolhas do cartão).
                self.assertEqual(list(versao.itens.order_by('ordem').values_list('numero', flat=True)), [1, 2, 1, 2, 3])
                self.assertEqual(list(versao.itens.order_by('ordem').values_list('ordem', flat=True)), [1, 2, 3, 4, 5])

    def test_objetiva_longa_vem_antes_das_curtas(self):
        from provas.services.version_generator import gerar_versoes_prova

        item = self.prova.itens.get(questao=self.questoes[4])  # OBJ5 passa a ter texto de apoio
        item.snapshot_questao = {**item.snapshot_questao, 'texto_auxiliar': 'Texto de apoio'}
        item.save()

        versoes = gerar_versoes_prova(self.prova, quantidade_versoes=3)
        self.assertEqual(self._enunciados(versoes[0]), ['DIS2', 'DIS4', 'OBJ5', 'OBJ1', 'OBJ3'])
        for versao in versoes:
            with self.subTest(versao=versao.identificador):
                self.assertEqual(self._enunciados(versao)[2], 'OBJ5')

    def test_montagem_manual_poe_discursivas_primeiro_mantendo_a_ordem_do_docente(self):
        from provas.services.manual_generator import criar_prova_manual

        ids = [q.pk for q in reversed(self.questoes)]  # OBJ5, DIS4, OBJ3, DIS2, OBJ1
        prova = criar_prova_manual(titulo='Manual cartão', disciplina=self.disc, questoes_ids=ids)
        itens = list(prova.itens.order_by('ordem'))
        self.assertEqual([item.questao.enunciado for item in itens], ['DIS4', 'DIS2', 'OBJ5', 'OBJ3', 'OBJ1'])
        self.assertEqual([item.numero for item in itens], [1, 2, 1, 2, 3])

    def test_troca_entre_tipos_bloqueada(self):
        from provas.services.question_replacer import SubstituicaoQuestaoError, substituir_questao_prova

        item_objetivo = self.prova.itens.get(ordem=1)
        discursiva_nova = Questao.objects.create(disciplina=self.disc, tipo='DIS', dificuldade='F', enunciado='Nova')
        with self.assertRaisesMessage(SubstituicaoQuestaoError, 'mesmo tipo'):
            substituir_questao_prova(item_objetivo, discursiva_nova)

    def test_cartao_avisa_quais_discursivas_ficam_no_caderno(self):
        from django.template.loader import render_to_string

        from provas.services.folha_service import texto_discursivas_no_caderno
        from provas.services.version_generator import gerar_versoes_prova

        versao = gerar_versoes_prova(self.prova, quantidade_versoes=1)[0]
        aviso = texto_discursivas_no_caderno(versao)
        self.assertIn('As 2 questões discursivas são respondidas no caderno', aviso)

        geo = build_layout(num_questoes=3, num_alternativas=5, id_digits=8, columns=3)
        html = render_to_string('provas/pdf_folha_resposta.html', {'geo': geo, 'aviso_discursivas': aviso})
        self.assertIn('As 2 questões discursivas são respondidas no caderno', html)

        so_objetivas = Prova.objects.create(titulo='Só objetivas', disciplina=self.disc)
        pq = ProvaQuestao.objects.create(prova=so_objetivas, questao=self.questoes[0], numero=1, ordem=1)
        versao_obj = ProvaVersao.objects.create(prova=so_objetivas, identificador='A', numero_versao=1)
        ProvaVersaoItem.objects.create(versao=versao_obj, prova_questao=pq, numero=1, ordem=1)
        self.assertEqual(texto_discursivas_no_caderno(versao_obj), '')

    def test_capa_do_caderno_orienta_o_cartao_sem_qr_falso(self):
        from django.template.loader import render_to_string

        contexto = {'prova': self.prova, 'incluir_capa': True}  # caderno do estudante sai sempre com capa
        for template in ('provas/pdf_capa_prova.html', 'provas/pdf_prova_completa.html'):
            with self.subTest(template=template):
                html = render_to_string(template, contexto)
                self.assertNotIn('Gabarito Oficial — Módulo 3', html)
                self.assertIn('Cartão-Resposta', html)
                self.assertIn('Somente o cartão é corrigido', html)
                self.assertIn('o caderno e o cartão-resposta', html)


class ModelosAntigosRemovidosTest(TestCase):
    """Restos de desenhos antigos não voltam: PDF de teste, "número do caderno", termos e demo do cartão."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username='doc_legado', password='segredo123')  # noqa: S106
        self.user.groups.add(Group.objects.get(name='Docente'))
        self.client.force_login(self.user)

    def test_sem_pdf_de_teste_nem_botao_modelo(self):
        from django.urls import NoReverseMatch

        with self.assertRaises(NoReverseMatch):
            reverse('provas:pdf_teste')
        self.assertNotContains(self.client.get(reverse('provas:mascara_list')), 'Modelo PDF')

    def test_pdfs_identificam_o_caderno_pela_versao(self):
        from django.template.loader import render_to_string

        prova = SimpleNamespace(titulo='Prova Legado', numero_caderno=1, pk=9)
        contexto = {
            'prova': prova,
            'incluir_capa': True,
            'identificador_display': 'Versão B',
            'itens_detalhados': [],
            'resumo_itens': [],
        }
        for template in (
            'provas/pdf_prova_completa.html',
            'provas/pdf_capa_prova.html',
            'provas/pdf_gabarito_versao.html',
        ):
            with self.subTest(template=template):
                html = render_to_string(template, contexto)
                self.assertIn('Versão B'.upper() if 'gabarito' in template else 'Versão B', html)
                self.assertNotIn('Caderno 1', html)
                self.assertNotIn('CADERNO 01', html)

    def test_montagem_manual_nao_pede_numero_do_caderno_e_titulos_sem_caderno(self):
        from provas.forms import ProvaManualForm

        self.assertNotIn('numero_caderno', ProvaManualForm().fields)
        self.assertEqual(str(Prova(titulo='AV1 Oficial')), 'AV1 Oficial')

    def test_cartao_usa_o_termo_cartao_resposta_e_omite_disciplina_vazia(self):
        from django.template.loader import render_to_string

        geo = build_layout(num_questoes=5, id_digits=0)
        html = render_to_string('provas/pdf_folha_resposta.html', {'geo': geo, 'versao_ident': 'A'})
        self.assertIn('CARTÃO-RESPOSTA', html)
        self.assertNotIn('FOLHA DE RESPOSTA', html)
        self.assertNotIn('— &nbsp;•', html)
        self.assertIn('Versão A', html)

    def test_demo_do_cartao_segue_o_formato_real(self):
        resp = self.client.get(reverse('provas:folha_resposta_demo'), {'q': 10, 'disc': 2})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'application/pdf')
        self.assertIn('cartao_resposta_demo.pdf', resp['Content-Disposition'])


class CadernoPadraoLayoutTest(SimpleTestCase):
    """Caderno em blocos (pdf_prova_completa + caderno_layout): numeração, blocos e layout no PDF."""

    @staticmethod
    def _questao(tipo, texto_auxiliar='', topico=None, origem='', imagem=None):
        return SimpleNamespace(
            tipo=tipo,
            topico=SimpleNamespace(nome=topico) if topico else None,
            origem=origem,
            numero_questao=None,
            texto_auxiliar=texto_auxiliar,
            imagem=imagem,
            enunciado=f'Enunciado {tipo}',
            get_tipo_display=lambda: 'Objetiva' if tipo == 'OBJ' else 'Discursiva',
            alternativas=SimpleNamespace(all=list),  # fallback do gabarito: item.alternativas_versao|default:...
        )

    @classmethod
    def _item(cls, tipo, texto_auxiliar='', **questao):
        alternativas = [{'letra': 'A', 'texto': 'Texto da alternativa A'}] if tipo == 'OBJ' else []
        return {
            'questao': cls._questao(tipo, texto_auxiliar, **questao),
            'snapshot_questao': {},
            'alternativas_versao': alternativas,
            'pontuacao': None,
        }

    def _html(self, itens, **extra):
        from django.template.loader import render_to_string

        from .services.caderno_layout import organizar_caderno

        itens_render, blocos = organizar_caderno(itens)
        return render_to_string(
            'provas/pdf_prova_completa.html',
            {'prova': SimpleNamespace(titulo='T', pk=1), 'itens_render': itens_render, 'blocos': blocos, **extra},
        )

    @staticmethod
    def _caixas(html):
        import weasyprint
        from django.conf import settings

        from .services.pdf_generator import build_django_url_fetcher

        doc = weasyprint.HTML(
            string=html, base_url=str(settings.BASE_DIR), url_fetcher=build_django_url_fetcher()
        ).render()
        caixas = {}
        pilha = [page._page_box for page in doc.pages]
        while pilha:
            caixa = pilha.pop()
            elemento = getattr(caixa, 'element', None)
            if elemento is not None and type(caixa).__name__ != 'TextBox':
                for classe in (elemento.get('class') or '').split():
                    caixas.setdefault(classe, []).append(caixa)
            pilha.extend(getattr(caixa, 'children', []) or [])
        return caixas

    def test_discursivas_primeiro_com_numeracao_propria_e_objetivas_como_no_cartao(self):
        import re

        # Versão antiga, com a discursiva no meio: as objetivas seguem 1, 2 (as bolhas do cartão).
        html = self._html([self._item('OBJ'), self._item('DIS'), self._item('OBJ')], incluir_gabarito=True)
        self.assertEqual(
            re.findall(r'questao-numero-badge">([^<]*)<', html), ['Discursiva 01', 'Questão 01', 'Questão 02']
        )
        self.assertEqual(re.findall(r'>\s*([QD]\d+)\s*</td>', html), ['D01', 'Q01', 'Q02'])
        self.assertLess(html.index('Questões discursivas'), html.index('Questões objetivas'))

    def test_objetivas_curtas_em_colunas_e_longas_em_largura_total(self):
        from .services.caderno_layout import organizar_caderno

        itens = [self._item('OBJ', texto_auxiliar='Texto de apoio'), self._item('OBJ'), self._item('OBJ')]
        _, blocos = organizar_caderno(itens)
        self.assertEqual([(b['formato'], len(b['itens'])) for b in blocos], [('largura_total', 1), ('colunas', 2)])
        self.assertEqual([b['secao'] for b in blocos], ['objetivas', ''])
        self.assertEqual(self._html(itens).count('questoes-wrapper questoes-colunas'), 1)

    def test_linhas_da_discursiva_seis_por_padrao_e_quinze_quando_pedido(self):
        self.assertEqual(self._html([self._item('DIS')]).count('class="linha-pautada-item"'), 6)
        self.assertEqual(self._html([self._item('DIS')], linhas_discursiva=15).count('class="linha-pautada-item"'), 15)

    def test_cabecalho_so_com_numero_e_pontuacao_sem_icone_e_sem_legenda(self):
        import re

        meta = {'topico': 'Tema da questão', 'origem': 'ENADE ADS (simulado)'}
        itens = [
            self._item('DIS', **meta),
            self._item('OBJ', texto_auxiliar='Contexto da questão', **meta),  # longa, com texto de apoio
            self._item('OBJ', imagem=SimpleNamespace(url='/media/figura.png'), **meta),  # longa, com imagem
            self._item('OBJ', **meta),  # curta
        ]
        itens[3]['pontuacao'] = Decimal('0.33')
        html = self._html(itens)
        blocos = re.split(r'<div class="questao-bloco">', html)[1:]

        self.assertEqual(len(blocos), 4)
        for bloco in blocos:  # todo tipo de questão: sem tema, origem e tipo
            for ausente in ('Tema da questão', 'ENADE ADS (simulado)', 'questao-tipo-badge', 'questao-header-dir'):
                self.assertNotIn(ausente, bloco)
        # Pontuação colada ao número, entre parênteses (decimal pt-BR).
        self.assertIn('questao-numero-badge">Questão 03 <span class="questao-peso-badge">(0,33 pts)</span>', blocos[3])
        self.assertIn('Texto de apoio:', blocos[1])
        self.assertNotIn('&#128196;', html)
        self.assertIn('src="/media/figura.png"', blocos[2])
        self.assertNotIn('Figura de apoio —', re.sub(r'alt="[^"]*"', '', blocos[2]))  # só no alt, sem legenda

    def test_versao_no_rodape_de_todas_as_paginas(self):
        from django.template.loader import render_to_string

        from .services.caderno_layout import organizar_caderno

        itens_render, blocos = organizar_caderno([self._item('OBJ') for _ in range(60)])  # várias páginas
        contexto = {
            'prova': SimpleNamespace(titulo='AV1 Redes', pk=1, numero_caderno=1),
            'identificador_display': 'Versão B',
            'incluir_capa': True,
            'itens_render': itens_render,
            'blocos': blocos,
            'itens_objetivas': itens_render,
            'blocos_objetivas': blocos,
        }
        for template in ('provas/pdf_prova_completa.html', 'provas/pdf_prova_enade.html'):
            with self.subTest(template=template):
                import weasyprint
                from django.conf import settings

                from .services.pdf_generator import build_django_url_fetcher

                doc = weasyprint.HTML(
                    string=render_to_string(template, contexto),
                    base_url=str(settings.BASE_DIR),
                    url_fetcher=build_django_url_fetcher(),
                ).render()
                self.assertGreaterEqual(len(doc.pages), 2)
                for numero, pagina in enumerate(doc.pages, start=1):
                    rodape = ''
                    for caixa in pagina._page_box.children:
                        if getattr(caixa, 'at_keyword', '') == '@bottom-left':
                            pilha = [caixa]
                            while pilha:
                                atual = pilha.pop()
                                rodape += getattr(atual, 'text', '') or ''
                                pilha.extend(getattr(atual, 'children', []) or [])
                    self.assertIn('Versão B', rodape, f'página {numero} sem a versão no rodapé')

    def test_templates_de_pdf_sem_emoji_nem_simbolos_fora_da_fonte_do_servidor(self):
        import re
        from pathlib import Path

        # A imagem Docker só tem as fontes Liberation: emoji e dingbats viram "quadradinho" no PDF.
        def fora_da_fonte(codigo):
            return 0x2700 <= codigo <= 0x27BF or codigo >= 0x1F000

        pasta = Path(__file__).resolve().parent / 'templates' / 'provas'
        for arquivo in sorted(pasta.glob('pdf_*.html')):
            with self.subTest(template=arquivo.name):
                fonte = arquivo.read_text(encoding='utf-8')
                caracteres = [c for c in fonte if fora_da_fonte(ord(c))]
                entidades = [e for e in re.findall(r'&#(\d+);', fonte) if fora_da_fonte(int(e))]
                self.assertEqual((caracteres, entidades), ([], []))

    def test_pdf_alternativas_na_linha_da_letra_discursiva_com_linhas_e_curtas_em_duas_colunas(self):
        caixas = self._caixas(self._html([self._item('DIS')] + [self._item('OBJ') for _ in range(4)]))

        self.assertEqual(caixas['alt-letra-col'][0].position_y, caixas['alt-texto-col'][0].position_y)
        self.assertGreater(caixas['linha-pautada-traco'][0].style['border_bottom_width'], 0)
        colunas = {round(bloco.position_x) for bloco in caixas['questao-bloco'] if bloco.element_tag == 'div'}
        self.assertEqual(len(colunas), 2)  # objetivas curtas lado a lado


class PdfsAvulsosEnadeTest(TestCase):
    """Pacote ZIP e "Apenas capa" usam o layout ENADE em Simulado ENADE."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username='doc_avulsos', password='segredo123')  # noqa: S106
        self.user.groups.add(Group.objects.get_or_create(name='Administrador')[0])
        self.client.force_login(self.user)
        self.disc = Disciplina.objects.create(nome='Disc Avulsos')

    def _prova(self, titulo, priorizar_enade):
        mascara = Mascara.objects.create(
            titulo=titulo,
            disciplina=self.disc,
            quantidade_questoes=1,
            percentual_facil=100,
            percentual_medio=0,
            percentual_dificil=0,
            priorizar_enade=priorizar_enade,
            autor=self.user,
        )
        prova = Prova.objects.create(titulo=titulo, mascara=mascara, disciplina=self.disc)
        questao = Questao.objects.create(disciplina=self.disc, tipo='DIS', dificuldade='F', enunciado='Disserte.')
        pq = ProvaQuestao.objects.create(
            prova=prova, questao=questao, numero=1, ordem=1, snapshot_questao={'tipo': 'DIS'}
        )
        versao = ProvaVersao.objects.create(prova=prova, identificador='A', numero_versao=1)
        ProvaVersaoItem.objects.create(versao=versao, prova_questao=pq, numero=1, ordem=1)
        return prova

    def _renderizados(self, rota, prova):
        from unittest import mock

        with mock.patch.object(PDFGeneratorService, 'render_pdf_from_template', return_value=b'%PDF-teste') as render:
            resposta = self.client.get(reverse(rota, kwargs={'pk': prova.pk}))
        self.assertEqual(resposta.status_code, 200)
        return [(chamada.kwargs['template_name'], chamada.kwargs['context']) for chamada in render.call_args_list]

    def test_pacote_zip_usa_o_caderno_enade_no_simulado(self):
        enade = self._renderizados('provas:prova_pacote_zip', self._prova('Simulado ENADE — teste', True))
        self.assertEqual(
            [template for template, _ in enade], ['provas/pdf_prova_enade.html', 'provas/pdf_gabarito_versao.html']
        )
        self.assertEqual(enade[0][1]['linhas_discursiva'], 15)

        comum = self._renderizados('provas:prova_pacote_zip', self._prova('AV1 Comum', False))
        self.assertEqual(
            [template for template, _ in comum], ['provas/pdf_prova_completa.html', 'provas/pdf_gabarito_versao.html']
        )

    def test_capa_avulsa_do_simulado_e_a_capa_enade(self):
        [(template, contexto)] = self._renderizados(
            'provas:prova_capa_pdf', self._prova('Simulado ENADE — teste', True)
        )
        self.assertEqual(template, 'provas/pdf_prova_enade.html')
        self.assertTrue(contexto['somente_capa'])

        [(template, _)] = self._renderizados('provas:prova_capa_pdf', self._prova('AV1 Comum', False))
        self.assertEqual(template, 'provas/pdf_capa_prova.html')

    def test_template_enade_somente_capa_omite_as_questoes(self):
        from django.template.loader import render_to_string

        from .services.caderno_service import contexto_caderno

        prova = self._prova('Simulado ENADE — teste', True)
        template, contexto = contexto_caderno(prova, prova.versoes.first(), incluir_gabarito=True)
        completo = render_to_string(template, contexto)
        so_capa = render_to_string(template, {**contexto, 'somente_capa': True})

        self.assertIn('Disserte.', completo)
        self.assertIn('Identificação do(a) Estudante', so_capa)
        self.assertNotIn('Disserte.', so_capa)
        self.assertNotIn('enade-gabarito', so_capa)

    def test_seed_enade_limpar_mantem_as_questoes_usadas_em_provas(self):
        from io import StringIO
        from unittest import mock

        from django.core.management import call_command

        from .management.commands.seed_enade import ORIGEM

        usada = Questao.objects.create(
            disciplina=self.disc, tipo='DIS', dificuldade='F', enunciado='Usada', origem=ORIGEM
        )
        livre = Questao.objects.create(
            disciplina=self.disc, tipo='DIS', dificuldade='F', enunciado='Livre', origem=ORIGEM
        )
        prova = Prova.objects.create(titulo='Prova Seed', disciplina=self.disc)
        ProvaQuestao.objects.create(prova=prova, questao=usada, numero=1, ordem=1, snapshot_questao={'tipo': 'DIS'})

        saida = StringIO()
        with mock.patch('provas.management.commands.seed_enade.ENADE_AREAS', {}):  # sem reabastecer
            call_command('seed_enade', limpar=True, stdout=saida)

        self.assertTrue(Questao.objects.filter(pk=usada.pk).exists())
        self.assertFalse(Questao.objects.filter(pk=livre.pk).exists())
        self.assertIn('Removidas 1 questões', saida.getvalue())
        self.assertIn('Mantidas 1 questões', saida.getvalue())

    def test_capas_mantem_valor_e_tempo_numa_linha_e_selo_dourado_existe(self):
        from django.template.loader import render_to_string

        from .services.caderno_service import contexto_caderno

        prova = self._prova('Simulado ENADE — teste', True)
        for layout in ('', 'padrao'):
            template, contexto = contexto_caderno(prova, prova.versoes.first(), layout=layout)
            html = render_to_string(template, {**contexto, 'valor_total': '10,00', 'tempo_minutos': 180})
            self.assertIn('<span class="sem-quebra">10,00 ponto(s)</span>', html)
        for css in ('provas/css/print_prova.css', 'provas/css/print_enade.css'):
            self.assertIn('.sem-quebra', Path(finders.find(css)).read_text(encoding='utf-8'))
        self.assertIn(
            '.badge-pill-gold', Path(finders.find('banco_questoes/css/custom.css')).read_text(encoding='utf-8')
        )


class LinhasDiscursivaViewTest(TestCase):
    """A view do caderno manda 15 linhas para prova ENADE mesmo no layout padrão; 6 para as demais."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username='doc_linhas', password='segredo123')  # noqa: S106
        self.user.groups.add(Group.objects.get_or_create(name='Administrador')[0])
        self.client.force_login(self.user)
        self.disc = Disciplina.objects.create(nome='Disc Linhas')

    def _prova(self, titulo_mascara, priorizar_enade):
        mascara = Mascara.objects.create(
            titulo=titulo_mascara,
            disciplina=self.disc,
            quantidade_questoes=1,
            percentual_facil=100,
            percentual_medio=0,
            percentual_dificil=0,
            priorizar_enade=priorizar_enade,
            autor=self.user,
        )
        prova = Prova.objects.create(titulo=titulo_mascara, mascara=mascara, disciplina=self.disc)
        questao = Questao.objects.create(disciplina=self.disc, tipo='DIS', dificuldade='F', enunciado='Disserte.')
        pq = ProvaQuestao.objects.create(
            prova=prova, questao=questao, numero=1, ordem=1, snapshot_questao={'tipo': 'DIS'}
        )
        versao = ProvaVersao.objects.create(prova=prova, identificador='A', numero_versao=1)
        ProvaVersaoItem.objects.create(versao=versao, prova_questao=pq, numero=1, ordem=1)
        return prova

    def _contexto(self, prova, **params):
        from unittest import mock

        with mock.patch.object(PDFGeneratorService, 'render_pdf_from_template', return_value=b'%PDF-teste') as render:
            resposta = self.client.get(reverse('provas:prova_pdf', kwargs={'pk': prova.pk}), params)
        self.assertEqual(resposta.status_code, 200)
        return render.call_args.kwargs['template_name'], render.call_args.kwargs['context']

    def test_prova_enade_tem_quinze_linhas_em_qualquer_layout(self):
        prova = self._prova('Simulado ENADE — teste', priorizar_enade=True)
        template, contexto = self._contexto(prova)
        self.assertEqual(template, 'provas/pdf_prova_enade.html')
        template, contexto = self._contexto(prova, layout='padrao')
        self.assertEqual(template, 'provas/pdf_prova_completa.html')
        self.assertEqual(contexto['linhas_discursiva'], 15)

    def test_prova_comum_tem_seis_linhas(self):
        template, contexto = self._contexto(self._prova('AV1 Comum', priorizar_enade=False))
        self.assertEqual(template, 'provas/pdf_prova_completa.html')
        self.assertEqual(contexto['linhas_discursiva'], 6)


class CadernoLayoutRegrasTest(SimpleTestCase):
    """Regras de services.caderno_layout: critério de questão longa e numeração por tipo."""

    def test_criterio_de_objetiva_longa(self):
        from .services.caderno_layout import CARACTERES_POR_LINHA, LIMITE_LINHAS_CURTA, eh_longa

        alternativas = ['curta'] * 5
        linha = 'x' * CARACTERES_POR_LINHA
        no_limite = '\n'.join([linha] * (LIMITE_LINHAS_CURTA - len(alternativas)))
        self.assertFalse(eh_longa('Enunciado curto', alternativas))
        self.assertFalse(eh_longa(no_limite, alternativas))
        self.assertTrue(eh_longa(no_limite + '\nmais uma linha', alternativas))
        self.assertTrue(eh_longa(linha * 30, alternativas))  # um parágrafo só, mas extenso
        self.assertTrue(eh_longa('Curto', alternativas, texto_auxiliar='Texto de apoio'))
        self.assertTrue(eh_longa('Curto', alternativas, tem_imagem=True))

    def test_quinze_linhas_so_em_prova_enade(self):
        from .services.caderno_layout import linhas_da_discursiva

        def prova(titulo, priorizar_enade):
            return SimpleNamespace(mascara=SimpleNamespace(titulo=titulo, priorizar_enade=priorizar_enade))

        self.assertEqual(linhas_da_discursiva(prova('Simulado ENADE — ADS', True)), 15)
        self.assertEqual(linhas_da_discursiva(prova('Simulado ENADE — ADS', False)), 6)
        self.assertEqual(linhas_da_discursiva(prova('AV1 Redes', True)), 6)
        self.assertEqual(linhas_da_discursiva(SimpleNamespace(mascara=None)), 6)  # montagem manual sem máscara

    def test_numeracao_por_tipo_e_ordem_do_gabarito(self):
        from .services.caderno_layout import numeros_por_tipo, ordenar_como_caderno

        self.assertEqual(numeros_por_tipo(['OBJ', 'DIS', 'OBJ', 'DIS', 'OBJ']), [1, 1, 2, 2, 3])
        itens = ordenar_como_caderno([{'tipo': 'OBJ'}, {'tipo': 'DIS'}, {'tipo': 'OBJ'}])
        self.assertEqual([item['rotulo_curto'] for item in itens], ['D01', 'Q01', 'Q02'])
        self.assertEqual([item['rotulo'] for item in itens], ['Discursiva 01', 'Questão 01', 'Questão 02'])


class AtaAvaliacaoTest(TestCase):
    """Ata de Avaliação (lista de presença da sala): alunos, versão de cada um, cabeçalho e PDF."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username='doc_ata', password='segredo123')  # noqa: S106
        self.user.groups.add(Group.objects.get_or_create(name='Administrador')[0])
        self.client.force_login(self.user)

        autor = get_user_model().objects.create_user(username='autor_ata', first_name='Maria', last_name='Docente')
        self.prova = Prova.objects.create(
            titulo='AV1 Ata', disciplina=Disciplina.objects.create(nome='Disc Ata'), autor=autor
        )
        self.versoes = [
            ProvaVersao.objects.create(prova=self.prova, identificador=ident, numero_versao=i)
            for i, ident in enumerate(['A', 'B'], start=1)
        ]
        self.turma = Turma.objects.create(periodo='2026.2', nome='RSD', turno=Turma.Turno.MANHA)
        self.matriculas = {}
        for nome, matricula in (('Carla Dias', 'C2'), ('Ana Souza', 'A2'), ('Bruno Silva', 'B2')):
            aluno = Aluno.objects.create(nome=nome, matricula=matricula)
            self.matriculas[nome] = Matricula.objects.create(aluno=aluno, turma=self.turma)
        # Fora da ata: aluno inativo, matrícula trancada e aluno de outra turma.
        Matricula.objects.create(
            aluno=Aluno.objects.create(nome='Inativo', matricula='I2', ativo=False), turma=self.turma
        )
        Matricula.objects.create(
            aluno=Aluno.objects.create(nome='Trancada', matricula='T2'),
            turma=self.turma,
            situacao=Matricula.Situacao.TRANCADO,
        )
        Matricula.objects.create(
            aluno=Aluno.objects.create(nome='Outra Turma', matricula='O2'),
            turma=Turma.objects.create(periodo='2026.2', nome='Outra'),
        )
        self.aplicacao = ProvaAplicacao.objects.create(
            prova=self.prova, turma=self.turma, data_aplicacao=date(2026, 11, 10)
        )

    def _linhas(self):
        from provas.services.ata_service import linhas_ata_avaliacao

        return linhas_ata_avaliacao(self.aplicacao)

    def test_lista_so_matriculados_ativos_em_ordem_alfabetica(self):
        linhas = self._linhas()
        self.assertEqual([linha['nome'] for linha in linhas], ['Ana Souza', 'Bruno Silva', 'Carla Dias'])
        self.assertEqual([linha['numero'] for linha in linhas], [1, 2, 3])
        self.assertEqual([linha['matricula'] for linha in linhas], ['A2', 'B2', 'C2'])
        # Só nº, matrícula e nome: nota sempre em branco e sem versão (pedido de 2026-10-02).
        self.assertTrue(all(set(linha) == {'numero', 'matricula', 'nome'} for linha in linhas))

    def test_aluno_com_cartao_que_saiu_da_turma_continua_na_ata(self):
        carla = self.matriculas['Carla Dias']
        FolhaResposta.objects.create(prova_aplicacao=self.aplicacao, aluno=carla.aluno, prova_versao=self.versoes[0])
        carla.situacao = Matricula.Situacao.CANCELADO
        carla.save()

        linhas = self._linhas()
        self.assertEqual(len(linhas), 3)
        self.assertEqual(linhas[2]['nome'], 'Carla Dias')

    def test_html_tem_cabecalho_e_colunas_do_modelo(self):
        from django.template.loader import render_to_string

        from provas.services.ata_service import contexto_ata_avaliacao

        html = render_to_string('provas/pdf_ata_avaliacao.html', contexto_ata_avaliacao(self.aplicacao))
        for trecho in (
            'Faculdade de Educação Tecnológica do Rio de Janeiro Fernando Mota',
            'Análise e Desenvolvimento de Sistemas',
            '2026.2',
            '<td>RSD</td>',
            '<td>Manhã</td>',  # turno vem da turma
            'Disc Ata',
            'Maria Docente',
            'AV1 Ata',
            '10/11/2026',
            'ANA SOUZA',
            'Ass. Presença',
            'Ass. Recebido',
            '>Nota</th>',
        ):
            with self.subTest(trecho=trecho):
                self.assertIn(trecho, html)
        self.assertNotIn('Código', html)  # disciplina só pelo nome, sem o código do Coruja

    def test_turma_antiga_sem_turno_deixa_a_linha_em_branco(self):
        from provas.services.ata_service import contexto_ata_avaliacao

        self.assertEqual(contexto_ata_avaliacao(self.aplicacao)['turno'], 'Manhã')
        self.turma.turno = ''
        self.turma.save()
        self.aplicacao.refresh_from_db()
        self.assertEqual(contexto_ata_avaliacao(self.aplicacao)['turno'], '')

    def test_ata_sem_coluna_versao_mesmo_com_varias_versoes_distribuidas(self):
        import re

        from django.template.loader import render_to_string

        from provas.services.ata_service import contexto_ata_avaliacao

        atribuir_versoes(self.aplicacao)  # prova com versões A e B distribuídas
        html = render_to_string('provas/pdf_ata_avaliacao.html', contexto_ata_avaliacao(self.aplicacao))
        self.assertNotIn('Versão', html)
        self.assertEqual(
            re.findall(r'<th[^>]*>([^<]+)</th>', html.split('class="ata-lista"')[1]),
            ['Nº', 'Matrícula', 'Nome do Aluno', 'Ass. Presença', 'Ass. Recebido', 'Nota'],
        )

    def test_view_entrega_o_pdf_sem_gerar_cartoes(self):
        resp = self.client.get(reverse('provas:aplicacao_ata_pdf', args=[self.aplicacao.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'application/pdf')
        self.assertTrue(resp.content.startswith(b'%PDF'))
        self.assertIn('ata-20262-rsd-manha-av1-ata.pdf', resp['Content-Disposition'])
        self.assertFalse(self.aplicacao.folhas.exists())
        self.assertFalse(self.aplicacao.atribuicoes.exists())

        self.assertEqual(self.client.get(reverse('provas:aplicacao_ata_pdf', args=[999999])).status_code, 404)

    def test_painel_da_aplicacao_tem_o_link_da_ata(self):
        resp = self.client.get(reverse('provas:aplicacao_detail', args=[self.aplicacao.pk]))
        self.assertContains(resp, reverse('provas:aplicacao_ata_pdf', args=[self.aplicacao.pk]))

    def test_nome_oficial_da_faculdade(self):
        from django.template.loader import render_to_string

        self.client.logout()
        self.assertContains(self.client.get(reverse('login')), 'Tecnológica do Rio de Janeiro Fernando Mota')
        html = render_to_string('provas/pdf_capa_prova.html', {'prova': self.prova})
        self.assertIn('Tecnológica do Rio de Janeiro Fernando Mota', html)
        self.assertNotIn('Tecnológica do Estado do Rio de Janeiro', html)


class ProvaManualNotaPelaSomaDosPontosTest(TestCase):
    """
    Prova montada à mão sem máscara guia vale a soma dos pontos das questões: a nota do aluno é
    a soma dos pontos obtidos (sem conversão para 0–10), e a capa mostra esse total.
    """

    def setUp(self):
        self.disc = Disciplina.objects.create(nome='Disc Soma de Pontos')
        self.questoes = [
            Questao.objects.create(
                disciplina=self.disc, tipo='DIS', dificuldade='M', enunciado=f'Discursiva {i}', criterios_correcao='x'
            )
            for i in range(3)
        ]

    def _prova(self, mascara=None):
        from .services.manual_generator import criar_prova_manual

        pontos = dict(zip((q.pk for q in self.questoes), ('2', '2', '3'), strict=True))  # 7 pontos
        return criar_prova_manual(
            'Prova de 7 pontos',
            disciplina=self.disc,
            questoes_ids=[q.pk for q in self.questoes],
            pontuacoes=pontos,
            mascara=mascara,
        )

    def test_sem_mascara_a_nota_e_a_soma_dos_pontos_obtidos(self):
        from desempenho.models import Desempenho
        from desempenho.services import escala_da_prova, notas_da_aplicacao

        from .services.caderno_service import valor_total_do_snapshot
        from .services.version_generator import gerar_versoes_prova

        prova = self._prova()
        self.assertEqual(prova.snapshot_mascara['valor_total'], '7.00')
        self.assertEqual(escala_da_prova(prova), Decimal('7.00'))
        self.assertEqual(valor_total_do_snapshot(prova.snapshot_mascara), Decimal('7.00'))  # capa: "7,00 ponto(s)"

        versao = gerar_versoes_prova(prova, 1)[0]
        aplicacao = ProvaAplicacao.objects.create(
            prova=prova, turma=Turma.objects.create(periodo='2026.2', nome='T Soma'), data_aplicacao=date(2026, 11, 10)
        )
        for nome, notas in (('Ana', ('10', '10', '10')), ('Bruno', ('0', '0', '10'))):
            folha = FolhaResposta.objects.create(
                prova_aplicacao=aplicacao, aluno=Aluno.objects.create(nome=nome, matricula=nome), prova_versao=versao
            )
            for questao, nota in zip(self.questoes, notas, strict=True):
                Desempenho.objects.create(folha_resposta=folha, questao=questao, nota=Decimal(nota))

        notas = {linha['aluno']: linha['nota'] for linha in notas_da_aplicacao(aplicacao)['linhas']}
        self.assertEqual(notas, {'Ana': Decimal('7.00'), 'Bruno': Decimal('3.00')})

    def test_com_mascara_guia_continua_proporcional_ao_valor_da_mascara(self):
        from desempenho.services import escala_da_prova

        guia = MascaraProva.objects.create(
            titulo='Guia de 10 pontos',
            disciplina=self.disc,
            quantidade_questoes=3,
            modalidade=MascaraProva.Modalidade.DISCURSIVA,
            percentual_facil=0,
            percentual_medio=100,
            percentual_dificil=0,
            valor_total=Decimal('10.00'),
        )
        self.assertEqual(escala_da_prova(self._prova(guia)), Decimal('10.00'))

    def test_migracao_preenche_as_provas_manuais_antigas(self):
        migracao = import_module('provas.migrations.0014_valor_total_prova_manual')
        manual = self._prova()
        manual.snapshot_mascara.pop('valor_total')  # como as provas criadas antes da mudança
        manual.save(update_fields=['snapshot_mascara'])
        automatica = Prova.objects.create(titulo='Por máscara', disciplina=self.disc, snapshot_mascara={})

        migracao.preencher_valor_total(apps, None)
        manual.refresh_from_db()
        automatica.refresh_from_db()
        self.assertEqual(manual.snapshot_mascara['valor_total'], '7.00')
        self.assertEqual(automatica.snapshot_mascara, {})

        migracao.remover_valor_total(apps, None)
        manual.refresh_from_db()
        self.assertNotIn('valor_total', manual.snapshot_mascara)

    def test_tela_de_montagem_explica_a_regra(self):
        usuario = get_user_model().objects.create_user(username='admin_soma', password='x')  # noqa: S106
        usuario.groups.add(Group.objects.get_or_create(name='Administrador')[0])
        self.client.force_login(usuario)
        tela = self.client.get(reverse('provas:prova_manual_create'), {'disciplina': self.disc.pk})
        self.assertContains(tela, 'a nota de cada aluno é a soma dos pontos que ele obtiver')
        self.assertNotContains(tela, 'na escala de 0 a 10')
