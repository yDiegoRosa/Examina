"""
Organização do caderno de prova em blocos (decisão de 2026-10-01, para gastar menos folhas):

1. Questões discursivas primeiro, em largura total, numeradas à parte ("Discursiva 01").
2. Objetivas longas — com texto de apoio, com imagem ou com enunciado + alternativas
   extensos — em largura total.
3. Objetivas curtas, em duas colunas (cada questão inteira numa coluna).

O cartão-resposta numera só as objetivas, de 1 a N, na ordem da versão (``ordem``): a
N-ésima objetiva é a "Questão N" do caderno e do cartão, onde quer que estejam as
discursivas. Por isso a ordem longas → curtas é gravada na geração da prova e das
versões, e o PDF nunca reordena objetivas: só leva as discursivas para o início e agrupa
as objetivas consecutivas pelo formato (largura total × duas colunas).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from typing import Any

# Estimativa de tamanho de uma objetiva na coluna (~88 mm, Arial 10 pt): caracteres por
# linha e o máximo de linhas (enunciado + alternativas) para ainda ser "curta" — cerca de
# 40% da altura de uma coluna, para que a questão inteira caiba sem deixar grandes vãos.
CARACTERES_POR_LINHA = 52
LIMITE_LINHAS_CURTA = 20

GRUPO_DISCURSIVA = 0
GRUPO_OBJETIVA_LONGA = 1
GRUPO_OBJETIVA_CURTA = 2

# Linhas de resposta de cada discursiva: 15 só nas provas ENADE (como no exame), em qualquer
# layout; nas demais, 6.
LINHAS_DISCURSIVA_PADRAO = 6
LINHAS_DISCURSIVA_ENADE = 15


def eh_mascara_enade(mascara) -> bool:
    """Máscara de Simulado ENADE: prioridade ENADE e "ENADE" no título (critério do layout ENADE)."""
    return bool(mascara and getattr(mascara, 'priorizar_enade', False) and 'ENADE' in (mascara.titulo or '').upper())


def eh_prova_enade(prova) -> bool:
    """Prova de Simulado ENADE: gerada (ou montada) a partir de uma máscara de Simulado ENADE."""
    return eh_mascara_enade(getattr(prova, 'mascara', None))


def linhas_da_discursiva(prova) -> int:
    return LINHAS_DISCURSIVA_ENADE if eh_prova_enade(prova) else LINHAS_DISCURSIVA_PADRAO


def _linhas(texto: str) -> int:
    """Linhas estimadas de um texto na coluna (cada parágrafo ocupa ao menos uma linha)."""
    return sum(
        max(1, math.ceil(len(paragrafo) / CARACTERES_POR_LINHA))
        for paragrafo in (texto or '').splitlines()
        if paragrafo.strip()
    )


def eh_longa(enunciado: str, alternativas: Iterable[str], texto_auxiliar: str = '', tem_imagem: bool = False) -> bool:
    """Critério de objetiva longa: texto de apoio, imagem ou enunciado + alternativas além do limite."""
    if (texto_auxiliar or '').strip() or tem_imagem:
        return True
    return _linhas(enunciado) + sum(_linhas(texto) for texto in alternativas) > LIMITE_LINHAS_CURTA


def grupo_da_questao(questao) -> int:
    """Grupo de uma ``Questao`` do banco na ordem do caderno (discursiva → longa → curta)."""
    if questao.tipo != 'OBJ':
        return GRUPO_DISCURSIVA
    longa = eh_longa(
        questao.enunciado,
        [alternativa.texto for alternativa in questao.alternativas.all()],
        questao.texto_auxiliar,
        bool(questao.imagem),
    )
    return GRUPO_OBJETIVA_LONGA if longa else GRUPO_OBJETIVA_CURTA


def grupo_do_item(pq) -> int:
    """Grupo de um ``ProvaQuestao``, pelo snapshot congelado (a imagem vem da questão)."""
    snap = pq.snapshot_questao or {}
    questao = pq.questao
    if (snap.get('tipo') or questao.tipo) != 'OBJ':
        return GRUPO_DISCURSIVA
    alternativas = [alt.get('texto', '') for alt in snap.get('alternativas') or []] or [
        alt.texto for alt in questao.alternativas.all()
    ]
    longa = eh_longa(
        snap.get('enunciado') or questao.enunciado,
        alternativas,
        snap.get('texto_auxiliar', questao.texto_auxiliar),
        bool(questao.imagem),
    )
    return GRUPO_OBJETIVA_LONGA if longa else GRUPO_OBJETIVA_CURTA


def numeros_por_tipo(tipos: Iterable[str]) -> list[int]:
    """Numeração de cada item dentro do seu tipo: objetivas 1..N (as do cartão) e discursivas 1..k."""
    contadores: dict[bool, int] = {}
    numeros = []
    for tipo in tipos:
        objetiva = tipo == 'OBJ'
        contadores[objetiva] = contadores.get(objetiva, 0) + 1
        numeros.append(contadores[objetiva])
    return numeros


def _definir(item: Any, chave: str, valor: Any) -> None:
    if isinstance(item, dict):
        item[chave] = valor
    else:
        setattr(item, chave, valor)


def numerar_por_tipo(itens: list, tipo_de: Callable[[Any], str]) -> list:
    """
    Anota em cada item (dict ou objeto), na ordem recebida: ``numero_exibido``, ``rotulo``
    ("Questão 05" / "Discursiva 01") e ``rotulo_curto`` ("Q05" / "D01").
    """
    tipos = [tipo_de(item) for item in itens]
    for item, tipo, numero in zip(itens, tipos, numeros_por_tipo(tipos), strict=True):
        objetiva = tipo == 'OBJ'
        _definir(item, 'numero_exibido', numero)
        _definir(item, 'rotulo', f'{"Questão" if objetiva else "Discursiva"} {numero:02d}')
        _definir(item, 'rotulo_curto', f'{"Q" if objetiva else "D"}{numero:02d}')
    return itens


def discursivas_primeiro(itens: list, tipo_de: Callable[[Any], str]) -> list:
    """Mesma lista com as discursivas no início (ordem relativa de cada tipo preservada)."""
    return [i for i in itens if tipo_de(i) != 'OBJ'] + [i for i in itens if tipo_de(i) == 'OBJ']


def _formato_do_item(item: dict) -> str:
    if item['tipo'] != 'OBJ':
        return 'discursivas'
    return 'largura_total' if item['longa'] else 'colunas'


def ordenar_como_caderno(itens: list[dict]) -> list[dict]:
    """Numera por tipo e põe as discursivas no início — para listas com chave ``tipo`` (gabaritos)."""
    numerar_por_tipo(itens, lambda item: item['tipo'])
    return discursivas_primeiro(itens, lambda item: item['tipo'])


def organizar_caderno(itens: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    Organiza os itens do PDF do caderno (dicts com ``questao``, ``snapshot_questao`` e
    ``alternativas_versao``, na ordem da versão).

    Numera por tipo e devolve ``(itens na ordem do caderno, blocos)``. Cada bloco é
    ``{'formato': 'discursivas' | 'largura_total' | 'colunas', 'itens': [...],
    'secao': título da seção ou ''}``: as discursivas num bloco só, no início; as objetivas
    consecutivas agrupadas pelo formato, sem mudar a ordem delas.
    """
    for item in itens:
        snap = item.get('snapshot_questao') or {}
        questao = item['questao']
        item['tipo'] = snap.get('tipo') or questao.tipo
        item['longa'] = item['tipo'] != 'OBJ' or eh_longa(
            snap.get('enunciado') or questao.enunciado,
            [alt.get('texto', '') for alt in item.get('alternativas_versao') or []],
            snap.get('texto_auxiliar', getattr(questao, 'texto_auxiliar', '')),
            bool(getattr(questao, 'imagem', None)),
        )
    numerar_por_tipo(itens, lambda item: item['tipo'])
    ordenados = discursivas_primeiro(itens, lambda item: item['tipo'])

    blocos: list[dict] = []
    for item in ordenados:
        formato = _formato_do_item(item)
        if blocos and blocos[-1]['formato'] == formato:
            blocos[-1]['itens'].append(item)
        else:
            blocos.append({'formato': formato, 'itens': [item], 'secao': ''})

    if blocos and blocos[0]['formato'] == 'discursivas':
        blocos[0]['secao'] = 'discursivas'
    primeiro_objetivo = next((bloco for bloco in blocos if bloco['formato'] != 'discursivas'), None)
    if primeiro_objetivo:
        primeiro_objetivo['secao'] = 'objetivas'
    return ordenados, blocos
