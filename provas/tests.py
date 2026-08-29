from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import resolve, reverse

from banco_questoes.models import Alternativa, Disciplina, Questao, Topico

from .forms import MascaraCriterioTopicoFormSet, MascaraForm
from .models import Mascara, MascaraCriterioDificuldade, MascaraCriterioTopico, MascaraProva
from .services.pdf_generator import PDFGenerationError, PDFGeneratorService
from .views import mascara_list


class ProvasAppBaseTest(TestCase):
    """Testes base de rotas e integridade do app provas (Módulo 2)."""

    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username='docente_provas', password='secret_password')  # noqa: S106

    def test_provas_index_url_resolves(self):
        """Verifica se a rota /provas/ resolve para a view mascara_list (painel principal)."""
        found = resolve('/provas/')
        self.assertEqual(found.func, mascara_list)

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
        self.assertContains(response, 'Máscaras de Avaliação')


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
        self.assertEqual(primeiras_4_ids, {self.questoes[2].id, self.questoes[3].id, self.questoes[4].id, self.questoes[5].id})

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
            'mascara_id', 'titulo', 'disciplina_id', 'disciplina_nome',
            'quantidade_questoes', 'modalidade', 'modalidade_display',
            'quantidade_objetivas', 'quantidade_discursivas',
            'percentual_facil', 'percentual_medio', 'percentual_dificil',
            'valor_total', 'tempo_estimado_minutos',
            'embaralhar_questoes', 'embaralhar_alternativas', 'priorizar_enade',
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
        self.assertNotIn(self.q1_original, candidatas, 'Questão atual não deve ser sugerida como substituta de si mesma')
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





