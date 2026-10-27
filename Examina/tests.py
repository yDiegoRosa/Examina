"""Testes de nível de projeto: mídia autenticada, entradas inválidas e páginas de erro."""

import tempfile
import time
from datetime import date
from pathlib import Path

from django.conf import settings
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


class ManterConectadoTest(TestCase):
    """
    "Manter conectado neste computador" no login: desmarcado, a sessão termina ao fechar o
    navegador e após SESSAO_INATIVIDADE_MINUTOS sem uso; marcado, dura 2 semanas como antes.
    """

    SENHA = 'segredo123'  # noqa: S105

    def setUp(self):
        self.user = get_user_model().objects.create_user(username='doc_sessao', password=self.SENHA)
        self.user.groups.add(Group.objects.get(name='Docente'))
        self.pagina = reverse('banco_questoes:questao_list')

    def _entrar(self, lembrar=False):
        dados = {'username': 'doc_sessao', 'password': self.SENHA}
        if lembrar:
            dados['lembrar'] = 'on'
        resp = self.client.post(reverse('login'), dados)
        self.assertEqual(resp.status_code, 302)

    def _ultimo_acesso_ha(self, segundos):
        from Examina.middleware import ULTIMO_ACESSO

        sessao = self.client.session
        sessao[ULTIMO_ACESSO] = int(time.time()) - segundos
        sessao.save()

    def test_tela_de_login_mostra_a_caixa_desmarcada_e_o_prazo(self):
        resp = self.client.get(reverse('login'))
        self.assertContains(resp, 'name="lembrar"')
        self.assertNotContains(resp, 'checked')
        self.assertContains(resp, 'Manter conectado neste computador')
        self.assertContains(resp, 'ou após 2 h sem uso')

    def test_sem_marcar_a_sessao_termina_ao_fechar_o_navegador(self):
        from Examina.middleware import SESSAO_CURTA, ULTIMO_ACESSO

        self._entrar()
        sessao = self.client.session
        self.assertTrue(sessao.get_expire_at_browser_close())
        self.assertTrue(sessao[SESSAO_CURTA])
        self.assertAlmostEqual(sessao[ULTIMO_ACESSO], time.time(), delta=5)

    def test_marcando_a_sessao_dura_duas_semanas(self):
        from Examina.middleware import SESSAO_CURTA

        self._entrar(lembrar=True)
        sessao = self.client.session
        self.assertFalse(sessao.get_expire_at_browser_close())
        self.assertEqual(sessao.get_expiry_age(), settings.SESSION_COOKIE_AGE)
        self.assertNotIn(SESSAO_CURTA, sessao)

    def test_sem_uso_alem_do_limite_volta_ao_login_com_aviso(self):
        self._entrar()
        self._ultimo_acesso_ha(settings.SESSAO_INATIVIDADE_MINUTOS * 60 + 60)

        resp = self.client.get(self.pagina, follow=True)

        self.assertEqual(resp.redirect_chain[0][1], 302)
        self.assertTrue(resp.redirect_chain[0][0].startswith(reverse('login')))
        self.assertContains(resp, 'Sua sessão terminou por falta de uso')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_uso_dentro_do_limite_continua_e_renova_o_prazo(self):
        from Examina.middleware import ULTIMO_ACESSO

        self._entrar()
        self._ultimo_acesso_ha(30 * 60)

        self.assertEqual(self.client.get(self.pagina).status_code, 200)
        self.assertAlmostEqual(self.client.session[ULTIMO_ACESSO], time.time(), delta=5)

    @override_settings(SESSAO_INATIVIDADE_MINUTOS=1)
    def test_sessao_mantida_nao_expira_por_falta_de_uso(self):
        self._entrar(lembrar=True)
        self._ultimo_acesso_ha(3600)
        self.assertEqual(self.client.get(self.pagina).status_code, 200)

    def test_texto_do_prazo(self):
        from Examina.views import texto_duracao

        self.assertEqual(texto_duracao(120), '2 h')
        self.assertEqual(texto_duracao(90), '1 h 30 min')
        self.assertEqual(texto_duracao(45), '45 min')


class RotacaoDosLogsTest(TestCase):
    """Os containers de produção limitam o tamanho dos logs (senão crescem para sempre)."""

    def test_todos_os_servicos_usam_o_limite_de_log(self):
        compose = (Path(__file__).resolve().parent.parent / 'docker-compose.prod.yml').read_text(encoding='utf-8')
        self.assertIn('x-logging: &logging', compose)
        self.assertIn('max-size: "10m"', compose)
        self.assertIn('max-file: "5"', compose)
        import re

        bloco_servicos = compose.split('\nservices:\n', 1)[1].split('\nvolumes:\n', 1)[0]
        servicos = re.findall(r'^  ([a-z]+):$', bloco_servicos, flags=re.MULTILINE)
        self.assertEqual(servicos, ['db', 'web', 'caddy'])
        self.assertEqual(bloco_servicos.count('logging: *logging'), len(servicos))
