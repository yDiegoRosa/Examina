"""Testes de nível de projeto: mídia autenticada, entradas inválidas e páginas de erro."""

import tempfile
from datetime import date
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.views.defaults import server_error

from academico.models import Aluno, Matricula, Turma
from banco_questoes.models import Disciplina, Questao
from banco_questoes.utils import parse_pk
from provas.models import FolhaDigitalizada, Prova, ProvaAplicacao, ProvaQuestao

MEDIA_TMP = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=MEDIA_TMP)
class MediaProtegidaTest(TestCase):
    """/media/ não é público: só Docente/Coordenador baixam folhas escaneadas e imagens."""

    def setUp(self):
        pasta = Path(MEDIA_TMP) / 'folhas_digitalizadas'
        pasta.mkdir(parents=True, exist_ok=True)
        (pasta / 'folha.png').write_bytes(b'conteudo-da-folha')
        self.url = '/media/folhas_digitalizadas/folha.png'

        user_model = get_user_model()
        self.docente = user_model.objects.create_user(username='doc_media', password='segredo123')  # noqa: S106
        self.docente.groups.add(Group.objects.get(name='Docente'))
        self.sem_grupo = user_model.objects.create_user(username='sem_grupo_media', password='segredo123')  # noqa: S106

    def test_anonimo_redirecionado_ao_login(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login/', resp['Location'])

    def test_usuario_sem_grupo_recebe_403(self):
        self.client.force_login(self.sem_grupo)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_docente_recebe_o_arquivo(self):
        self.client.force_login(self.docente)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(b''.join(resp.streaming_content), b'conteudo-da-folha')
        self.assertEqual(resp['Content-Type'], 'image/png')

    def test_arquivo_inexistente_404(self):
        self.client.force_login(self.docente)
        self.assertEqual(self.client.get('/media/folhas_digitalizadas/nao-existe.png').status_code, 404)

    def test_caminho_fora_da_midia_recusado(self):
        self.client.force_login(self.docente)
        resp = self.client.get('/media/..%2F..%2Fmanage.py')
        self.assertIn(resp.status_code, (400, 404))
        self.assertNotIn(b'DJANGO_SETTINGS_MODULE', resp.content)


class ParsePkTest(TestCase):
    def test_aceita_so_digitos_ascii(self):
        self.assertEqual(parse_pk('42'), 42)
        self.assertEqual(parse_pk(' 7 '), 7)
        for invalido in (None, '', 'abc', '1.5', '-3', '١٢', '9' * 19):
            with self.subTest(valor=invalido):
                self.assertIsNone(parse_pk(invalido))


@override_settings(MEDIA_ROOT=MEDIA_TMP)
class EntradasInvalidasTest(TestCase):
    """IDs inválidos em GET/POST (ex.: ?turma=abc) não podem gerar erro 500."""

    def setUp(self):
        user = get_user_model().objects.create_user(username='doc_entradas', password='segredo123')  # noqa: S106
        user.groups.add(Group.objects.get(name='Docente'))
        self.client.force_login(user)

        self.disc = Disciplina.objects.create(nome='Disc Entradas')
        questao = Questao.objects.create(disciplina=self.disc, tipo='OBJ', dificuldade='F', enunciado='Q')
        prova = Prova.objects.create(titulo='Prova Entradas', disciplina=self.disc)
        self.item = ProvaQuestao.objects.create(prova=prova, questao=questao, numero=1, ordem=1)
        self.turma = Turma.objects.create(periodo='2026.2', nome='T Entradas')
        self.aluno = Aluno.objects.create(nome='Ana', matricula='E1')
        self.aplicacao = ProvaAplicacao.objects.create(prova=prova, turma=self.turma, data_aplicacao=date(2026, 11, 10))
        self.digitalizacao = FolhaDigitalizada.objects.create(
            prova_aplicacao=self.aplicacao,
            imagem=SimpleUploadedFile('f.png', b'x', content_type='image/png'),
            status=FolhaDigitalizada.Status.ERRO,
        )

    def test_filtros_get_invalidos_sao_ignorados(self):
        urls = [
            f'{reverse("provas:mascara_list")}?disciplina=abc',
            f'{reverse("provas:mascara_create")}?disciplina=abc',
            f'{reverse("provas:prova_manual_create")}?disciplina={self.disc.pk}&topico=abc',
            f'{reverse("desempenho:dashboard")}?turma=abc&aplicacao=1%27',
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_apis_com_id_invalido(self):
        resp = self.client.get(reverse('provas:api_topicos_estoque'), {'disciplina_id': 'abc'})
        self.assertEqual(resp.status_code, 400)
        resp = self.client.get(reverse('banco_questoes:api_topicos_por_disciplina'), {'disciplina_id': 'abc'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {'topicos': []})
        resp = self.client.post(
            reverse('provas:item_substituir_questao', args=[self.item.pk]),
            {'nova_questao_id': 'abc'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()['success'])

    def test_posts_com_id_invalido(self):
        resp = self.client.post(
            reverse('provas:digitalizacao_vincular_folha', args=[self.digitalizacao.pk]), {'folha_id': 'abc'}
        )
        self.assertRedirects(resp, reverse('provas:aplicacao_revisao_manual', args=[self.aplicacao.pk]))

        resp = self.client.post(
            reverse('academico:turma_matricular', args=[self.turma.pk]), {'alunos': ['abc', self.aluno.pk]}
        )
        self.assertRedirects(resp, reverse('academico:turma_matriculas', args=[self.turma.pk]))
        self.assertTrue(Matricula.objects.filter(turma=self.turma, aluno=self.aluno).exists())

        resp = self.client.post(reverse('academico:turma_desmatricular', args=[self.turma.pk]), {'matricula': 'abc'})
        self.assertEqual(resp.status_code, 404)


class PaginasDeErroTest(TestCase):
    """Páginas 403/404/500 próprias (usadas com DEBUG=False, como nos testes e em produção)."""

    def test_404_personalizada(self):
        user = get_user_model().objects.create_user(username='doc_404', password='segredo123')  # noqa: S106
        user.groups.add(Group.objects.get(name='Docente'))
        self.client.force_login(user)
        resp = self.client.get(reverse('provas:aplicacao_detail', args=[999999]))
        self.assertEqual(resp.status_code, 404)
        self.assertTemplateUsed(resp, '404.html')
        self.assertContains(resp, 'Página não encontrada', status_code=404)

    def test_403_personalizada_com_motivo(self):
        user = get_user_model().objects.create_user(username='sem_grupo_403', password='segredo123')  # noqa: S106
        self.client.force_login(user)
        resp = self.client.get(reverse('provas:aplicacao_list'))
        self.assertEqual(resp.status_code, 403)
        self.assertTemplateUsed(resp, '403.html')
        self.assertContains(resp, 'Acesso restrito a docentes e coordenadores.', status_code=403)

    def test_500_independente_renderiza_sem_contexto(self):
        resp = server_error(RequestFactory().get('/'))
        self.assertEqual(resp.status_code, 500)
        self.assertIn('Erro interno do servidor (500)', resp.content.decode())
