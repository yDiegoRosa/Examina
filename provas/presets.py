"""
presets.py — Configurações pré-definidas de Máscara de Prova.

Fonte única de verdade para valores iniciais de máscaras "modelo". Hoje expõe o
preset do **Simulado ENADE — Componente Específico**, usado tanto para
pré-preencher o formulário de criação quanto para montar uma máscara já
distribuída nas 5 grandes áreas do CE.

Estrutura seguida (Componente Específico do ENADE 2021 — Tecnologia em ADS):
30 questões = **27 objetivas + 3 discursivas**, 4h de referência e distribuição
temática nas 5 áreas abaixo. Não há bloco de Formação Geral (o sistema não possui
acervo de FG); o simulado cobre apenas o Componente Específico.

As 5 áreas agrupam várias disciplinas do currículo. Como o motor de seleção
distribui por **tópico**, o preset resolve dinamicamente, a partir do acervo, os
tópicos de cada área e monta a distribuição percentual (peso igual por área
presente, normalizado para somar 100%).
"""

import math

from .models import Mascara

# --- Parâmetros do Componente Específico (ENADE ADS) -----------------------
ENADE_CE_TOTAL = 30
ENADE_CE_OBJETIVAS = 27
ENADE_CE_DISCURSIVAS = 3
# Distribuição de dificuldade proposta (soma 100). Ajustável pelo docente no formulário.
ENADE_PERC_FACIL = 30
ENADE_PERC_MEDIO = 50
ENADE_PERC_DIFICIL = 20
ENADE_TEMPO_MINUTOS = 180  # referência para o Componente Específico
ENADE_VALOR_TOTAL = 10

# Nº máximo de tópicos aproveitados por área (evita distribuição excessivamente
# granular e reduz a chance de déficit de estoque em células muito específicas).
ENADE_MAX_TOPICOS_POR_AREA = 5

# Mapeamento das 5 grandes áreas do Componente Específico -> padrões de nome de
# disciplina (casados por ``icontains``, tolerante a siglas/variações de cadastro).
ENADE_AREAS = {
    'Engenharia de Software': [
        'Engenharia de Requisitos',
        'Modelagem de Sistemas',
        'Projeto de Sistemas',
        'Análise de Sistemas',
        'Engenharia de Software',
    ],
    'Desenvolvimento de Sistemas': [
        'Fundamentos de Algoritmos',
        'Fundamentos de Programação',
        'Estrutura de Dados',
        'Orientada a Objetos',
        'Aplicações Web',
        'Aplicações WEB',
        'Tecnologias WEB',
        'Dispositivos Móveis',
    ],
    'Banco de Dados': [
        'Banco de Dados',
        'SQL',
    ],
    'Infraestrutura e Redes': [
        'Sistemas Operacionais',
        'Organização de Computadores',
        'Redes',
        'Sistemas Distribuídos',
    ],
    'Segurança da Informação e Governança': [
        'Segurança',
        'Gerência e Projeto de Sistemas',
        'Governança',
    ],
}


def preset_enade_initial(disciplina=None):
    """
    Retorna o dicionário de valores iniciais para pré-preencher o MascaraForm
    no formato de Simulado ENADE — Componente Específico (30 = 27 objetivas + 3
    discursivas). Sem disciplina, o simulado abrange todo o acervo (necessário
    para distribuir a prova entre as várias disciplinas das 5 áreas).
    """
    escopo = f' ({disciplina.nome})' if disciplina else ' (ADS)'
    initial = {
        'titulo': f'Simulado ENADE — Componente Específico{escopo}',
        'modalidade': Mascara.Modalidade.MISTA,
        'quantidade_questoes': ENADE_CE_TOTAL,
        'quantidade_objetivas': ENADE_CE_OBJETIVAS,
        'quantidade_discursivas': ENADE_CE_DISCURSIVAS,
        'percentual_facil': ENADE_PERC_FACIL,
        'percentual_medio': ENADE_PERC_MEDIO,
        'percentual_dificil': ENADE_PERC_DIFICIL,
        'valor_total': ENADE_VALOR_TOTAL,
        'tempo_estimado_minutos': ENADE_TEMPO_MINUTOS,
        'priorizar_enade': True,
        'embaralhar_questoes': True,
        'embaralhar_alternativas': True,
    }
    if disciplina is not None:
        initial['disciplina'] = disciplina
    return initial


