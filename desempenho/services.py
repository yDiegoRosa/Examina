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


# ---------------------------------------------------------------------------
# Psicometria básica (RF14): índice de facilidade por questão
# ---------------------------------------------------------------------------
# Faixas pedagógicas do índice de facilidade (p): p < 0,30 difícil; > 0,70 fácil.
FACILIDADE_DIFICIL = 0.30
FACILIDADE_FACIL = 0.70


def _dificuldade_label(codigo: str) -> str:
    try:
        return Questao.Dificuldade(codigo).label
    except ValueError:
        return codigo or '—'


def _classificar_facilidade(indice: float) -> tuple[str, str]:
    if indice < FACILIDADE_DIFICIL:
        return 'dificil', 'Difícil'
    if indice > FACILIDADE_FACIL:
        return 'facil', 'Fácil'
    return 'medio', 'Médio'


def indice_facilidade_por_questao(min_respostas: int = 1) -> list[dict]:
    """
    Índice de facilidade (p) de cada questão = **média global de `acertou`** entre
    todas as aplicações da questão (não apenas uma prova). p alto ⇒ questão fácil;
    p baixo ⇒ difícil. Considera só registros corrigidos (`acertou` definido).

    ``min_respostas`` descarta questões com poucas respostas (índice instável).

    Retorna dicts com dados da questão, ``total_respostas``, ``acertos``,
    ``indice_facilidade`` e a ``classe`` (difícil/médio/fácil), ordenados do menor
    índice (mais difícil) para o maior.
    """
    linhas = (
        Desempenho.objects.filter(acertou__isnull=False)
        .values(
            'questao',
            'questao__enunciado',
            'questao__dificuldade',
            'questao__topico__nome',
            'questao__disciplina__nome',
        )
        .annotate(total=Count('id'), acertos=Count('id', filter=Q(acertou=True)))
        .filter(total__gte=min_respostas)
    )

    resultado = []
    for linha in linhas:
        total = linha['total']
        acertos = linha['acertos']
        indice = round(acertos / total, 4) if total else 0.0
        classe, classe_label = _classificar_facilidade(indice)
        resultado.append(
            {
                'questao_id': linha['questao'],
                'enunciado': linha['questao__enunciado'] or '',
                'disciplina': linha['questao__disciplina__nome'],
                'topico': linha['questao__topico__nome'] or 'Sem tópico',
                'dificuldade_cadastrada': _dificuldade_label(linha['questao__dificuldade']),
                'total_respostas': total,
                'acertos': acertos,
                'indice_facilidade': indice,
                'classe': classe,
                'classe_label': classe_label,
            }
        )

    resultado.sort(key=lambda r: (r['indice_facilidade'], -r['total_respostas']))
    return resultado


def questoes_fora_do_esperado(
    limite_baixo: float = 0.20,
    limite_alto: float = 0.90,
    min_respostas: int = 5,
) -> list[dict]:
    """
    Questões com índice de facilidade **muito baixo** (≤ ``limite_baixo``) ou **muito
    alto** (≥ ``limite_alto``) — candidatas a revisão (possível item mal formulado,
    ambíguo ou trivial), não apenas "difícil". Cada item recebe ``motivo``.
    """
    fora = []
    for item in indice_facilidade_por_questao(min_respostas=min_respostas):
        indice = item['indice_facilidade']
        if indice <= limite_baixo:
            fora.append({**item, 'motivo': 'muito_dificil', 'motivo_label': 'Índice muito baixo (quase ninguém acertou)'})
        elif indice >= limite_alto:
            fora.append({**item, 'motivo': 'muito_facil', 'motivo_label': 'Índice muito alto (quase todos acertaram)'})
    return fora
