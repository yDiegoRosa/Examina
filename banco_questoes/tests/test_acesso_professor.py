"""
Acesso por perfil (pedido de 2026-10-17): o professor vê e altera só o que é dele; o
coordenador vê tudo; só o administrador gerencia cadastros e o Simulado ENADE.

Cenário: professora A leciona D1 na turma T1; professor B leciona D2 na turma T2.
Objeto de outro professor responde 404 (não revela que existe).
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from academico.acesso import disciplinas_permitidas, filtro_turma_disciplina, pode_aplicar, turmas_permitidas
from academico.models import Aluno, Docencia, Matricula, Turma
from banco_questoes.models import Disciplina, Questao, Topico


class CenarioAcesso(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        docente = Group.objects.get(name='Docente')
        cls.admin = user_model.objects.create_user(username='admin_inst', password='x')  # noqa: S106
        cls.admin.groups.add(Group.objects.get_or_create(name='Administrador')[0])
        cls.coord = user_model.objects.create_user(username='coord_inst', password='x')  # noqa: S106
        cls.coord.groups.add(Group.objects.get(name='Coordenador'))
        cls.prof_a = user_model.objects.create_user(username='prof_a', password='x')  # noqa: S106
        cls.prof_a.groups.add(docente)
        cls.prof_b = user_model.objects.create_user(username='prof_b', password='x')  # noqa: S106
        cls.prof_b.groups.add(docente)

        cls.d1 = Disciplina.objects.create(nome='Disciplina Um')
        cls.d2 = Disciplina.objects.create(nome='Disciplina Dois')
        cls.top1 = Topico.objects.create(disciplina=cls.d1, nome='Tópico Um')
        cls.top2 = Topico.objects.create(disciplina=cls.d2, nome='Tópico Dois')
        cls.t1 = Turma.objects.create(periodo='2026.2', nome='T1', turno=Turma.Turno.NOITE)
        cls.t2 = Turma.objects.create(periodo='2026.2', nome='T2', turno=Turma.Turno.MANHA)
        Docencia.objects.create(professor=cls.prof_a, disciplina=cls.d1, turma=cls.t1)
        Docencia.objects.create(professor=cls.prof_b, disciplina=cls.d2, turma=cls.t2)

        cls.aluno1 = Aluno.objects.create(nome='Aluno da T1', matricula='M-T1')
        cls.aluno2 = Aluno.objects.create(nome='Aluno da T2', matricula='M-T2')
        Matricula.objects.create(aluno=cls.aluno1, turma=cls.t1)
        Matricula.objects.create(aluno=cls.aluno2, turma=cls.t2)

    def _questao(self, autor, disciplina=None, topico=None, enunciado='Enunciado', tipo='DIS'):
        return Questao.objects.create(
            disciplina=disciplina or self.d1,
            topico=topico or self.top1,
            tipo=tipo,
            dificuldade='F',
            enunciado=enunciado,
            autor=autor,
        )


class RegrasDeAcessoTest(CenarioAcesso):
    def test_disciplinas_e_turmas_do_professor(self):
        self.assertEqual(list(disciplinas_permitidas(self.prof_a)), [self.d1])
        self.assertEqual(list(turmas_permitidas(self.prof_a)), [self.t1])
        for quem_ve_tudo in (self.admin, self.coord):
            self.assertEqual(set(turmas_permitidas(quem_ve_tudo)), {self.t1, self.t2})
            self.assertTrue({self.d1, self.d2} <= set(disciplinas_permitidas(quem_ve_tudo)))

    def test_docencia_encerrada_nao_vale(self):
        Docencia.objects.filter(professor=self.prof_a).update(ativo=False)
        self.assertFalse(turmas_permitidas(self.prof_a).exists())
        self.assertFalse(disciplinas_permitidas(self.prof_a).exists())

    def test_pode_aplicar_so_a_propria_prova_na_turma_da_docencia(self):
        from provas.models import Prova

        prova_a = Prova.objects.create(titulo='Prova A', disciplina=self.d1, autor=self.prof_a)
        self.assertTrue(pode_aplicar(self.prof_a, prova_a, self.t1))
        self.assertFalse(pode_aplicar(self.prof_a, prova_a, self.t2))  # não leciona na T2
        self.assertFalse(pode_aplicar(self.prof_b, prova_a, self.t2))  # prova de outro professor
        self.assertTrue(pode_aplicar(self.admin, prova_a, self.t2))

    def test_filtro_de_desempenho_pelos_pares_turma_disciplina(self):
        self.assertIsNone(filtro_turma_disciplina(self.coord, 'turma', 'disciplina'))
        filtro = filtro_turma_disciplina(self.prof_a, 'turma', 'disciplina')
        self.assertEqual(
            list(Docencia.objects.filter(filtro).values_list('turma', 'disciplina')), [(self.t1.pk, self.d1.pk)]
        )
        sem_docencia = get_user_model().objects.create_user(username='sem_docencia', password='x')  # noqa: S106
        self.assertFalse(Docencia.objects.filter(filtro_turma_disciplina(sem_docencia, 'turma', 'disciplina')).exists())


class AcademicoPorPerfilTest(CenarioAcesso):
    def test_professor_consulta_so_a_turma_e_os_alunos_dele(self):
        self.client.force_login(self.prof_a)
        turmas = self.client.get(reverse('academico:turma_list'))
        self.assertEqual(list(turmas.context['turmas']), [self.t1])
        alunos = self.client.get(reverse('academico:aluno_list'))
        self.assertEqual(list(alunos.context['alunos']), [self.aluno1])
        self.assertNotContains(alunos, reverse('academico:aluno_create'))

        self.assertEqual(self.client.get(reverse('academico:turma_matriculas', args=[self.t1.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('academico:turma_matriculas', args=[self.t2.pk])).status_code, 404)

    def test_professor_e_coordenador_nao_cadastram(self):
        for usuario in (self.prof_a, self.coord):
            self.client.force_login(usuario)
            self.assertEqual(self.client.get(reverse('academico:aluno_create')).status_code, 403)
            self.assertEqual(self.client.get(reverse('academico:turma_create')).status_code, 403)
            self.assertEqual(self.client.get(reverse('academico:turma_docencias', args=[self.t1.pk])).status_code, 403)
            resposta = self.client.post(
                reverse('academico:turma_matricular', args=[self.t1.pk]), {'alunos': [self.aluno2.pk]}
            )
            self.assertEqual(resposta.status_code, 403)
        self.assertFalse(Matricula.objects.filter(aluno=self.aluno2, turma=self.t1).exists())

    def test_coordenador_consulta_todas_as_turmas(self):
        self.client.force_login(self.coord)
        turmas = self.client.get(reverse('academico:turma_list'))
        self.assertEqual(set(turmas.context['turmas']), {self.t1, self.t2})

    def test_administrador_cadastra_e_encerra_docencia(self):
        self.client.force_login(self.admin)
        url = reverse('academico:turma_docencias', args=[self.t2.pk])
        self.assertContains(self.client.get(url), 'prof_b')
        resposta = self.client.post(url, {'professor': self.prof_a.pk, 'disciplina': self.d2.pk})
        self.assertRedirects(resposta, url)
        docencia = Docencia.objects.get(professor=self.prof_a, disciplina=self.d2, turma=self.t2)
        self.assertIn(self.t2, turmas_permitidas(self.prof_a))

        self.client.post(reverse('academico:turma_docencia_toggle', args=[self.t2.pk]), {'docencia': docencia.pk})
        docencia.refresh_from_db()
        self.assertFalse(docencia.ativo)
        self.assertNotIn(self.t2, turmas_permitidas(self.prof_a))


class BancoDeQuestoesPorPerfilTest(CenarioAcesso):
    def setUp(self):
        self.q_a = self._questao(self.prof_a, enunciado='Questão da professora A')
        self.q_b = self._questao(self.prof_b, self.d2, self.top2, enunciado='Questão do professor B')

    def test_professor_lista_e_exporta_so_as_dele(self):
        self.client.force_login(self.prof_a)
        lista = self.client.get(reverse('banco_questoes:questao_list'))
        self.assertEqual([q.pk for q in lista.context['questoes']], [self.q_a.pk])
        self.assertEqual(lista.context['metricas']['total'], 1)
        self.assertNotContains(lista, 'Questão do professor B')

        csv = self.client.get(reverse('banco_questoes:download_modelo_csv')).content.decode('utf-8-sig')
        self.assertIn('Questão da professora A', csv)
        self.assertNotIn('Questão do professor B', csv)

    def test_professor_nao_abre_edita_nem_inativa_questao_de_outro(self):
        self.client.force_login(self.prof_a)
        for rota in ('questao_update', 'questao_history'):
            self.assertEqual(self.client.get(reverse(f'banco_questoes:{rota}', args=[self.q_b.pk])).status_code, 404)
        self.assertEqual(
            self.client.post(reverse('banco_questoes:questao_toggle_ativo', args=[self.q_b.pk])).status_code, 404
        )
        self.q_b.refresh_from_db()
        self.assertTrue(self.q_b.ativo)

        # A própria ele edita e inativa.
        self.assertEqual(self.client.get(reverse('banco_questoes:questao_update', args=[self.q_a.pk])).status_code, 200)
        self.client.post(reverse('banco_questoes:questao_toggle_ativo', args=[self.q_a.pk]))
        self.q_a.refresh_from_db()
        self.assertFalse(self.q_a.ativo)

    def test_coordenador_ve_tudo_mas_so_altera_as_dele(self):
        self.client.force_login(self.coord)
        lista = self.client.get(reverse('banco_questoes:questao_list'))
        self.assertEqual({q.pk for q in lista.context['questoes']}, {self.q_a.pk, self.q_b.pk})
        self.assertEqual(lista.context['ids_alteraveis'], set())
        self.assertEqual(
            self.client.get(reverse('banco_questoes:questao_history', args=[self.q_b.pk])).status_code, 200
        )
        self.assertEqual(self.client.get(reverse('banco_questoes:questao_update', args=[self.q_b.pk])).status_code, 403)

    def test_administrador_ve_e_altera_todas(self):
        self.client.force_login(self.admin)
        lista = self.client.get(reverse('banco_questoes:questao_list'))
        self.assertEqual(lista.context['ids_alteraveis'], {self.q_a.pk, self.q_b.pk})
        self.client.post(reverse('banco_questoes:questao_toggle_ativo', args=[self.q_b.pk]))
        self.q_b.refresh_from_db()
        self.assertFalse(self.q_b.ativo)


class ProvasEMascarasPorPerfilTest(CenarioAcesso):
    def setUp(self):
        from provas.models import Mascara, MascaraCriterioTopico, Prova, ProvaQuestao

        self.q_a1 = self._questao(self.prof_a, enunciado='Discursiva A1')
        self.q_a2 = self._questao(self.prof_a, enunciado='Discursiva A2')
        self.q_admin = self._questao(self.admin, enunciado='Discursiva do administrador')
        self.q_b = self._questao(self.prof_b, self.d2, self.top2, enunciado='Discursiva B')

        def mascara(autor, disciplina, topico, quantidade=1):
            m = Mascara.objects.create(
                titulo=f'Máscara de {autor.username}',
                disciplina=disciplina,
                quantidade_questoes=quantidade,
                modalidade=Mascara.Modalidade.DISCURSIVA,
                percentual_facil=100,
                percentual_medio=0,
                percentual_dificil=0,
                autor=autor,
            )
            MascaraCriterioTopico.objects.create(mascara=m, topico=topico, percentual=100)
            return m

        self.mascara = mascara
        self.m_a = mascara(self.prof_a, self.d1, self.top1)
        self.m_b = mascara(self.prof_b, self.d2, self.top2)
        self.p_a = Prova.objects.create(titulo='Prova da A', disciplina=self.d1, autor=self.prof_a)
        self.item_a = ProvaQuestao.objects.create(
            prova=self.p_a, questao=self.q_a1, numero=1, ordem=1, snapshot_questao={'tipo': 'DIS'}
        )
        self.p_b = Prova.objects.create(titulo='Prova do B', disciplina=self.d2, autor=self.prof_b)

    def test_listas_mostram_so_as_dele(self):
        self.client.force_login(self.prof_a)
        provas = self.client.get(reverse('provas:prova_list'))
        self.assertEqual([p.pk for p in provas.context['provas']], [self.p_a.pk])
        mascaras = self.client.get(reverse('provas:mascara_list'))
        self.assertEqual([m.pk for m in mascaras.context['mascaras']], [self.m_a.pk])

    def test_prova_e_mascara_de_outro_professor_dao_404(self):
        self.client.force_login(self.prof_a)
        for nome, args in (
            ('prova_revisao', [self.p_b.pk]),
            ('prova_pdf', [self.p_b.pk]),
            ('prova_capa_pdf', [self.p_b.pk]),
            ('prova_gabarito_pdf', [self.p_b.pk]),
            ('mascara_detail', [self.m_b.pk]),
            ('mascara_update', [self.m_b.pk]),
        ):
            with self.subTest(rota=nome):
                self.assertEqual(self.client.get(reverse(f'provas:{nome}', args=args)).status_code, 404)
        for nome, args in (
            ('mascara_gerar_prova', [self.m_b.pk]),
            ('mascara_toggle_ativo', [self.m_b.pk]),
            ('prova_gerar_versoes', [self.p_b.pk]),
        ):
            with self.subTest(rota=nome):
                self.assertEqual(self.client.post(reverse(f'provas:{nome}', args=args)).status_code, 404)

    def test_coordenador_ve_mas_nao_altera(self):
        self.client.force_login(self.coord)
        self.assertEqual(self.client.get(reverse('provas:prova_revisao', args=[self.p_b.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('provas:mascara_update', args=[self.m_b.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse('provas:prova_gerar_versoes', args=[self.p_b.pk])).status_code, 403)

    def test_geracao_por_mascara_usa_so_as_questoes_do_professor(self):
        from provas.models import Prova

        self.client.force_login(self.prof_a)
        # Pede 3 discursivas fáceis de T1: a professora tem 2; com a do administrador daria 3.
        m3 = self.mascara(self.prof_a, self.d1, self.top1, quantidade=3)
        self.client.post(reverse('provas:mascara_gerar_prova', args=[m3.pk]))
        self.assertFalse(Prova.objects.filter(mascara=m3).exists())

        self.client.post(reverse('provas:mascara_gerar_prova', args=[self.m_a.pk]))
        prova = Prova.objects.get(mascara=self.m_a)
        self.assertEqual(prova.autor, self.prof_a)
        self.assertIn(prova.itens.get().questao, {self.q_a1, self.q_a2})

    def test_montagem_manual_so_com_as_questoes_dele(self):
        from provas.models import Prova

        self.client.force_login(self.prof_a)
        url = reverse('provas:prova_manual_create')
        tela = self.client.get(url, {'disciplina': self.d1.pk})
        self.assertEqual({q.pk for q in tela.context['questoes_disponiveis']}, {self.q_a1.pk, self.q_a2.pk})
        self.assertEqual(list(tela.context['form'].fields['disciplina'].queryset), [self.d1])

        self.client.post(
            url,
            {
                'titulo': 'Manual com questão alheia',
                'disciplina': self.d1.pk,
                'questoes_selecionadas': [self.q_admin.pk],
            },
        )
        self.assertFalse(Prova.objects.filter(titulo='Manual com questão alheia').exists())

    def test_troca_de_questao_so_entre_as_dele(self):
        self.client.force_login(self.prof_a)
        candidatas = self.client.get(reverse('provas:item_candidatas_substituicao', args=[self.item_a.pk])).json()
        self.assertEqual([c['id'] for c in candidatas['candidatas']], [self.q_a2.pk])
        resposta = self.client.post(
            reverse('provas:item_substituir_questao', args=[self.item_a.pk]), {'nova_questao_id': self.q_admin.pk}
        )
        self.assertEqual(resposta.status_code, 404)
        self.item_a.refresh_from_db()
        self.assertEqual(self.item_a.questao, self.q_a1)

    def test_professor_so_cria_mascara_das_disciplinas_dele(self):
        from provas.forms import MascaraForm

        form = MascaraForm(usuario=self.prof_a)
        self.assertEqual(list(form.fields['disciplina'].queryset), [self.d1])
        self.assertTrue(form.fields['disciplina'].required)

    def test_simulado_enade_so_do_administrador(self):
        from provas.forms import MascaraForm
        from provas.models import Mascara

        self.client.force_login(self.prof_a)
        self.assertEqual(self.client.post(reverse('provas:mascara_enade_create')).status_code, 403)
        tela = self.client.get(reverse('provas:mascara_create'), {'preset': 'enade'})
        self.assertNotIn('ENADE', str(tela.context['form'].initial.get('titulo', '')))
        form = MascaraForm(data={'titulo': 'Simulado ENADE da turma', 'priorizar_enade': 'on'}, usuario=self.prof_a)
        self.assertIn('Só o administrador cria o Simulado ENADE', str(form.errors.get('titulo')))

        # Máscara ENADE do administrador: nem o coordenador (que vê tudo) gera a prova.
        enade = Mascara.objects.create(
            titulo='Simulado ENADE — CE', priorizar_enade=True, quantidade_questoes=1, autor=self.admin
        )
        self.client.force_login(self.coord)
        self.assertEqual(self.client.post(reverse('provas:mascara_gerar_prova', args=[enade.pk])).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse('provas:mascara_enade_create')).status_code, 302)
