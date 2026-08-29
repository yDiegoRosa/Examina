from django.contrib.auth.mixins import UserPassesTestMixin
from django.core.exceptions import PermissionDenied


def is_docente_ou_coordenador(user):
    """
    Verifica se o usuário tem perfil de Docente ou Coordenador.

    Retorna True se o usuário for superusuário, pertencer aos grupos
    'Docente' ou 'Coordenador', ou possuir a permissão 'add_questao'.
    """
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=['Docente', 'Coordenador']).exists() or user.has_perm(
        'banco_questoes.add_questao'
    )


def can_change_questao(user):
    """Verifica se o usuário pode editar questões."""
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=['Docente', 'Coordenador']).exists() or user.has_perm(
        'banco_questoes.change_questao'
    )


class DocenteOuCoordenadorRequiredMixin(UserPassesTestMixin):
    """
    Mixin para Class-Based Views exigindo que o usuário seja Docente,
    Coordenador ou superusuário.
    """

    raise_exception = True

    def test_func(self):
        return is_docente_ou_coordenador(self.request.user)


def docente_ou_coordenador_required(view_func):
    """Decorator para Function-Based Views exigindo grupo Docente ou Coordenador."""

    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.contrib.auth.views import redirect_to_login

            return redirect_to_login(request.get_full_path())
        if not is_docente_ou_coordenador(request.user):
            raise PermissionDenied('Acesso restrito a docentes e coordenadores.')
        return view_func(request, *args, **kwargs)

    return _wrapped_view

def pode_editar_questao_required(view_func):
    """Decorator para Function-Based Views exigindo permissão de edição de questão."""

    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.contrib.auth.views import redirect_to_login

            return redirect_to_login(request.get_full_path())
        if not can_change_questao(request.user):
            raise PermissionDenied('Acesso restrito a quem pode editar questões.')
        return view_func(request, *args, **kwargs)

    return _wrapped_view