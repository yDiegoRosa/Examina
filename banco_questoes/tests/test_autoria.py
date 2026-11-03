"""
Autoria das questões (pedido de 2026-11-02). O professor só vê e usa as questões de que é
autor, então o administrador precisa de dois recursos:

- importar "em nome de" um professor (as questões já entram no acervo dele);
- transferir em lote as que entraram com o login errado (marcadas ou todas do filtro).
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from banco_questoes.models import Disciplina, Questao, Topico

from .test_importacao_zip import ComMidiaTemporaria, linha, pacote, planilha

URL_IMPORTAR = reverse('banco_questoes:questao_import')
URL_TRANSFERIR = reverse('banco_questoes:questao_transferir_autor')
URL_LISTA = reverse('banco_questoes:questao_list')


def criar_usuarios(cls):
    user_model = get_user_model()
    cls.admin = user_model.objects.create_user(username='admin.inst', password='x')  # noqa: S106
    cls.admin.groups.add(Group.objects.get_or_create(name='Administrador')[0])
    cls.coord = user_model.objects.create_user(username='coord.inst', password='x')  # noqa: S106
    cls.coord.groups.add(Group.objects.get(name='Coordenador'))
    cls.prof = user_model.objects.create_user(
        username='ana.souza',
        password='x',  # noqa: S106
        first_name='Ana',
        last_name='Souza',
    )
    cls.prof.groups.add(Group.objects.get(name='Docente'))
    cls.outro_prof = user_model.objects.create_user(username='bruno.lima', password='x')  # noqa: S106
    cls.outro_prof.groups.add(Group.objects.get(name='Docente'))
    # Conta sem perfil de professor/coordenação/administração: não pode receber questões.
    cls.sem_perfil = user_model.objects.create_user(username='visitante', password='x')  # noqa: S106


def arquivo_zip(*enunciados):
    conteudo = pacote({'questoes.xlsx': planilha(*(linha(e) for e in enunciados))})
    return SimpleUploadedFile('questoes.zip', conteudo, 'application/zip')


class ImportarEmNomeDeTest(ComMidiaTemporaria):
    @classmethod
    def setUpTestData(cls):
        criar_usuarios(cls)

    def test_administrador_ve_o_campo_autor(self):
        self.client.force_login(self.admin)
        tela = self.client.get(URL_IMPORTAR)
        self.assertContains(tela, 'name="autor"')
        self.assertContains(tela, 'Eu mesmo')
        self.assertContains(tela, 'Ana Souza (ana.souza)')
        self.assertNotContains(tela, '>visitante<')

    def test_professor_nao_ve_o_campo_autor(self):
        self.client.force_login(self.prof)
        tela = self.client.get(URL_IMPORTAR)
        self.assertEqual(tela.status_code, 200)
        self.assertNotContains(tela, 'name="autor"')

    def test_administrador_importa_em_nome_do_professor(self):
        self.client.force_login(self.admin)
        resposta = self.client.post(
            URL_IMPORTAR, {'arquivo': arquivo_zip('Em nome da Ana 1', 'Em nome da Ana 2'), 'autor': self.prof.pk}
        )
        self.assertContains(resposta, 'em nome de Ana Souza')
        questoes = Questao.objects.filter(enunciado__startswith='Em nome da Ana')
        self.assertEqual(questoes.count(), 2)
        self.assertEqual({q.autor for q in questoes}, {self.prof})

        # E a professora passa a vê-las na lista dela.
        self.client.force_login(self.prof)
        self.assertContains(self.client.get(URL_LISTA), 'Em nome da Ana 1')

    def test_sem_escolher_o_autor_e_quem_importa(self):
        self.client.force_login(self.admin)
        self.client.post(URL_IMPORTAR, {'arquivo': arquivo_zip('Do próprio admin'), 'autor': ''})
        self.assertEqual(Questao.objects.get(enunciado='Do próprio admin').autor, self.admin)

    def test_professor_nao_consegue_importar_em_nome_de_outro(self):
        self.client.force_login(self.prof)
        self.client.post(URL_IMPORTAR, {'arquivo': arquivo_zip('Tentativa'), 'autor': self.outro_prof.pk})
        self.assertEqual(Questao.objects.get(enunciado='Tentativa').autor, self.prof)


class TransferirAutoriaTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        criar_usuarios(cls)
        cls.disc = Disciplina.objects.create(nome='Disciplina da transferência')
        cls.outra_disc = Disciplina.objects.create(nome='Outra disciplina da transferência')
        cls.topico = Topico.objects.create(disciplina=cls.disc, nome='Tópico T')
        cls.outro_topico = Topico.objects.create(disciplina=cls.outra_disc, nome='Tópico O')

    def setUp(self):
        self.q1 = self._questao('Q1', self.admin)
        self.q2 = self._questao('Q2', self.admin)
        self.q3 = self._questao('Q3', self.admin, disciplina=self.outra_disc)
        self.q_outro = self._questao('Q do Bruno', self.outro_prof)

    def _questao(self, enunciado, autor, disciplina=None):
        disciplina = disciplina or self.disc
        return Questao.objects.create(
            disciplina=disciplina,
            topico=self.topico if disciplina == self.disc else self.outro_topico,
            tipo='DIS',
            dificuldade='F',
            enunciado=enunciado,
            autor=autor,
        )

    def _autores(self):
        return {q.enunciado: q.autor.username for q in Questao.objects.all()}

    def test_transfere_as_questoes_marcadas(self):
        self.client.force_login(self.admin)
        resposta = self.client.post(
            URL_TRANSFERIR, {'novo_autor': self.prof.pk, 'questoes': [self.q1.pk, self.q3.pk]}, follow=True
        )
        self.assertContains(resposta, '2 questão(ões) transferida(s) para Ana Souza')
        self.assertEqual(
            self._autores(),
            {'Q1': 'ana.souza', 'Q2': 'admin.inst', 'Q3': 'ana.souza', 'Q do Bruno': 'bruno.lima'},
        )

    def test_historico_registra_quem_transferiu_e_o_motivo(self):
        self.client.force_login(self.admin)
        self.client.post(URL_TRANSFERIR, {'novo_autor': self.prof.pk, 'questoes': [self.q1.pk]})
        ultima = self.q1.history.order_by('-history_date').first()
        self.assertEqual(ultima.history_user, self.admin)
        self.assertEqual(ultima.history_change_reason, 'Autoria transferida para ana.souza')
        self.assertEqual(ultima.autor, self.prof)

        tela = self.client.get(reverse('banco_questoes:questao_history', args=[self.q1.pk]))
        self.assertContains(tela, 'Autoria transferida para ana.souza')

    def test_transfere_todas_do_filtro_atual(self):
        self.client.force_login(self.admin)
        filtros = f'autor={self.admin.pk}&disciplina={self.disc.pk}'
        resposta = self.client.post(
            URL_TRANSFERIR, {'novo_autor': self.prof.pk, 'todas_do_filtro': '1', 'filtros': filtros}
        )
        self.assertRedirects(resposta, f'{URL_LISTA}?{filtros}', fetch_redirect_response=False)
        # Só as do admin na disciplina filtrada (Q3 é de outra disciplina; a do Bruno, de outro autor).
        self.assertEqual(
            self._autores(),
            {'Q1': 'ana.souza', 'Q2': 'ana.souza', 'Q3': 'admin.inst', 'Q do Bruno': 'bruno.lima'},
        )

    def test_todas_sem_filtro_nao_transfere_o_acervo_inteiro(self):
        self.client.force_login(self.admin)
        antes = self._autores()
        resposta = self.client.post(
            URL_TRANSFERIR, {'novo_autor': self.prof.pk, 'todas_do_filtro': '1', 'filtros': ''}, follow=True
        )
        self.assertContains(resposta, 'filtre a lista antes')
        self.assertEqual(self._autores(), antes)

    def test_sem_questao_marcada_nada_muda(self):
        self.client.force_login(self.admin)
        antes = self._autores()
        resposta = self.client.post(URL_TRANSFERIR, {'novo_autor': self.prof.pk}, follow=True)
        self.assertContains(resposta, 'Marque ao menos uma questão')
        self.assertEqual(self._autores(), antes)

    def test_destino_precisa_ser_professor_coordenacao_ou_administracao(self):
        self.client.force_login(self.admin)
        antes = self._autores()
        resposta = self.client.post(
            URL_TRANSFERIR, {'novo_autor': self.sem_perfil.pk, 'questoes': [self.q1.pk]}, follow=True
        )
        self.assertContains(resposta, 'Escolha o professor')
        self.assertEqual(self._autores(), antes)

    def test_questoes_que_ja_sao_do_destino_nao_contam(self):
        self.client.force_login(self.admin)
        resposta = self.client.post(
            URL_TRANSFERIR, {'novo_autor': self.outro_prof.pk, 'questoes': [self.q_outro.pk]}, follow=True
        )
        self.assertContains(resposta, 'Nenhuma questão alterada')
        self.assertEqual(self.q_outro.history.count(), 1)

    def test_so_o_administrador_transfere(self):
        for usuario in (self.coord, self.prof, self.outro_prof):
            self.client.force_login(usuario)
            resposta = self.client.post(URL_TRANSFERIR, {'novo_autor': usuario.pk, 'questoes': [self.q_outro.pk]})
            self.assertEqual(resposta.status_code, 403, usuario.username)
        self.assertEqual(Questao.objects.get(pk=self.q_outro.pk).autor, self.outro_prof)

    def test_get_nao_e_aceito(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(URL_TRANSFERIR).status_code, 405)

    def test_barra_de_transferencia_so_para_o_administrador(self):
        self.client.force_login(self.admin)
        tela = self.client.get(URL_LISTA)
        self.assertContains(tela, 'id="form-transferir"')
        self.assertContains(tela, f'name="questoes" value="{self.q1.pk}"')

        self.client.force_login(self.coord)
        tela = self.client.get(URL_LISTA)
        self.assertEqual(tela.status_code, 200)
        self.assertNotContains(tela, 'id="form-transferir"')
        self.assertNotContains(tela, 'name="questoes"')

    def test_opcao_todas_do_filtro_so_aparece_com_filtro(self):
        self.client.force_login(self.admin)
        self.assertNotContains(self.client.get(URL_LISTA), 'name="todas_do_filtro"')
        tela = self.client.get(URL_LISTA, {'autor': self.admin.pk})
        self.assertContains(tela, 'name="todas_do_filtro"')
        self.assertContains(tela, 'Todas as 3 do filtro atual')
        self.assertContains(tela, f'name="filtros" value="autor={self.admin.pk}"')
