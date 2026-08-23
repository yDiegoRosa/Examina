import logging
from dataclasses import dataclass, field
from typing import Any

from django.db import transaction

from .models import Alternativa, Disciplina, Questao, Topico
from .parser import CSVQuestaoParser
from .validator import CSVQuestaoValidator

logger = logging.getLogger(__name__)


@dataclass
class ImportLineResult:
    """Resultado individual de processamento de uma linha do arquivo CSV."""

    linha: int
    status: str  # 'SUCESSO' ou 'REJEITADA'
    enunciado: str
    tipo: str
    disciplina: str
    questao_id: int | None = None
    erros: list[str] = field(default_factory=list)


@dataclass
class ImportExecutionResult:
    """Resultado consolidado da execução do pipeline de importação."""

    total_processadas: int = 0
    total_sucesso: int = 0
    total_rejeitadas: int = 0
    linhas: list[ImportLineResult] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        """Indica se houve linhas rejeitadas com erro."""
        return self.total_rejeitadas > 0


class CSVImportService:
    """
    Serviço orquestrador do pipeline de importação em lote de questões (RF02).

    Etapas do Pipeline:
    1. Parser: Leitura nativa e extração de colunas linha a linha.
    2. Validação & Deduplicação: Verificação de consistência e unicidade.
    3. Commit Transacional: Persistência no banco apenas das linhas válidas.
    """

    @classmethod
    def import_csv(cls, file_or_content: Any) -> ImportExecutionResult:
        """
        Executa o pipeline completo de importação a partir de um arquivo ou conteúdo CSV.

        Retorna uma instância de ImportExecutionResult com o resumo e detalhamento.
        """
        # 1. Leitura e extração estruturada
        parsed_rows = CSVQuestaoParser.parse(file_or_content)

        if not parsed_rows:
            return ImportExecutionResult()

        # 2. Validação e detecção de duplicidade (intra-arquivo e banco de dados)
        validation_report = CSVQuestaoValidator.validate_rows(parsed_rows, check_db_duplicates=True)

        exec_result = ImportExecutionResult(total_processadas=len(parsed_rows))

        # 3. Processamento e commit apenas das linhas válidas
        for row in parsed_rows:
            line_num = row['linha']
            val_res = next((r for r in validation_report.linhas_validas if r.linha == line_num), None)

            if val_res and val_res.is_valid:
                data = val_res.cleaned_data
                try:
                    with transaction.atomic():
                        # Cria ou recupera a disciplina
                        disc, _ = Disciplina.objects.get_or_create(nome=data['disciplina'])

                        # Cria ou recupera o tópico (se informado)
                        topico_obj = None
                        if data.get('topico'):
                            topico_obj, _ = Topico.objects.get_or_create(
                                disciplina=disc,
                                nome=data['topico'],
                            )

                        # Cria a questão
                        questao = Questao.objects.create(
                            disciplina=disc,
                            topico=topico_obj,
                            tipo=data['tipo'],
                            dificuldade=data['dificuldade'],
                            enunciado=data['enunciado'],
                            criterios_correcao=data.get('criterios_correcao', ''),
                            ativo=True,
                        )

                        # Associa tags
                        if data.get('tags'):
                            questao.tags.add(*data['tags'])

                        # Cria alternativas se questão objetiva
                        if data['tipo'] == Questao.Tipo.OBJETIVA:
                            for alt in data.get('alternativas', []):
                                Alternativa.objects.create(
                                    questao=questao,
                                    letra=alt['letra'],
                                    texto=alt['texto'],
                                    correta=alt['correta'],
                                )

                    exec_result.total_sucesso += 1
                    exec_result.linhas.append(
                        ImportLineResult(
                            linha=line_num,
                            status='SUCESSO',
                            enunciado=data['enunciado'],
                            tipo=data['tipo'],
                            disciplina=data['disciplina'],
                            questao_id=questao.pk,
                            erros=[],
                        )
                    )
                    logger.info('Questão Q%s importada com sucesso da linha %s.', questao.pk, line_num)

                except Exception as exc:
                    logger.exception('Erro ao persistir questão da linha %s: %s', line_num, exc)
                    exec_result.total_rejeitadas += 1
                    exec_result.linhas.append(
                        ImportLineResult(
                            linha=line_num,
                            status='REJEITADA',
                            enunciado=row.get('enunciado', ''),
                            tipo=row.get('tipo', ''),
                            disciplina=row.get('disciplina', ''),
                            questao_id=None,
                            erros=[f'Erro interno de persistência: {exc}'],
                        )
                    )
            else:
                inval_res = next((r for r in validation_report.linhas_invalidas if r.linha == line_num), None)
                erros = inval_res.errors if inval_res else ['Linha com inconsistências não identificadas.']
                exec_result.total_rejeitadas += 1
                exec_result.linhas.append(
                    ImportLineResult(
                        linha=line_num,
                        status='REJEITADA',
                        enunciado=row.get('enunciado', ''),
                        tipo=row.get('tipo', ''),
                        disciplina=row.get('disciplina', ''),
                        questao_id=None,
                        erros=erros,
                    )
                )

        return exec_result
