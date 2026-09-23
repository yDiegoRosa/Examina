"""
Leitor óptico (OMR) — estágios de entrada da correção automática.

Cobre duas etapas, ambas só com OpenCV (já instalado):

1. **Decodificação do QR** — lê o QR de uma folha escaneada, extrai o ``qr_token`` e
   localiza a :class:`~provas.models.FolhaResposta` correspondente.
2. **Correção de perspectiva (deskew)** — detecta os 4 marcadores ArUco e aplica
   ``warpPerspective`` para produzir uma imagem A4 canônica, alinhada à geometria
   fonte-única (``answer_sheet_layout``), pronta para a amostragem das bolhas.

A amostragem das bolhas e a gravação de ``Desempenho`` são etapas posteriores.
"""

from __future__ import annotations

import contextlib
import uuid as uuid_lib
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import cv2
import numpy as np

from .answer_sheet_layout import MARKER_IDS, MARKER_INSET, PAGE_H, PAGE_W

if TYPE_CHECKING:
    from provas.models import FolhaResposta

    from .answer_sheet_layout import Bubble

# Resolução da imagem canônica após o deskew (10 px/mm ≈ 254 DPI).
PX_POR_MM = 10
ARUCO_DICT = 'DICT_4X4_50'
PREFIXO_TOKEN = 'EXM1F'  # noqa: S105 — prefixo de folha, não senha

# --- Amostragem de bolhas (leitura das marcações) --------------------------
# Fração do raio efetivamente amostrada (evita o anel impresso da bolha).
INNER_R_FRAC = 0.62
# Fração mínima de pixels escuros no disco para considerar a bolha marcada.
MIN_FILL = 0.35
# Se a 2ª bolha mais preenchida alcançar AMBIG_RATIO × a 1ª (e >= MIN_FILL),
# a questão é considerada ambígua (mais de uma marcação).
AMBIG_RATIO = 0.80


class OmrError(Exception):
    """Erro no processamento óptico (QR ilegível, marcadores insuficientes, etc.)."""


# ---------------------------------------------------------------------------
# Carregamento de imagem (aceita caminho, bytes, arquivo ou ndarray)
# ---------------------------------------------------------------------------
def carregar_imagem(fonte) -> np.ndarray:
    """Normaliza a fonte para um ndarray BGR do OpenCV."""
    if isinstance(fonte, np.ndarray):
        return fonte

    dados: bytes | None = None
    if isinstance(fonte, (bytes, bytearray)):
        dados = bytes(fonte)
    elif hasattr(fonte, 'read'):  # arquivo/FieldFile
        with contextlib.suppress(AttributeError, TypeError, ValueError):
            fonte.open('rb')
        dados = fonte.read()
    else:  # caminho (str/Path)
        imagem = cv2.imread(str(fonte))
        if imagem is None:
            raise OmrError(f'Não foi possível ler a imagem: {fonte!r}.')
        return imagem

    if not dados:
        raise OmrError('Fonte de imagem vazia.')
    imagem = cv2.imdecode(np.frombuffer(dados, np.uint8), cv2.IMREAD_COLOR)
    if imagem is None:
        raise OmrError('Conteúdo não é uma imagem válida.')
    return imagem


# ---------------------------------------------------------------------------
# 1. Decodificação do QR → FolhaResposta
# ---------------------------------------------------------------------------
def decodificar_qr(fonte) -> str | None:
    """Lê o primeiro QR encontrado na imagem e devolve o payload (ou ``None``)."""
    imagem = carregar_imagem(fonte)
    detector = cv2.QRCodeDetector()
    dados, pontos, _ = detector.detectAndDecode(imagem)
    if dados:
        return dados

    # Fallback opcional: pyzbar (se estiver instalado) para QRs mais difíceis.
    try:
        from pyzbar.pyzbar import decode as _zbar_decode  # noqa: PLC0415
    except ImportError:
        return None
    resultados = _zbar_decode(imagem)
    if resultados:
        return resultados[0].data.decode('utf-8', errors='replace')
    return None


