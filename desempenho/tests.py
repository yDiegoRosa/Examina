"""Testes do model Desempenho (Módulo 4)."""

from datetime import date

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from academico.models import Aluno, Turma
from banco_questoes.models import Disciplina, Questao, Topico
from provas.models import FolhaResposta, Prova, ProvaAplicacao, ProvaVersao

from .models import Desempenho
from .services import taxa_erro_por_dificuldade, taxa_erro_por_topico


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
        return FolhaResposta.objects.create(
            prova_aplicacao=self.aplicacao, aluno=aluno, prova_versao=self.versao
        )

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
        aplic2 = ProvaAplicacao.objects.create(
            prova=self.prova, turma=turma2, data_aplicacao=date(2026, 11, 11)
        )
        aluno = Aluno.objects.create(nome='Carla', matricula='C1')
        folha = FolhaResposta.objects.create(prova_aplicacao=aplic2, aluno=aluno, prova_versao=self.versao)
        self._desempenho(folha, self.q_a1, False)

        por_topico = {linha['topico']: linha for linha in taxa_erro_por_topico(turma=self.turma)}
        self.assertEqual(por_topico['Tópico A']['total'], 4)  # continua 4 (turma 2 excluída)

    def test_registros_sem_acertou_sao_ignorados(self):
        aluno = Aluno.objects.create(nome='Diego', matricula='D1')
        folha = FolhaResposta.objects.create(
            prova_aplicacao=self.aplicacao, aluno=aluno, prova_versao=self.versao
        )
        Desempenho.objects.create(folha_resposta=folha, questao=self.q_a1, acertou=None)  # não corrigido

        por_topico = {linha['topico']: linha for linha in taxa_erro_por_topico(turma=self.turma)}
        self.assertEqual(por_topico['Tópico A']['total'], 4)  # o registro sem acertou não conta


class DashboardFragilidadesViewTest(TestCase):
    """Dashboard RF13: renderização, contexto e filtros (turma/aplicação/período)."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username='coord', password='segredo123')  # noqa: S106
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
