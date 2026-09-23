from dataclasses import dataclass, field

from banco_questoes.models import Questao

from .matrix_calculator import DIFICULDADE_DISPLAY_MAP


@dataclass
class ItemDeficitEstoque:
    """Representa uma célula específica que possui déficit de questões no banco."""

    topico_id: int | None
    topico_nome: str
    dificuldade: str  # 'F', 'M', 'D'
    dificuldade_display: str
    quantidade_necessaria: int
    quantidade_disponivel: int
    quantidade_faltante: int


@dataclass
class CelulaDiagnosticoEstoque:
    """Diagnóstico individual de uma célula da matriz de metas."""

    topico_id: int | None
    topico_nome: str
    dificuldade: str
    dificuldade_display: str
    quantidade_necessaria: int
    quantidade_disponivel: int
    quantidade_faltante: int
    suficiente: bool


@dataclass
class RelatorioDiagnosticoEstoque:
    """Relatório estruturado de auditoria e validação de estoque para uma máscara."""

    esta_valido: bool
    total_necessario: int
    total_disponivel: int
    deficits: list[ItemDeficitEstoque] = field(default_factory=list)
    celulas: list[CelulaDiagnosticoEstoque] = field(default_factory=list)

    @property
    def total_faltante(self) -> int:
        """Soma total de questões faltantes considerando todos os déficits por célula."""
        return sum(d.quantidade_faltante for d in self.deficits)

    @property
    def mensagem_resumo(self) -> str:
        """Mensagem descritiva clara detalhando o que falta no banco."""
        if self.esta_valido:
            return 'Estoque de questões suficiente para todas as células da avaliação.'

        itens_str = '; '.join(
            f'{d.topico_nome} [{d.dificuldade_display}]: faltam {d.quantidade_faltante} questão(ões) '
            f'(demanda: {d.quantidade_necessaria}, estoque: {d.quantidade_disponivel})'
            for d in self.deficits
        )
        return f'Estoque insuficiente no banco de questões para gerar a avaliação. Déficits identificados: {itens_str}.'


class EstoqueInsuficienteError(Exception):
    """
    Exceção levantada ao tentar gerar uma prova com déficit de questões no banco.
    Impede a criação de avaliações parciais ou incompletas.
    """

    def __init__(self, relatorio: RelatorioDiagnosticoEstoque, mensagem: str | None = None):
        self.relatorio = relatorio
        self.mensagem = mensagem or relatorio.mensagem_resumo
        super().__init__(self.mensagem)


def validar_disponibilidade_estoque(mascara) -> RelatorioDiagnosticoEstoque:
    """
    Audita o banco de questões e valida a disponibilidade real para cada balde do
    plano de seleção (Tópico × Dificuldade × Tipo), respeitando o split
    objetivas/discursivas da modalidade MISTA.

    Garantias:
    - Retorna `esta_valido=True` somente se todos os baldes possuírem quantidade suficiente.
    - Lista detalhadamente cada combinação deficiente com demanda, estoque e quantidade faltante.

    Args:
        mascara: Instância de Mascara de Avaliação.

    Returns:
        RelatorioDiagnosticoEstoque estruturado.
    """
    plano = mascara.montar_plano_selecao()

    questoes_base = Questao.objects.filter(ativo=True)
    if mascara.disciplina_id:
        questoes_base = questoes_base.filter(disciplina=mascara.disciplina)

    total_acervo = questoes_base.count()
    deficits: list[ItemDeficitEstoque] = []
    celulas_diagnostico: list[CelulaDiagnosticoEstoque] = []

    for balde in plano:
        qtd_necessaria = balde.quantidade

        qs_cel = questoes_base.filter(tipo=balde.tipo)
        if balde.dificuldade:
            qs_cel = qs_cel.filter(dificuldade=balde.dificuldade)
        if balde.topico_id is not None:
            qs_cel = qs_cel.filter(topico_id=balde.topico_id)

        qtd_disponivel = qs_cel.count()
        suficiente = qtd_disponivel >= qtd_necessaria
        qtd_faltante = max(0, qtd_necessaria - qtd_disponivel)

        if balde.dificuldade:
            dificuldade_display = DIFICULDADE_DISPLAY_MAP.get(balde.dificuldade, balde.dificuldade)
        else:
            dificuldade_display = 'Discursiva' if balde.tipo == Questao.Tipo.DISCURSIVA else 'Todas'

        cel_diag = CelulaDiagnosticoEstoque(
            topico_id=balde.topico_id,
            topico_nome=balde.topico_nome,
            dificuldade=balde.dificuldade,
            dificuldade_display=dificuldade_display,
            quantidade_necessaria=qtd_necessaria,
            quantidade_disponivel=qtd_disponivel,
            quantidade_faltante=qtd_faltante,
            suficiente=suficiente,
        )
        celulas_diagnostico.append(cel_diag)

        if not suficiente and qtd_necessaria > 0:
            deficits.append(
                ItemDeficitEstoque(
                    topico_id=balde.topico_id,
                    topico_nome=balde.topico_nome,
                    dificuldade=balde.dificuldade,
                    dificuldade_display=dificuldade_display,
                    quantidade_necessaria=qtd_necessaria,
                    quantidade_disponivel=qtd_disponivel,
                    quantidade_faltante=qtd_faltante,
                )
            )

    esta_valido = len(deficits) == 0 and total_acervo >= mascara.quantidade_questoes

    return RelatorioDiagnosticoEstoque(
        esta_valido=esta_valido,
        total_necessario=mascara.quantidade_questoes,
        total_disponivel=total_acervo,
        deficits=deficits,
        celulas=celulas_diagnostico,
    )
