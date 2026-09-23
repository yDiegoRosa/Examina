"""
Agregações analíticas do Módulo 4 (base do RF13/RF14) sobre o ``Desempenho``.

Calculam a **taxa de erro** (``1 - média(acertou)`` = erros/total) por tópico e por
nível de dificuldade, filtráveis por turma (ou por uma aplicação específica), para os
relatórios de fragilidades/forças da turma. Consideram apenas registros já corrigidos
(``acertou`` definido) — objetivas (OMR/manual) e discursivas (nota ≥ corte).
"""

from __future__ import annotations

from django.db.models import Count, Q

from banco_questoes.models import Questao

from .models import Desempenho

# Ordem pedagógica canônica dos níveis de dificuldade.
ORDEM_DIFICULDADE = {'F': 0, 'M': 1, 'D': 2}


def _base_queryset(turma=None, aplicacao=None, data_inicio=None, data_fim=None):
    """
    Desempenhos corrigidos, filtrados por aplicação (se dada; tem precedência) ou por
    turma e período (intervalo de ``data_aplicacao``).
    """
    qs = Desempenho.objects.filter(acertou__isnull=False)
    if aplicacao is not None:
        return qs.filter(folha_resposta__prova_aplicacao=aplicacao)
    if turma is not None:
        qs = qs.filter(folha_resposta__prova_aplicacao__turma=turma)
    if data_inicio is not None:
        qs = qs.filter(folha_resposta__prova_aplicacao__data_aplicacao__gte=data_inicio)
    if data_fim is not None:
        qs = qs.filter(folha_resposta__prova_aplicacao__data_aplicacao__lte=data_fim)
    return qs


def _taxa_erro(erros: int, total: int) -> float:
    return round(erros / total, 4) if total else 0.0


def taxa_erro_por_topico(turma=None, aplicacao=None, data_inicio=None, data_fim=None) -> list[dict]:
    """
    Taxa de erro agregada por tópico curricular (Desempenho → Questao → Topico).

    Retorna uma lista de dicts (``topico_id``, ``topico``, ``disciplina``, ``total``,
    ``acertos``, ``erros``, ``taxa_erro``), ordenada da maior taxa de erro para a menor
    (fragilidades primeiro). Questões sem tópico são agrupadas como "Sem tópico".
    """
    linhas = (
        _base_queryset(turma, aplicacao, data_inicio, data_fim)
        .values('questao__topico', 'questao__topico__nome', 'questao__topico__disciplina__nome')
        .annotate(total=Count('id'), acertos=Count('id', filter=Q(acertou=True)))
        .order_by()
    )

    resultado = []
    for linha in linhas:
        total = linha['total']
        acertos = linha['acertos']
        erros = total - acertos
        resultado.append(
            {
                'topico_id': linha['questao__topico'],
                'topico': linha['questao__topico__nome'] or 'Sem tópico',
                'disciplina': linha['questao__topico__disciplina__nome'],
                'total': total,
                'acertos': acertos,
                'erros': erros,
                'taxa_erro': _taxa_erro(erros, total),
            }
        )

    resultado.sort(key=lambda r: (-r['taxa_erro'], r['topico']))
    return resultado


def taxa_erro_por_dificuldade(turma=None, aplicacao=None, data_inicio=None, data_fim=None) -> list[dict]:
    """
    Taxa de erro agregada por nível de dificuldade (``Questao.dificuldade``).

    Retorna uma lista de dicts (``dificuldade``, ``dificuldade_display``, ``total``,
    ``acertos``, ``erros``, ``taxa_erro``), na ordem Fácil → Médio → Difícil.
    """
    linhas = (
        _base_queryset(turma, aplicacao, data_inicio, data_fim)
        .values('questao__dificuldade')
        .annotate(total=Count('id'), acertos=Count('id', filter=Q(acertou=True)))
        .order_by()
    )

    resultado = []
    for linha in linhas:
        codigo = linha['questao__dificuldade']
        total = linha['total']
        acertos = linha['acertos']
        erros = total - acertos
        try:
            rotulo = Questao.Dificuldade(codigo).label
        except ValueError:
            rotulo = codigo or '—'
        resultado.append(
            {
                'dificuldade': codigo,
                'dificuldade_display': rotulo,
                'total': total,
                'acertos': acertos,
                'erros': erros,
                'taxa_erro': _taxa_erro(erros, total),
            }
        )

    resultado.sort(key=lambda r: ORDEM_DIFICULDADE.get(r['dificuldade'], 99))
    return resultado
