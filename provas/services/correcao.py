"""
Correção automática de questões objetivas (RF11).

Fecha o fluxo do leitor óptico: a partir de uma :class:`~provas.models.FolhaDigitalizada`,
identifica a folha (QR), corrige a perspectiva, amostra as bolhas (``omr.ler_respostas``),
compara com o gabarito da versão (``ProvaVersaoItem``) e grava o resultado em
:class:`~desempenho.models.Desempenho` (``tipo_correcao='automatica'``).

Convenção de mapeamento: a folha numera as questões objetivas de 1..N na ordem dos
itens da versão (campo ``ordem``) — exatamente como a folha é impressa por
``folha_service.render_folha_pdf``. Assim, a bolha k corresponde ao k-ésimo item
objetivo da versão.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from django.db import transaction
from django.utils import timezone

from .answer_sheet_layout import build_layout
from .folha_service import (
    FOLHA_ALTERNATIVAS,
    FOLHA_COLUMNS,
    FOLHA_ID_DIGITS,
)
from .omr import OmrError, carregar_imagem, corrigir_perspectiva, identificar_folha, ler_respostas

if TYPE_CHECKING:
    from provas.models import FolhaDigitalizada, FolhaResposta, ProvaVersao, ProvaVersaoItem

# Marcador gravado em ``resposta_marcada`` quando a leitura acusa mais de uma bolha.
MARCA_AMBIGUA = '*'


@dataclass
class ResultadoCorrecao:
    """Resumo do processamento de uma folha digitalizada."""

    sucesso: bool
    mensagem: str
    folha_resposta: FolhaResposta | None = None
    total_questoes: int = 0
    acertos: int = 0
    brancos: int = 0
    ambiguas: int = 0


def _letra_correta(item: ProvaVersaoItem) -> str | None:
    """Letra correta (no espaço embaralhado da versão) de um item objetivo."""
    for alt in item.alternativas_embaralhadas or []:
        if alt.get('correta'):
            return alt.get('letra')
    return None


def _itens_objetivos_ordenados(versao: ProvaVersao) -> list[ProvaVersaoItem]:
    """Itens objetivos da versão na ordem de exibição (= ordem das bolhas na folha)."""
    objetivos = []
    for item in versao.itens.select_related('prova_questao__questao').order_by('ordem'):
        snap = item.prova_questao.snapshot_questao or {}
        tipo = snap.get('tipo') or item.prova_questao.questao.tipo
        if tipo == 'OBJ':
            objetivos.append(item)
    return objetivos


def _falha(digitalizacao: FolhaDigitalizada, mensagem: str) -> ResultadoCorrecao:
    """Marca a digitalização como ERRO e devolve um resultado malsucedido."""
    from provas.models import FolhaDigitalizada as FolhaDigitalizadaModel

    digitalizacao.status = FolhaDigitalizadaModel.Status.ERRO
    digitalizacao.mensagem = mensagem
    digitalizacao.save(update_fields=['status', 'mensagem', 'atualizado_em'])
    return ResultadoCorrecao(sucesso=False, mensagem=mensagem)


@transaction.atomic
def corrigir_folha_digitalizada(digitalizacao: FolhaDigitalizada, usuario=None) -> ResultadoCorrecao:
    """
    Processa uma folha digitalizada: leitura óptica + correção + gravação de ``Desempenho``.

    Não levanta exceção para falhas esperadas (QR ilegível, marcadores insuficientes,
    versão sem objetivas): marca a digitalização como ERRO e devolve
    ``ResultadoCorrecao(sucesso=False)`` — para não interromper o processamento em lote.
    """
    from desempenho.models import Desempenho
    from provas.models import FolhaDigitalizada as FolhaDigitalizadaModel
    from provas.models import FolhaResposta as FolhaRespostaModel

    try:
        imagem = carregar_imagem(digitalizacao.imagem)
    except OmrError as exc:
        return _falha(digitalizacao, f'Imagem ilegível: {exc}')

    folha = digitalizacao.folha_resposta or identificar_folha(imagem)
    if folha is None:
        return _falha(digitalizacao, 'QR não identificado: não foi possível localizar a folha-resposta.')

    itens_obj = _itens_objetivos_ordenados(folha.prova_versao)
    if not itens_obj:
        return _falha(digitalizacao, 'A versão da folha não possui questões objetivas.')

    try:
        canonica = corrigir_perspectiva(imagem)
    except OmrError as exc:
        return _falha(digitalizacao, f'Correção de perspectiva falhou: {exc}')

    geometry = build_layout(
        num_questoes=len(itens_obj),
        num_alternativas=FOLHA_ALTERNATIVAS,
        id_digits=FOLHA_ID_DIGITS,
        columns=FOLHA_COLUMNS,
    )
    leituras = ler_respostas(canonica, geometry.answer_bubbles)

    agora = timezone.now()
    acertos = brancos = ambiguas = 0

    for numero, item in enumerate(itens_obj, start=1):
        leitura = leituras.get(numero)
        correta = _letra_correta(item)

        if leitura is None or leitura.status == 'branco':
            resposta_marcada, acertou = '', False
            brancos += 1
        elif leitura.status == 'ambigua':
            resposta_marcada, acertou = MARCA_AMBIGUA, False
            ambiguas += 1
        else:
            resposta_marcada = leitura.marcada
            acertou = correta is not None and resposta_marcada == correta
            if acertou:
                acertos += 1

        Desempenho.objects.update_or_create(
            folha_resposta=folha,
            questao=item.questao,
            defaults={
                'tipo_correcao': Desempenho.TipoCorrecao.AUTOMATICA,
                'resposta_marcada': resposta_marcada,
                'acertou': acertou,
                'corrigido_por': usuario,
                'data_correcao': agora,
            },
        )

    folha.status = FolhaRespostaModel.Status.LIDA
    folha.save(update_fields=['status', 'atualizado_em'])

    total = len(itens_obj)
    mensagem = f'{acertos}/{total} acertos • {brancos} em branco • {ambiguas} ambígua(s).'
    digitalizacao.folha_resposta = folha
    digitalizacao.status = FolhaDigitalizadaModel.Status.PROCESSADA
    digitalizacao.mensagem = mensagem
    digitalizacao.save(update_fields=['folha_resposta', 'status', 'mensagem', 'atualizado_em'])

    return ResultadoCorrecao(
        sucesso=True,
        mensagem=mensagem,
        folha_resposta=folha,
        total_questoes=total,
        acertos=acertos,
        brancos=brancos,
        ambiguas=ambiguas,
    )


# ---------------------------------------------------------------------------
# Revisão manual (fila de falhas): correção pontual e revínculo de folha
# ---------------------------------------------------------------------------
LETRAS_VALIDAS = frozenset({'', 'A', 'B', 'C', 'D', 'E'})


def _item_da_questao(folha: FolhaResposta, questao) -> ProvaVersaoItem | None:
    """Item da versão da folha correspondente à questão base (para achar o gabarito)."""
    return folha.prova_versao.itens.select_related('prova_questao').filter(prova_questao__questao=questao).first()


@transaction.atomic
def corrigir_questao_manual(desempenho, resposta: str, usuario=None):
    """
    Correção manual pontual de uma questão inconclusiva (branco/dupla) da fila de revisão.

    Grava a alternativa informada (``''`` = em branco), recalcula ``acertou`` contra o
    gabarito da versão e marca ``tipo_correcao='MANUAL'``.

    :raises ValueError: se a resposta não for '' ou uma letra A–E.
    """
    from desempenho.models import Desempenho

    resposta = (resposta or '').strip().upper()
    if resposta not in LETRAS_VALIDAS:
        raise ValueError(f'Resposta inválida: {resposta!r}. Use uma letra de A a E ou deixe em branco.')

    item = _item_da_questao(desempenho.folha_resposta, desempenho.questao)
    correta = _letra_correta(item) if item else None

    desempenho.resposta_marcada = resposta
    desempenho.acertou = bool(resposta) and correta is not None and resposta == correta
    desempenho.tipo_correcao = Desempenho.TipoCorrecao.MANUAL
    desempenho.corrigido_por = usuario
    desempenho.data_correcao = timezone.now()
    desempenho.save(
        update_fields=[
            'resposta_marcada',
            'acertou',
            'tipo_correcao',
            'corrigido_por',
            'data_correcao',
            'atualizado_em',
        ]
    )
    return desempenho


def revincular_e_reprocessar(digitalizacao, folha, usuario=None) -> ResultadoCorrecao:
    """
    Vincula manualmente uma folha a uma digitalização com erro (ex.: QR ilegível) e
    reexecuta a correção automática — que, com a folha já vinculada, dispensa o QR.
    """
    from provas.models import FolhaDigitalizada as FolhaDigitalizadaModel

    digitalizacao.folha_resposta = folha
    digitalizacao.status = FolhaDigitalizadaModel.Status.PENDENTE
    digitalizacao.mensagem = ''
    digitalizacao.save(update_fields=['folha_resposta', 'status', 'mensagem', 'atualizado_em'])
    return corrigir_folha_digitalizada(digitalizacao, usuario=usuario)


# ---------------------------------------------------------------------------
# Lançamento manual de notas (discursivas) e fallback objetivo sem scan
# ---------------------------------------------------------------------------
NOTA_MIN = Decimal('0')
NOTA_MAX = Decimal('10')


def validar_nota(nota) -> Decimal:
    """Converte e valida uma nota no intervalo [0, 10]. :raises ValueError: se inválida."""
    try:
        valor = Decimal(str(nota).replace(',', '.'))
    except (InvalidOperation, TypeError, AttributeError) as exc:
        raise ValueError(f'Nota inválida: {nota!r}.') from exc
    if valor < NOTA_MIN or valor > NOTA_MAX:
        raise ValueError('A nota deve estar entre 0 e 10.')
    return valor


def itens_versao_para_lancamento(folha) -> list[dict]:
    """
    Linhas para a tela de lançamento manual: um dict por item da versão da folha,
    com o `Desempenho` atual (se houver) e o gabarito (para objetivas).
    """
    from desempenho.models import Desempenho

    desempenhos = {d.questao_id: d for d in Desempenho.objects.filter(folha_resposta=folha).select_related('questao')}
    linhas = []
    for item in folha.prova_versao.itens.select_related('prova_questao__questao').order_by('ordem'):
        questao = item.prova_questao.questao
        snap = item.prova_questao.snapshot_questao or {}
        tipo = snap.get('tipo') or questao.tipo
        linhas.append(
            {
                'item': item,
                'questao': questao,
                'tipo': tipo,
                'desempenho': desempenhos.get(questao.id),
                'gabarito': _letra_correta(item) if tipo == 'OBJ' else None,
            }
        )
    return linhas


@transaction.atomic
def lancar_nota_discursiva(folha, questao, nota, corte, usuario=None):
    """
    Lança/atualiza a nota (0–10) de uma questão discursiva; define
    ``acertou = nota >= corte`` (unificação binária p/ os dashboards) e
    ``tipo_correcao='MANUAL'``.
    """
    from desempenho.models import Desempenho

    nota = validar_nota(nota)
    corte = Decimal(str(corte))
    desempenho, _ = Desempenho.objects.update_or_create(
        folha_resposta=folha,
        questao=questao,
        defaults={
            'tipo_correcao': Desempenho.TipoCorrecao.MANUAL,
            'nota': nota,
            'acertou': nota >= corte,
            'resposta_marcada': '',
            'corrigido_por': usuario,
            'data_correcao': timezone.now(),
        },
    )
    return desempenho


@transaction.atomic
def lancar_objetiva_manual(folha, questao, resposta, usuario=None):
    """
    Lançamento manual da resposta de uma questão objetiva (fallback quando a folha
    não passou pelo OMR). Grava ``resposta_marcada``, calcula ``acertou`` pelo
    gabarito da versão e marca ``tipo_correcao='MANUAL'``.
    """
    from desempenho.models import Desempenho

    resposta = (resposta or '').strip().upper()
    if resposta not in LETRAS_VALIDAS:
        raise ValueError(f'Resposta inválida: {resposta!r}. Use uma letra de A a E ou deixe em branco.')

    item = _item_da_questao(folha, questao)
    correta = _letra_correta(item) if item else None
    desempenho, _ = Desempenho.objects.update_or_create(
        folha_resposta=folha,
        questao=questao,
        defaults={
            'tipo_correcao': Desempenho.TipoCorrecao.MANUAL,
            'resposta_marcada': resposta,
            'acertou': bool(resposta) and correta is not None and resposta == correta,
            'nota': None,
            'corrigido_por': usuario,
            'data_correcao': timezone.now(),
        },
    )
    return desempenho


@transaction.atomic
def recalcular_acertou_discursivas(aplicacao, corte) -> int:
    """
    Reaplica ``acertou = nota >= corte`` aos `Desempenho` que têm nota lançada
    (discursivas) da aplicação. Retorna quantos registros mudaram.
    """
    from desempenho.models import Desempenho

    corte = Decimal(str(corte))
    alterados = 0
    for desempenho in Desempenho.objects.filter(folha_resposta__prova_aplicacao=aplicacao, nota__isnull=False):
        novo = desempenho.nota >= corte
        if desempenho.acertou != novo:
            desempenho.acertou = novo
            desempenho.save(update_fields=['acertou', 'atualizado_em'])
            alterados += 1
    return alterados
