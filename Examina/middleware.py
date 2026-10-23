"""Middlewares do projeto."""

from django.utils.cache import add_never_cache_headers


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
