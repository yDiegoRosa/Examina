import re
from dataclasses import dataclass, field
from typing import Any

from .models import Questao


def normalize_enunciado(text: str) -> str:
    """
    Normaliza o enunciado para comparação uniforme.

    - Converte para minúsculas
    - Comprime múltiplos espaços e quebras de linha em espaço único
    - Remove espaços nas bordas
    """
    if not text:
        return ''
    cleaned = re.sub(r'\s+', ' ', text.strip())
    return cleaned.lower()


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


# Disciplina e dificuldade padrão para importação do formato de 10 colunas
DISCIPLINA_PADRAO = 'Não Classificada'
DIFICULDADE_PADRAO = Questao.Dificuldade.MEDIO


class CSVQuestaoValidator:
    """Validador de regras de negócio, integridade e duplicidade para questões de CSV."""

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
        Aplica todas as regras sintáticas e condicionais a uma linha estruturada pelo parser.

        Suporta tanto o formato novo (10 colunas) quanto o legado (13 colunas).
        Campos ausentes recebem valores-padrão.

        Retorna uma instância de RowValidationResult contendo erros e dados limpos.
        """
        linha_num = row.get('linha', 0)
        errors: list[str] = []
        cleaned: dict[str, Any] = {}

        # 0. Campos do novo formato (origem e numero_questao)
        cleaned['origem'] = (row.get('origem') or '').strip()
        cleaned['numero_questao'] = (row.get('numero_questao') or '').strip()

        # 1. Validação de Enunciado (sempre obrigatório)
        enunciado = (row.get('enunciado') or '').strip()
        if not enunciado:
            errors.append(f'Linha {linha_num}: O campo "enunciado" é obrigatório.')
        else:
            cleaned['enunciado'] = enunciado
            cleaned['enunciado_normalizado'] = normalize_enunciado(enunciado)

        # 2. Disciplina — usa valor do CSV ou fallback para padrão
        disciplina = (row.get('disciplina') or '').strip()
        cleaned['disciplina'] = disciplina if disciplina else DISCIPLINA_PADRAO

        # 3. Tópico (opcional)
        topico = row.get('topico')
        cleaned['topico'] = topico.strip() if isinstance(topico, str) and topico.strip() else None

        # 4. Tags (lista, opcional)
        tags = row.get('tags', [])
        cleaned['tags'] = [t.strip() for t in tags if isinstance(t, str) and t.strip()]

        # 5. Tipo — aceita valor explícito ou usa o inferido pelo parser
        tipo_raw = (row.get('tipo') or '').strip().upper()
        if not tipo_raw:
            # Tipo já deve ter sido inferido pelo parser; fallback defensivo
            alternativas = row.get('alternativas', [])
            tipo_raw = 'OBJ' if alternativas else 'DIS'

        if tipo_raw in cls.TIPOS_VALIDOS:
            tipo_normalizado = cls.TIPOS_VALIDOS[tipo_raw]
            cleaned['tipo'] = tipo_normalizado
        else:
            errors.append(f'Linha {linha_num}: Tipo de questão inválido ("{tipo_raw}"). Valores aceitos: OBJ ou DIS.')
            tipo_normalizado = None

        # 6. Dificuldade — aceita valor explícito ou usa padrão
        dif_raw = (row.get('dificuldade') or '').strip().upper()
        if not dif_raw:
            cleaned['dificuldade'] = DIFICULDADE_PADRAO
        elif dif_raw in cls.DIFICULDADES_VALIDAS:
            cleaned['dificuldade'] = cls.DIFICULDADES_VALIDAS[dif_raw]
        else:
            errors.append(
                f'Linha {linha_num}: Nível de dificuldade inválido ("{dif_raw}"). Valores aceitos: F, M ou D.'
            )

        # 7. Regras Condicionais: Objetiva vs Discursiva
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
            errors.append(
                f'Linha {linha_num}: O campo "gabarito" é obrigatório para questões objetivas (ex.: A, B, C).'
            )
        else:
            letras_disponiveis = [a['letra'] for a in alternativas]
            if correta not in letras_disponiveis:
                errors.append(
                    f'Linha {linha_num}: A alternativa do gabarito "{correta}" não corresponde a nenhuma alternativa preenchida ({", ".join(letras_disponiveis) or "nenhuma"}).'
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
        cleaned['criterios_correcao'] = (row.get('criterios_correcao') or '').strip()

    @classmethod
    def _validate_discursiva(
        cls,
        row: dict[str, Any],
        linha_num: int,
        errors: list[str],
        cleaned: dict[str, Any],
    ) -> None:
        """Valida regras exclusivas de questões discursivas.

        No formato de 10 colunas, o campo 'comentários' é opcional para discursivas.
        """
        criterios = (row.get('criterios_correcao') or '').strip()
        cleaned['criterios_correcao'] = criterios
        cleaned['alternativas'] = []
        cleaned['correta'] = None

    @classmethod
    def find_db_duplicate(cls, enunciado_normalizado: str) -> Questao | None:
        """
        Busca no banco de dados se já existe questão com enunciado idêntico normalizado.

        Usa consulta case-insensitive direta e verificação de espaços.
        """
        if not enunciado_normalizado:
            return None

        # 1. Busca rápida exata/case-insensitive
        match = Questao.objects.filter(enunciado__iexact=enunciado_normalizado).first()
        if match:
            return match

        # 2. Busca por candidatos para comparar normalizados caso espaços variem
        prefixo = enunciado_normalizado[:40] if len(enunciado_normalizado) >= 40 else enunciado_normalizado
        candidatos = Questao.objects.filter(enunciado__icontains=prefixo).only('id', 'enunciado')[:20]
        for c in candidatos:
            if normalize_enunciado(c.enunciado) == enunciado_normalizado:
                return c

        return None

    @classmethod
    def validate_rows(
        cls,
        parsed_rows: list[dict[str, Any]],
        check_db_duplicates: bool = True,
    ) -> CSVValidationReport:
        """
        Valida uma lista de linhas extraídas pelo parser, aplicando:
        1. Validação de formato e regras por linha.
        2. Detecção de duplicidade intra-arquivo (mesmo CSV).
        3. Detecção de duplicidade contra o banco de dados.

        Gera o relatório consolidado de validação.
        """
        report = CSVValidationReport(total_processadas=len(parsed_rows))
        seen_enunciados_no_arquivo: dict[str, int] = {}

        for row in parsed_rows:
            result = cls.validate_row(row)

            # Se a linha já tiver erros de sintaxe básicos, não valida duplicata
            if not result.is_valid:
                report.total_invalidas += 1
                report.linhas_invalidas.append(result)
                continue

            enunciado_norm = result.cleaned_data.get('enunciado_normalizado', '')

            # A. Verificação de duplicidade Intra-Arquivo
            if enunciado_norm in seen_enunciados_no_arquivo:
                linha_anterior = seen_enunciados_no_arquivo[enunciado_norm]
                result.errors.append(
                    f'Linha {result.linha}: Enunciado duplicado no próprio arquivo (idêntico ao da Linha {linha_anterior}).'
                )
                result.is_valid = False
                result.cleaned_data = {}
                report.total_invalidas += 1
                report.linhas_invalidas.append(result)
                continue

            seen_enunciados_no_arquivo[enunciado_norm] = result.linha

            # B. Verificação de duplicidade contra o Banco de Dados
            if check_db_duplicates:
                existente = cls.find_db_duplicate(enunciado_norm)
                if existente:
                    result.errors.append(
                        f'Linha {result.linha}: Questão duplicada. Já existe uma questão cadastrada no banco com este mesmo enunciado (ID Q{existente.pk}).'
                    )
                    result.is_valid = False
                    result.cleaned_data = {}
                    report.total_invalidas += 1
                    report.linhas_invalidas.append(result)
                    continue

            # Linha 100% válida e não-duplicada
            report.total_validas += 1
            report.linhas_validas.append(result)

        return report
