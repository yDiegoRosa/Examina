from django.utils.functional import SimpleLazyObject

from .permissions import eh_administrador, ve_tudo


def perfil(request):
    """
    Perfil do usuário para os templates (menu e botões): ``perfil_admin`` (gerencia
    cadastros e o Simulado ENADE) e ``perfil_ve_tudo`` (administrador ou coordenador).
    Avaliados só se o template usar.
    """
    user = request.user
    return {
        'perfil_admin': SimpleLazyObject(lambda: eh_administrador(user)),
        'perfil_ve_tudo': SimpleLazyObject(lambda: ve_tudo(user)),
    }
