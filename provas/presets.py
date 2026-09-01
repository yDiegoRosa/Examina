"""
presets.py — Configurações pré-definidas de Máscara de Prova.

Fonte única de verdade para valores iniciais de máscaras "modelo". Hoje expõe o
preset do **Simulado ENADE**; usado para pré-preencher o formulário de criação.

Estrutura oficial do ENADE: 40 questões = Formação Geral (10) + Componente
Específico (30), com 35 objetivas + 5 discursivas e pesos 25% (FG) / 75% (CE).

Nesta Fase 1 modelamos a prova como uma máscara MISTA de **disciplina única**
(35 objetivas + 5 discursivas), reutilizando toda a geração/PDF existentes. Os
blocos Formação Geral / Componente Específico e os pesos 25/75 serão adicionados
na Fase 2 (recurso estruturado), quando houver acervo de Formação Geral.
"""

from .models import Mascara

# --- Parâmetros oficiais do ENADE ------------------------------------------
ENADE_TOTAL_QUESTOES = 40
ENADE_QTD_OBJETIVAS = 35
ENADE_QTD_DISCURSIVAS = 5
# Distribuição de dificuldade proposta (soma 100). Ajustável pelo docente no formulário.
ENADE_PERC_FACIL = 30
ENADE_PERC_MEDIO = 50
ENADE_PERC_DIFICIL = 20
ENADE_TEMPO_MINUTOS = 240  # 4 horas, como a prova oficial
ENADE_VALOR_TOTAL = 10


def preset_enade_initial(disciplina=None):
    """
    Retorna o dicionário de valores iniciais para pré-preencher o MascaraForm
    no formato de Simulado ENADE. Sem disciplina, o simulado abrange todo o acervo do curso.
    """
    initial = {
        'titulo': f'Simulado ENADE — {disciplina.nome}' if disciplina else 'Simulado ENADE 2026 — ADS',
        'modalidade': Mascara.Modalidade.MISTA,
        'quantidade_questoes': ENADE_TOTAL_QUESTOES,
        'quantidade_objetivas': ENADE_QTD_OBJETIVAS,
        'quantidade_discursivas': ENADE_QTD_DISCURSIVAS,
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