def _distribuir_percentuais_inteiros(pesos, total=100):
    """
    Converte uma lista de pesos (floats) em percentuais **inteiros >= 1** cuja
    soma é exatamente ``total``, usando piso 1 por item + Método do Maior Resto
    para o restante. Requer ``len(pesos) < total``.
    """
    n = len(pesos)
    if n == 0:
        return []

    restante = total - n
    if restante < 0:
        # Segurança: mais itens do que pontos disponíveis (caller já limita isso).
        return [1] * total

    soma_pesos = sum(pesos) or 1.0
    exatos = [(p / soma_pesos) * restante for p in pesos]
    base = [math.floor(e) for e in exatos]
    resultado = [1 + b for b in base]

    faltam = restante - sum(base)
    ordem_restos = sorted(range(n), key=lambda i: exatos[i] - base[i], reverse=True)
    for k in range(faltam):
        resultado[ordem_restos[k % n]] += 1

    return resultado


def construir_distribuicao_topicos_enade():
    """
    Monta a distribuição percentual por tópico das 5 áreas do Componente
    Específico, a partir do acervo real.

    Regras:
    - Cada área é resolvida para as disciplinas cujo nome casa com seus padrões.
    - Só entram tópicos que possuem questões ativas (reduz déficit de estoque).
    - Peso igual por **área presente**; dentro da área, peso igual por tópico.
    - Percentuais inteiros >= 1, somando exatamente 100%.

    Returns:
        Lista de ``{'topico_id': int, 'percentual': int}``. Vazia se o acervo não
        tiver tópicos com questões nas áreas mapeadas.
    """
    from django.db.models import Count, Q

    from banco_questoes.models import Disciplina, Topico

    areas_topicos = []  # [(area, [topico_ids])]
    topicos_usados: set[int] = set()

    for area, padroes in ENADE_AREAS.items():
        filtro_disc = Q()
        for padrao in padroes:
            filtro_disc |= Q(nome__icontains=padrao)

        disc_ids = list(Disciplina.objects.filter(filtro_disc, ativo=True).values_list('pk', flat=True))
        if not disc_ids:
            continue

        # Apenas tópicos com objetivas ativas entram (os baldes objetivos precisam
        # de estoque); prioriza os mais abastecidos para reduzir déficit de estoque.
        topico_ids = list(
            Topico.objects.filter(disciplina_id__in=disc_ids, ativo=True)
            .exclude(pk__in=topicos_usados)
            .annotate(
                n_obj=Count('questoes', filter=Q(questoes__ativo=True, questoes__tipo='OBJ')),
            )
            .filter(n_obj__gt=0)
            .order_by('-n_obj', 'nome')
            .values_list('pk', flat=True)
        )[:ENADE_MAX_TOPICOS_POR_AREA]

        if topico_ids:
            topicos_usados.update(topico_ids)
            areas_topicos.append((area, topico_ids))

    if not areas_topicos:
        return []

    pesos = []  # [(topico_id, peso_float)]
    peso_area = 1.0 / len(areas_topicos)
    for _area, topico_ids in areas_topicos:
        peso_topico = peso_area / len(topico_ids)
        for tid in topico_ids:
            pesos.append((tid, peso_topico))

    percentuais = _distribuir_percentuais_inteiros([p for _tid, p in pesos])

    return [
        {'topico_id': tid, 'percentual': perc}
        for (tid, _peso), perc in zip(pesos, percentuais, strict=True)
        if perc > 0
    ]
