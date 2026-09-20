"""Testes do app acadêmico: models (Aluno/Turma/Matrícula) e CRUDs de Aluno/Turma."""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from .models import Aluno, Matricula, Turma

User = get_user_model()


class AlunoModelTest(TestCase):
    def test_str_e_defaults(self):
        aluno = Aluno.objects.create(nome='Maria Silva', matricula='2026010123')
        self.assertEqual(str(aluno), 'Maria Silva (2026010123)')
        self.assertTrue(aluno.ativo)

    def test_matricula_unica(self):
        Aluno.objects.create(nome='A', matricula='X1')
        with self.assertRaises(IntegrityError), transaction.atomic():
            Aluno.objects.create(nome='B', matricula='X1')


class TurmaModelTest(TestCase):
    def test_str(self):
        turma = Turma.objects.create(periodo='2026.2', nome='MAC Noite')
        self.assertEqual(str(turma), '2026.2 — MAC Noite')

    def test_periodo_invalido_reprova_no_full_clean(self):
        turma = Turma(periodo='2026', nome='MAC Noite')
        with self.assertRaises(ValidationError):
            turma.full_clean()

    def test_periodo_valido_passa(self):
        turma = Turma(periodo='2026.2', nome='CAL Manhã')
        turma.full_clean()  # não deve levantar

    def test_unicidade_periodo_nome(self):
        Turma.objects.create(periodo='2026.2', nome='MAC Noite')
        with self.assertRaises(IntegrityError), transaction.atomic():
            Turma.objects.create(periodo='2026.2', nome='MAC Noite')

    def test_mesma_turma_em_periodos_diferentes_e_permitida(self):
        Turma.objects.create(periodo='2026.1', nome='MAC Noite')
        Turma.objects.create(periodo='2026.2', nome='MAC Noite')
        self.assertEqual(Turma.objects.filter(nome='MAC Noite').count(), 2)


class MatriculaModelTest(TestCase):
    def setUp(self):
        self.aluno = Aluno.objects.create(nome='João', matricula='2026777')
        self.turma = Turma.objects.create(periodo='2026.2', nome='MAC Noite')

    def test_default_situacao_e_propriedade_ativa(self):
        m = Matricula.objects.create(aluno=self.aluno, turma=self.turma)
        self.assertEqual(m.situacao, Matricula.Situacao.MATRICULADO)
        self.assertTrue(m.ativa)

    def test_situacao_nao_matriculado_nao_e_ativa(self):
        m = Matricula.objects.create(aluno=self.aluno, turma=self.turma, situacao=Matricula.Situacao.TRANCADO)
        self.assertFalse(m.ativa)

    def test_unicidade_aluno_turma(self):
        Matricula.objects.create(aluno=self.aluno, turma=self.turma)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Matricula.objects.create(aluno=self.aluno, turma=self.turma)


class AlunoCrudViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='coord', password='segredo123')  # noqa: S106
        self.client.login(username='coord', password='segredo123')  # noqa: S106

    def test_login_obrigatorio(self):
        self.client.logout()
        resp = self.client.get(reverse('academico:aluno_list'))
        self.assertEqual(resp.status_code, 302)

    def test_list_ok(self):
        Aluno.objects.create(nome='Ana', matricula='M1')
        resp = self.client.get(reverse('academico:aluno_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Ana')

    def test_busca_por_matricula(self):
        Aluno.objects.create(nome='Ana', matricula='AAA111')
        Aluno.objects.create(nome='Bruno', matricula='BBB222')
        resp = self.client.get(reverse('academico:aluno_list'), {'q': 'BBB'})
        self.assertContains(resp, 'Bruno')
        self.assertNotContains(resp, 'AAA111')

    def test_create(self):
        resp = self.client.post(
            reverse('academico:aluno_create'),
            {'nome': 'Carlos Lima', 'matricula': '2026999', 'ativo': 'on'},
        )
        self.assertRedirects(resp, reverse('academico:aluno_list'))
        self.assertTrue(Aluno.objects.filter(matricula='2026999').exists())

    def test_update(self):
        aluno = Aluno.objects.create(nome='Nome Antigo', matricula='UP1')
        resp = self.client.post(
            reverse('academico:aluno_update', args=[aluno.pk]),
            {'nome': 'Nome Novo', 'matricula': 'UP1', 'ativo': 'on'},
        )
        self.assertRedirects(resp, reverse('academico:aluno_list'))
        aluno.refresh_from_db()
        self.assertEqual(aluno.nome, 'Nome Novo')

    def test_toggle_ativo(self):
        aluno = Aluno.objects.create(nome='Toggle', matricula='TG1', ativo=True)
        resp = self.client.post(reverse('academico:aluno_toggle_ativo', args=[aluno.pk]))
        self.assertRedirects(resp, reverse('academico:aluno_list'))
        aluno.refresh_from_db()
        self.assertFalse(aluno.ativo)


class TurmaCrudViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='coord2', password='segredo123')  # noqa: S106
        self.client.login(username='coord2', password='segredo123')  # noqa: S106

    def test_list_ok(self):
        Turma.objects.create(periodo='2026.2', nome='MAC Noite')
        resp = self.client.get(reverse('academico:turma_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'MAC Noite')

    def test_create(self):
        resp = self.client.post(
            reverse('academico:turma_create'),
            {'periodo': '2026.2', 'nome': 'CAL Noite', 'ativo': 'on'},
        )
        self.assertRedirects(resp, reverse('academico:turma_list'))
        self.assertTrue(Turma.objects.filter(periodo='2026.2', nome='CAL Noite').exists())

    def test_create_periodo_invalido_reexibe_form(self):
        resp = self.client.post(
            reverse('academico:turma_create'),
            {'periodo': 'errado', 'nome': 'X', 'ativo': 'on'},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Turma.objects.filter(nome='X').exists())

    def test_toggle_ativo(self):
        turma = Turma.objects.create(periodo='2026.2', nome='ED Manhã', ativo=True)
        resp = self.client.post(reverse('academico:turma_toggle_ativo', args=[turma.pk]))
        self.assertRedirects(resp, reverse('academico:turma_list'))
        turma.refresh_from_db()
        self.assertFalse(turma.ativo)
