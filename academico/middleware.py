"""Middleware do acadêmico: troca obrigatória da senha temporária do professor."""

from django.shortcuts import redirect
from django.urls import reverse

from .professores import troca_de_senha_pendente


class TrocaDeSenhaObrigatoriaMiddleware:
    """
    Professor com senha temporária (conta criada em Acadêmico → Professores ou senha redefinida)
    só consegue abrir "Alterar senha" e "Sair" até escolher a própria senha; qualquer outra página
    redireciona para a troca. A marca é limpa por ``Examina.views.AlterarSenhaView``.
    Precisa vir depois de AuthenticationMiddleware.
    """

    URLS_LIBERADAS = ('password_change', 'logout')

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if troca_de_senha_pendente(request.user) and request.path not in {reverse(n) for n in self.URLS_LIBERADAS}:
            return redirect('password_change')
        return self.get_response(request)
