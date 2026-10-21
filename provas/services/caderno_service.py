"""
Caderno de questões em PDF: contexto do template (capa + questões em blocos) de uma prova
ou de uma versão, e os cadernos nominais de uma aplicação — um por aluno, com nome e
matrícula na capa e a versão do cartão-resposta do aluno.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from banco_questoes.models import Questao

from .caderno_layout import eh_prova_enade, linhas_da_discursiva, organizar_caderno
from .pdf_generator import PDFGeneratorService

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from provas.models import FolhaResposta, Prova, ProvaAplicacao, ProvaVersao

TEMPLATE_CADERNO_PADRAO = 'provas/pdf_prova_completa.html'
TEMPLATE_CADERNO_ENADE = 'provas/pdf_prova_enade.html'

_MODALIDADE_DISPLAY = {
    'OBJ': 'Somente Objetivas',
    'DIS': 'Somente Discursivas',
    'MISTA': 'Mista (Objetivas e Discursivas)',
}


def valor_total_do_snapshot(snap: dict) -> Decimal | None:
    """``valor_total`` do snapshot como número (o JSON guarda texto: "7.00" sai "7,00" na capa)."""
    try:
        return Decimal(str(snap.get('valor_total')))
    except (InvalidOperation, TypeError, ValueError):
        return None


def itens_do_caderno(prova: Prova, versao: ProvaVersao | None) -> list[dict]:
    """Itens na ordem da versão (alternativas embaralhadas persistidas) ou da prova base."""
    if versao:
        itens_versao = versao.itens.select_related(
            'prova_questao__questao',
            'prova_questao__questao__topico',
            'prova_questao__questao__disciplina',
        ).order_by('ordem')
        return [
            {
                'numero': iv.numero,
                'ordem': iv.ordem,
                'pontuacao': iv.pontuacao,
                'questao': iv.prova_questao.questao,
                'snapshot_questao': iv.prova_questao.snapshot_questao or {},
                'alternativas_versao': iv.alternativas_embaralhadas,
            }
            for iv in itens_versao
        ]

    itens = (
        prova.itens.select_related('questao', 'questao__topico', 'questao__disciplina')
        .prefetch_related('questao__alternativas')
        .order_by('ordem')
    )
    return [
        {
            'numero': it.numero,
            'ordem': it.ordem,
            'pontuacao': it.pontuacao,
            'questao': it.questao,
            'snapshot_questao': it.snapshot_questao,
            'alternativas_versao': [
                {'letra': a.letra, 'texto': a.texto, 'correta': a.correta} for a in it.questao.alternativas.all()
            ],
        }
        for it in itens
    ]


def contexto_caderno(
    prova: Prova,
    versao: ProvaVersao | None,
    *,
    incluir_capa: bool = True,
    incluir_gabarito: bool = False,
    layout: str = '',
    turma: str = '',
    turno: str = '',
    data_aplicacao: str = '',
    instrucoes_extras: str = '',
) -> tuple[str, dict]:
    """
    Template e contexto do caderno de questões da prova (ou de uma versão).

    O layout ENADE (discursivas antes das objetivas) vale para provas de Simulado ENADE ou
    quando forçado com ``layout='enade'``; senão, o layout institucional padrão.

    :return: ``(template_name, context)`` para ``PDFGeneratorService.render_pdf_from_template``.
    """
    disciplina = prova.disciplina_efetiva
    mascara = prova.mascara
    snap = prova.snapshot_mascara or {}

    valor_total = None
    tempo_minutos = None
    modalidade = None
    if mascara:
        valor_total = mascara.valor_total
        tempo_minutos = mascara.tempo_estimado_minutos
        modalidade = mascara.get_modalidade_display()
    elif snap:
        valor_total = valor_total_do_snapshot(snap)
        tempo_minutos = snap.get('tempo_estimado_minutos')
        modalidade_code = snap.get('modalidade', '')
        modalidade = _MODALIDADE_DISPLAY.get(modalidade_code, modalidade_code)

    docente_nome = None
    if prova.autor:
        docente_nome = prova.autor.get_full_name() or prova.autor.username

    layout = (layout or '').lower()
    usar_layout_enade = layout == 'enade' or (layout == '' and eh_prova_enade(prova))

    # Caderno em blocos (os dois layouts): discursivas primeiro, numeradas à parte; objetivas
    # longas em largura total e curtas em duas colunas — ver services.caderno_layout.
    itens_render, blocos = organizar_caderno(itens_do_caderno(prova, versao))
    itens_discursivas = [it for it in itens_render if it['tipo'] != Questao.Tipo.OBJETIVA]
    itens_objetivas = [it for it in itens_render if it['tipo'] == Questao.Tipo.OBJETIVA]

    context = {
        'prova': prova,
        'versao': versao,
        'identificador_display': f'Versão {versao.identificador}' if versao else 'Versão única',
        'itens': itens_render,
        'itens_render': itens_render,
        'blocos': blocos,
        'blocos_objetivas': [bloco for bloco in blocos if bloco['formato'] != 'discursivas'],
        'itens_discursivas': itens_discursivas,
        'itens_objetivas': itens_objetivas,
        'disciplina_nome': disciplina.nome if disciplina else None,
        'docente_nome': docente_nome,
        'turma': snap.get('turma') or turma,
        'turno': snap.get('turno') or turno,
        'data_aplicacao': data_aplicacao,
        'valor_total': valor_total,
        'tempo_minutos': tempo_minutos,
        'modalidade': modalidade,
        'total_questoes': len(itens_render),
        'total_objetivas': len(itens_objetivas),
        'total_discursivas': len(itens_discursivas),
        'instrucoes_extras': instrucoes_extras,
        'incluir_capa': incluir_capa,
        'incluir_gabarito': incluir_gabarito,
        'linhas_discursiva': linhas_da_discursiva(prova),  # 15 só em prova ENADE, em qualquer layout
    }
    template_name = TEMPLATE_CADERNO_ENADE if usar_layout_enade else TEMPLATE_CADERNO_PADRAO
    return template_name, context


def render_cadernos_nominais(
    aplicacao: ProvaAplicacao, folhas: Iterable[FolhaResposta]
) -> Iterator[tuple[FolhaResposta, bytes]]:
    """
    Caderno de cada aluno (PDF), na versão do seu cartão-resposta: capa com nome e
    matrícula do aluno e com turma, turno e data da aplicação; sem gabarito.

    O contexto de cada versão é montado uma vez; por aluno muda só a identificação.

    :raises PDFGenerationError: se a compilação de algum PDF falhar.
    """
    turma = aplicacao.turma
    dados_aplicacao = {
        'turma': f'{turma.nome} / {turma.periodo}',
        'turno': turma.get_turno_display() if turma.turno else '',
        'data_aplicacao': aplicacao.data_aplicacao.strftime('%d/%m/%Y') if aplicacao.data_aplicacao else '',
    }

    por_versao: dict[int, tuple[str, dict]] = {}
    for folha in folhas:
        versao = folha.prova_versao
        if versao.pk not in por_versao:
            por_versao[versao.pk] = contexto_caderno(aplicacao.prova, versao, **dados_aplicacao)
        template_name, contexto = por_versao[versao.pk]
        contexto_aluno = {**contexto, 'aluno_nome': folha.aluno.nome, 'aluno_matricula': folha.aluno.matricula}
        yield folha, PDFGeneratorService.render_pdf_from_template(template_name, contexto_aluno)
