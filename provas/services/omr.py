"""
Leitor óptico (OMR) do cartão-resposta — só com OpenCV.

Cobre três etapas:

1. **Decodificação do QR** — lê o QR do cartão escaneado, extrai o ``qr_token`` e
   localiza a :class:`~provas.models.FolhaResposta` correspondente.
2. **Correção de perspectiva (deskew)** — detecta os 4 marcadores ArUco e aplica
   ``warpPerspective`` para produzir uma imagem A4 canônica, alinhada à geometria
   fonte-única (``answer_sheet_layout``).
3. **Amostragem das bolhas** (:func:`ler_respostas`) — determina a alternativa marcada
   (ou branco/ambígua) em cada questão.

A comparação com o gabarito e a gravação de ``Desempenho`` ficam em
:mod:`provas.services.correcao`.
"""

from __future__ import annotations

import contextlib
import uuid as uuid_lib
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import cv2
import numpy as np

from .answer_sheet_layout import ANS_BUBBLE_R_ANTERIOR, MARKER_IDS, MARKER_INSET, PAGE_H, PAGE_W, QR_SIZE, QR_X, QR_Y

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

# --- Fotos reais (teste de campo de 2026-10-07) ------------------------------
# O detector ArUco com os parâmetros padrão não achava nenhum marcador numa foto de
# celular em resolução cheia (3000×4000): a janela de limiarização é pequena demais para
# marcadores desse tamanho. Se a 1ª tentativa (imagem original, parâmetros padrão) não
# achar os 4, tenta de novo com a imagem reduzida a estas larguras e janela maior.
LARGURAS_DETECCAO_MARCADORES = (1500, 1200, 1000, 800)
# Folha curvada (segurada na mão) desloca as bolhas até ~2 mm, e de forma diferente em
# cada coluna — o que os 4 cantos não corrigem. O ajuste fino procura o anel impresso das
# bolhas até AJUSTE_BUSCA_MM em volta da posição esperada e desloca a questão inteira pela
# mediana dos deslocamentos (a bolha preenchida não tem anel visível; as outras definem).
AJUSTE_BUSCA_MM = 3.0
# Correlação mínima (TM_CCOEFF_NORMED) para aceitar um anel encontrado; abaixo disso, ou
# com menos de AJUSTE_MIN_ANEIS anéis aceitos na questão, a posição esperada é mantida.
AJUSTE_SCORE_MIN = 0.45
AJUSTE_MIN_ANEIS = 2
# Margem (mm) em volta do QR no recorte da imagem endireitada (plano B do QR).
QR_MARGEM_RECORTE_MM = 6.0


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


def ler_qr_da_folha(fonte) -> tuple[str | None, str]:
    """
    Lê o QR do cartão: na imagem inteira e, se falhar, só na região do QR da imagem
    endireitada pelos marcadores (plano B — em fotos de celular reais o QR inteiro na foto
    não era lido, e o recorte endireitado era).

    :return: ``(payload ou None, origem)``, com origem ``'imagem'``, ``'recorte'`` ou ``''``.
    """
    imagem = carregar_imagem(fonte)
    payload = decodificar_qr(imagem)
    if extrair_qr_token(payload):
        return payload, 'imagem'

    try:
        canonica = corrigir_perspectiva(imagem)
    except OmrError:
        return payload, ''
    margem = QR_MARGEM_RECORTE_MM * PX_POR_MM
    x0 = max(0, int(QR_X * PX_POR_MM - margem))
    y0 = max(0, int(QR_Y * PX_POR_MM - margem))
    x1 = int((QR_X + QR_SIZE) * PX_POR_MM + margem)
    y1 = int((QR_Y + QR_SIZE) * PX_POR_MM + margem)
    recorte = canonica[y0:y1, x0:x1]
    for ampliacao in (1, 2):
        tentativa = recorte if ampliacao == 1 else cv2.resize(recorte, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        payload_recorte = decodificar_qr(tentativa)
        if extrair_qr_token(payload_recorte):
            return payload_recorte, 'recorte'
    return payload, ''


def identificar_folha(fonte) -> FolhaResposta | None:
    """Compõe leitura do QR + extração + busca: imagem → ``FolhaResposta`` (ou ``None``)."""
    payload, _ = ler_qr_da_folha(fonte)
    token = extrair_qr_token(payload)
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


def detectar_marcadores_com_largura(cinza: np.ndarray) -> tuple[dict[int, tuple[float, float]], int | None]:
    """
    Detecta os marcadores ArUco: 1º na imagem original com os parâmetros padrão; se não
    achar os 4, reduz a imagem (``LARGURAS_DETECCAO_MARCADORES``) com janela de
    limiarização maior. Junta o que cada tentativa achar (vale a 1ª detecção de cada id).

    :return: ``({id: (x, y)} em pixels da imagem original, largura em que os 4 fecharam)``;
        a largura é ``None`` quando bastou a imagem original.
    """
    dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, ARUCO_DICT))
    janela_maior = cv2.aruco.DetectorParameters()
    janela_maior.adaptiveThreshWinSizeMax = 63
    janela_maior.adaptiveThreshWinSizeStep = 10

    tentativas: list[tuple[int | None, cv2.aruco.DetectorParameters]] = [(None, cv2.aruco.DetectorParameters())]
    tentativas += [(largura, janela_maior) for largura in LARGURAS_DETECCAO_MARCADORES if largura < cinza.shape[1]]

    centros: dict[int, tuple[float, float]] = {}
    for largura, parametros in tentativas:
        escala = 1.0 if largura is None else largura / cinza.shape[1]
        imagem = (
            cinza if largura is None else cv2.resize(cinza, None, fx=escala, fy=escala, interpolation=cv2.INTER_AREA)
        )
        corners, ids, _ = cv2.aruco.ArucoDetector(dictionary, parametros).detectMarkers(imagem)
        if ids is not None:
            for quad, marker_id in zip(corners, ids.flatten(), strict=False):
                centro = quad[0].mean(axis=0) / escala
                centros.setdefault(int(marker_id), (float(centro[0]), float(centro[1])))
        if all(mid in centros for mid in MARKER_IDS.values()):
            return centros, largura
    return centros, None


