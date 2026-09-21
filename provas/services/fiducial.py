"""
Geração de marcadores fiduciais (ArUco) e do QR Code da folha de resposta.

Complementa :mod:`provas.services.answer_sheet_layout` (que define *onde* os
marcadores e o QR ficam) fornecendo *o conteúdo visual* desses elementos como
data-URIs PNG embutíveis diretamente no HTML renderizado pelo WeasyPrint.

Todos os imports pesados (OpenCV, qrcode) são feitos de forma preguiçosa, no mesmo
padrão de :mod:`provas.services.pdf_generator`, para não onerar o import do módulo
nem quebrar ambientes onde uma das libs esteja ausente. Se o ArUco não estiver
disponível no build de OpenCV instalado, :func:`render_marcadores` degrada para
marcadores de fallback (quadrados pretos), preservando exatamente a mesma geometria.
"""

from __future__ import annotations

import base64
import logging
from io import BytesIO

from .answer_sheet_layout import SheetGeometry

logger = logging.getLogger(__name__)

# Dicionário ArUco usado nos cantos (4×4, até 50 IDs — sobra para 4 cantos).
ARUCO_DICTIONARY = 'DICT_4X4_50'


class FiducialError(Exception):
    """Erro na geração de marcadores fiduciais ou do QR Code."""


def aruco_disponivel() -> bool:
    """Indica se o OpenCV instalado expõe o módulo ArUco."""
    try:
        import cv2  # noqa: PLC0415

        return hasattr(cv2, 'aruco')
    except ImportError:
        return False


def gerar_marcador_aruco_data_uri(
    marker_id: int,
    size_px: int = 240,
    dictionary_name: str = ARUCO_DICTIONARY,
) -> str:
    """
    Gera um marcador ArUco como data-URI PNG (fundo branco, módulos pretos).

    :param marker_id: ID do marcador dentro do dicionário.
    :param size_px: lado da imagem em pixels (alta resolução para impressão nítida).
    :param dictionary_name: nome do dicionário ArUco (ex.: ``DICT_4X4_50``).
    :raises FiducialError: se o OpenCV/ArUco não estiver disponível.
    """
    try:
        import cv2  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - ambiente sem OpenCV
        raise FiducialError('OpenCV não está disponível para gerar marcadores ArUco.') from exc

    if not hasattr(cv2, 'aruco'):  # pragma: no cover - build sem contrib/aruco
        raise FiducialError('O build de OpenCV instalado não expõe cv2.aruco.')

    aruco = cv2.aruco
    dict_id = getattr(aruco, dictionary_name, None)
    if dict_id is None:  # pragma: no cover - dicionário inexistente
        raise FiducialError(f'Dicionário ArUco desconhecido: {dictionary_name}.')

    dictionary = aruco.getPredefinedDictionary(dict_id)
    # OpenCV >= 4.7 usa generateImageMarker; versões antigas usavam drawMarker.
    gerar = getattr(aruco, 'generateImageMarker', None) or getattr(aruco, 'drawMarker', None)
    if gerar is None:  # pragma: no cover - API inesperada
        raise FiducialError('API de geração de marcador ArUco não encontrada no OpenCV.')

    imagem = gerar(dictionary, marker_id, size_px)
    ok, buffer = cv2.imencode('.png', imagem)
    if not ok:  # pragma: no cover - falha de codificação
        raise FiducialError('Falha ao codificar o marcador ArUco em PNG.')

    b64 = base64.b64encode(buffer.tobytes()).decode('ascii')
    return f'data:image/png;base64,{b64}'


def gerar_qr_data_uri(
    payload: str,
    box_size: int = 10,
    border: int = 2,
) -> str:
    """
    Gera um QR Code como data-URI PNG.

    :param payload: conteúdo a codificar (ver :func:`montar_payload_qr`).
    :param box_size: pixels por módulo do QR.
    :param border: quiet zone em módulos.
    :raises FiducialError: se a lib ``qrcode`` não estiver disponível.
    """
    try:
        import qrcode  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - ambiente sem qrcode
        raise FiducialError('A biblioteca qrcode não está disponível.') from exc

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    imagem = qr.make_image(fill_color='black', back_color='white')

    buffer = BytesIO()
    imagem.save(buffer, format='PNG')
    b64 = base64.b64encode(buffer.getvalue()).decode('ascii')
    return f'data:image/png;base64,{b64}'


def montar_payload_qr(
    *,
    prova_id: int | str,
    versao: str,
    num_questoes: int,
    num_alternativas: int,
    schema_version: str,
) -> str:
    """
    Monta o payload compacto do QR que identifica a folha para o OMR.

    Formato: ``EXM1|prova=<id>|versao=<ident>|nq=<n>|na=<n>|geo=<schema>``.
    O ``schema`` permite ao leitor validar que a folha corresponde à geometria
    esperada antes de amostrar as bolhas.
    """
    return f'EXM1|prova={prova_id}|versao={versao}|nq={num_questoes}|na={num_alternativas}|geo={schema_version}'


def montar_payload_folha(qr_token) -> str:
    """
    Monta o payload do QR de uma folha nominal (RF10) a partir do seu token único.

    Formato: ``EXM1F|<uuid>``. O leitor OMR resolve a ``FolhaResposta`` por esse
    token para obter aluno, versão e gabarito.
    """
    return f'EXM1F|{qr_token}'


def render_marcadores(geometry: SheetGeometry, size_px: int = 240) -> dict[str, dict]:
    """
    Produz o conteúdo visual dos 4 marcadores da geometria informada.

    Retorna um mapa ``{corner: {'id', 'tipo', 'data_uri'}}`` onde ``tipo`` é
    ``'aruco'`` (com ``data_uri`` PNG) ou ``'quadrado'`` (fallback CSS, ``data_uri``
    ``None``). A degradação para quadrado preto **não altera a geometria**: os
    cantos permanecem nos mesmos retângulos.
    """
    usar_aruco = aruco_disponivel()
    resultado: dict[str, dict] = {}
    for marker in geometry.markers:
        if usar_aruco:
            try:
                data_uri = gerar_marcador_aruco_data_uri(marker.id, size_px=size_px)
                resultado[marker.corner] = {'id': marker.id, 'tipo': 'aruco', 'data_uri': data_uri}
                continue
            except FiducialError as exc:  # pragma: no cover - fallback em runtime
                logger.warning('Falha ao gerar marcador ArUco %s; usando fallback: %s', marker.id, exc)
                usar_aruco = False
        resultado[marker.corner] = {'id': marker.id, 'tipo': 'quadrado', 'data_uri': None}
    return resultado
