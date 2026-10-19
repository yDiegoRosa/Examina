"""Testes do model Desempenho (Módulo 4)."""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from academico.models import Aluno, Turma
from banco_questoes.models import Disciplina, Questao, Topico
from provas.models import FolhaResposta, Prova, ProvaAplicacao, ProvaQuestao, ProvaVersao, ProvaVersaoItem

from .models import Desempenho
from .services import (
    escala_da_prova,
    indice_facilidade_por_questao,
    notas_da_aplicacao,
    questoes_fora_do_esperado,
    taxa_erro_por_dificuldade,
    taxa_erro_por_topico,
)


class DesempenhoModelTest(TestCase):
    def setUp(self):
        self.disc = Disciplina.objects.create(nome='Disc Desemp')
        self.topico = Topico.objects.create(disciplina=self.disc, nome='Top')
        self.questao = Questao.objects.create(
            disciplina=self.disc, topico=self.topico, tipo='OBJ', dificuldade='F', enunciado='Q'
        )
        self.prova = Prova.objects.create(titulo='Prova', disciplina=self.disc)
        self.versao = ProvaVersao.objects.create(prova=self.prova, identificador='A', numero_versao=1)
        self.turma = Turma.objects.create(periodo='2026.2', nome='MAC Noite')
        self.aluno = Aluno.objects.create(nome='Ana', matricula='A1')
        self.aplicacao = ProvaAplicacao.objects.create(
            prova=self.prova, turma=self.turma, data_aplicacao=date(2026, 11, 10)
        )
        self.folha = FolhaResposta.objects.create(
            prova_aplicacao=self.aplicacao, aluno=self.aluno, prova_versao=self.versao
        )

    def test_defaults_e_nulaveis(self):
        d = Desempenho.objects.create(folha_resposta=self.folha, questao=self.questao)
        self.assertEqual(d.tipo_correcao, Desempenho.TipoCorrecao.AUTOMATICA)
        self.assertIsNone(d.acertou)
        self.assertIsNone(d.nota)
        self.assertIsNone(d.data_correcao)
        self.assertEqual(d.resposta_marcada, '')

    def test_unique_folha_questao(self):
        Desempenho.objects.create(folha_resposta=self.folha, questao=self.questao)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Desempenho.objects.create(folha_resposta=self.folha, questao=self.questao)

    def test_registro_de_correcao(self):
        d = Desempenho.objects.create(
            folha_resposta=self.folha,
            questao=self.questao,
            tipo_correcao=Desempenho.TipoCorrecao.AUTOMATICA,
            resposta_marcada='B',
            acertou=True,
            nota='1.00',
        )
        self.assertTrue(d.acertou)
        self.assertIn('marcou B', str(d))

    def test_historico_guarda_cada_alteracao_da_nota(self):
        professor = get_user_model().objects.create_user(username='prof_hist', password='segredo123')  # noqa: S106
        d = Desempenho.objects.create(folha_resposta=self.folha, questao=self.questao, nota=Decimal('5.00'))
        d.nota = Decimal('7.50')
        d.tipo_correcao = Desempenho.TipoCorrecao.MANUAL
        d.corrigido_por = professor
        d.save()

        historico = list(d.history.order_by('history_date'))
        self.assertEqual([h.nota for h in historico], [Decimal('5.00'), Decimal('7.50')])
        self.assertEqual(historico[-1].history_type, '~')  # alteração
        self.assertEqual(historico[-1].corrigido_por, professor)


