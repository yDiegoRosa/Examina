import math
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CelulaMeta:
    """Representa uma célula individual na matriz de distribuição pedagógica."""

    topico_id: int | None
    topico_nome: str
    dificuldade: str  # 'F', 'M', 'D'
    dificuldade_display: str
    percentual_topico: int
    percentual_dificuldade: int
    percentual_composto: float  # % da prova representado por esta combinação (0..100)
    cota_exata: float  # Quantidade teórica contínua
    cota_inteira: int  # Parte inteira (piso)
    resto: float  # Fração residual para desempate do maior resto
    quantidade: int  # Quantidade final inteira de questões


@dataclass
class LinhaMatrizTopico:
    """Representa uma linha da matriz (um tópico curricular com suas 3 dificuldades)."""

    topico_id: int | None
    topico_nome: str
    percentual_topico: int
    celulas: dict[str, CelulaMeta] = field(default_factory=dict)
    total_questoes: int = 0


@dataclass
class MatrizMetasResultado:
    """Resultado estruturado da matriz bidimensional de metas por tópico e dificuldade."""

    total_questoes: int
    linhas: list[LinhaMatrizTopico]
    celulas: list[CelulaMeta]
    totais_por_dificuldade: dict[str, int]
    percentuais_por_dificuldade: dict[str, int]
    eh_distribuicao_livre: bool

    def get_quantidade(self, topico_id: int | None, dificuldade: str) -> int:
        """Retorna a quantidade de questões alocadas para uma célula específica."""
        for cel in self.celulas:
            if cel.topico_id == topico_id and cel.dificuldade == dificuldade:
                return cel.quantidade
        return 0

    def get_celula(self, topico_id: int | None, dificuldade: str) -> CelulaMeta | None:
        """Retorna o objeto CelulaMeta de uma célula específica."""
        for cel in self.celulas:
            if cel.topico_id == topico_id and cel.dificuldade == dificuldade:
                return cel
        return None


DIFICULDADE_DISPLAY_MAP = {
    'F': 'Fácil',
    'M': 'Médio',
    'D': 'Difícil',
}

# Prioridade pedagógica canônica para desempates: Médio > Fácil > Difícil
DIFICULDADE_PRIORITY_MAP = {
    'M': 3,
    'F': 2,
    'D': 1,
}


