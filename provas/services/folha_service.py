"""
Folhas-resposta nominais (RF10): uma ``FolhaResposta`` e um PDF por aluno matriculado
numa aplicação de prova.

Reúne as peças já existentes — geometria (``answer_sheet_layout``), marcadores/QR
(``fiducial``), atribuição de versão (``version_assigner``) e o motor de PDF
(``pdf_generator``) — para transformar "N versões genéricas" em "uma folha nominal por
aluno", com o ``qr_token`` da folha impresso como QR.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import transaction

from . import fiducial
from .answer_sheet_layout import build_layout
from .pdf_generator import PDFGeneratorService

if TYPE_CHECKING:
    from provas.models import FolhaResposta, ProvaAplicacao, ProvaVersao

# Parâmetros visuais da folha nominal.
FOLHA_ALTERNATIVAS = 5
FOLHA_ID_DIGITS = 8  # mantém a matriz de matrícula como redundância ao QR
FOLHA_COLUMNS = 3


def contar_questoes_objetivas(versao: ProvaVersao) -> int:
    """Conta os itens objetivos (OBJ) de uma versão — número de bolhas da folha."""
    total = 0
    for item in versao.itens.select_related('prova_questao__questao').all():
        snap = item.prova_questao.snapshot_questao or {}
        tipo = snap.get('tipo') or item.prova_questao.questao.tipo
        if tipo == 'OBJ':
            total += 1
    return total


@transaction.atomic
def gerar_folhas_da_aplicacao(aplicacao: ProvaAplicacao) -> list[FolhaResposta]:
    """
    Cria (idempotentemente) uma ``FolhaResposta`` por aluno matriculado da aplicação.

    A versão de cada folha vem da ``AtribuicaoVersao``. Se ainda não houver atribuições,
    executa o round-robin automaticamente. O ``qr_token`` de folhas já existentes é
    preservado; apenas a versão é atualizada caso a atribuição tenha mudado.
    """
    from provas.models import FolhaResposta
    from provas.services.version_assigner import atribuir_versoes

    atribuicoes = list(aplicacao.atribuicoes.select_related('aluno', 'versao').order_by('sequencia'))
    if not atribuicoes:
        atribuicoes = atribuir_versoes(aplicacao)

    folhas: list[FolhaResposta] = []
    for atrib in atribuicoes:
        folha, criada = FolhaResposta.objects.get_or_create(
            prova_aplicacao=aplicacao,
            aluno=atrib.aluno,
            defaults={'prova_versao': atrib.versao},
        )
        if not criada and folha.prova_versao_id != atrib.versao_id:
            folha.prova_versao = atrib.versao
            folha.save(update_fields=['prova_versao', 'atualizado_em'])
        folhas.append(folha)

    folhas.sort(key=lambda f: f.aluno.nome)
    return folhas


def render_folha_pdf(folha: FolhaResposta) -> bytes:
    """
    Renderiza o PDF da folha-resposta nominal de um aluno (nome/matrícula + QR do token).

    :raises ValueError: se a versão não tiver questões objetivas.
    """
    versao = folha.prova_versao
    aplicacao = folha.prova_aplicacao
    prova = aplicacao.prova

    num_questoes = contar_questoes_objetivas(versao)
    if num_questoes < 1:
        raise ValueError('A versão não possui questões objetivas para compor a folha-resposta.')

    geometry = build_layout(
        num_questoes=num_questoes,
        num_alternativas=FOLHA_ALTERNATIVAS,
        id_digits=FOLHA_ID_DIGITS,
        columns=FOLHA_COLUMNS,
    )

    marcadores_info = fiducial.render_marcadores(geometry)
    marcadores = [
        {
            'corner': m.corner,
            'left': m.left,
            'top': m.top,
            'size': m.size,
            'tipo': marcadores_info[m.corner]['tipo'],
            'data_uri': marcadores_info[m.corner]['data_uri'],
        }
        for m in geometry.markers
    ]

    try:
        qr_data_uri = fiducial.gerar_qr_data_uri(fiducial.montar_payload_folha(folha.qr_token))
    except fiducial.FiducialError:
        qr_data_uri = None

    disciplina = prova.disciplina_efetiva
    context = {
        'geo': geometry,
        'marcadores': marcadores,
        'qr_data_uri': qr_data_uri,
        'instituicao': 'FAETERJ-Rio',
        'prova_titulo': prova.titulo,
        'disciplina_nome': disciplina.nome if disciplina else None,
        'versao_ident': versao.identificador,
        'data_aplicacao': aplicacao.data_aplicacao.strftime('%d/%m/%Y') if aplicacao.data_aplicacao else '',
        'aluno_nome': folha.aluno.nome,
        'aluno_matricula': folha.aluno.matricula,
        'instrucoes': [],
    }

    return PDFGeneratorService.render_pdf_from_template('provas/pdf_folha_resposta.html', context=context)
