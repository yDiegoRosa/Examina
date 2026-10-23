"""Views de nível de projeto."""

import os
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.utils._os import safe_join

from banco_questoes.permissions import docente_ou_coordenador_required


def _midia_visivel(user, nome: str) -> bool:
    """
    Acesso por perfil na mídia: quem vê tudo (administrador, coordenador) baixa qualquer arquivo;
    o professor, só a digitalização de uma aplicação que ele vê e a imagem de uma questão dele
    ou de uma prova dele. ``nome`` é o caminho relativo a ``MEDIA_ROOT``, como fica no banco.
    """
    from academico.acesso import aplicacoes_visiveis, provas_visiveis, questoes_visiveis, ve_tudo
    from banco_questoes.models import Questao
    from provas.models import FolhaDigitalizada, ProvaQuestao

    if ve_tudo(user):
        return True
    if FolhaDigitalizada.objects.filter(imagem=nome, prova_aplicacao__in=aplicacoes_visiveis(user)).exists():
        return True
    if questoes_visiveis(user, Questao.objects.filter(imagem=nome)).exists():
        return True
    return ProvaQuestao.objects.filter(questao__imagem=nome, prova__in=provas_visiveis(user)).exists()


@login_required
@docente_ou_coordenador_required
def media_protegida(request, caminho):
    """
    Entrega um arquivo de ``MEDIA_ROOT`` apenas para quem pode vê-lo.

    A mídia contém dados sensíveis — folhas escaneadas (nome e matrícula dos alunos) e
    imagens de questões ainda não aplicadas —, por isso não há servidor público de
    ``/media/``: todo acesso passa por aqui, em desenvolvimento e em produção.
    ``safe_join`` recusa caminhos que escapem de ``MEDIA_ROOT`` (SuspiciousFileOperation → 400).
    Arquivo de outro professor responde 404, como os demais objetos (não revela que existe).
    """
    arquivo = Path(safe_join(settings.MEDIA_ROOT, caminho))
    if not arquivo.is_file():
        raise Http404('Arquivo não encontrado.')
    nome = os.path.relpath(arquivo, os.path.abspath(settings.MEDIA_ROOT)).replace(os.sep, '/')
    if not _midia_visivel(request.user, nome):
        raise Http404('Arquivo não encontrado.')
    return FileResponse(arquivo.open('rb'))
