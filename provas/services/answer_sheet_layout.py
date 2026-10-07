"""
Geometria do Cartão-Resposta (OMR) — FONTE ÚNICA de coordenadas.

Este módulo é a fonte única e autoritativa da geometria do cartão-resposta usado
pela leitura óptica (OMR) do Examina. Todas as posições (cantos fiduciais, área do
QR e cada bolha de resposta / matrícula) são definidas aqui em **milímetros**, com
origem no canto superior-esquerdo da página física (A4). O ``@page`` do template usa
``margin: 0``, de modo que essas coordenadas em mm mapeiam diretamente para a página.

O mesmo objeto de geometria produzido aqui é consumido:

* pelo template ``pdf_folha_resposta.html`` (para *imprimir* o cartão);
* pelo leitor OMR (``omr.ler_respostas`` / ``correcao``), para *ler* o cartão.

A matriz de matrícula (``id_digits``) é opcional e não afeta a posição das bolhas de
resposta; o cartão nominal atual não a usa (o QR identifica o aluno).

Manter geometria e render acoplados a esta única fonte é o que garante que a posição
das bolhas seja "fixa e conhecida" — a premissa de todo o reconhecimento óptico.

O módulo é **puro** (sem Django/OpenCV) para permanecer trivialmente testável.
A renderização dos marcadores ArUco e do QR fica em :mod:`provas.services.fiducial`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# Versão do esquema de layout. Incrementar sempre que a geometria mudar de forma
# incompatível — o valor é embutido no QR para o OMR validar contra o gabarito.
SCHEMA_VERSION = 'fr-v1'

# --- Dimensões da página (A4 retrato, em mm) --------------------------------
PAGE_W = 210.0
PAGE_H = 297.0

# --- Marcadores fiduciais (cantos de referência) ----------------------------
MARKER_SIZE = 12.0  # lado do marcador (mm)
MARKER_INSET = 14.0  # distância do CENTRO do marcador até a borda da página (mm)

# IDs ArUco por canto (DICT_4X4_50). A identidade por canto dá orientação
# inequívoca e correção de perspectiva (homografia) no OMR.
MARKER_IDS = {'TL': 0, 'TR': 1, 'BR': 2, 'BL': 3}

# --- Região de conteúdo (dentro do quadro dos marcadores) -------------------
CONTENT_LEFT = 16.0
CONTENT_RIGHT = 194.0
CONTENT_TOP = 24.0
CONTENT_BOTTOM = 278.0

# --- Área do QR (topo-direito da banda de cabeçalho) ------------------------
QR_SIZE = 24.0
QR_X = CONTENT_RIGHT - QR_SIZE  # 170.0
QR_Y = CONTENT_TOP  # 24.0

# --- Bloco de identificação (nome manuscrito + matriz de matrícula) ---------
ID_FIELDS_TOP = 54.0  # linhas manuscritas (Nome / Turma / Data)
ID_FIELDS_H = 18.0

ID_MATRIX_TITLE_Y = 74.0
ID_VALUE_LABEL_X = 16.0  # coluna dos rótulos 0–9, à esquerda da matriz
ID_FIRST_COL_CX = 24.0  # centro da 1ª coluna de dígitos
ID_PITCH_X = 5.0  # passo horizontal entre dígitos
ID_FIRST_ROW_CY = 80.0  # centro da linha do valor 0
ID_PITCH_Y = 4.2  # passo vertical entre valores 0–9
ID_BUBBLE_R = 1.6
ID_ROWS = 10  # dígitos 0–9

# --- Grade de respostas -----------------------------------------------------
ANS_HEADER_Y = 128.0  # linha dos rótulos A–E (topo das colunas)
ANS_FIRST_ROW_CY = 134.0  # centro da 1ª linha de questões
ANS_PITCH_Y = 8.0  # passo vertical entre questões
# Raio da bolha: 2,4 mm (era 1,9; aumentado em 2026-10-06 para facilitar o preenchimento).
# Os centros não mudaram e o leitor mede só o disco interno (omr.INNER_R_FRAC × raio =
# 1,49 mm), que ainda cabe dentro da bolha antiga: cartões impressos com 1,9 continuam
# legíveis, por isso o SCHEMA_VERSION não mudou. Não passe de ~2,8 mm sem rever isso.
ANS_BUBBLE_R = 2.4
ANS_ALT_PITCH_X = 6.0  # passo horizontal entre alternativas
ANS_COL_WIDTH = 60.0  # passo horizontal entre colunas de questões
ANS_FIRST_COL_X0 = 18.0  # borda esquerda (x0) da 1ª coluna
ANS_FIRST_ALT_OFFSET = 12.0  # deslocamento do 1º bolha a partir do x0 da coluna
ANS_NUM_LABEL_OFFSET = 0.0  # deslocamento do rótulo numérico a partir do x0

ALL_OPTION_LABELS = ['A', 'B', 'C', 'D', 'E']

MIN_ALTERNATIVAS = 2
MAX_ALTERNATIVAS = 5
MIN_COLUMNS = 1
MAX_COLUMNS = 4


def _r(value: float) -> float:
    """Arredonda para 0,01 mm — estabiliza a serialização e as comparações."""
    return round(float(value), 2)


@dataclass(frozen=True)
class Marker:
    """Marcador fiducial de canto (quadrado de lado ``size`` mm, centrado em cx/cy)."""

    id: int
    corner: str  # 'TL' | 'TR' | 'BR' | 'BL'
    cx: float
    cy: float
    size: float

    @property
    def left(self) -> float:
        return _r(self.cx - self.size / 2)

    @property
    def top(self) -> float:
        return _r(self.cy - self.size / 2)

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'corner': self.corner,
            'cx': _r(self.cx),
            'cy': _r(self.cy),
            'size': _r(self.size),
        }


@dataclass(frozen=True)
class Rect:
    """Retângulo em mm (canto superior-esquerdo + largura/altura)."""

    x: float
    y: float
    w: float
    h: float

    def to_dict(self) -> dict:
        return {'x': _r(self.x), 'y': _r(self.y), 'w': _r(self.w), 'h': _r(self.h)}


@dataclass(frozen=True)
class Bubble:
    """
    Bolha preenchível, centrada em (cx, cy) com raio ``r`` (tudo em mm).

    ``group`` é 'resposta' ou 'matricula'. Os demais campos qualificam a bolha:
    respostas usam ``question`` + ``option`` (letra); a matrícula usa ``digit``
    (posição do dígito, 0-based) + ``value`` (0–9).
    """

    group: str
    cx: float
    cy: float
    r: float
    question: int | None = None
    option: str | None = None
    digit: int | None = None
    value: int | None = None

    @property
    def left(self) -> float:
        """Borda esquerda (mm) — conveniência de posicionamento absoluto no template."""
        return _r(self.cx - self.r)

    @property
    def top(self) -> float:
        """Borda superior (mm) — conveniência de posicionamento absoluto no template."""
        return _r(self.cy - self.r)

    @property
    def diameter(self) -> float:
        return _r(2 * self.r)

    def to_dict(self) -> dict:
        d: dict = {'group': self.group, 'cx': _r(self.cx), 'cy': _r(self.cy), 'r': _r(self.r)}
        if self.question is not None:
            d['question'] = self.question
        if self.option is not None:
            d['option'] = self.option
        if self.digit is not None:
            d['digit'] = self.digit
        if self.value is not None:
            d['value'] = self.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Bubble:
        return cls(
            group=d['group'],
            cx=d['cx'],
            cy=d['cy'],
            r=d['r'],
            question=d.get('question'),
            option=d.get('option'),
            digit=d.get('digit'),
            value=d.get('value'),
        )


@dataclass(frozen=True)
class TextLabel:
    """Rótulo textual posicionado (auxílio de renderização, não faz parte do OMR)."""

    text: str
    x: float
    y: float


@dataclass(frozen=True)
class SheetGeometry:
    """
    Geometria completa e imutável de uma folha de resposta.

    Os campos ``markers``, ``qr``, ``id_bubbles`` e ``answer_bubbles`` compõem a
    geometria **autoritativa** (serializada em :meth:`to_dict` para o OMR). Os
    demais campos (rects e rótulos) são auxiliares de renderização do template.
    """

    schema_version: str
    page_w: float
    page_h: float
    num_questoes: int
    num_alternativas: int
    id_digits: int
    columns: int
    option_labels: list[str]

    markers: list[Marker]
    qr: Rect
    id_bubbles: list[Bubble]
    answer_bubbles: list[Bubble]

    # Auxiliares de renderização (não integram a geometria do OMR)
    header_rect: Rect
    id_fields_rect: Rect
    instrucoes_rect: Rect
    id_value_labels: list[TextLabel] = field(default_factory=list)
    id_matrix_title: TextLabel | None = None
    answer_headers: list[TextLabel] = field(default_factory=list)
    question_labels: list[TextLabel] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serializa apenas a geometria autoritativa (consumida pelo OMR)."""
        return {
            'schema_version': self.schema_version,
            'page': {'w': _r(self.page_w), 'h': _r(self.page_h)},
            'params': {
                'num_questoes': self.num_questoes,
                'num_alternativas': self.num_alternativas,
                'id_digits': self.id_digits,
                'columns': self.columns,
                'option_labels': list(self.option_labels),
            },
            'markers': [m.to_dict() for m in self.markers],
            'qr': self.qr.to_dict(),
            'bubbles': {
                'matricula': [b.to_dict() for b in self.id_bubbles],
                'resposta': [b.to_dict() for b in self.answer_bubbles],
            },
        }


