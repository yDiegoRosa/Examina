from dataclasses import dataclass, field
from typing import Any

from .models import Questao


@dataclass
class RowValidationResult:
    """Resultado da validação de uma única linha do CSV."""

    linha: int
    is_valid: bool
    errors: list[str] = field(default_factory=list)
    cleaned_data: dict[str, Any] = field(default_factory=dict)
    raw_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class CSVValidationReport:
    """Relatório consolidado da validação de todas as linhas do CSV."""

    total_processadas: int = 0
    total_validas: int = 0
    total_invalidas: int = 0
    linhas_validas: list[RowValidationResult] = field(default_factory=list)
    linhas_invalidas: list[RowValidationResult] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """Retorna True se todo o arquivo for válido (zero linhas inválidas)."""
        return self.total_invalidas == 0 and self.total_processadas > 0


class CSVQuestaoValidator:
    """Validador de regras de negócio e integridade para linhas de questões de CSV."""

    TIPOS_VALIDOS = {
        'OBJ': Questao.Tipo.OBJETIVA,
        'OBJETIVA': Questao.Tipo.OBJETIVA,
        'DIS': Questao.Tipo.DISCURSIVA,
        'DISCURSIVA': Questao.Tipo.DISCURSIVA,
    }

    DIFICULDADES_VALIDAS = {
        'F': Questao.Dificuldade.FACIL,
        'FACIL': Questao.Dificuldade.FACIL,
        'FÁCIL': Questao.Dificuldade.FACIL,
        'M': Questao.Dificuldade.MEDIO,
        'MEDIO': Questao.Dificuldade.MEDIO,
        'MÉDIO': Questao.Dificuldade.MEDIO,
        'D': Questao.Dificuldade.DIFICIL,
        'DIFICIL': Questao.Dificuldade.DIFICIL,
        'DIFÍCIL': Questao.Dificuldade.DIFICIL,
    }

    @classmethod
    def validate_row(cls, row: dict[str, Any]) -> RowValidationResult:
        """
        Aplica todas as regras de validação a uma linha estruturada pelo parser.

        Retorna uma instância de RowValidationResult contendo erros e dados limpos.
        """
        linha_num = row.get('linha', 0)
        errors: list[str] = []
        cleaned: dict[str, Any] = {}

        # 1. Validação de Enunciado
        enunciado = (row.get('enunciado') or '').strip()
        if not enunciado:
            errors.append(f'Linha {linha_num}: O campo "enunciado" é obrigatório.')
        else:
            cleaned['enunciado'] = enunciado

        # 2. Validação de Disciplina
        disciplina = (row.get('disciplina') or '').strip()
        if not disciplina:
            errors.append(f'Linha {linha_num}: O campo "disciplina" é obrigatório.')
        else:
            cleaned['disciplina'] = disciplina

        # Tópico (opcional)
        topico = row.get('topico')
        cleaned['topico'] = topico.strip() if isinstance(topico, str) and topico.strip() else None

        # Tags (lista)
        tags = row.get('tags', [])
        cleaned['tags'] = [t.strip() for t in tags if isinstance(t, str) and t.strip()]

        # 3. Validação de Tipo
        tipo_raw = (row.get('tipo') or '').strip().upper()
        if not tipo_raw:
            errors.append(f'Linha {linha_num}: O campo "tipo" é obrigatório (OBJ ou DIS).')
            tipo_normalizado = None
        elif tipo_raw in cls.TIPOS_VALIDOS:
            tipo_normalizado = cls.TIPOS_VALIDOS[tipo_raw]
            cleaned['tipo'] = tipo_normalizado
        else:
            errors.append(f'Linha {linha_num}: Tipo de questão inválido ("{tipo_raw}"). Valores aceitos: OBJ ou DIS.')
            tipo_normalizado = None

        # 4. Validação de Dificuldade
        dif_raw = (row.get('dificuldade') or '').strip().upper()
        if not dif_raw:
            errors.append(f'Linha {linha_num}: O campo "dificuldade" é obrigatório (F, M ou D).')
        elif dif_raw in cls.DIFICULDADES_VALIDAS:
            cleaned['dificuldade'] = cls.DIFICULDADES_VALIDAS[dif_raw]
        else:
            errors.append(
                f'Linha {linha_num}: Nível de dificuldade inválido ("{dif_raw}"). Valores aceitos: F, M ou D.'
            )

        # 5. Regras Condicionais: Objetiva vs Discursiva
        if tipo_normalizado == Questao.Tipo.OBJETIVA:
            cls._validate_objetiva(row, linha_num, errors, cleaned)
        elif tipo_normalizado == Questao.Tipo.DISCURSIVA:
            cls._validate_discursiva(row, linha_num, errors, cleaned)

        is_valid = len(errors) == 0
        return RowValidationResult(
            linha=linha_num,
            is_valid=is_valid,
            errors=errors,
            cleaned_data=cleaned if is_valid else {},
            raw_data=row,
        )

    @classmethod
    def _validate_objetiva(
        cls,
        row: dict[str, Any],
        linha_num: int,
        errors: list[str],
        cleaned: dict[str, Any],
    ) -> None:
        """Valida regras exclusivas de questões objetivas."""
        alternativas = row.get('alternativas', [])
        correta = (row.get('correta') or '').strip().upper()

        # Deve conter no mínimo 2 alternativas
        if len(alternativas) < 2:
            errors.append(
                f'Linha {linha_num}: Questões objetivas exigem no mínimo 2 alternativas preenchidas (encontrada(s): {len(alternativas)}).'
            )

        # Deve indicar a correta
        if not correta:
            errors.append(f'Linha {linha_num}: O campo "correta" é obrigatório para questões objetivas (ex.: A, B, C).')
        else:
            letras_disponiveis = [a['letra'] for a in alternativas]
            if correta not in letras_disponiveis:
                errors.append(
                    f'Linha {linha_num}: A alternativa correta "{correta}" não corresponde a nenhuma alternativa preenchida ({", ".join(letras_disponiveis) or "nenhuma"}).'
                )

        # Atualiza a flag correta nas alternativas estruturadas
        alts_com_flag = [
            {
                'letra': a['letra'],
                'texto': a['texto'],
                'correta': (a['letra'] == correta),
            }
            for a in alternativas
        ]

        cleaned['alternativas'] = alts_com_flag
        cleaned['correta'] = correta
        cleaned['criterios_correcao'] = ''

    @classmethod
    def _validate_discursiva(
        cls,
        row: dict[str, Any],
        linha_num: int,
        errors: list[str],
        cleaned: dict[str, Any],
    ) -> None:
        """Valida regras exclusivas de questões discursivas."""
        criterios = (row.get('criterios_correcao') or '').strip()

        if not criterios:
            errors.append(f'Linha {linha_num}: Critérios de correção são obrigatórios para questões discursivas.')
        else:
            cleaned['criterios_correcao'] = criterios

        cleaned['alternativas'] = []
        cleaned['correta'] = None

    @classmethod
    def validate_rows(cls, parsed_rows: list[dict[str, Any]]) -> CSVValidationReport:
        """Valida uma lista de linhas extraídas pelo parser e gera o relatório consolidado."""
        report = CSVValidationReport(total_processadas=len(parsed_rows))

        for row in parsed_rows:
            result = cls.validate_row(row)
            if result.is_valid:
                report.total_validas += 1
                report.linhas_validas.append(result)
            else:
                report.total_invalidas += 1
                report.linhas_invalidas.append(result)

        return report
