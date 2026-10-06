from django.contrib.auth.backends import ModelBackend
from django.core.exceptions import PermissionDenied

from .seguranca_login import minutos_de_bloqueio


class ModelBackendComLimite(ModelBackend):
    """
    ``ModelBackend`` que recusa o login de usuário bloqueado pelo limite de tentativas
    (``seguranca_login``), sem testar a senha. Vale para a tela de login e para o /admin/.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        if minutos_de_bloqueio(username):
            # PermissionDenied interrompe o authenticate() (nenhum outro backend é tentado).
            raise PermissionDenied
        return super().authenticate(request, username=username, password=password, **kwargs)