def detectar_marcadores(fonte) -> dict[int, tuple[float, float]]:
    """Detecta os marcadores ArUco e devolve ``{id: (x, y)}`` (centro em pixels)."""
    imagem = carregar_imagem(fonte)
    cinza = cv2.cvtColor(imagem, cv2.COLOR_BGR2GRAY) if imagem.ndim == 3 else imagem
    centros, _ = detectar_marcadores_com_largura(cinza)
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
    deslocamento_mm: tuple[float, float] = (0.0, 0.0)  # ajuste fino aplicado (x, y)


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


def _modelo_anel(r_px: int) -> np.ndarray:
    """Anel escuro (a borda impressa da bolha) sobre fundo claro, centrado em (r+4, r+4)."""
    lado = 2 * r_px + 9
    modelo = np.full((lado, lado), 255, np.uint8)
    cv2.circle(modelo, (r_px + 4, r_px + 4), r_px, 0, 3)
    return modelo


def _deslocamento_da_questao(cinza: np.ndarray, bolhas: list[Bubble], px_por_mm: int) -> tuple[float, float]:
    """
    Ajuste fino (em px) de uma questão: procura o anel impresso de cada bolha até
    ``AJUSTE_BUSCA_MM`` da posição esperada e devolve a mediana dos deslocamentos.

    Tenta o raio atual e o dos cartões antigos (``ANS_BUBBLE_R_ANTERIOR``) e fica com o de
    melhor correlação. Mantém a posição esperada, ``(0, 0)``, se menos de
    ``AJUSTE_MIN_ANEIS`` anéis passarem de ``AJUSTE_SCORE_MIN`` ou se os anéis achados
    discordarem entre si (mais de 1 mm da mediana) — melhor não mexer do que mexer errado.
    """
    busca = int(round(AJUSTE_BUSCA_MM * px_por_mm))
    tolerancia = 1.0 * px_por_mm
    melhor: tuple[float, float, float] | None = None  # (correlação mediana, dx, dy)

    for raio_mm in dict.fromkeys((bolhas[0].r, ANS_BUBBLE_R_ANTERIOR)):
        modelo = _modelo_anel(int(round(raio_mm * px_por_mm)))
        meio = modelo.shape[0] // 2
        achados: list[tuple[float, float, float]] = []
        for bolha in bolhas:
            cx, cy = bolha.cx * px_por_mm, bolha.cy * px_por_mm
            x0, y0 = int(round(cx)) - meio - busca, int(round(cy)) - meio - busca
            janela = cinza[max(0, y0) : y0 + modelo.shape[0] + 2 * busca, max(0, x0) : x0 + modelo.shape[1] + 2 * busca]
            if x0 < 0 or y0 < 0 or janela.shape[0] < modelo.shape[0] or janela.shape[1] < modelo.shape[1]:
                continue
            if float(janela.std()) < 1.0:  # região lisa: correlação indefinida
                continue
            resultado = cv2.matchTemplate(janela, modelo, cv2.TM_CCOEFF_NORMED)
            _, correlacao, _, (mx, my) = cv2.minMaxLoc(resultado)
            if correlacao >= AJUSTE_SCORE_MIN:
                achados.append((correlacao, x0 + mx + meio - cx, y0 + my + meio - cy))

        if len(achados) < AJUSTE_MIN_ANEIS:
            continue
        dx = float(np.median([a[1] for a in achados]))
        dy = float(np.median([a[2] for a in achados]))
        coerentes = [a for a in achados if abs(a[1] - dx) <= tolerancia and abs(a[2] - dy) <= tolerancia]
        if len(coerentes) < AJUSTE_MIN_ANEIS:
            continue
        correlacao = float(np.median([a[0] for a in coerentes]))
        if melhor is None or correlacao > melhor[0]:
            melhor = (correlacao, dx, dy)

    return (0.0, 0.0) if melhor is None else (melhor[1], melhor[2])


def ler_respostas(
    imagem_canonica: np.ndarray,
    answer_bubbles: list[Bubble],
    px_por_mm: int = PX_POR_MM,
) -> dict[int, LeituraQuestao]:
    """
    Amostra as bolhas de resposta na imagem canônica e determina a alternativa marcada.

    Antes de medir, cada questão passa pelo ajuste fino (:func:`_deslocamento_da_questao`),
    que corrige deslocamentos locais de folha curvada. Para cada bolha, mede a fração de
    pixels escuros num disco interno (``INNER_R_FRAC`` do raio, evitando o anel impresso).
    Por questão, a alternativa mais preenchida é a marcada, desde que ultrapasse
    ``MIN_FILL`` e não haja empate com a segunda (``AMBIG_RATIO``). Caso contrário, a
    questão é ``'branco'`` ou ``'ambigua'``.

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
        dx, dy = _deslocamento_da_questao(cinza, bolhas, px_por_mm)
        preenchimentos: dict[str, float] = {}
        for bolha in bolhas:
            r_px = bolha.r * px_por_mm * INNER_R_FRAC
            preenchimentos[bolha.option] = _fracao_preenchida(
                binaria, bolha.cx * px_por_mm + dx, bolha.cy * px_por_mm + dy, r_px
            )

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
            deslocamento_mm=(round(dx / px_por_mm, 2), round(dy / px_por_mm, 2)),
        )

    return resultado
