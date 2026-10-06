"""
Limite de tentativas de login.

Depois de ``LOGIN_MAX_TENTATIVAS`` senhas erradas para o mesmo usuário dentro de
``LOGIN_BLOQUEIO_MINUTOS``, novas tentativas desse usuário são recusadas, mesmo com a senha
certa, até a janela passar. Vale para a tela de login e para o /admin/ (ver
``auth_backends.ModelBackendComLimite``).

O limite é por usuário, não por IP: atrás do proxy HTTPS todos os acessos chegam com o IP do
proxy. O IP fica gravado só para consulta no admin, onde apagar as tentativas de um usuário
o desbloqueia. As tentativas ficam no banco (``TentativaLogin``) porque o cache padrão do
Django não é compartilhado entre os processos do gunicorn.
"""

from __future__ import annotations

import math
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

# Tentativas mais antigas que isso são apagadas ao registrar uma nova (a tabela não cresce).
RETENCAO_TENTATIVAS = timedelta(days=1)


def _normalizar(username) -> str:
    return (username or '').strip().lower()


def _janela() -> timedelta:
    return timedelta(minutes=settings.LOGIN_BLOQUEIO_MINUTOS)


def minutos_de_bloqueio(username) -> int:
    """Minutos que faltam para o usuário poder tentar de novo (0 = liberado)."""
    from .models import TentativaLogin

    nome = _normalizar(username)
    limite = settings.LOGIN_MAX_TENTATIVAS
    if not nome or limite < 1:
        return 0

    agora = timezone.now()
    recentes = list(
        TentativaLogin.objects.filter(username=nome, criado_em__gte=agora - _janela())
        .order_by('-criado_em')
        .values_list('criado_em', flat=True)[:limite]
    )
    if len(recentes) < limite:
        return 0
    # Libera quando a mais antiga das últimas ``limite`` tentativas sair da janela.
    restante = recentes[-1] + _janela() - agora
    return max(1, math.ceil(restante.total_seconds() / 60))


def registrar_falha(username, request=None) -> None:
    """Grava uma tentativa errada (usuário já bloqueado não prolonga o próprio bloqueio)."""
    from .models import TentativaLogin

    nome = _normalizar(username)
    if not nome or minutos_de_bloqueio(nome):
        return
    TentativaLogin.objects.filter(criado_em__lt=timezone.now() - RETENCAO_TENTATIVAS).delete()
    ip = request.META.get('REMOTE_ADDR') if request is not None else None
    TentativaLogin.objects.create(username=nome[:150], ip=ip or None)


def limpar_falhas(username) -> None:
    """Login bem-sucedido zera as tentativas do usuário."""
    from .models import TentativaLogin

    TentativaLogin.objects.filter(username=_normalizar(username)).delete()


def ao_falhar_login(sender, credentials, request=None, **kwargs) -> None:
    """Receptor de ``user_login_failed``."""
    registrar_falha(credentials.get('username'), request)


def ao_entrar(sender, request, user, **kwargs) -> None:
    """Receptor de ``user_logged_in``."""
    limpar_falhas(user.get_username())
