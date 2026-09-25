"""
Agregações analíticas do Módulo 4 (base do RF13/RF14) sobre o ``Desempenho``.

Calculam a **taxa de erro** (``1 - média(acertou)`` = erros/total) por tópico e por
nível de dificuldade, filtráveis por turma (ou por uma aplicação específica), para os
relatórios de fragilidades/forças da turma. Consideram apenas registros já corrigidos
(``acertou`` definido) — objetivas (OMR/manual) e discursivas (nota ≥ corte).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

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
            fora.append(
                {**item, 'motivo': 'muito_dificil', 'motivo_label': 'Índice muito baixo (quase ninguém acertou)'}
            )
        elif indice >= limite_alto:
            fora.append({**item, 'motivo': 'muito_facil', 'motivo_label': 'Índice muito alto (quase todos acertaram)'})
    return fora


# ---------------------------------------------------------------------------
# Nota final por aluno numa aplicação (boletim da turma)
# ---------------------------------------------------------------------------
ESCALA_PADRAO = Decimal('10')
DUAS_CASAS = Decimal('0.01')


def escala_da_prova(prova) -> Decimal:
    """
    Valor total da prova (escala da nota final): o ``valor_total`` congelado no snapshot
    da máscara; na falta dele, o da máscara atual; senão 10.
    """
    candidatos = [(prova.snapshot_mascara or {}).get('valor_total')]
    if prova.mascara_id:
        candidatos.append(prova.mascara.valor_total)
    for valor in candidatos:
        try:
            escala = Decimal(str(valor))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if escala > 0:
            return escala
    return ESCALA_PADRAO


def _fracao_obtida(tipo: str, desempenho: Desempenho | None) -> Decimal | None:
    """
    Fração do peso obtida numa questão (0–1), ou ``None`` se ainda não corrigida.

    Objetiva: 1 se acertou, 0 se errou. Discursiva: nota/10 (proporcional); sem nota
    lançada, cai para ``acertou`` (1/0) quando houver.
    """
    if desempenho is None:
        return None
    if tipo != 'OBJ' and desempenho.nota is not None:
        return desempenho.nota / ESCALA_PADRAO
    if desempenho.acertou is None:
        return None
    return Decimal(1) if desempenho.acertou else Decimal(0)


def notas_da_aplicacao(aplicacao) -> dict:
    """
    Nota final de cada aluno (folha-resposta) de uma aplicação.

    Regra (proporcional): ``nota = Σ(peso × fração) / Σ(peso) × escala``, onde o peso é
    a pontuação do item na prova, a fração vem de :func:`_fracao_obtida` e a escala é o
    valor total da prova (:func:`escala_da_prova`). Questões ainda não corrigidas contam
    zero e marcam a linha como ``incompleta``; folhas anuladas não recebem nota.

    Retorna ``{'escala', 'linhas', 'media', 'total_completas', 'total_incompletas'}``;
    cada linha traz aluno, versão, objetivas (acertos/total), discursivas
    (corrigidas/total), pontos obtidos/possíveis, ``nota`` e ``situacao``.
    """
    from provas.models import FolhaResposta, ProvaVersaoItem

    escala = escala_da_prova(aplicacao.prova)
    folhas = list(aplicacao.folhas.select_related('aluno', 'prova_versao').order_by('aluno__nome', 'aluno__matricula'))

    # Itens por versão: (questao_id, tipo, peso) na ordem do caderno.
    itens_por_versao: dict[int, list[tuple[int, str, Decimal]]] = {}
    for item in (
        ProvaVersaoItem.objects.filter(versao_id__in={folha.prova_versao_id for folha in folhas})
        .select_related('prova_questao__questao')
        .order_by('versao_id', 'ordem')
    ):
        pq = item.prova_questao
        tipo = (pq.snapshot_questao or {}).get('tipo') or pq.questao.tipo
        itens_por_versao.setdefault(item.versao_id, []).append((pq.questao_id, tipo, Decimal(str(pq.pontuacao))))

    desempenhos = {
        (d.folha_resposta_id, d.questao_id): d
        for d in Desempenho.objects.filter(folha_resposta__prova_aplicacao=aplicacao)
    }

    linhas = []
    for folha in folhas:
        itens = itens_por_versao.get(folha.prova_versao_id, [])
        pontos_possiveis = sum((peso for _, _, peso in itens), Decimal(0))
        pontos_obtidos = Decimal(0)
        acertos_obj = total_obj = corrigidas_dis = total_dis = pendentes = 0

        for questao_id, tipo, peso in itens:
            fracao = _fracao_obtida(tipo, desempenhos.get((folha.id, questao_id)))
            if tipo == 'OBJ':
                total_obj += 1
                acertos_obj += int(fracao == 1)
            else:
                total_dis += 1
                corrigidas_dis += int(fracao is not None)
            if fracao is None:
                pendentes += 1
                continue
            pontos_obtidos += peso * fracao

        if folha.status == FolhaResposta.Status.ANULADA:
            nota, situacao = None, 'anulada'
        elif not pontos_possiveis:
            nota, situacao = None, 'sem_itens'
        else:
            nota = (pontos_obtidos / pontos_possiveis * escala).quantize(DUAS_CASAS, rounding=ROUND_HALF_UP)
            situacao = 'incompleta' if pendentes else 'completa'

        linhas.append(
            {
                'folha': folha,
                'matricula': folha.aluno.matricula,
                'aluno': folha.aluno.nome,
                'versao': folha.prova_versao.identificador,
                'acertos_objetivas': acertos_obj,
                'total_objetivas': total_obj,
                'discursivas_corrigidas': corrigidas_dis,
                'total_discursivas': total_dis,
                'pendentes': pendentes,
                'pontos_obtidos': pontos_obtidos.quantize(DUAS_CASAS, rounding=ROUND_HALF_UP),
                'pontos_possiveis': pontos_possiveis.quantize(DUAS_CASAS, rounding=ROUND_HALF_UP),
                'nota': nota,
                'situacao': situacao,
            }
        )

    completas = [linha['nota'] for linha in linhas if linha['situacao'] == 'completa']
    media = (sum(completas) / len(completas)).quantize(DUAS_CASAS, rounding=ROUND_HALF_UP) if completas else None
    return {
        'escala': escala,
        'linhas': linhas,
        'media': media,
        'total_completas': len(completas),
        'total_incompletas': sum(linha['situacao'] == 'incompleta' for linha in linhas),
    }