def max_questoes_por_pagina(columns: int = 3) -> int:
    """Capacidade de questões numa única página para o nº de colunas informado."""
    max_rows = int((CONTENT_BOTTOM - ANS_FIRST_ROW_CY) // ANS_PITCH_Y) + 1
    return columns * max_rows


def build_layout(
    num_questoes: int,
    num_alternativas: int = 5,
    id_digits: int = 8,
    columns: int = 3,
) -> SheetGeometry:
    """
    Constrói a geometria determinística da folha de resposta.

    :param num_questoes: total de questões objetivas (bolhas de resposta).
    :param num_alternativas: alternativas por questão (2–5; padrão A–E).
    :param id_digits: nº de dígitos da matriz de matrícula (0 desativa a matriz).
    :param columns: nº de colunas da grade de respostas (1–4).
    :raises ValueError: parâmetros inválidos ou questões acima da capacidade da página.
    """
    if num_questoes < 1:
        raise ValueError('num_questoes deve ser >= 1.')
    if not (MIN_ALTERNATIVAS <= num_alternativas <= MAX_ALTERNATIVAS):
        raise ValueError(f'num_alternativas deve estar entre {MIN_ALTERNATIVAS} e {MAX_ALTERNATIVAS}.')
    if not (MIN_COLUMNS <= columns <= MAX_COLUMNS):
        raise ValueError(f'columns deve estar entre {MIN_COLUMNS} e {MAX_COLUMNS}.')
    if id_digits < 0:
        raise ValueError('id_digits deve ser >= 0.')

    capacidade = max_questoes_por_pagina(columns)
    if num_questoes > capacidade:
        raise ValueError(
            f'{num_questoes} questões excedem a capacidade de uma página '
            f'({capacidade} com {columns} colunas). Paginação será tratada em etapa futura.'
        )

    option_labels = ALL_OPTION_LABELS[:num_alternativas]

    # --- Marcadores fiduciais nos quatro cantos -----------------------------
    markers = [
        Marker(MARKER_IDS['TL'], 'TL', MARKER_INSET, MARKER_INSET, MARKER_SIZE),
        Marker(MARKER_IDS['TR'], 'TR', PAGE_W - MARKER_INSET, MARKER_INSET, MARKER_SIZE),
        Marker(MARKER_IDS['BR'], 'BR', PAGE_W - MARKER_INSET, PAGE_H - MARKER_INSET, MARKER_SIZE),
        Marker(MARKER_IDS['BL'], 'BL', MARKER_INSET, PAGE_H - MARKER_INSET, MARKER_SIZE),
    ]

    # --- Área do QR e cabeçalho ---------------------------------------------
    qr = Rect(QR_X, QR_Y, QR_SIZE, QR_SIZE)
    header_rect = Rect(CONTENT_LEFT, CONTENT_TOP, (QR_X - 4.0) - CONTENT_LEFT, QR_SIZE)
    id_fields_rect = Rect(CONTENT_LEFT, ID_FIELDS_TOP, CONTENT_RIGHT - CONTENT_LEFT, ID_FIELDS_H)

    # --- Matriz de matrícula (id_digits colunas × 0–9) ----------------------
    id_bubbles: list[Bubble] = []
    id_value_labels: list[TextLabel] = []
    id_matrix_title: TextLabel | None = None
    if id_digits > 0:
        id_matrix_title = TextLabel('MATRÍCULA', ID_VALUE_LABEL_X, ID_MATRIX_TITLE_Y)
        for value in range(ID_ROWS):
            cy = ID_FIRST_ROW_CY + value * ID_PITCH_Y
            id_value_labels.append(TextLabel(str(value), ID_VALUE_LABEL_X, _r(cy)))
            for digit in range(id_digits):
                cx = ID_FIRST_COL_CX + digit * ID_PITCH_X
                id_bubbles.append(Bubble('matricula', _r(cx), _r(cy), ID_BUBBLE_R, digit=digit, value=value))
        id_right = ID_FIRST_COL_CX + (id_digits - 1) * ID_PITCH_X + ID_BUBBLE_R
        instrucoes_x = max(id_right + 8.0, 70.0)
    else:
        instrucoes_x = CONTENT_LEFT

    # Instruções ficam ABAIXO da faixa de campos (largura total) e ao LADO da
    # matriz de matrícula — nunca sobre a faixa de campos.
    id_block_bottom = ID_FIRST_ROW_CY + (ID_ROWS - 1) * ID_PITCH_Y + ID_BUBBLE_R
    instrucoes_top = ID_FIELDS_TOP + ID_FIELDS_H + 1.0
    instrucoes_rect = Rect(
        instrucoes_x,
        instrucoes_top,
        CONTENT_RIGHT - instrucoes_x,
        id_block_bottom - instrucoes_top,
    )

    # --- Grade de respostas (numeração coluna-a-coluna) ---------------------
    per_column = math.ceil(num_questoes / columns)
    answer_bubbles: list[Bubble] = []
    answer_headers: list[TextLabel] = []
    question_labels: list[TextLabel] = []

    for col in range(columns):
        col_x0 = ANS_FIRST_COL_X0 + col * ANS_COL_WIDTH
        # Cabeçalho de alternativas (A–E) no topo de cada coluna
        for j, label in enumerate(option_labels):
            hx = col_x0 + ANS_FIRST_ALT_OFFSET + j * ANS_ALT_PITCH_X
            answer_headers.append(TextLabel(label, _r(hx), ANS_HEADER_Y))

        for row in range(per_column):
            q = col * per_column + row + 1
            if q > num_questoes:
                break
            cy = ANS_FIRST_ROW_CY + row * ANS_PITCH_Y
            question_labels.append(TextLabel(str(q), _r(col_x0 + ANS_NUM_LABEL_OFFSET), _r(cy)))
            for j, label in enumerate(option_labels):
                cx = col_x0 + ANS_FIRST_ALT_OFFSET + j * ANS_ALT_PITCH_X
                answer_bubbles.append(Bubble('resposta', _r(cx), _r(cy), ANS_BUBBLE_R, question=q, option=label))

    return SheetGeometry(
        schema_version=SCHEMA_VERSION,
        page_w=PAGE_W,
        page_h=PAGE_H,
        num_questoes=num_questoes,
        num_alternativas=num_alternativas,
        id_digits=id_digits,
        columns=columns,
        option_labels=option_labels,
        markers=markers,
        qr=qr,
        id_bubbles=id_bubbles,
        answer_bubbles=answer_bubbles,
        header_rect=header_rect,
        id_fields_rect=id_fields_rect,
        instrucoes_rect=instrucoes_rect,
        id_value_labels=id_value_labels,
        id_matrix_title=id_matrix_title,
        answer_headers=answer_headers,
        question_labels=question_labels,
    )


def geometry_from_dict(data: dict) -> dict:
    """
    Reconstrói a geometria autoritativa a partir de :meth:`SheetGeometry.to_dict`.

    Retorna um dicionário com objetos (``markers``, ``qr``, ``id_bubbles``,
    ``answer_bubbles``) — suficiente para o OMR localizar cantos e bolhas. Os
    auxiliares de renderização (rótulos) não são reconstruídos por não integrarem
    a geometria de leitura.
    """
    markers = [Marker(m['id'], m['corner'], m['cx'], m['cy'], m['size']) for m in data['markers']]
    qr = Rect(**data['qr'])
    id_bubbles = [Bubble.from_dict(b) for b in data['bubbles']['matricula']]
    answer_bubbles = [Bubble.from_dict(b) for b in data['bubbles']['resposta']]
    return {
        'schema_version': data['schema_version'],
        'markers': markers,
        'qr': qr,
        'id_bubbles': id_bubbles,
        'answer_bubbles': answer_bubbles,
    }