def calcular_metas_celulas(
    quantidade_total: int,
    percentual_facil: int,
    percentual_medio: int,
    percentual_dificil: int,
    topicos: list[dict[str, Any]] | None = None,
) -> MatrizMetasResultado:
    """
    Calcula as quantidades inteiras exatas de questões para cada célula (Tópico × Dificuldade)
    usando o Método do Maior Resto (Hamilton-Hare / Hare-Niemeyer).

    Garantias:
    1. A soma total de todas as células é estritamente igual a `quantidade_total`.
    2. Todas as quantidades calculadas são números inteiros >= 0.
    3. Resíduos fracionários são distribuídos com desempate determinístico.

    Args:
        quantidade_total: Número total de questões da avaliação (N).
        percentual_facil: Percentual de questões fáceis (0..100).
        percentual_medio: Percentual de questões médias (0..100).
        percentual_dificil: Percentual de questões difíceis (0..100).
        topicos: Lista de dicionários [{'id': int, 'nome': str, 'percentual': int}, ...].
                 Se vazio ou None, considera distribuição livre (1 tópico sintético de 100%).

    Returns:
        MatrizMetasResultado com a distribuição completa calculada.
    """
    if quantidade_total <= 0:
        return MatrizMetasResultado(
            total_questoes=0,
            linhas=[],
            celulas=[],
            totais_por_dificuldade={'F': 0, 'M': 0, 'D': 0},
            percentuais_por_dificuldade={
                'F': percentual_facil,
                'M': percentual_medio,
                'D': percentual_dificil,
            },
            eh_distribuicao_livre=bool(not topicos),
        )

    dificuldades_config = [
        ('F', percentual_facil),
        ('M', percentual_medio),
        ('D', percentual_dificil),
    ]

    eh_distribuicao_livre = not topicos or len(topicos) == 0

    if eh_distribuicao_livre:
        topicos_normalizados = [
            {'id': None, 'nome': 'Sorteio Livre (Todos os Tópicos)', 'percentual': 100}
        ]
    else:
        topicos_normalizados = [
            {
                'id': t.get('id'),
                'nome': t.get('nome', f'Tópico {t.get("id")}'),
                'percentual': int(t.get('percentual', 0)),
            }
            for t in topicos
        ]

    # 1. Criação das células com cálculo das cotas contínuas e inteiras
    celulas: list[CelulaMeta] = []
    total_alocado_base = 0

    for topico in topicos_normalizados:
        p_top = topico['percentual']

        for dif_code, p_dif in dificuldades_config:
            # Percentual composto da célula em relação à prova inteira (ex.: 50% * 40% = 20.0%)
            p_comp = (p_top * p_dif) / 100.0
            # Cota exata teórica de questões (ex.: 10 * 0.20 = 2.0)
            cota_exata = (quantidade_total * p_top * p_dif) / 10000.0
            cota_inteira = math.floor(cota_exata)
            # Arredondamento para evitar imprecisões de ponto flutuante em comparações
            resto = round(cota_exata - cota_inteira, 8)

            celula = CelulaMeta(
                topico_id=topico['id'],
                topico_nome=topico['nome'],
                dificuldade=dif_code,
                dificuldade_display=DIFICULDADE_DISPLAY_MAP.get(dif_code, dif_code),
                percentual_topico=p_top,
                percentual_dificuldade=p_dif,
                percentual_composto=round(p_comp, 4),
                cota_exata=round(cota_exata, 6),
                cota_inteira=cota_inteira,
                resto=resto,
                quantidade=cota_inteira,
            )
            celulas.append(celula)
            total_alocado_base += cota_inteira

    # 2. Distribuição das vagas residuais pelo Método do Maior Resto
    vagas_restantes = quantidade_total - total_alocado_base

    if vagas_restantes > 0:
        # Ordenação decrescente:
        # 1º: Maior resto fracionário
        # 2º: Maior cota exata (desempate por peso geral)
        # 3º: Prioridade pedagógica de dificuldade (Médio > Fácil > Difícil)
        # 4º: ID ou índice do tópico (estabilidade determinística)
        def _sort_key(c: CelulaMeta) -> tuple[float, float, int, int]:
            t_id_numeric = c.topico_id if c.topico_id is not None else 0
            return (
                c.resto,
                c.cota_exata,
                DIFICULDADE_PRIORITY_MAP.get(c.dificuldade, 0),
                -t_id_numeric,
            )

        celulas_ordenadas = sorted(celulas, key=_sort_key, reverse=True)

        for i in range(min(vagas_restantes, len(celulas_ordenadas))):
            celulas_ordenadas[i].quantidade += 1

    # 3. Estruturação em Linhas e Totais
    linhas: list[LinhaMatrizTopico] = []
    totais_por_dificuldade = {'F': 0, 'M': 0, 'D': 0}

    for topico in topicos_normalizados:
        top_id = topico['id']
        linha_celulas = {
            c.dificuldade: c
            for c in celulas
            if c.topico_id == top_id
        }
        soma_linha = sum(c.quantidade for c in linha_celulas.values())

        linhas.append(
            LinhaMatrizTopico(
                topico_id=top_id,
                topico_nome=topico['nome'],
                percentual_topico=topico['percentual'],
                celulas=linha_celulas,
                total_questoes=soma_linha,
            )
        )

    for c in celulas:
        totais_por_dificuldade[c.dificuldade] += c.quantidade

    return MatrizMetasResultado(
        total_questoes=quantidade_total,
        linhas=linhas,
        celulas=celulas,
        totais_por_dificuldade=totais_por_dificuldade,
        percentuais_por_dificuldade={
            'F': percentual_facil,
            'M': percentual_medio,
            'D': percentual_dificil,
        },
        eh_distribuicao_livre=eh_distribuicao_livre,
    )
