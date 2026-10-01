"""
Serviço de geração e persistência de Versões/Cadernos de Avaliação (Prova A, B, C...).

Implementa o algoritmo de embaralhamento de ordem de questões e permutação de
alternativas para cada versão, garantindo imutabilidade e persistência definitiva
dos gabaritos correspondentes.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

from django.db import transaction

from .caderno_layout import (
    GRUPO_DISCURSIVA,
    GRUPO_OBJETIVA_CURTA,
    GRUPO_OBJETIVA_LONGA,
    grupo_do_item,
    numeros_por_tipo,
)

if TYPE_CHECKING:
    from provas.models import Prova, ProvaQuestao, ProvaVersao

LETRAS_PADRAO = ('A', 'B', 'C', 'D', 'E', 'F', 'G', 'H')
LETRAS_ALTERNATIVAS = ('A', 'B', 'C', 'D', 'E')


QUANTIDADE_VERSOES_PADRAO = 3


class VersoesDistribuidasError(ValueError):
    """Levantada ao tentar (re)gerar versões de uma prova cujas versões já foram distribuídas a alunos."""


def quantidade_versoes_da_prova(prova: Prova) -> int:
    """
    Quantos cadernos (versões) gerar para a prova: o valor congelado no snapshot da
    máscara no momento da geração; na falta dele (prova antiga), o da máscara atual;
    sem máscara (montagem manual livre), o padrão de 3 (A/B/C).
    """
    candidatos = [(prova.snapshot_mascara or {}).get('quantidade_versoes')]
    if prova.mascara_id:
        candidatos.append(prova.mascara.quantidade_versoes)
    for valor in candidatos:
        try:
            quantidade = int(valor)
        except (TypeError, ValueError):
            continue
        if 1 <= quantidade <= len(LETRAS_PADRAO):
            return quantidade
    return QUANTIDADE_VERSOES_PADRAO


def _embaralhar_alternativas_padrao(prova: Prova) -> bool:
    """Regra de embaralhamento de alternativas: lê da máscara ou assume True."""
    mascara = getattr(prova, 'mascara', None)
    return getattr(mascara, 'embaralhar_alternativas', True) if mascara else True


def tipo_do_item(pq: ProvaQuestao) -> str:
    """Tipo do item da prova ('OBJ'/'DIS'), priorizando o snapshot congelado na geração."""
    return (pq.snapshot_questao or {}).get('tipo') or pq.questao.tipo


def montar_alternativas_versao(pq: ProvaQuestao, embaralhar: bool) -> list[dict]:
    """
    Monta as alternativas de um item objetivo para uma versão: parte do snapshot da
    questão (ou das alternativas atuais, na falta dele), embaralha se ``embaralhar`` e
    reatribui as letras visuais (A, B, C, D, E), preservando a flag ``correta``.

    Retorna ``[]`` para questões discursivas.
    """
    q = pq.questao
    snap_q = pq.snapshot_questao or {}
    if tipo_do_item(pq) != 'OBJ':
        return []

    # Extrai alternativas originais
    snap_alts = snap_q.get('alternativas')
    if snap_alts:
        alts_originais = [
            {
                'letra_original': a.get('letra', ''),
                'texto': a.get('texto', ''),
                'correta': a.get('correta', False),
            }
            for a in snap_alts
        ]
    else:
        alts_originais = [
            {
                'letra_original': a.letra,
                'texto': a.texto,
                'correta': a.correta,
            }
            for a in q.alternativas.all()
        ]

    alts_trabalho = list(alts_originais)
    if embaralhar and len(alts_trabalho) > 1:
        random.shuffle(alts_trabalho)

    # Reatribui letras visuais (A, B, C, D, E)
    alts_embaralhadas = []
    for i_alt, alt_d in enumerate(alts_trabalho):
        letra_visual = LETRAS_ALTERNATIVAS[i_alt] if i_alt < len(LETRAS_ALTERNATIVAS) else alt_d['letra_original']
        alts_embaralhadas.append(
            {
                'letra': letra_visual,
                'letra_original': alt_d.get('letra_original', ''),
                'texto': alt_d['texto'],
                'correta': alt_d['correta'],
            }
        )
    return alts_embaralhadas


def sincronizar_itens_versao(pq: ProvaQuestao) -> int:
    """
    Reconstrói as alternativas de todos os itens de versão ligados a ``pq`` — usado
    após a troca da questão de um item, para que os cadernos A/B/C já gerados não
    fiquem com as alternativas (e o gabarito) da questão antiga. A posição do item em
    cada versão é mantida; a versão 1 (A) não embaralha, as demais seguem a máscara.

    Retorna quantos itens de versão foram atualizados.
    """
    embaralhar = _embaralhar_alternativas_padrao(pq.prova)
    atualizados = 0
    for item_versao in pq.itens_versao.select_related('versao'):
        item_versao.alternativas_embaralhadas = montar_alternativas_versao(
            pq, embaralhar=embaralhar and item_versao.versao.numero_versao > 1
        )
        item_versao.save(update_fields=['alternativas_embaralhadas'])
        atualizados += 1
    return atualizados


def gerar_versoes_prova(
    prova: Prova,
    quantidade_versoes: int = 3,
    identificadores: list[str] | None = None,
    embaralhar_questoes: bool | None = None,
    embaralhar_alternativas: bool | None = None,
    substituir_existentes: bool = True,
) -> list[ProvaVersao]:
    """
    Gera e persiste múltiplos cadernos/versões (ex.: Prova A, Prova B, Prova C)
    para uma avaliação já gerada.

    Para cada versão:
    1. Define a permutação da ordem das questões (respeitando `embaralhar_questoes`).
    2. Para questões objetivas, permuta as alternativas e reatribui as letras (A, B, C, D, E),
       mantendo a correlação de gabarito com a alternativa correta (respeitando `embaralhar_alternativas`).
    3. Persiste instâncias de `ProvaVersao` e `ProvaVersaoItem` no banco de dados.

    Args:
        prova: Instância de Prova base.
        quantidade_versoes: Número de cadernos a gerar (padrão: 3 para A, B e C).
        identificadores: Lista personalizada de identificadores (ex.: ['A', 'B', 'C']).
        embaralhar_questoes: Se deve permutar as questões (padrão: lê da máscara ou True).
        embaralhar_alternativas: Se deve permutar as opções (padrão: lê da máscara ou True).
        substituir_existentes: Se True, remove versões anteriores antes de gerar novas.

    Returns:
        Lista de instâncias de ProvaVersao criadas e persistidas.

    Raises:
        VersoesDistribuidasError: se as versões atuais já foram atribuídas a alunos
            (atribuições ou folhas-resposta) — regerar invalidaria gabaritos e notas.
    """
    from provas.models import ProvaVersao, ProvaVersaoItem

    if prova.versoes_distribuidas:
        raise VersoesDistribuidasError(
            'As versões desta prova já foram distribuídas a alunos (atribuições ou cartões-resposta gerados). '
            'Regerá-las invalidaria os gabaritos e as notas já vinculados.'
        )

    # Determina regras de embaralhamento a partir da máscara ou parâmetros
    mascara = getattr(prova, 'mascara', None)
    if embaralhar_questoes is None:
        embaralhar_questoes = getattr(mascara, 'embaralhar_questoes', True) if mascara else True

    if embaralhar_alternativas is None:
        embaralhar_alternativas = _embaralhar_alternativas_padrao(prova)

    if not identificadores:
        identificadores = list(LETRAS_PADRAO[:quantidade_versoes])
    else:
        quantidade_versoes = len(identificadores)

    itens_base = list(prova.itens.select_related('questao').prefetch_related('questao__alternativas').order_by('ordem'))

    if not itens_base:
        return []

    grupos = {pq.pk: grupo_do_item(pq) for pq in itens_base}
    ordem_dos_grupos = (GRUPO_DISCURSIVA, GRUPO_OBJETIVA_LONGA, GRUPO_OBJETIVA_CURTA)
    versoes_criadas: list[ProvaVersao] = []

    with transaction.atomic():
        if substituir_existentes:
            prova.versoes.all().delete()

        for idx_versao, ident in enumerate(identificadores, start=1):
            versao = ProvaVersao.objects.create(
                prova=prova,
                identificador=ident,
                numero_versao=idx_versao,
            )

            # 1. Ordem das questões nesta versão, em blocos (ver caderno_layout): discursivas,
            #    objetivas longas e objetivas curtas. Nas versões B, C…, embaralha dentro de
            #    cada bloco, sem misturá-los.
            blocos = [[pq for pq in itens_base if grupos[pq.pk] == grupo] for grupo in ordem_dos_grupos]
            if idx_versao > 1 and embaralhar_questoes:
                for bloco in blocos:
                    random.shuffle(bloco)
            itens_versao_ordenados = [pq for bloco in blocos for pq in bloco]

            # 2. Monta os itens da versão. O número é contado dentro do tipo: objetivas
            #    1..N (a numeração do cartão-resposta) e discursivas 1..k ("Discursiva 01").
            numeros = numeros_por_tipo(tipo_do_item(pq) for pq in itens_versao_ordenados)
            itens_versao_db = []
            for posicao, (pq, numero) in enumerate(zip(itens_versao_ordenados, numeros, strict=True), start=1):
                # Embaralha alternativas apenas para versões subsequentes (B, C...)
                alts_embaralhadas = montar_alternativas_versao(
                    pq, embaralhar=idx_versao > 1 and embaralhar_alternativas
                )

                itens_versao_db.append(
                    ProvaVersaoItem(
                        versao=versao,
                        prova_questao=pq,
                        numero=numero,
                        ordem=posicao,
                        alternativas_embaralhadas=alts_embaralhadas,
                    )
                )

            ProvaVersaoItem.objects.bulk_create(itens_versao_db)
            versoes_criadas.append(versao)

    return versoes_criadas
