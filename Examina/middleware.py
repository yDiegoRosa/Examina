"""Middlewares do projeto."""

import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.views import redirect_to_login
from django.utils.cache import add_never_cache_headers

# Chaves gravadas na sessão de quem entrou sem "Manter conectado" (ver Examina.views.LoginView).
SESSAO_CURTA = 'sessao_curta'
ULTIMO_ACESSO = 'ultimo_acesso'


class SemCacheParaUsuarioLogadoMiddleware:
    """
    Respostas para usuário logado não ficam guardadas no navegador (`Cache-Control: no-store`).

    Sem isso, depois de "Sair" o botão voltar — principalmente no celular, que guarda a página
    inteira na memória (back/forward cache) — mostrava a tela anterior como se a pessoa ainda
    estivesse logada. Sem cópia guardada, o navegador pede a página de novo e o servidor manda
    para o login. Vale também para PDFs e imagens com dados de alunos em computador compartilhado.

    Respostas que já definem o próprio Cache-Control não são alteradas.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        usuario = getattr(request, 'user', None)
        if usuario is not None and usuario.is_authenticated and not response.has_header('Cache-Control'):
            add_never_cache_headers(response)
        return response


class EncerraSessaoInativaMiddleware:
    """
    Quem entrou sem marcar "Manter conectado neste computador" sai depois de
    SESSAO_INATIVIDADE_MINUTOS sem usar o sistema e volta ao login com um aviso.

    O cookie dessa sessão já some ao fechar o navegador, mas navegadores que reabrem as abas
    ("continuar de onde parou") o restauram, e no computador compartilhado o caso comum é o
    navegador ficar aberto depois que o professor sai: o limite de inatividade cobre os dois.
    Precisa vir depois de AuthenticationMiddleware e MessageMiddleware.
    """

    # O último acesso é gravado no máximo uma vez por minuto (não salva a sessão a cada clique).
    INTERVALO_GRAVACAO = 60

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        sessao = request.session
        if sessao.get(SESSAO_CURTA) and request.user.is_authenticated:
            agora = int(time.time())
            ultimo = sessao.get(ULTIMO_ACESSO, agora)
            if agora - ultimo > settings.SESSAO_INATIVIDADE_MINUTOS * 60:
                logout(request)
                messages.info(request, 'Sua sessão terminou por falta de uso. Entre novamente.')
                return redirect_to_login(request.get_full_path())
            if agora - ultimo >= self.INTERVALO_GRAVACAO:
                sessao[ULTIMO_ACESSO] = agora
        return self.get_response(request)
