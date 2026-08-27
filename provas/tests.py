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

