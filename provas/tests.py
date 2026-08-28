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

        self.assertRedirects(response, reverse('provas:mascara_detail', kwargs={'pk': self.mascara.pk}))
        self.assertContains(response, 'gerada com sucesso')
        self.assertEqual(Prova.objects.count(), 1)



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