def extrair_qr_token(payload: str | None) -> str | None:
    """Extrai o token (UUID) do payload ``EXM1F|<uuid>``; ``None`` se inválido."""
    if not payload:
        return None
    partes = payload.split('|', 1)
    if len(partes) != 2 or partes[0] != PREFIXO_TOKEN:
        return None
    bruto = partes[1].strip()
    try:
        return str(uuid_lib.UUID(bruto))
    except (ValueError, AttributeError):
        return None


def localizar_folha(qr_token: str) -> FolhaResposta | None:
    """Localiza a ``FolhaResposta`` pelo token; ``None`` se não existir."""
    from provas.models import FolhaResposta

    return (
        FolhaResposta.objects.select_related('aluno', 'prova_versao', 'prova_aplicacao')
        .filter(qr_token=qr_token)
        .first()
    )


def identificar_folha(fonte) -> FolhaResposta | None:
    """Compõe decodificação + extração + busca: imagem → ``FolhaResposta`` (ou ``None``)."""
    token = extrair_qr_token(decodificar_qr(fonte))
    if not token:
        return None
    return localizar_folha(token)


# ---------------------------------------------------------------------------
# 2. Correção de perspectiva (deskew) via marcadores ArUco
# ---------------------------------------------------------------------------
def _centros_mm_por_id() -> dict[int, tuple[float, float]]:
    """Centros esperados (mm) de cada marcador, por id ArUco (fonte única)."""
    return {
        MARKER_IDS['TL']: (MARKER_INSET, MARKER_INSET),
        MARKER_IDS['TR']: (PAGE_W - MARKER_INSET, MARKER_INSET),
        MARKER_IDS['BR']: (PAGE_W - MARKER_INSET, PAGE_H - MARKER_INSET),
        MARKER_IDS['BL']: (MARKER_INSET, PAGE_H - MARKER_INSET),
    }


def detectar_marcadores(fonte) -> dict[int, tuple[float, float]]:
    """Detecta os marcadores ArUco e devolve ``{id: (x, y)}`` (centro em pixels)."""
    imagem = carregar_imagem(fonte)
    cinza = cv2.cvtColor(imagem, cv2.COLOR_BGR2GRAY) if imagem.ndim == 3 else imagem

    dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, ARUCO_DICT))
    detector = cv2.aruco.ArucoDetector(dictionary, cv2.aruco.DetectorParameters())
    corners, ids, _ = detector.detectMarkers(cinza)

    centros: dict[int, tuple[float, float]] = {}
    if ids is not None:
        for quad, marker_id in zip(corners, ids.flatten(), strict=False):
            centro = quad[0].mean(axis=0)
            centros[int(marker_id)] = (float(centro[0]), float(centro[1]))
    return centros


def corrigir_perspectiva(fonte, px_por_mm: int = PX_POR_MM) -> np.ndarray:
    """
    Normaliza a folha para uma imagem A4 canônica alinhada à geometria.

    Mapeia os 4 marcadores detectados para suas posições conhecidas (mm × ``px_por_mm``)
    e aplica ``warpPerspective``. Na imagem resultante, coordenadas em mm da geometria
    convertem para pixels multiplicando por ``px_por_mm``.

    :raises OmrError: se algum dos 4 marcadores não for detectado.
    """
    imagem = carregar_imagem(fonte)
    centros = detectar_marcadores(imagem)
    esperados = _centros_mm_por_id()

    faltando = [mid for mid in esperados if mid not in centros]
    if faltando:
        raise OmrError(f'Marcadores fiduciais insuficientes: detectados {sorted(centros)}, faltam {faltando}.')

    ordem = [MARKER_IDS['TL'], MARKER_IDS['TR'], MARKER_IDS['BR'], MARKER_IDS['BL']]
    origem = np.array([centros[mid] for mid in ordem], dtype=np.float32)
    destino = np.array(
        [(esperados[mid][0] * px_por_mm, esperados[mid][1] * px_por_mm) for mid in ordem],
        dtype=np.float32,
    )

    matriz = cv2.getPerspectiveTransform(origem, destino)
    largura = int(round(PAGE_W * px_por_mm))
    altura = int(round(PAGE_H * px_por_mm))
    return cv2.warpPerspective(imagem, matriz, (largura, altura), borderValue=(255, 255, 255))


