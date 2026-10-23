"""Testes do app acadêmico: models (Aluno/Turma/Matrícula) e CRUDs de Aluno/Turma."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from .models import Aluno, Matricula, Turma

User = get_user_model()


class TestProtecoesHistorico(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.usuario = User.objects.create_superuser(username='admin_historico')
        cls.aluno = Aluno.objects.create(nome='Ana Teste', matricula='001234')
        cls.outro_aluno = Aluno.objects.create(nome='Bruno Teste', matricula='005678')
        cls.turma = Turma.objects.create(nome='Turma vinculada', periodo='2026.1')
        cls.turma_livre = Turma.objects.create(nome='Turma livre', periodo='2026.1')
        cls.vinculo = Matricula.objects.create(aluno=cls.aluno, turma=cls.turma)

    def setUp(self):
        self.client.force_login(self.usuario)

    def test_periodo_protegido_no_admin_e_na_tela_em_todas_as_situacoes(self):
        rotas = ('admin:academico_turma_change', 'academico:turma_update')

        for rota in rotas:
            for situacao in Matricula.Situacao.values:
                with self.subTest(rota=rota, situacao=situacao):
                    self.vinculo.situacao = situacao
                    self.vinculo.save(update_fields=['situacao'])

                    resposta = self.client.post(
                        reverse(rota, args=[self.turma.pk]),
                        {
                            'nome': 'Nome corrigido',
                            'periodo': '2027.1',
                            'turno': 'MANHA',
                            'ativo': 'on',
                        },
                    )
                    self.turma.refresh_from_db()

                    self.assertEqual(resposta.status_code, 302)
                    self.assertEqual(self.turma.nome, 'Nome corrigido')
                    self.assertEqual(self.turma.periodo, '2026.1')

    def test_turma_sem_vinculo_permite_corrigir_periodo_no_admin_e_na_tela(self):
        rotas = ('admin:academico_turma_change', 'academico:turma_update')

        for rota in rotas:
            with self.subTest(rota=rota):
                self.turma_livre.periodo = '2026.1'
                self.turma_livre.save(update_fields=['periodo'])

                resposta = self.client.post(
                    reverse(rota, args=[self.turma_livre.pk]),
                    {
                        'nome': self.turma_livre.nome,
                        'periodo': '2026.2',
                        'turno': 'NOITE',
                        'ativo': 'on',
                    },
                )
                self.turma_livre.refresh_from_db()

                self.assertEqual(resposta.status_code, 302)
                self.assertEqual(self.turma_livre.periodo, '2026.2')

    def test_admin_permite_cancelar_mas_nao_trocar_aluno_ou_turma(self):
        resposta = self.client.post(
            reverse('admin:academico_matricula_change', args=[self.vinculo.pk]),
            {
                'aluno': self.outro_aluno.pk,
                'turma': self.turma_livre.pk,
                'situacao': Matricula.Situacao.CANCELADO,
                'data_matricula': self.vinculo.data_matricula.isoformat(),
                'data_encerramento': self.vinculo.data_matricula.isoformat(),
                'observacao': 'Cancelamento de teste',
            },
        )
        self.vinculo.refresh_from_db()

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(self.vinculo.aluno_id, self.aluno.pk)
        self.assertEqual(self.vinculo.turma_id, self.turma.pk)
        self.assertEqual(self.vinculo.situacao, Matricula.Situacao.CANCELADO)
        self.assertEqual(self.vinculo.observacao, 'Cancelamento de teste')
        self.assertFalse(self.vinculo.ativa)

    def test_admin_recusa_exclusao_individual(self):
        resposta = self.client.post(
            reverse('admin:academico_matricula_delete', args=[self.vinculo.pk]),
            {'post': 'yes'},
        )

        self.assertEqual(resposta.status_code, 403)
        self.assertTrue(Matricula.objects.filter(pk=self.vinculo.pk).exists())

    def test_admin_nao_exclui_vinculo_por_acao_em_lote(self):
        resposta = self.client.post(
            reverse('admin:academico_matricula_changelist'),
            {
                'action': 'delete_selected',
                '_selected_action': [str(self.vinculo.pk)],
                'post': 'yes',
                'index': '0',
            },
        )

        self.assertIn(resposta.status_code, (200, 302, 403))
        self.assertTrue(Matricula.objects.filter(pk=self.vinculo.pk).exists())

    def test_admin_continua_permitindo_criar_vinculos(self):
        resposta = self.client.post(
            reverse('admin:academico_matricula_add'),
            {
                'aluno': self.outro_aluno.pk,
                'turma': self.turma_livre.pk,
                'situacao': Matricula.Situacao.MATRICULADO,
                'data_matricula': self.vinculo.data_matricula.isoformat(),
            },
        )

        self.assertEqual(resposta.status_code, 302)
        self.assertTrue(
            Matricula.objects.filter(
                aluno=self.outro_aluno,
                turma=self.turma_livre,
                situacao=Matricula.Situacao.MATRICULADO,
            ).exists(),
        )


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
        self.assertEqual(str(turma), '2026.2 — MAC Noite')  # turma antiga, sem turno

    def test_str_inclui_o_turno(self):
        turma = Turma.objects.create(periodo='2026.2', nome='RSD', turno=Turma.Turno.MANHA)
        self.assertEqual(str(turma), '2026.2 — RSD (Manhã)')

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

    def test_mesmo_nome_e_periodo_em_turnos_diferentes_e_permitido(self):
        Turma.objects.create(periodo='2026.2', nome='RSD', turno=Turma.Turno.MANHA)
        Turma.objects.create(periodo='2026.2', nome='RSD', turno=Turma.Turno.NOITE)
        self.assertEqual(Turma.objects.filter(periodo='2026.2', nome='RSD').count(), 2)

        with self.assertRaises(IntegrityError), transaction.atomic():
            Turma.objects.create(periodo='2026.2', nome='RSD', turno=Turma.Turno.NOITE)


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
        self.user.groups.add(Group.objects.get_or_create(name='Administrador')[0])  # cadastros: só o administrador
        self.client.login(username='coord', password='segredo123')  # noqa: S106

    def test_login_obrigatorio(self):
        self.client.logout()
        resp = self.client.get(reverse('academico:aluno_list'))
        self.assertEqual(resp.status_code, 302)

    def test_usuario_sem_grupo_recebe_403(self):
        User.objects.create_user(username='sem_grupo', password='segredo123')  # noqa: S106
        self.client.login(username='sem_grupo', password='segredo123')  # noqa: S106
        for url in (reverse('academico:aluno_list'), reverse('academico:turma_list')):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

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
        self.user.groups.add(Group.objects.get_or_create(name='Administrador')[0])  # cadastros: só o administrador
        self.client.login(username='coord2', password='segredo123')  # noqa: S106

    def test_list_ok(self):
        Turma.objects.create(periodo='2026.2', nome='MAC Noite', turno=Turma.Turno.NOITE)
        resp = self.client.get(reverse('academico:turma_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'MAC Noite')
        self.assertContains(resp, '<td>Noite</td>', html=True)

    def test_create(self):
        resp = self.client.post(
            reverse('academico:turma_create'),
            {'periodo': '2026.2', 'nome': 'CAL Noite', 'turno': 'NOITE', 'ativo': 'on'},
        )
        self.assertRedirects(resp, reverse('academico:turma_list'))
        turma = Turma.objects.get(periodo='2026.2', nome='CAL Noite')
        self.assertEqual(turma.turno, Turma.Turno.NOITE)
        self.assertEqual(turma.get_turno_display(), 'Noite')

    def test_create_exige_turno_manha_ou_noite(self):
        resp = self.client.get(reverse('academico:turma_create'))
        self.assertContains(resp, 'Selecione o turno')
        self.assertContains(resp, '<option value="MANHA">Manhã</option>', html=True)
        self.assertContains(resp, '<option value="NOITE">Noite</option>', html=True)

        for turno in ('', 'TARDE'):
            with self.subTest(turno=turno):
                resp = self.client.post(
                    reverse('academico:turma_create'),
                    {'periodo': '2026.2', 'nome': 'Sem turno', 'turno': turno, 'ativo': 'on'},
                )
                self.assertEqual(resp.status_code, 200)
                self.assertTrue(resp.context['form'].errors.get('turno'))
        self.assertFalse(Turma.objects.filter(nome='Sem turno').exists())

    def test_create_mesmo_nome_outro_turno_e_duplicada_no_mesmo_turno(self):
        Turma.objects.create(periodo='2026.2', nome='RSD', turno=Turma.Turno.MANHA)
        url = reverse('academico:turma_create')

        resp = self.client.post(url, {'periodo': '2026.2', 'nome': 'RSD', 'turno': 'NOITE', 'ativo': 'on'})
        self.assertRedirects(resp, reverse('academico:turma_list'))

        resp = self.client.post(url, {'periodo': '2026.2', 'nome': 'RSD', 'turno': 'MANHA', 'ativo': 'on'})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['form'].non_field_errors())  # duplicada: mensagem no formulário, sem erro 500
        self.assertEqual(Turma.objects.filter(periodo='2026.2', nome='RSD').count(), 2)

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


class TurmaMatriculaViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='coord3', password='segredo123')  # noqa: S106
        self.user.groups.add(Group.objects.get_or_create(name='Administrador')[0])  # cadastros: só o administrador
        self.client.login(username='coord3', password='segredo123')  # noqa: S106
        self.turma = Turma.objects.create(periodo='2026.2', nome='MAC Noite')
        self.ana = Aluno.objects.create(nome='Ana', matricula='A1')
        self.bruno = Aluno.objects.create(nome='Bruno', matricula='B1')

    def test_login_obrigatorio(self):
        self.client.logout()
        resp = self.client.get(reverse('academico:turma_matriculas', args=[self.turma.pk]))
        self.assertEqual(resp.status_code, 302)

    def test_pagina_lista_e_exclui_matriculados_dos_disponiveis(self):
        Matricula.objects.create(aluno=self.ana, turma=self.turma)
        resp = self.client.get(reverse('academico:turma_matriculas', args=[self.turma.pk]))
        self.assertEqual(resp.status_code, 200)
        disponiveis = list(resp.context['alunos_disponiveis'])
        self.assertIn(self.bruno, disponiveis)
        self.assertNotIn(self.ana, disponiveis)  # já matriculada

    def test_matricular_multiplos(self):
        resp = self.client.post(
            reverse('academico:turma_matricular', args=[self.turma.pk]),
            {'alunos': [self.ana.pk, self.bruno.pk]},
        )
        self.assertRedirects(resp, reverse('academico:turma_matriculas', args=[self.turma.pk]))
        self.assertEqual(
            Matricula.objects.filter(turma=self.turma, situacao=Matricula.Situacao.MATRICULADO).count(),
            2,
        )

    def test_desmatricular_e_soft_e_preserva_linha(self):
        m = Matricula.objects.create(aluno=self.ana, turma=self.turma)
        resp = self.client.post(
            reverse('academico:turma_desmatricular', args=[self.turma.pk]),
            {'matricula': m.pk},
        )
        self.assertRedirects(resp, reverse('academico:turma_matriculas', args=[self.turma.pk]))
        m.refresh_from_db()
        self.assertEqual(m.situacao, Matricula.Situacao.CANCELADO)
        self.assertIsNotNone(m.data_encerramento)
        # A linha permanece (histórico preservado).
        self.assertTrue(Matricula.objects.filter(pk=m.pk).exists())

    def test_rematricular_reativa_a_mesma_linha(self):
        m = Matricula.objects.create(aluno=self.ana, turma=self.turma, situacao=Matricula.Situacao.CANCELADO)
        self.client.post(
            reverse('academico:turma_matricular', args=[self.turma.pk]),
            {'alunos': [self.ana.pk]},
        )
        # Sem violar unique (aluno, turma): a mesma linha é reativada.
        self.assertEqual(Matricula.objects.filter(aluno=self.ana, turma=self.turma).count(), 1)
        m.refresh_from_db()
        self.assertEqual(m.situacao, Matricula.Situacao.MATRICULADO)
        self.assertIsNone(m.data_encerramento)