class TaxaErroAnaliseTest(TestCase):
    """Agregações de taxa de erro por tópico e por dificuldade (base do RF13/14)."""

    def setUp(self):
        self.disc = Disciplina.objects.create(nome='Disc Análise')
        self.top_a = Topico.objects.create(disciplina=self.disc, nome='Tópico A')
        self.top_b = Topico.objects.create(disciplina=self.disc, nome='Tópico B')

        self.q_a1 = self._questao(self.top_a, 'F')
        self.q_a2 = self._questao(self.top_a, 'F')
        self.q_b1 = self._questao(self.top_b, 'M')
        self.q_sem = self._questao(None, 'D')  # sem tópico

        self.prova = Prova.objects.create(titulo='Prova Análise', disciplina=self.disc)
        self.versao = ProvaVersao.objects.create(prova=self.prova, identificador='A', numero_versao=1)
        self.turma = Turma.objects.create(periodo='2026.2', nome='Turma 1')
        self.aplicacao = ProvaAplicacao.objects.create(
            prova=self.prova, turma=self.turma, data_aplicacao=date(2026, 11, 10)
        )
        self.folha1 = self._folha('Ana', 'A1')
        self.folha2 = self._folha('Bruno', 'B1')

        # folha1: q_a1 ✓, q_a2 ✗, q_b1 ✗, q_sem ✓
        self._desempenho(self.folha1, self.q_a1, True)
        self._desempenho(self.folha1, self.q_a2, False)
        self._desempenho(self.folha1, self.q_b1, False)
        self._desempenho(self.folha1, self.q_sem, True)
        # folha2: q_a1 ✓, q_a2 ✓, q_b1 ✗, q_sem ✗
        self._desempenho(self.folha2, self.q_a1, True)
        self._desempenho(self.folha2, self.q_a2, True)
        self._desempenho(self.folha2, self.q_b1, False)
        self._desempenho(self.folha2, self.q_sem, False)

    def _questao(self, topico, dificuldade):
        return Questao.objects.create(
            disciplina=self.disc, topico=topico, tipo='OBJ', dificuldade=dificuldade, enunciado='Q'
        )

    def _folha(self, nome, matricula):
        aluno = Aluno.objects.create(nome=nome, matricula=matricula)
        return FolhaResposta.objects.create(prova_aplicacao=self.aplicacao, aluno=aluno, prova_versao=self.versao)

    def _desempenho(self, folha, questao, acertou):
        return Desempenho.objects.create(
            folha_resposta=folha,
            questao=questao,
            tipo_correcao=Desempenho.TipoCorrecao.AUTOMATICA,
            acertou=acertou,
        )

    def test_taxa_erro_por_topico(self):
        linhas = taxa_erro_por_topico(turma=self.turma)
        por_topico = {linha['topico']: linha for linha in linhas}

        # Tópico A: 4 registros, 3 acertos → taxa 0.25
        self.assertEqual(por_topico['Tópico A']['total'], 4)
        self.assertEqual(por_topico['Tópico A']['acertos'], 3)
        self.assertEqual(por_topico['Tópico A']['taxa_erro'], 0.25)
        # Tópico B: 2 registros, 0 acertos → taxa 1.0
        self.assertEqual(por_topico['Tópico B']['taxa_erro'], 1.0)
        # Sem tópico: 2 registros, 1 acerto → taxa 0.5
        self.assertEqual(por_topico['Sem tópico']['taxa_erro'], 0.5)
        # Ordenação: maior taxa de erro primeiro (fragilidades)
        self.assertEqual([linha['topico'] for linha in linhas], ['Tópico B', 'Sem tópico', 'Tópico A'])

    def test_taxa_erro_por_dificuldade(self):
        linhas = taxa_erro_por_dificuldade(turma=self.turma)
        self.assertEqual([linha['dificuldade'] for linha in linhas], ['F', 'M', 'D'])
        por_dif = {linha['dificuldade']: linha for linha in linhas}
        self.assertEqual(por_dif['F']['taxa_erro'], 0.25)  # 4 reg, 3 acertos
        self.assertEqual(por_dif['M']['taxa_erro'], 1.0)  # 2 reg, 0 acerto
        self.assertEqual(por_dif['D']['taxa_erro'], 0.5)  # 2 reg, 1 acerto
        self.assertEqual(por_dif['M']['dificuldade_display'], 'Médio')

    def test_filtra_por_turma(self):
        # Outra turma/aplicação com desempenho que NÃO deve entrar no resultado da turma 1.
        turma2 = Turma.objects.create(periodo='2026.2', nome='Turma 2')
        aplic2 = ProvaAplicacao.objects.create(prova=self.prova, turma=turma2, data_aplicacao=date(2026, 11, 11))
        aluno = Aluno.objects.create(nome='Carla', matricula='C1')
        folha = FolhaResposta.objects.create(prova_aplicacao=aplic2, aluno=aluno, prova_versao=self.versao)
        self._desempenho(folha, self.q_a1, False)

        por_topico = {linha['topico']: linha for linha in taxa_erro_por_topico(turma=self.turma)}
        self.assertEqual(por_topico['Tópico A']['total'], 4)  # continua 4 (turma 2 excluída)

    def test_registros_sem_acertou_sao_ignorados(self):
        aluno = Aluno.objects.create(nome='Diego', matricula='D1')
        folha = FolhaResposta.objects.create(prova_aplicacao=self.aplicacao, aluno=aluno, prova_versao=self.versao)
        Desempenho.objects.create(folha_resposta=folha, questao=self.q_a1, acertou=None)  # não corrigido

        por_topico = {linha['topico']: linha for linha in taxa_erro_por_topico(turma=self.turma)}
        self.assertEqual(por_topico['Tópico A']['total'], 4)  # o registro sem acertou não conta