# ---------------------------------------------------------------------------
# 3. Amostragem das bolhas → resposta marcada por questão
# ---------------------------------------------------------------------------
@dataclass
class LeituraQuestao:
    """Resultado da leitura óptica de uma questão objetiva."""

    questao: int  # número da questão na folha (1-based)
    marcada: str | None  # letra marcada ('A'…'E') ou None (branco/ambígua)
    status: str  # 'ok' | 'branco' | 'ambigua'
    preenchimentos: dict[str, float] = field(default_factory=dict)  # fração por opção


def _fracao_preenchida(binaria: np.ndarray, cx_px: float, cy_px: float, r_px: float) -> float:
    """Fração de pixels escuros dentro do disco (cx, cy, r) na imagem binária (escuro=255)."""
    altura, largura = binaria.shape
    x0 = max(0, int(cx_px - r_px))
    x1 = min(largura, int(cx_px + r_px) + 1)
    y0 = max(0, int(cy_px - r_px))
    y1 = min(altura, int(cy_px + r_px) + 1)
    if x1 <= x0 or y1 <= y0:
        return 0.0

    recorte = binaria[y0:y1, x0:x1]
    ys, xs = np.ogrid[y0:y1, x0:x1]
    disco = (xs - cx_px) ** 2 + (ys - cy_px) ** 2 <= r_px**2
    total = int(disco.sum())
    if total == 0:
        return 0.0
    escuros = int(((recorte > 0) & disco).sum())
    return escuros / total


def ler_respostas(
    imagem_canonica: np.ndarray,
    answer_bubbles: list[Bubble],
    px_por_mm: int = PX_POR_MM,
) -> dict[int, LeituraQuestao]:
    """
    Amostra as bolhas de resposta na imagem canônica e determina a alternativa marcada.

    Para cada bolha, mede a fração de pixels escuros num disco interno (``INNER_R_FRAC``
    do raio, evitando o anel impresso). Por questão, a alternativa mais preenchida é a
    marcada, desde que ultrapasse ``MIN_FILL`` e não haja empate com a segunda
    (``AMBIG_RATIO``). Caso contrário, a questão é ``'branco'`` ou ``'ambigua'``.

    Args:
        imagem_canonica: imagem A4 já corrigida (saída de :func:`corrigir_perspectiva`).
        answer_bubbles: bolhas de resposta da geometria (``group='resposta'``), em mm.
        px_por_mm: escala mm→pixel da imagem canônica.

    Returns:
        Dicionário ``{numero_questao: LeituraQuestao}``.
    """
    cinza = cv2.cvtColor(imagem_canonica, cv2.COLOR_BGR2GRAY) if imagem_canonica.ndim == 3 else imagem_canonica
    _, binaria = cv2.threshold(cinza, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    por_questao: dict[int, list[Bubble]] = defaultdict(list)
    for bolha in answer_bubbles:
        if bolha.group == 'resposta' and bolha.question is not None:
            por_questao[bolha.question].append(bolha)

    resultado: dict[int, LeituraQuestao] = {}
    for questao, bolhas in por_questao.items():
        preenchimentos: dict[str, float] = {}
        for bolha in bolhas:
            r_px = bolha.r * px_por_mm * INNER_R_FRAC
            preenchimentos[bolha.option] = _fracao_preenchida(binaria, bolha.cx * px_por_mm, bolha.cy * px_por_mm, r_px)

        ordenadas = sorted(preenchimentos.items(), key=lambda kv: kv[1], reverse=True)
        melhor_letra, melhor = ordenadas[0]
        segundo = ordenadas[1][1] if len(ordenadas) > 1 else 0.0

        if melhor < MIN_FILL:
            status, marcada = 'branco', None
        elif segundo >= AMBIG_RATIO * melhor and segundo >= MIN_FILL:
            status, marcada = 'ambigua', None
        else:
            status, marcada = 'ok', melhor_letra

        resultado[questao] = LeituraQuestao(
            questao=questao,
            marcada=marcada,
            status=status,
            preenchimentos=preenchimentos,
        )

    return resultado
