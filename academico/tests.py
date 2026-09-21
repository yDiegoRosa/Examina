"""Testes das regras dos modelos acadêmicos."""

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.urls import reverse

from .models import Aluno, Matricula, Turma


class TestModelosAcademicos(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.aluno = Aluno.objects.create(
            nome='Aluno Teste',
            matricula='001234',
        )
        cls.turma = Turma.objects.create(
            nome='Turma Teste',
            periodo='2026.1',
        )
        cls.vinculo = Matricula.objects.create(
            aluno=cls.aluno,
            turma=cls.turma,
        )

    def test_aluno_preserva_zeros_e_comeca_ativo(self):
        self.aluno.refresh_from_db()

        self.assertEqual(self.aluno.matricula, '001234')
        self.assertTrue(self.aluno.ativo)

    def test_matricula_do_aluno_nao_pode_repetir(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Aluno.objects.create(
                nome='Outro Aluno',
                matricula='001234',
            )

    def test_turma_nao_pode_repetir_nome_no_mesmo_periodo(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Turma.objects.create(
                nome='Turma Teste',
                periodo='2026.1',
            )

    def test_novo_periodo_preserva_vinculo_anterior(self):
        nova_turma = Turma.objects.create(
            nome='Turma Teste',
            periodo='2026.2',
        )
        self.vinculo.ativo = False
        self.vinculo.save(update_fields=['ativo'])

        novo_vinculo = Matricula.objects.create(
            aluno=self.aluno,
            turma=nova_turma,
        )
        self.vinculo.refresh_from_db()

        self.assertEqual(self.aluno.matriculas.count(), 2)
        self.assertEqual(self.vinculo.turma.periodo, '2026.1')
        self.assertFalse(self.vinculo.ativo)
        self.assertEqual(novo_vinculo.turma.periodo, '2026.2')
        self.assertTrue(novo_vinculo.ativo)

    def test_aluno_nao_pode_ter_vinculo_duplicado_na_turma(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Matricula.objects.create(
                aluno=self.aluno,
                turma=self.turma,
            )

    def test_aluno_com_vinculo_nao_pode_ser_excluido(self):
        with self.assertRaises(ProtectedError):
            self.aluno.delete()

        self.assertTrue(Aluno.objects.filter(pk=self.aluno.pk).exists())
        self.assertTrue(Matricula.objects.filter(pk=self.vinculo.pk).exists())

    def test_turma_com_vinculo_nao_pode_ser_excluida(self):
        with self.assertRaises(ProtectedError):
            self.turma.delete()

        self.assertTrue(Turma.objects.filter(pk=self.turma.pk).exists())
        self.assertTrue(Matricula.objects.filter(pk=self.vinculo.pk).exists())

    def test_inativar_aluno_preserva_vinculo(self):
        self.aluno.ativo = False
        self.aluno.save(update_fields=['ativo'])

        self.aluno.refresh_from_db()
        self.vinculo.refresh_from_db()

        self.assertFalse(self.aluno.ativo)
        self.assertEqual(self.vinculo.aluno_id, self.aluno.pk)
        self.assertEqual(self.vinculo.turma_id, self.turma.pk)


class TestAdminAcademico(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.usuario = get_user_model().objects.create_superuser(
            username='admin_teste',
        )
        cls.aluno = Aluno.objects.create(
            nome='Aluno Teste',
            matricula='001234',
        )
        cls.turma = Turma.objects.create(
            nome='Turma Teste',
            periodo='2026.1',
        )
        cls.vinculo = Matricula.objects.create(
            aluno=cls.aluno,
            turma=cls.turma,
        )

    def setUp(self):
        self.client.force_login(self.usuario)

    def test_periodo_fica_protegido_mesmo_com_vinculo_inativo(self):
        self.vinculo.ativo = False
        self.vinculo.save(update_fields=['ativo'])

        url = reverse('admin:academico_turma_change', args=[self.turma.pk])
        resposta = self.client.post(
            url,
            {
                'nome': 'Nome corrigido',
                'periodo': '2027.1',
                'ativo': 'on',
            },
        )
        self.turma.refresh_from_db()

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(self.turma.nome, 'Nome corrigido')
        self.assertEqual(self.turma.periodo, '2026.1')

    def test_turma_sem_vinculos_permite_corrigir_periodo(self):
        turma_sem_vinculos = Turma.objects.create(
            nome='Outra Turma',
            periodo='2026.1',
        )
        url = reverse(
            'admin:academico_turma_change',
            args=[turma_sem_vinculos.pk],
        )
        resposta = self.client.post(
            url,
            {
                'nome': turma_sem_vinculos.nome,
                'periodo': '2026.2',
                'ativo': 'on',
            },
        )
        turma_sem_vinculos.refresh_from_db()

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(turma_sem_vinculos.periodo, '2026.2')

    def test_vinculo_permite_inativar_mas_nao_trocar_aluno_ou_turma(self):
        outro_aluno = Aluno.objects.create(
            nome='Outro Aluno',
            matricula='005678',
        )
        outra_turma = Turma.objects.create(
            nome='Outra Turma',
            periodo='2026.2',
        )
        url = reverse(
            'admin:academico_matricula_change',
            args=[self.vinculo.pk],
        )
        resposta = self.client.post(
            url,
            {
                'aluno': outro_aluno.pk,
                'turma': outra_turma.pk,
            },
        )
        self.vinculo.refresh_from_db()

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(self.vinculo.aluno_id, self.aluno.pk)
        self.assertEqual(self.vinculo.turma_id, self.turma.pk)
        self.assertFalse(self.vinculo.ativo)

    def test_admin_recusa_exclusao_do_vinculo(self):
        url = reverse(
            'admin:academico_matricula_delete',
            args=[self.vinculo.pk],
        )
        resposta = self.client.post(url, {'post': 'yes'})

        self.assertEqual(resposta.status_code, 403)
        self.assertTrue(
            Matricula.objects.filter(pk=self.vinculo.pk).exists(),
        )