class DashboardFragilidadesViewTest(TestCase):
    """Dashboard RF13: renderização, contexto e filtros (turma/aplicação/período)."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username='coord', password='segredo123')  # noqa: S106
        self.user.groups.add(Group.objects.get_or_create(name='Administrador')[0])
        self.disc = Disciplina.objects.create(nome='Disc Dash')
        self.topico = Topico.objects.create(disciplina=self.disc, nome='Redes')
        self.questao = Questao.objects.create(
            disciplina=self.disc, topico=self.topico, tipo='OBJ', dificuldade='M', enunciado='Q'
        )
        self.prova = Prova.objects.create(titulo='Prova Dash', disciplina=self.disc)
        self.versao = ProvaVersao.objects.create(prova=self.prova, identificador='A', numero_versao=1)
        self.turma = Turma.objects.create(periodo='2026.2', nome='Turma Dash')
        self.aluno = Aluno.objects.create(nome='Ana', matricula='A1')
        self.aplicacao = ProvaAplicacao.objects.create(
            prova=self.prova, turma=self.turma, data_aplicacao=date(2026, 11, 10)
        )
        self.folha = FolhaResposta.objects.create(
            prova_aplicacao=self.aplicacao, aluno=self.aluno, prova_versao=self.versao
        )
        Desempenho.objects.create(folha_resposta=self.folha, questao=self.questao, acertou=False)  # errou

    def test_login_obrigatorio(self):
        resp = self.client.get(reverse('desempenho:dashboard'))
        self.assertEqual(resp.status_code, 302)

    def test_usuario_sem_grupo_recebe_403(self):
        get_user_model().objects.create_user(username='sem_grupo', password='segredo123')  # noqa: S106
        self.client.login(username='sem_grupo', password='segredo123')  # noqa: S106
        for url in (reverse('desempenho:dashboard'), reverse('desempenho:psicometria')):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_renderiza_com_dados(self):
        self.client.login(username='coord', password='segredo123')  # noqa: S106
        resp = self.client.get(reverse('desempenho:dashboard'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['total_registros'], 1)
        self.assertContains(resp, 'Redes')
        self.assertEqual(resp.context['por_topico'][0]['taxa_erro'], 1.0)

    def test_filtro_por_aplicacao(self):
        self.client.login(username='coord', password='segredo123')  # noqa: S106
        resp = self.client.get(reverse('desempenho:dashboard'), {'aplicacao': self.aplicacao.pk})
        self.assertEqual(resp.context['total_registros'], 1)

    def test_filtro_por_periodo_exclui(self):
        self.client.login(username='coord', password='segredo123')  # noqa: S106
        resp = self.client.get(reverse('desempenho:dashboard'), {'data_inicio': '2027-01-01'})
        self.assertEqual(resp.context['total_registros'], 0)
        self.assertContains(resp, 'Nenhuma questão corrigida')


class PsicometriaTest(TestCase):
    """RF14: índice de facilidade global por questão, outliers e exportação CSV/PDF."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username='coord2', password='segredo123')  # noqa: S106
        self.user.groups.add(Group.objects.get_or_create(name='Administrador')[0])
        self.disc = Disciplina.objects.create(nome='Disc Psi')
        self.prova = Prova.objects.create(titulo='Prova Psi', disciplina=self.disc)
        self.versao = ProvaVersao.objects.create(prova=self.prova, identificador='A', numero_versao=1)
        self.turma = Turma.objects.create(periodo='2026.2', nome='T Psi')
        self.aplic1 = ProvaAplicacao.objects.create(
            prova=self.prova, turma=self.turma, data_aplicacao=date(2026, 11, 10)
        )
        self.aplic2 = ProvaAplicacao.objects.create(
            prova=self.prova, turma=self.turma, data_aplicacao=date(2026, 11, 20)
        )
        self._contador = 0

    def _questao(self, dificuldade='M'):
        return Questao.objects.create(disciplina=self.disc, tipo='OBJ', dificuldade=dificuldade, enunciado='Q')

    def _resposta(self, questao, aplicacao, acertou):
        self._contador += 1
        aluno = Aluno.objects.create(nome=f'Aluno {self._contador}', matricula=f'M{self._contador}')
        folha = FolhaResposta.objects.create(prova_aplicacao=aplicacao, aluno=aluno, prova_versao=self.versao)
        Desempenho.objects.create(folha_resposta=folha, questao=questao, acertou=acertou)

    def test_indice_facilidade_global_agrega_aplicacoes(self):
        q = self._questao()
        self._resposta(q, self.aplic1, True)
        self._resposta(q, self.aplic1, True)
        self._resposta(q, self.aplic2, False)  # aplicação diferente entra no mesmo índice
        itens = {i['questao_id']: i for i in indice_facilidade_por_questao(min_respostas=1)}
        self.assertEqual(itens[q.id]['total_respostas'], 3)
        self.assertEqual(itens[q.id]['acertos'], 2)
        self.assertAlmostEqual(itens[q.id]['indice_facilidade'], round(2 / 3, 4))
        self.assertEqual(itens[q.id]['classe'], 'medio')

    def test_min_respostas_filtra(self):
        q = self._questao()
        self._resposta(q, self.aplic1, True)  # só 1 resposta
        ids = {i['questao_id'] for i in indice_facilidade_por_questao(min_respostas=5)}
        self.assertNotIn(q.id, ids)

    def test_fora_do_esperado(self):
        q_dificil = self._questao('D')
        q_facil = self._questao('F')
        q_medio = self._questao('M')
        for _ in range(5):
            self._resposta(q_dificil, self.aplic1, False)  # p = 0.0
            self._resposta(q_facil, self.aplic1, True)  # p = 1.0
        for i in range(5):
            self._resposta(q_medio, self.aplic1, i < 3)  # p = 0.6

        fora = {f['questao_id']: f for f in questoes_fora_do_esperado(min_respostas=5)}
        self.assertEqual(fora[q_dificil.id]['motivo'], 'muito_dificil')
        self.assertEqual(fora[q_facil.id]['motivo'], 'muito_facil')
        self.assertNotIn(q_medio.id, fora)

    def test_limites_voltam_no_formulario_com_ponto_decimal(self):
        self.client.login(username='coord2', password='segredo123')  # noqa: S106
        resp = self.client.get(reverse('desempenho:psicometria'), {'limite_baixo': '0.25', 'limite_alto': '0.85'})
        self.assertContains(resp, 'value="0.25"')
        self.assertContains(resp, 'value="0.85"')

    def test_view_render(self):
        q = self._questao()
        for _ in range(5):
            self._resposta(q, self.aplic1, True)
        self.client.login(username='coord2', password='segredo123')  # noqa: S106
        resp = self.client.get(reverse('desempenho:psicometria'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['total_questoes'], 1)

    def test_export_csv(self):
        q = self._questao()
        for _ in range(5):
            self._resposta(q, self.aplic1, True)
        self.client.login(username='coord2', password='segredo123')  # noqa: S106
        resp = self.client.get(reverse('desempenho:psicometria'), {'formato': 'csv'})
        self.assertEqual(resp['Content-Type'], 'text/csv; charset=utf-8')
        self.assertIn('attachment', resp['Content-Disposition'])
        conteudo = resp.content.decode('utf-8')
        self.assertIn('Índice de facilidade', conteudo)
        self.assertIn(str(q.id), conteudo)

    def test_export_pdf(self):
        q = self._questao()
        for _ in range(5):
            self._resposta(q, self.aplic1, True)
        self.client.login(username='coord2', password='segredo123')  # noqa: S106
        resp = self.client.get(reverse('desempenho:psicometria'), {'formato': 'pdf'})
        self.assertEqual(resp['Content-Type'], 'application/pdf')
        self.assertTrue(resp.content.startswith(b'%PDF'))


class AgregacoesCalculoTest(TestCase):
    """Bordas e precisão dos cálculos de agregação (RF13/RF14)."""

    def setUp(self):
        self.disc = Disciplina.objects.create(nome='Disc Calc')
        self.prova = Prova.objects.create(titulo='Prova Calc', disciplina=self.disc)
        self.versao = ProvaVersao.objects.create(prova=self.prova, identificador='A', numero_versao=1)
        self.turma = Turma.objects.create(periodo='2026.2', nome='T Calc')
        self.aplic1 = ProvaAplicacao.objects.create(
            prova=self.prova, turma=self.turma, data_aplicacao=date(2026, 11, 10)
        )
        self.aplic2 = ProvaAplicacao.objects.create(
            prova=self.prova, turma=self.turma, data_aplicacao=date(2026, 11, 20)
        )
        self._contador = 0

    def _questao(self, topico=None, dificuldade='M'):
        return Questao.objects.create(
            disciplina=self.disc, topico=topico, tipo='OBJ', dificuldade=dificuldade, enunciado='Q'
        )

    def _resp(self, questao, acertou, aplicacao=None):
        self._contador += 1
        aluno = Aluno.objects.create(nome=f'A{self._contador}', matricula=f'M{self._contador}')
        folha = FolhaResposta.objects.create(
            prova_aplicacao=aplicacao or self.aplic1, aluno=aluno, prova_versao=self.versao
        )
        Desempenho.objects.create(folha_resposta=folha, questao=questao, acertou=acertou)

    def _padrao(self, questao, acertos, total, aplicacao=None):
        """Cria `acertos` corretas e `total-acertos` erradas para a questão."""
        for i in range(total):
            self._resp(questao, i < acertos, aplicacao)

    # --- Vazio -------------------------------------------------------------
    def test_agregacoes_vazias(self):
        self.assertEqual(taxa_erro_por_topico(), [])
        self.assertEqual(taxa_erro_por_dificuldade(), [])
        self.assertEqual(indice_facilidade_por_questao(), [])
        self.assertEqual(questoes_fora_do_esperado(), [])

    # --- Filtros no nível do serviço --------------------------------------
    def test_taxa_erro_filtra_por_aplicacao(self):
        top = Topico.objects.create(disciplina=self.disc, nome='T')
        q = self._questao(topico=top)
        self._resp(q, False, self.aplic1)  # erro na aplic1
        self._resp(q, True, self.aplic2)  # acerto na aplic2
        por_topico = {t['topico']: t for t in taxa_erro_por_topico(aplicacao=self.aplic1)}
        self.assertEqual(por_topico['T']['total'], 1)
        self.assertEqual(por_topico['T']['taxa_erro'], 1.0)

    def test_taxa_erro_filtra_por_periodo(self):
        top = Topico.objects.create(disciplina=self.disc, nome='T')
        q = self._questao(topico=top)
        self._resp(q, False, self.aplic1)  # 2026-11-10
        self._resp(q, True, self.aplic2)  # 2026-11-20
        por_topico = {t['topico']: t for t in taxa_erro_por_topico(data_inicio=date(2026, 11, 15))}
        self.assertEqual(por_topico['T']['total'], 1)  # só a aplic2
        self.assertEqual(por_topico['T']['acertos'], 1)
        self.assertEqual(por_topico['T']['taxa_erro'], 0.0)

    # --- Valores exatos ----------------------------------------------------
    def test_taxa_erro_zero_e_fracao(self):
        top_zero = Topico.objects.create(disciplina=self.disc, nome='Zero')
        top_frac = Topico.objects.create(disciplina=self.disc, nome='Frac')
        self._padrao(self._questao(topico=top_zero), acertos=3, total=3)  # taxa 0.0
        self._padrao(self._questao(topico=top_frac), acertos=1, total=3)  # taxa 2/3
        por_topico = {t['topico']: t for t in taxa_erro_por_topico()}
        self.assertEqual(por_topico['Zero']['taxa_erro'], 0.0)
        self.assertEqual(por_topico['Frac']['taxa_erro'], round(2 / 3, 4))
        self.assertEqual(por_topico['Frac']['disciplina'], 'Disc Calc')

    # --- Limites de classe do índice de facilidade -------------------------
    def test_indice_classe_nos_limites(self):
        q30 = self._questao(dificuldade='M')
        q70 = self._questao(dificuldade='M')
        q20 = self._questao(dificuldade='D')
        q80 = self._questao(dificuldade='F')
        self._padrao(q30, 3, 10)  # p = 0.30
        self._padrao(q70, 7, 10)  # p = 0.70
        self._padrao(q20, 1, 5)  # p = 0.20
        self._padrao(q80, 4, 5)  # p = 0.80
        por_q = {i['questao_id']: i for i in indice_facilidade_por_questao(min_respostas=1)}
        self.assertEqual(por_q[q30.id]['classe'], 'medio')  # 0.30 não é < 0.30
        self.assertEqual(por_q[q70.id]['classe'], 'medio')  # 0.70 não é > 0.70
        self.assertEqual(por_q[q20.id]['classe'], 'dificil')
        self.assertEqual(por_q[q80.id]['classe'], 'facil')

    # --- Limiares de outlier inclusivos ------------------------------------
    def test_outlier_limites_inclusivos(self):
        q_baixo = self._questao(dificuldade='D')
        q_alto = self._questao(dificuldade='F')
        q_ok_baixo = self._questao(dificuldade='M')
        q_ok_alto = self._questao(dificuldade='M')
        self._padrao(q_baixo, 1, 5)  # p = 0.20 (== limite_baixo)
        self._padrao(q_alto, 9, 10)  # p = 0.90 (== limite_alto)
        self._padrao(q_ok_baixo, 2, 5)  # p = 0.40
        self._padrao(q_ok_alto, 4, 5)  # p = 0.80
        fora = {f['questao_id']: f for f in questoes_fora_do_esperado()}
        self.assertEqual(fora[q_baixo.id]['motivo'], 'muito_dificil')  # inclusivo em 0.20
        self.assertEqual(fora[q_alto.id]['motivo'], 'muito_facil')  # inclusivo em 0.90
        self.assertNotIn(q_ok_baixo.id, fora)
        self.assertNotIn(q_ok_alto.id, fora)


class NotasDaAplicacaoServiceTest(TestCase):
    """Nota final proporcional: Σ(peso × fração)/Σpeso × escala; pendentes contam zero (incompleta)."""

    def setUp(self):
        self.disc = Disciplina.objects.create(nome='Disc Notas')
        self.prova = Prova.objects.create(titulo='Prova Notas', disciplina=self.disc)
        self.versao = ProvaVersao.objects.create(prova=self.prova, identificador='A', numero_versao=1)
        # Pesos: OBJ1 = 2, OBJ2 = 1, DIS = 1  → pontos possíveis = 4
        self.obj1 = self._item('OBJ', Decimal('2'), ordem=1)
        self.obj2 = self._item('OBJ', Decimal('1'), ordem=2)
        self.dis = self._item('DIS', Decimal('1'), ordem=3)
        self.aplicacao = ProvaAplicacao.objects.create(
            prova=self.prova,
            turma=Turma.objects.create(periodo='2026.2', nome='T Notas'),
            data_aplicacao=date(2026, 11, 10),
        )

    def _item(self, tipo, peso, ordem):
        questao = Questao.objects.create(disciplina=self.disc, tipo=tipo, dificuldade='M', enunciado=f'Q{ordem}')
        pq = ProvaQuestao.objects.create(
            prova=self.prova,
            questao=questao,
            numero=ordem,
            ordem=ordem,
            pontuacao=peso,
            snapshot_questao={'tipo': tipo},
        )
        ProvaVersaoItem.objects.create(versao=self.versao, prova_questao=pq, numero=ordem, ordem=ordem)
        return questao

    def _folha(self, nome, matricula, status=FolhaResposta.Status.GERADA):
        return FolhaResposta.objects.create(
            prova_aplicacao=self.aplicacao,
            aluno=Aluno.objects.create(nome=nome, matricula=matricula),
            prova_versao=self.versao,
            status=status,
        )

    def test_nota_proporcional_completa(self):
        folha = self._folha('Ana', 'A1')
        Desempenho.objects.create(folha_resposta=folha, questao=self.obj1, acertou=True)  # +2
        Desempenho.objects.create(folha_resposta=folha, questao=self.obj2, acertou=False)  # +0
        Desempenho.objects.create(folha_resposta=folha, questao=self.dis, nota=Decimal('5'), acertou=False)  # +0,5

        linha = notas_da_aplicacao(self.aplicacao)['linhas'][0]

        self.assertEqual(linha['pontos_obtidos'], Decimal('2.50'))
        self.assertEqual(linha['pontos_possiveis'], Decimal('4.00'))
        self.assertEqual(linha['nota'], Decimal('6.25'))  # 2,5/4 × 10
        self.assertEqual(linha['situacao'], 'completa')
        self.assertEqual((linha['acertos_objetivas'], linha['total_objetivas']), (1, 2))
        self.assertEqual((linha['discursivas_corrigidas'], linha['total_discursivas']), (1, 1))

    def test_pendentes_contam_zero_e_marcam_incompleta(self):
        folha = self._folha('Bia', 'B1')
        Desempenho.objects.create(folha_resposta=folha, questao=self.obj1, acertou=True)
        Desempenho.objects.create(folha_resposta=folha, questao=self.obj2, acertou=None)  # não corrigida

        linha = notas_da_aplicacao(self.aplicacao)['linhas'][0]

        self.assertEqual(linha['pendentes'], 2)  # OBJ2 sem acertou + DIS sem registro
        self.assertEqual(linha['nota'], Decimal('5.00'))  # 2/4 × 10
        self.assertEqual(linha['situacao'], 'incompleta')

    def test_folha_anulada_sem_nota_e_media_so_de_completas(self):
        completa = self._folha('Ana', 'A1')
        for questao in (self.obj1, self.obj2):
            Desempenho.objects.create(folha_resposta=completa, questao=questao, acertou=True)
        Desempenho.objects.create(folha_resposta=completa, questao=self.dis, nota=Decimal('10'))
        self._folha('Caio', 'C1', status=FolhaResposta.Status.ANULADA)
        self._folha('Duda', 'D1')  # nada corrigido → incompleta

        boletim = notas_da_aplicacao(self.aplicacao)
        por_aluno = {linha['aluno']: linha for linha in boletim['linhas']}

        self.assertEqual(por_aluno['Ana']['nota'], Decimal('10.00'))
        self.assertIsNone(por_aluno['Caio']['nota'])
        self.assertEqual(por_aluno['Caio']['situacao'], 'anulada')
        self.assertEqual(por_aluno['Duda']['nota'], Decimal('0.00'))
        self.assertEqual(boletim['media'], Decimal('10.00'))  # só a completa entra
        self.assertEqual((boletim['total_completas'], boletim['total_incompletas']), (1, 1))

    def test_escala_vem_do_snapshot_da_mascara(self):
        self.assertEqual(escala_da_prova(self.prova), Decimal('10'))  # sem máscara/snapshot
        self.prova.snapshot_mascara = {'valor_total': '8.00'}
        self.prova.save(update_fields=['snapshot_mascara'])
        folha = self._folha('Ana', 'A1')
        Desempenho.objects.create(folha_resposta=folha, questao=self.obj1, acertou=True)
        Desempenho.objects.create(folha_resposta=folha, questao=self.obj2, acertou=True)
        Desempenho.objects.create(folha_resposta=folha, questao=self.dis, nota=Decimal('0'))

        boletim = notas_da_aplicacao(self.aplicacao)

        self.assertEqual(boletim['escala'], Decimal('8.00'))
        self.assertEqual(boletim['linhas'][0]['nota'], Decimal('6.00'))  # 3/4 × 8
