import logging
import tempfile
from datetime import date
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from unittest import skipUnless

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.core.exceptions import ValidationError
from django.db import IntegrityError, OperationalError, connection, transaction
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.urls import resolve, reverse

from academico.models import Aluno, Matricula, Turma
from banco_questoes.models import Alternativa, Disciplina, Questao, Topico

from .forms import MascaraCriterioTopicoFormSet, MascaraForm
from .models import (
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
from .presets import preset_enade_initial
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
from .services.folha_service import (
    contar_questoes_objetivas,
    gerar_folhas_da_aplicacao,
    render_folha_pdf,
)
from .services.pdf_generator import PDFGenerationError, PDFGeneratorService, resolve_static_media_url
from .services.version_assigner import atribuir_versoes
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
            'titulo': 'Avaliação de Teste Unitário',
            'disciplina': 'Estruturas de Dados',
            'docente': 'Prof. Teste',
            'turma': 'ADS-2026',
            'data': '26/08/2026',
            'questoes': [self.q_obj, self.q_dis],
            'incluir_gabarito': True,
        }

        pdf_bytes = PDFGeneratorService.render_pdf_from_template(
            'provas/pdf_template_teste.html',
            context=context,
        )

        self.assertIsInstance(pdf_bytes, bytes)
        self.assertTrue(len(pdf_bytes) > 1000)
        self.assertTrue(pdf_bytes.startswith(b'%PDF-'))

    def test_pdf_service_raises_on_invalid_template(self):
        """Verifica se erro no template levanta PDFGenerationError apropriado."""
        with self.assertRaises(PDFGenerationError):
            PDFGeneratorService.render_pdf_from_template('provas/template_inexistente_xyz.html', {})

    def test_pdf_teste_view_requires_login(self):
        """Verifica se a view /provas/pdf-teste/ exige autenticação."""
        url = reverse('provas:pdf_teste')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_pdf_teste_view_authenticated_returns_pdf_response(self):
        """Verifica se /provas/pdf-teste/ retorna stream de PDF (application/pdf) com status 200."""
        self.client.login(username='prof_weasy', password='secret_password')  # noqa: S106
        url = reverse('provas:pdf_teste')
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn('inline; filename="avaliacao_teste_faeterj.pdf"', response['Content-Disposition'])
        self.assertTrue(response.content.startswith(b'%PDF-'))
        self.assertTrue(len(response.content) > 1000)

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
    """Testes do preset de Simulado ENADE (WS2 Fase 1) — máscara MISTA de 40 questões."""

    def setUp(self):
        self.disciplina = Disciplina.objects.create(nome='Análise e Desenvolvimento de Sistemas')
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username='coord_enade', password='secret_password')  # noqa: S106

    def test_preset_initial_tem_estrutura_oficial_enade(self):
        """O preset propõe 40 questões (35 objetivas + 5 discursivas) em modalidade mista."""
        initial = preset_enade_initial(self.disciplina)
        self.assertEqual(initial['modalidade'], MascaraProva.Modalidade.MISTA)
        self.assertEqual(initial['quantidade_questoes'], 40)
        self.assertEqual(initial['quantidade_objetivas'], 35)
        self.assertEqual(initial['quantidade_discursivas'], 5)
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
        self.assertEqual(initial['titulo'], 'Simulado ENADE 2026 — ADS')

    def test_mascara_montada_pelo_preset_passa_na_validacao(self):
        """Uma máscara construída com os valores do preset deve passar no full_clean()."""
        initial = preset_enade_initial(self.disciplina)
        mascara = MascaraProva(autor=self.user, **initial)
        # Não deve levantar ValidationError: soma de dificuldade = 100 e objetivas + discursivas = total.
        mascara.full_clean()

    def test_create_view_com_preset_enade_pre_preenche_formulario(self):
        """GET em mascara_create?preset=enade retorna o formulário com os valores ENADE."""
        self.client.login(username='coord_enade', password='secret_password')  # noqa: S106
        url = reverse('provas:mascara_create')
        response = self.client.get(f'{url}?preset=enade&disciplina={self.disciplina.pk}')
        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        self.assertEqual(form.initial['quantidade_questoes'], 40)
        self.assertEqual(form.initial['modalidade'], MascaraProva.Modalidade.MISTA)
        self.assertEqual(form.initial['quantidade_objetivas'], 35)
        self.assertEqual(form.initial['disciplina'], self.disciplina)


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

    def test_round_robin_distribui_e_evita_vizinhos_iguais(self):
        # 7 alunos, nomes em ordem controlada (A..G) para checar adjacência.
        for i, letra in enumerate('ABCDEFG'):
            self._matricular(f'{letra} Aluno', f'M{i:02d}')

        atribuicoes = atribuir_versoes(self.aplicacao)
        self.assertEqual(len(atribuicoes), 7)

        # Ordenadas por sequência; nenhum par adjacente com a mesma versão.
        ordenadas = sorted(atribuicoes, key=lambda a: a.sequencia)
        letras = [a.versao.identificador for a in ordenadas]
        self.assertEqual(letras[:3], ['A', 'B', 'C'])  # round-robin
        for anterior, atual in zip(letras, letras[1:], strict=False):
            self.assertNotEqual(anterior, atual)

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


class FolhaRespostaTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='coord_f', password='segredo123')  # noqa: S106
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
