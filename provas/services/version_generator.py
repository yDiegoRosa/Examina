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

if TYPE_CHECKING:
    from provas.models import Prova, ProvaVersao

LETRAS_PADRAO = ('A', 'B', 'C', 'D', 'E', 'F', 'G', 'H')
LETRAS_ALTERNATIVAS = ('A', 'B', 'C', 'D', 'E')


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
    """
    from provas.models import ProvaVersao, ProvaVersaoItem

    # Determina regras de embaralhamento a partir da máscara ou parâmetros
    mascara = getattr(prova, 'mascara', None)
    if embaralhar_questoes is None:
        embaralhar_questoes = getattr(mascara, 'embaralhar_questoes', True) if mascara else True

    if embaralhar_alternativas is None:
        embaralhar_alternativas = getattr(mascara, 'embaralhar_alternativas', True) if mascara else True

    if not identificadores:
        identificadores = list(LETRAS_PADRAO[:quantidade_versoes])
    else:
        quantidade_versoes = len(identificadores)

    itens_base = list(
        prova.itens.select_related('questao')
        .prefetch_related('questao__alternativas')
        .order_by('ordem')
    )

    if not itens_base:
        return []

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

            # 1. Ordem das questões nesta versão
            itens_versao_ordenados = list(itens_base)
            if idx_versao > 1 and embaralhar_questoes and len(itens_versao_ordenados) > 1:
                # Embaralha questões para versões subsequentes (B, C...)
                random.shuffle(itens_versao_ordenados)

            # 2. Monta os itens da versão
            itens_versao_db = []
            for num_questao, pq in enumerate(itens_versao_ordenados, start=1):
                q = pq.questao
                snap_q = pq.snapshot_questao or {}
                tipo = snap_q.get('tipo') or q.tipo

                alts_embaralhadas = []
                if tipo == 'OBJ':
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
                    if idx_versao > 1 and embaralhar_alternativas and len(alts_trabalho) > 1:
                        # Embaralha alternativas para versões subsequentes
                        random.shuffle(alts_trabalho)

                    # Reatribui letras visuais (A, B, C, D, E)
                    for i_alt, alt_d in enumerate(alts_trabalho):
                        letra_visual = LETRAS_ALTERNATIVAS[i_alt] if i_alt < len(LETRAS_ALTERNATIVAS) else alt_d['letra_original']
                        alts_embaralhadas.append({
                            'letra': letra_visual,
                            'letra_original': alt_d.get('letra_original', ''),
                            'texto': alt_d['texto'],
                            'correta': alt_d['correta'],
                        })

                itens_versao_db.append(
                    ProvaVersaoItem(
                        versao=versao,
                        prova_questao=pq,
                        numero=num_questao,
                        ordem=num_questao,
                        alternativas_embaralhadas=alts_embaralhadas,
                    )
                )

            ProvaVersaoItem.objects.bulk_create(itens_versao_db)
            versoes_criadas.append(versao)

    return versoes_criadas
