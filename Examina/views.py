"""Views de nível de projeto."""

from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.utils._os import safe_join

from banco_questoes.permissions import docente_ou_coordenador_required


@login_required
@docente_ou_coordenador_required
def media_protegida(request, caminho):
    """
    Entrega um arquivo de ``MEDIA_ROOT`` apenas para Docente/Coordenador.

    A mídia contém dados sensíveis — folhas escaneadas (nome e matrícula dos alunos) e
    imagens de questões ainda não aplicadas —, por isso não há servidor público de
    ``/media/``: todo acesso passa por aqui, em desenvolvimento e em produção.
    ``safe_join`` recusa caminhos que escapem de ``MEDIA_ROOT`` (SuspiciousFileOperation → 400).
    """
    arquivo = Path(safe_join(settings.MEDIA_ROOT, caminho))
    if not arquivo.is_file():
        raise Http404('Arquivo não encontrado.')
    return FileResponse(arquivo.open('rb'))
