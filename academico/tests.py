"""Testes do app acadêmico: models (Aluno/Turma/Matrícula) e CRUDs de Aluno/Turma."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from banco_questoes.models import Disciplina

from .models import Aluno, Docencia, Matricula, PerfilProfessor, Turma

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
        cls.seg = Disciplina.objects.get(nome='Segurança da Informação (SEG)')

    def _dados(self, rota, **extra):
        # Admin do Django: nome digitado; tela: disciplina (o nome vira a sigla).
        base = {'nome': 'Nome corrigido'} if rota.startswith('admin:') else {'disciplina': self.seg.pk}
        return {**base, **extra}

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
                        self._dados(rota, periodo='2027.1', turno='MANHA', ativo='on'),
                    )
                    self.turma.refresh_from_db()

                    self.assertEqual(resposta.status_code, 302)
                    self.assertEqual(self.turma.nome, 'Nome corrigido' if rota.startswith('admin:') else 'SEG')
                    self.assertEqual(self.turma.periodo, '2026.1')

    def test_turma_sem_vinculo_permite_corrigir_periodo_no_admin_e_na_tela(self):
        rotas = ('admin:academico_turma_change', 'academico:turma_update')

        for rota in rotas:
            with self.subTest(rota=rota):
                self.turma_livre.periodo = '2026.1'
                self.turma_livre.save(update_fields=['periodo'])

                dados = self._dados(rota, periodo='2026.2', turno='NOITE', ativo='on')
                if rota.startswith('admin:'):
                    dados['nome'] = self.turma_livre.nome
                resposta = self.client.post(reverse(rota, args=[self.turma_livre.pk]), dados)
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
        self.seg = Disciplina.objects.get(nome='Segurança da Informação (SEG)')

    def test_list_ok(self):
        Turma.objects.create(periodo='2026.2', nome='MAC Noite', turno=Turma.Turno.NOITE)
        resp = self.client.get(reverse('academico:turma_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'MAC Noite')
        self.assertContains(resp, '<td>Noite</td>', html=True)

    def test_create_pela_disciplina_usa_a_sigla_como_nome(self):
        resp = self.client.post(
            reverse('academico:turma_create'),
            {'periodo': '2026.2', 'disciplina': self.seg.pk, 'turno': 'NOITE', 'ativo': 'on'},
        )
        self.assertRedirects(resp, reverse('academico:turma_list'))
        turma = Turma.objects.get(periodo='2026.2', nome='SEG')
        self.assertEqual(turma.disciplina, self.seg)
        self.assertEqual(turma.turno, Turma.Turno.NOITE)
        self.assertEqual(turma.get_turno_display(), 'Noite')
        self.assertFalse(turma.docencias.exists())  # sem professor escolhido, sem docência

    def test_tela_nao_tem_mais_campo_de_nome(self):
        resp = self.client.get(reverse('academico:turma_create'))
        self.assertNotContains(resp, 'name="nome"')
        self.assertContains(resp, 'name="disciplina"')
        self.assertContains(resp, 'name="complemento"')
        self.assertContains(resp, 'name="professor"')

    def test_create_com_professor_cria_a_docencia_na_disciplina_da_turma(self):
        professor = User.objects.create_user(username='ana.souza', first_name='Ana', last_name='Souza')
        professor.groups.add(Group.objects.get(name='Docente'))
        resp = self.client.post(
            reverse('academico:turma_create'),
            {'periodo': '2026.2', 'disciplina': self.seg.pk, 'turno': 'MANHA', 'professor': professor.pk},
        )
        self.assertRedirects(resp, reverse('academico:turma_list'))
        turma = Turma.objects.get(periodo='2026.2', nome='SEG', turno=Turma.Turno.MANHA)
        self.assertTrue(Docencia.objects.filter(professor=professor, disciplina=self.seg, turma=turma).exists())

    def test_complemento_diferencia_turmas_da_mesma_disciplina(self):
        Turma.objects.create(periodo='2026.2', nome='SEG', turno=Turma.Turno.NOITE, disciplina=self.seg)
        resp = self.client.post(
            reverse('academico:turma_create'),
            {'periodo': '2026.2', 'disciplina': self.seg.pk, 'complemento': ' b ', 'turno': 'NOITE'},
        )
        self.assertRedirects(resp, reverse('academico:turma_list'))
        self.assertTrue(Turma.objects.filter(periodo='2026.2', nome='SEG-B', complemento='B').exists())

    def test_complemento_invalido_e_recusado(self):
        resp = self.client.post(
            reverse('academico:turma_create'),
            {'periodo': '2026.2', 'disciplina': self.seg.pk, 'complemento': 'A B', 'turno': 'NOITE'},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['form'].errors.get('complemento'))

    def test_disciplina_e_obrigatoria(self):
        resp = self.client.post(reverse('academico:turma_create'), {'periodo': '2026.2', 'turno': 'NOITE'})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['form'].errors.get('disciplina'))

    def test_editar_turma_antiga_sem_disciplina_passa_a_usar_a_sigla(self):
        antiga = Turma.objects.create(periodo='2026.2', nome='seg noite', turno=Turma.Turno.NOITE)
        resp = self.client.get(reverse('academico:turma_update', args=[antiga.pk]))
        self.assertNotContains(resp, 'name="professor"')  # na edição, docentes ficam na tela própria
        resp = self.client.post(
            reverse('academico:turma_update', args=[antiga.pk]),
            {'periodo': '2026.2', 'disciplina': self.seg.pk, 'turno': 'NOITE', 'ativo': 'on'},
        )
        self.assertRedirects(resp, reverse('academico:turma_list'))
        antiga.refresh_from_db()
        self.assertEqual((antiga.nome, antiga.disciplina), ('SEG', self.seg))

    def test_tela_de_docentes_ja_vem_com_a_disciplina_da_turma(self):
        turma = Turma.objects.create(periodo='2026.2', nome='SEG', turno=Turma.Turno.NOITE, disciplina=self.seg)
        resp = self.client.get(reverse('academico:turma_docencias', args=[turma.pk]))
        self.assertEqual(resp.context['form']['disciplina'].value(), self.seg.pk)

    def test_create_exige_turno_manha_ou_noite(self):
        resp = self.client.get(reverse('academico:turma_create'))
        self.assertContains(resp, 'Selecione o turno')
        self.assertContains(resp, '<option value="MANHA">Manhã</option>', html=True)
        self.assertContains(resp, '<option value="NOITE">Noite</option>', html=True)

        for turno in ('', 'TARDE'):
            with self.subTest(turno=turno):
                resp = self.client.post(
                    reverse('academico:turma_create'),
                    {'periodo': '2026.2', 'disciplina': self.seg.pk, 'turno': turno, 'ativo': 'on'},
                )
                self.assertEqual(resp.status_code, 200)
                self.assertTrue(resp.context['form'].errors.get('turno'))
        self.assertFalse(Turma.objects.filter(disciplina=self.seg).exists())

    def test_create_mesmo_nome_outro_turno_e_duplicada_no_mesmo_turno(self):
        Turma.objects.create(periodo='2026.2', nome='SEG', turno=Turma.Turno.MANHA, disciplina=self.seg)
        url = reverse('academico:turma_create')
        dados = {'periodo': '2026.2', 'disciplina': self.seg.pk, 'ativo': 'on'}

        resp = self.client.post(url, {**dados, 'turno': 'NOITE'})
        self.assertRedirects(resp, reverse('academico:turma_list'))

        resp = self.client.post(url, {**dados, 'turno': 'MANHA'})
        self.assertEqual(resp.status_code, 200)
        erros = resp.context['form'].non_field_errors()  # duplicada: mensagem no formulário, sem erro 500
        self.assertIn('preencha o complemento', erros[0])
        self.assertEqual(Turma.objects.filter(periodo='2026.2', nome='SEG').count(), 2)

    def test_create_periodo_invalido_reexibe_form(self):
        resp = self.client.post(
            reverse('academico:turma_create'),
            {'periodo': 'errado', 'disciplina': self.seg.pk, 'turno': 'NOITE', 'ativo': 'on'},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Turma.objects.filter(disciplina=self.seg).exists())

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


class LoginDoProfessorTest(TestCase):
    """Login nome.sobrenome gerado do nome completo."""

    def test_login_base(self):
        from .professores import login_base

        casos = {
            'Ana Maria de Souza': 'ana.souza',
            'José Antônio da Conceição': 'jose.conceicao',
            'João': 'joao',
            '  maria   DA  silva ': 'maria.silva',
            "Pedro D'Ávila": 'pedro.davila',
            'Luiz dos Santos e Silva': 'luiz.silva',
        }
        for nome, esperado in casos.items():
            with self.subTest(nome=nome):
                self.assertEqual(login_base(nome), esperado)

    def test_login_repetido_ganha_numero(self):
        from .professores import gerar_login

        User.objects.create_user(username='Ana.Souza')
        self.assertEqual(gerar_login('Ana Souza'), 'ana.souza2')
        User.objects.create_user(username='ana.souza2')
        self.assertEqual(gerar_login('Ana Lima Souza'), 'ana.souza3')

    def test_senha_temporaria_e_diferente_a_cada_vez(self):
        from .professores import gerar_senha_temporaria

        senhas = {gerar_senha_temporaria() for _ in range(20)}
        self.assertEqual(len(senhas), 20)
        self.assertTrue(all(len(s) == 10 for s in senhas))


class ProfessorCrudViewTest(TestCase):
    """Acadêmico → Professores: só o administrador; senha temporária mostrada uma vez."""

    def setUp(self):
        self.admin = User.objects.create_user(username='adm_prof', password='segredo123')  # noqa: S106
        self.admin.groups.add(Group.objects.get_or_create(name='Administrador')[0])
        self.client.force_login(self.admin)

    def _criar(self, nome='Ana Maria de Souza', email='Ana@Faeterj.edu.br'):
        resp = self.client.post(reverse('academico:professor_create'), {'nome_completo': nome, 'email': email})
        self.assertRedirects(resp, reverse('academico:professor_credenciais'), fetch_redirect_response=False)
        return self.client.get(reverse('academico:professor_credenciais'))

    def test_cadastro_gera_login_senha_temporaria_e_grupo_docente(self):
        resp = self._criar()
        professor = User.objects.get(username='ana.souza')
        senha = resp.context['senha']
        self.assertContains(resp, 'ana.souza')
        self.assertContains(resp, senha)
        self.assertEqual((professor.first_name, professor.last_name), ('Ana', 'Maria de Souza'))
        self.assertEqual(professor.email, 'ana@faeterj.edu.br')
        self.assertTrue(professor.check_password(senha))
        self.assertTrue(professor.groups.filter(name='Docente').exists())
        self.assertTrue(professor.perfil_professor.troca_senha_pendente)

    def test_senha_aparece_uma_unica_vez(self):
        self._criar()
        self.assertNotIn('professor_credenciais', self.client.session)
        resp = self.client.get(reverse('academico:professor_credenciais'))
        self.assertRedirects(resp, reverse('academico:professor_list'))

    def test_lista_mostra_aguardando_primeiro_acesso(self):
        self._criar()
        resp = self.client.get(reverse('academico:professor_list'))
        self.assertContains(resp, 'Aguardando 1º acesso')
        self.assertEqual(resp.context['metricas']['aguardando'], 1)

    def test_editar_muda_nome_e_email_mas_nao_o_login(self):
        self._criar()
        professor = User.objects.get(username='ana.souza')
        resp = self.client.post(
            reverse('academico:professor_update', args=[professor.pk]),
            {'nome_completo': 'Ana Paula Souza', 'email': ''},
        )
        self.assertRedirects(resp, reverse('academico:professor_list'))
        professor.refresh_from_db()
        self.assertEqual(
            (professor.username, professor.first_name, professor.last_name), ('ana.souza', 'Ana', 'Paula Souza')
        )

    def test_redefinir_senha_gera_outra_e_volta_a_exigir_troca(self):
        senha_antiga = self._criar().context['senha']
        professor = User.objects.get(username='ana.souza')
        PerfilProfessor.objects.filter(usuario=professor).update(troca_senha_pendente=False)

        self.client.post(reverse('academico:professor_redefinir_senha', args=[professor.pk]))
        nova = self.client.get(reverse('academico:professor_credenciais')).context['senha']

        professor.refresh_from_db()
        self.assertNotEqual(nova, senha_antiga)
        self.assertTrue(professor.check_password(nova))
        self.assertFalse(professor.check_password(senha_antiga))
        self.assertTrue(professor.perfil_professor.troca_senha_pendente)

    def test_desativar_e_reativar(self):
        self._criar()
        professor = User.objects.get(username='ana.souza')
        self.client.post(reverse('academico:professor_toggle_ativo', args=[professor.pk]))
        professor.refresh_from_db()
        self.assertFalse(professor.is_active)
        self.client.post(reverse('academico:professor_toggle_ativo', args=[professor.pk]))
        professor.refresh_from_db()
        self.assertTrue(professor.is_active)

    def test_contas_de_administracao_ficam_fora_da_aba(self):
        # Admin/superusuário que também está no grupo Docente não aparece nem pode ter a senha
        # redefinida ou ser desativado por aqui; o coordenador que dá aula continua na lista.
        docente = Group.objects.get(name='Docente')
        self.admin.groups.add(docente)
        raiz = User.objects.create_superuser('raiz_prof', 'r@exemplo.com', 'segredo123')  # noqa: S106
        raiz.groups.add(docente)
        coord = User.objects.create_user(username='coord.prof', password='segredo123')  # noqa: S106
        coord.groups.add(docente, Group.objects.get(name='Coordenador'))

        resp = self.client.get(reverse('academico:professor_list'))
        self.assertEqual({u.username for u in resp.context['professores']}, {'coord.prof'})
        self.assertEqual(resp.context['metricas']['total'], 1)

        for conta in (self.admin, raiz):
            for rota in ('academico:professor_redefinir_senha', 'academico:professor_toggle_ativo'):
                with self.subTest(conta=conta.username, rota=rota):
                    self.assertEqual(self.client.post(reverse(rota, args=[conta.pk])).status_code, 404)
            conta.refresh_from_db()
            self.assertTrue(conta.is_active)
            self.assertTrue(conta.check_password('segredo123'))

    def test_so_o_administrador_acessa(self):
        docente = User.objects.create_user(username='doc_sem_admin', password='segredo123')  # noqa: S106
        docente.groups.add(Group.objects.get(name='Docente'))
        self.client.force_login(docente)
        for rota in ('academico:professor_list', 'academico:professor_create'):
            with self.subTest(rota=rota):
                self.assertEqual(self.client.get(reverse(rota)).status_code, 403)
        self.client.logout()
        resp = self.client.get(reverse('academico:professor_list'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse('login'), resp['Location'])


class TrocaDeSenhaObrigatoriaTest(TestCase):
    """No primeiro login com a senha temporária, só a tela de trocar a senha abre."""

    NOVA = 'Examina#Forte2026'  # noqa: S105

    def setUp(self):
        from .professores import criar_professor

        self.professor, self.temporaria = criar_professor('Carla Mendes')

    def _entrar(self):
        resp = self.client.post(reverse('login'), {'username': 'carla.mendes', 'password': self.temporaria})
        self.assertEqual(resp.status_code, 302)

    def test_qualquer_pagina_leva_para_a_troca_de_senha(self):
        self._entrar()
        for rota in ('banco_questoes:questao_list', 'provas:index', 'academico:turma_list'):
            with self.subTest(rota=rota):
                self.assertRedirects(self.client.get(reverse(rota)), reverse('password_change'))
        resp = self.client.get(reverse('password_change'))
        self.assertContains(resp, 'Primeiro acesso')
        self.assertNotContains(resp, '>Cancelar<')

    def test_nova_senha_nao_pode_ser_a_temporaria(self):
        self._entrar()
        resp = self.client.post(
            reverse('password_change'),
            {'old_password': self.temporaria, 'new_password1': self.temporaria, 'new_password2': self.temporaria},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn('diferente da atual', str(resp.context['form'].errors))

    def test_trocar_a_senha_libera_o_sistema(self):
        self._entrar()
        resp = self.client.post(
            reverse('password_change'),
            {'old_password': self.temporaria, 'new_password1': self.NOVA, 'new_password2': self.NOVA},
        )
        self.assertRedirects(resp, reverse('password_change_done'))
        self.professor.perfil_professor.refresh_from_db()
        self.assertFalse(self.professor.perfil_professor.troca_senha_pendente)
        self.assertEqual(self.client.get(reverse('banco_questoes:questao_list')).status_code, 200)

    def test_sair_continua_liberado(self):
        self._entrar()
        resp = self.client.post(reverse('logout'))
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_contas_antigas_sem_perfil_nao_sao_afetadas(self):
        antigo = User.objects.create_user(username='prof_antigo', password='segredo123')  # noqa: S106
        antigo.groups.add(Group.objects.get(name='Docente'))
        self.client.force_login(antigo)
        self.assertEqual(self.client.get(reverse('banco_questoes:questao_list')).status_code, 200)


class LigarTurmasADisciplinaMigrationTest(TestCase):
    """A migration 0006 liga as turmas antigas à disciplina pela sigla do começo do nome."""

    def test_liga_pela_sigla_e_deixa_sem_disciplina_o_que_nao_reconhece(self):
        import importlib

        from django.apps import apps

        migracao = importlib.import_module('academico.migrations.0006_ligar_turmas_a_disciplina')
        fac = Turma.objects.create(periodo='2026.1', nome='FAC NOITE')
        mac = Turma.objects.create(periodo='2026.1', nome='mac Manhã')
        outra = Turma.objects.create(periodo='2026.1', nome='Turma livre')
        curso = Turma.objects.create(periodo='2026.1', nome='ADS-3A')  # curso + período, não a disciplina ADS

        migracao.ligar_turmas(apps, None)

        for turma in (fac, mac, outra, curso):
            turma.refresh_from_db()
        self.assertEqual(fac.disciplina.sigla, 'FAC')
        self.assertEqual(mac.disciplina.sigla, 'MAC')
        self.assertIsNone(outra.disciplina)
        self.assertIsNone(curso.disciplina)
        self.assertEqual(fac.nome, 'FAC NOITE')  # o nome só muda ao editar na tela
