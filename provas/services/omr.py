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
from typing import TYPE_CHECKING

import cv2
import numpy as np

from .answer_sheet_layout import MARKER_IDS, MARKER_INSET, PAGE_H, PAGE_W

if TYPE_CHECKING:
    from provas.models import FolhaResposta

# Resolução da imagem canônica após o deskew (10 px/mm ≈ 254 DPI).
PX_POR_MM = 10
ARUCO_DICT = 'DICT_4X4_50'
PREFIXO_TOKEN = 'EXM1F'


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
