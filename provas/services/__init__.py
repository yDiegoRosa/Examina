from .matrix_calculator import (
    CelulaMeta,
    LinhaMatrizTopico,
    MatrizMetasResultado,
    calcular_metas_celulas,
)
from .pdf_generator import PDFGenerationError, PDFGeneratorService
from .question_replacer import (
    SubstituicaoQuestaoError,
    obter_candidatas_substituicao,
    substituir_questao_prova,
)
from .question_selector import (
    consultar_questoes_candidatas_priorizadas,
    gerar_prova_automatizada,
    selecionar_questoes_por_matriz,
)
from .snapshot_builder import (
    construir_snapshot_criterios_dificuldade,
    construir_snapshot_criterios_topico,
    construir_snapshot_mascara,
    construir_snapshot_matriz_metas,
    construir_snapshot_questao,
)
from .stock_validator import (
    CelulaDiagnosticoEstoque,
    EstoqueInsuficienteError,
    ItemDeficitEstoque,
    RelatorioDiagnosticoEstoque,
    validar_disponibilidade_estoque,
)

__all__ = [
    'CelulaDiagnosticoEstoque',
    'CelulaMeta',
    'EstoqueInsuficienteError',
    'ItemDeficitEstoque',
    'LinhaMatrizTopico',
    'MatrizMetasResultado',
    'PDFGenerationError',
    'PDFGeneratorService',
    'RelatorioDiagnosticoEstoque',
    'SubstituicaoQuestaoError',
    'calcular_metas_celulas',
    'construir_snapshot_criterios_dificuldade',
    'construir_snapshot_criterios_topico',
    'construir_snapshot_mascara',
    'construir_snapshot_matriz_metas',
    'construir_snapshot_questao',
    'consultar_questoes_candidatas_priorizadas',
    'gerar_prova_automatizada',
    'obter_candidatas_substituicao',
    'selecionar_questoes_por_matriz',
    'substituir_questao_prova',
    'validar_disponibilidade_estoque',
]

