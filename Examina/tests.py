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
    """
    /media/ não é público: o professor baixa só a digitalização das aplicações dele e as imagens
    das questões dele (ou das provas dele); administrador e coordenador baixam tudo.
    """

    def setUp(self):
        for pasta, arquivo, conteudo in (
            ('folhas_digitalizadas', 'folha.png', b'conteudo-da-folha'),
            ('questoes/imagens', 'figura.png', b'figura-da-questao'),
        ):
            (Path(MEDIA_TMP) / pasta).mkdir(parents=True, exist_ok=True)
            (Path(MEDIA_TMP) / pasta / arquivo).write_bytes(conteudo)
        self.url = '/media/folhas_digitalizadas/folha.png'
        self.url_figura = '/media/questoes/imagens/figura.png'

        user_model = get_user_model()
        self.docente = user_model.objects.create_user(username='doc_media', password='segredo123')  # noqa: S106
        self.docente.groups.add(Group.objects.get(name='Docente'))
        self.outro_docente = user_model.objects.create_user(username='outro_doc_media', password='x')  # noqa: S106
        self.outro_docente.groups.add(Group.objects.get(name='Docente'))
        self.coordenador = user_model.objects.create_user(username='coord_media', password='x')  # noqa: S106
        self.coordenador.groups.add(Group.objects.get(name='Coordenador'))
        self.sem_grupo = user_model.objects.create_user(username='sem_grupo_media', password='segredo123')  # noqa: S106

        # A digitalização é de uma aplicação da prova do docente.
        prova = Prova.objects.create(titulo='Prova da mídia', autor=self.docente)
        aplicacao = ProvaAplicacao.objects.create(
            prova=prova,
            turma=Turma.objects.create(periodo='2026.2', nome='T Mídia'),
            data_aplicacao=date(2026, 11, 10),
        )
        FolhaDigitalizada.objects.create(prova_aplicacao=aplicacao, imagem='folhas_digitalizadas/folha.png')
        self.prova = prova
        self.questao_figura = Questao.objects.create(
            disciplina=Disciplina.objects.create(nome='Disc Mídia'),
            tipo='DIS',
            dificuldade='F',
            enunciado='Questão com figura',
            imagem='questoes/imagens/figura.png',
            autor=self.outro_docente,
        )

    def test_anonimo_redirecionado_ao_login(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login/', resp['Location'])

    def test_usuario_sem_grupo_recebe_403(self):
        self.client.force_login(self.sem_grupo)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_docente_recebe_a_digitalizacao_da_aplicacao_dele(self):
        self.client.force_login(self.docente)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(b''.join(resp.streaming_content), b'conteudo-da-folha')
        self.assertEqual(resp['Content-Type'], 'image/png')

    def test_outro_docente_nao_recebe_a_digitalizacao(self):
        self.client.force_login(self.outro_docente)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_coordenador_recebe_tudo(self):
        self.client.force_login(self.coordenador)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.client.get(self.url_figura).status_code, 200)

    def test_imagem_de_questao_so_do_autor_ou_de_prova_dele(self):
        self.client.force_login(self.outro_docente)  # autor da questão
        self.assertEqual(self.client.get(self.url_figura).status_code, 200)

        self.client.force_login(self.docente)
        self.assertEqual(self.client.get(self.url_figura).status_code, 404)
        # Questão de outro autor numa prova dele (prova montada antes do acesso por perfil).
        ProvaQuestao.objects.create(prova=self.prova, questao=self.questao_figura, numero=1, ordem=1)
        self.assertEqual(self.client.get(self.url_figura).status_code, 200)

    def test_arquivo_sem_dono_conhecido_so_para_quem_ve_tudo(self):
        (Path(MEDIA_TMP) / 'avulso.txt').write_bytes(b'avulso')
        self.client.force_login(self.docente)
        self.assertEqual(self.client.get('/media/avulso.txt').status_code, 404)
        self.client.force_login(self.coordenador)
        self.assertEqual(self.client.get('/media/avulso.txt').status_code, 200)

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
        user.groups.add(Group.objects.get_or_create(name='Administrador')[0])  # robustez de entrada, não acesso
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


class SemCacheDepoisDeSairTest(TestCase):
    """
    Depois de "Sair", o botão voltar não pode mostrar a tela anterior (o celular guardava a
    página inteira na memória): páginas de usuário logado vão com "no-store" e a sessão acaba.
    """

    def setUp(self):
        self.user = get_user_model().objects.create_user(username='doc_cache', password='segredo123')  # noqa: S106
        self.user.groups.add(Group.objects.get(name='Docente'))

    def test_pagina_de_usuario_logado_nao_fica_no_cache(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('banco_questoes:questao_list'))
        self.assertEqual(resp.status_code, 200)
        cache = resp['Cache-Control']
        for diretiva in ('no-store', 'no-cache', 'private'):
            self.assertIn(diretiva, cache)

    def test_download_de_usuario_logado_tambem_nao_fica_no_cache(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('banco_questoes:download_modelo_csv'))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('no-store', resp['Cache-Control'])

    def test_visitante_nao_recebe_o_cabecalho_do_middleware(self):
        resp = self.client.get(reverse('banco_questoes:questao_list'))  # redireciona para o login
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(resp.has_header('Cache-Control'))

    def test_depois_de_sair_a_pagina_manda_para_o_login(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('banco_questoes:questao_list')).status_code, 200)

        self.client.post(reverse('logout'))

        resp = self.client.get(reverse('banco_questoes:questao_list'))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp['Location'].startswith(reverse('login')))

    def test_pagina_restaurada_pelo_voltar_e_recarregada(self):
        js = (Path(__file__).resolve().parent.parent / 'banco_questoes/static/banco_questoes/js/main.js').read_text(
            encoding='utf-8'
        )
        self.assertIn("addEventListener('pageshow'", js)
        self.assertIn('e.persisted', js)
        self.assertIn('window.location.reload()', js)
