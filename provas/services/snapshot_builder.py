"""
Serviço de construção de snapshots JSON para registro histórico imutável.

Cada função serializa um aspecto do estado da máscara ou questão no instante
da geração, garantindo rastreabilidade e auditabilidade mesmo que os registros
originais sejam alterados posteriormente.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from banco_questoes.models import Questao
    from provas.models import Mascara


def construir_snapshot_mascara(mascara: Mascara) -> dict[str, Any]:
    """
    Serializa todos os parâmetros configuráveis da máscara no instante da geração.

    Retorna um dicionário contendo: identificação, disciplina, quantidades,
    modalidade, percentuais de dificuldade, valor total, tempo estimado e
    regras de embaralhamento / priorização.
    """
    return {
        'mascara_id': mascara.pk,
        'titulo': mascara.titulo,
        'disciplina_id': mascara.disciplina_id,
        'disciplina_nome': mascara.disciplina.nome if mascara.disciplina else 'Todas as disciplinas',
        'quantidade_questoes': mascara.quantidade_questoes,
        'modalidade': mascara.modalidade,
        'modalidade_display': mascara.get_modalidade_display(),
        'quantidade_objetivas': mascara.quantidade_objetivas,
        'quantidade_discursivas': mascara.quantidade_discursivas,
        'percentual_facil': mascara.percentual_facil,
        'percentual_medio': mascara.percentual_medio,
        'percentual_dificil': mascara.percentual_dificil,
        'valor_total': str(mascara.valor_total),
        'tempo_estimado_minutos': mascara.tempo_estimado_minutos,
        'embaralhar_questoes': mascara.embaralhar_questoes,
        'embaralhar_alternativas': mascara.embaralhar_alternativas,
        'priorizar_enade': mascara.priorizar_enade,
        'quantidade_versoes': mascara.quantidade_versoes,
    }


def construir_snapshot_criterios_dificuldade(mascara: Mascara) -> list[dict[str, Any]]:
    """
    Serializa os critérios normalizados de dificuldade (F, M, D) com percentuais
    e quantidades inteiras calculadas pelo Método do Maior Resto.
    """
    criterios = mascara.criterios_dificuldade.all().order_by('dificuldade')
    return [
        {
            'dificuldade': crit.dificuldade,
            'dificuldade_display': crit.get_dificuldade_display(),
            'percentual': crit.percentual,
            'quantidade': crit.quantidade_questoes,
        }
        for crit in criterios
    ]


def construir_snapshot_criterios_topico(mascara: Mascara) -> list[dict[str, Any]]:
    """
    Serializa os critérios de tópico curricular com percentuais e quantidades calculadas.
    Retorna lista vazia caso a máscara use distribuição livre (sem tópicos fixos).
    """
    criterios = mascara.topicos_distribuicao.select_related('topico').all().order_by('topico__nome')
    return [
        {
            'topico_id': crit.topico_id,
            'topico_nome': crit.topico.nome,
            'percentual': crit.percentual,
            'quantidade': crit.quantidade_questoes,
        }
        for crit in criterios
    ]


def construir_snapshot_matriz_metas(mascara: Mascara) -> dict[str, Any]:
    """
    Serializa a matriz 2D completa de metas por célula (Tópico × Dificuldade),
    incluindo totais por dificuldade e flag de distribuição livre.
    """
    matriz = mascara.calcular_matriz_metas()
    celulas_serializadas = [
        {
            'topico_id': cel.topico_id,
            'topico_nome': cel.topico_nome,
            'dificuldade': cel.dificuldade,
            'dificuldade_display': cel.dificuldade_display,
            'percentual_topico': cel.percentual_topico,
            'percentual_dificuldade': cel.percentual_dificuldade,
            'percentual_composto': cel.percentual_composto,
            'quantidade': cel.quantidade,
        }
        for cel in matriz.celulas
    ]

    return {
        'total_questoes': matriz.total_questoes,
        'eh_distribuicao_livre': matriz.eh_distribuicao_livre,
        'totais_por_dificuldade': matriz.totais_por_dificuldade,
        'celulas': celulas_serializadas,
    }


def construir_snapshot_questao(questao: Questao) -> dict[str, Any]:
    """
    Serializa os dados essenciais de uma questão no instante da inclusão na prova.

    Para questões objetivas, inclui alternativas com gabarito.
    Para questões discursivas, inclui critérios de correção.
    """
    topico = getattr(questao, 'topico', None)

    snapshot: dict[str, Any] = {
        'questao_id': questao.pk,
        'tipo': questao.tipo,
        'tipo_display': questao.get_tipo_display(),
        'dificuldade': questao.dificuldade,
        'dificuldade_display': questao.get_dificuldade_display(),
        'disciplina_id': questao.disciplina_id,
        'disciplina_nome': questao.disciplina.nome if questao.disciplina else '',
        'topico_id': topico.pk if topico else None,
        'topico_nome': topico.nome if topico else '',
        'origem': questao.origem,
        'numero_questao': questao.numero_questao,
        'enunciado': questao.enunciado,
        'texto_auxiliar': questao.texto_auxiliar,
    }

    if questao.tipo == questao.Tipo.OBJETIVA:
        alternativas = questao.alternativas.all().order_by('letra')
        snapshot['alternativas'] = [
            {
                'letra': alt.letra,
                'texto': alt.texto,
                'correta': alt.correta,
            }
            for alt in alternativas
        ]
        snapshot['criterios_correcao'] = ''
    else:
        snapshot['alternativas'] = []
        snapshot['criterios_correcao'] = questao.criterios_correcao

    return snapshot
