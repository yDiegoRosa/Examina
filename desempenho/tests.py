"""Testes do model Desempenho (Módulo 4)."""

from datetime import date

from django.db import IntegrityError, transaction
from django.test import TestCase

from academico.models import Aluno, Turma
from banco_questoes.models import Disciplina, Questao, Topico
from provas.models import FolhaResposta, Prova, ProvaAplicacao, ProvaVersao

from .models import Desempenho


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
