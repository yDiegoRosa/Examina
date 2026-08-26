import csv
import io
from typing import Any


class CSVParseError(Exception):
    """Exceção levantada quando o arquivo CSV ou Excel possui problemas estruturais."""


# Alias para clareza em contextos multi-formato
FileParseError = CSVParseError


def clean_cell_value(val: Any) -> str:
    """
    Converte e limpa o valor de uma célula do Excel ou CSV para string uniforme.

    - None -> ''
    - float com valor inteiro (ex.: 26.0) -> '26'
    - int -> '26'
    - float com decimais (ex.: 12.5) -> '12.5'
    - str -> valor com strip nas bordas mantendo quebras de linha internas (\n)
    """
    if val is None:
        return ''
    if isinstance(val, bool):
        return str(val)
    if isinstance(val, int):
        return str(val)
    if isinstance(val, float):
        return str(int(val)) if val.is_integer() else str(val)
    if isinstance(val, str):
        return val.strip()
    return str(val).strip()


# Mapeamento de nomes alternativos de colunas para o nome interno canônico
HEADER_ALIAS_MAP = {
    # Origem
    'origem': 'origem',
    # Número da questão
    'questão': 'numero_questao',
    'questao': 'numero_questao',
    'numero_questao': 'numero_questao',
    'nº da questão': 'numero_questao',
    'nº': 'numero_questao',
    # Enunciado
    'enunciado': 'enunciado',
    # Alternativas — formato novo (opção a, opcao_a) e legado (alt_a)
    'opção a': 'alt_a',
    'opcao_a': 'alt_a',
    'opcao a': 'alt_a',
    'alt_a': 'alt_a',
    'alternativa a': 'alt_a',
    'opção b': 'alt_b',
    'opcao_b': 'alt_b',
    'opcao b': 'alt_b',
    'alt_b': 'alt_b',
    'alternativa b': 'alt_b',
    'opção c': 'alt_c',
    'opcao_c': 'alt_c',
    'opcao c': 'alt_c',
    'alt_c': 'alt_c',
    'alternativa c': 'alt_c',
    'opção d': 'alt_d',
    'opcao_d': 'alt_d',
    'opcao d': 'alt_d',
    'alt_d': 'alt_d',
    'alternativa d': 'alt_d',
    'opção e': 'alt_e',
    'opcao_e': 'alt_e',
    'opcao e': 'alt_e',
    'alt_e': 'alt_e',
    'alternativa e': 'alt_e',
    # Gabarito
    'gabarito': 'gabarito',
    'correta': 'gabarito',
    'resposta': 'gabarito',
    # Comentários / Justificativa
    'comentários': 'comentarios',
    'comentários / justificativa': 'comentarios',
    'comentarios / justificativa': 'comentarios',
    'comentários/justificativa': 'comentarios',
    'comentarios/justificativa': 'comentarios',
    'comentarios': 'comentarios',
    'justificativa': 'comentarios',
    'criterios_correcao': 'comentarios',
    # Campos legados (aceitos mas tratados internamente)
    'tipo': 'tipo',
    'disciplina': 'disciplina',
    'topico': 'topico',
    'dificuldade': 'dificuldade',
    'tags': 'tags',
}


class CSVQuestaoParser:
    """
    Parser nativo para leitura e estruturação de arquivos CSV de questões.

    Atende a especificação de 10 colunas (formato do cliente):
    origem, questao, enunciado, opcao_a, opcao_b, opcao_c, opcao_d, opcao_e, gabarito, comentarios

    Também aceita o formato legado de 13 colunas para retrocompatibilidade.
    """

    COLUNAS_OBRIGATORIAS_CABECALHO = {
        'enunciado',
    }

    LETRAS_ALTERNATIVAS = ('a', 'b', 'c', 'd', 'e')
    ALIAS_MAP = HEADER_ALIAS_MAP

    @classmethod
    def _to_string_io(cls, file_or_content: Any) -> io.StringIO:
        """Converte diferentes tipos de entrada (bytes, str, UploadedFile, IO) em StringIO limpo."""
        if isinstance(file_or_content, str):
            content = file_or_content
        elif isinstance(file_or_content, bytes):
            content = cls._decode_bytes(file_or_content)
        elif hasattr(file_or_content, 'read'):
            raw = file_or_content.read()
            content = cls._decode_bytes(raw) if isinstance(raw, bytes) else str(raw)
        else:
            raise CSVParseError('Tipo de entrada inválido para leitura de CSV.')

        return io.StringIO(content.strip())

    @staticmethod
    def _decode_bytes(data: bytes) -> str:
        """Tenta decodificar bytes usando utf-8-sig (remove BOM), utf-8 e fallback para latin-1."""
        for encoding in ('utf-8-sig', 'utf-8', 'latin-1'):
            try:
                return data.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                continue
        raise CSVParseError('Não foi possível decodificar o arquivo CSV com codificação suportada.')

    @staticmethod
    def _detect_delimiter(sample_text: str) -> str:
        """Detecta se o delimitador utilizado é vírgula (,) ou ponto-e-vírgula (;)."""
        first_line = sample_text.split('\n', 1)[0] if '\n' in sample_text else sample_text
        if ';' in first_line and ',' not in first_line:
            return ';'
        if ';' in first_line and ',' in first_line:
            return ';' if first_line.count(';') > first_line.count(',') else ','
        return ','

    @classmethod
    def _normalize_headers(cls, raw_headers: list[str]) -> dict[str, str]:
        """
        Normaliza os cabeçalhos do CSV e mapeia para os nomes canônicos internos.

        Retorna um dicionário {nome_original_normalizado: nome_canonico}.
        """
        mapping = {}
        for h in raw_headers:
            if not h:
                continue
            key = str(h).strip().lower()
            canonical = cls.ALIAS_MAP.get(key, key)
            mapping[key] = canonical
        return mapping

    @classmethod
    def parse(cls, file_or_content: Any) -> list[dict[str, Any]]:
        """
        Lê o arquivo CSV linha a linha e retorna lista de dicionários estruturados.
        """
        string_io = cls._to_string_io(file_or_content)
        content_sample = string_io.getvalue()

        if not content_sample:
            return []

        delimiter = cls._detect_delimiter(content_sample)
        reader = csv.DictReader(string_io, delimiter=delimiter)

        if not reader.fieldnames:
            raise CSVParseError('O arquivo CSV está vazio ou sem cabeçalho.')

        header_mapping = cls._normalize_headers(list(reader.fieldnames))
        canonical_headers = set(header_mapping.values())

        missing_headers = cls.COLUNAS_OBRIGATORIAS_CABECALHO - canonical_headers
        if missing_headers:
            raise CSVParseError(f'Cabeçalho do CSV incompleto. Colunas ausentes: {", ".join(sorted(missing_headers))}')

        parsed_rows: list[dict[str, Any]] = []

        for line_num, raw_row in enumerate(reader, start=2):
            clean_row: dict[str, str] = {}
            for k, v in raw_row.items():
                if not k:
                    continue
                key_lower = str(k).strip().lower()
                canonical_key = header_mapping.get(key_lower, key_lower)
                clean_row[canonical_key] = clean_cell_value(v)

            if not any(clean_row.values()):
                continue

            origem = clean_row.get('origem', '')
            numero_questao = clean_row.get('numero_questao', '')
            enunciado = clean_row.get('enunciado', '')
            correta_letra = clean_row.get('gabarito', '').upper() or None
            comentarios = clean_row.get('comentarios', '')

            tipo_raw = clean_row.get('tipo', '').upper()
            disciplina = clean_row.get('disciplina', '')
            topico = clean_row.get('topico', '') or None
            dificuldade = clean_row.get('dificuldade', '').upper()

            raw_tags = clean_row.get('tags', '')
            tags: list[str] = []
            if raw_tags:
                tag_delimiters = [',', ';']
                tags_list = [raw_tags]
                for d in tag_delimiters:
                    tags_list = [part for item in tags_list for part in item.split(d)]
                tags = [t.strip() for t in tags_list if t.strip()]

            alternativas: list[dict[str, Any]] = []
            for letra in cls.LETRAS_ALTERNATIVAS:
                col_name = f'alt_{letra}'
                texto_alt = clean_row.get(col_name, '')
                if texto_alt:
                    letra_upper = letra.upper()
                    alternativas.append(
                        {
                            'letra': letra_upper,
                            'texto': texto_alt,
                            'correta': (correta_letra == letra_upper),
                        }
                    )

            if not tipo_raw:
                tipo_raw = 'OBJ' if alternativas else 'DIS'

            parsed_rows.append(
                {
                    'linha': line_num,
                    'origem': origem,
                    'numero_questao': numero_questao,
                    'enunciado': enunciado,
                    'tipo': tipo_raw,
                    'disciplina': disciplina,
                    'topico': topico,
                    'dificuldade': dificuldade,
                    'tags': tags,
                    'alternativas': alternativas,
                    'correta': correta_letra,
                    'criterios_correcao': comentarios,
                    'raw': clean_row,
                }
            )

        return parsed_rows


class ExcelQuestaoParser:
    """
    Parser nativo para leitura e estruturação de planilhas Microsoft Excel (.xlsx) de questões.

    Suporta especificidades reais de planilhas:
    - Leitura direta da matriz de células (independente de objetos ListObjects / tabelas fragmentadas).
    - Descarte de colunas e linhas vazias além da área de dados (ex.: colunas K–Z, linhas vazias no final).
    - Conversão correta de números/floats (ex.: 26.0 -> '26', opções numéricas de cálculos -> '12', '15.5').
    - Preservação de quebras de linha (\n) em enunciados longos.
    - Suporte a comentários vazios/opcionais.
    """

    COLUNAS_OBRIGATORIAS_CABECALHO = {
        'enunciado',
    }

    LETRAS_ALTERNATIVAS = ('a', 'b', 'c', 'd', 'e')
    ALIAS_MAP = HEADER_ALIAS_MAP

    @classmethod
    def _load_workbook(cls, file_or_content: Any):
        """Carrega o workbook openpyxl a partir de bytes, UploadedFile, caminho ou IO."""
        import openpyxl

        if isinstance(file_or_content, (bytes, bytearray)):
            stream = io.BytesIO(file_or_content)
            return openpyxl.load_workbook(stream, data_only=True)
        if hasattr(file_or_content, 'read'):
            raw = file_or_content.read()
            if hasattr(file_or_content, 'seek'):
                file_or_content.seek(0)
            stream = io.BytesIO(raw)
            return openpyxl.load_workbook(stream, data_only=True)
        if isinstance(file_or_content, str):
            return openpyxl.load_workbook(file_or_content, data_only=True)
        raise CSVParseError('Tipo de entrada inválido para leitura de arquivo Excel.')

    @classmethod
    def parse(cls, file_or_content: Any) -> list[dict[str, Any]]:
        """
        Lê a planilha Excel ativa e retorna lista de dicionários estruturados.
        """
        try:
            wb = cls._load_workbook(file_or_content)
        except Exception as exc:
            raise CSVParseError(f'Não foi possível abrir o arquivo Excel: {exc}') from exc

        ws = wb.active
        if ws is None or ws.max_row < 1:
            return []

        # 1. Leitura do cabeçalho na Linha 1
        header_row = [cell.value for cell in ws[1]]
        if not any(header_row):
            raise CSVParseError('A planilha Excel está vazia ou sem cabeçalho na primeira linha.')

        # Mapeia cada índice de coluna (0-indexed) para o nome canônico
        col_mapping: list[tuple[int, str]] = []
        canonical_headers: set[str] = set()

        for col_idx, raw_header in enumerate(header_row):
            if raw_header is None:
                continue
            key_lower = str(raw_header).strip().lower()
            if not key_lower:
                continue
            canonical_name = cls.ALIAS_MAP.get(key_lower, key_lower)
            col_mapping.append((col_idx, canonical_name))
            canonical_headers.add(canonical_name)

        missing_headers = cls.COLUNAS_OBRIGATORIAS_CABECALHO - canonical_headers
        if missing_headers:
            raise CSVParseError(
                f'Cabeçalho da planilha Excel incompleto. Colunas ausentes: {", ".join(sorted(missing_headers))}'
            )

        parsed_rows: list[dict[str, Any]] = []

        # 2. Leitura das linhas de dados (linha 2 em diante)
        for row_idx, row_cells in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            clean_row: dict[str, str] = {}

            # Extrai apenas as colunas mapeadas no cabeçalho (ignora colunas K-Z vazias)
            for col_idx, canonical_name in col_mapping:
                cell_val = row_cells[col_idx] if col_idx < len(row_cells) else None
                clean_row[canonical_name] = clean_cell_value(cell_val)

            # Se a linha inteira estiver vazia (ex.: linhas residuais 32–35), ignora
            if not any(clean_row.values()):
                continue

            origem = clean_row.get('origem', '')
            numero_questao = clean_row.get('numero_questao', '')
            enunciado = clean_row.get('enunciado', '')
            correta_letra = clean_row.get('gabarito', '').upper() or None
            comentarios = clean_row.get('comentarios', '')

            tipo_raw = clean_row.get('tipo', '').upper()
            disciplina = clean_row.get('disciplina', '')
            topico = clean_row.get('topico', '') or None
            dificuldade = clean_row.get('dificuldade', '').upper()

            raw_tags = clean_row.get('tags', '')
            tags: list[str] = []
            if raw_tags:
                tag_delimiters = [',', ';']
                tags_list = [raw_tags]
                for d in tag_delimiters:
                    tags_list = [part for item in tags_list for part in item.split(d)]
                tags = [t.strip() for t in tags_list if t.strip()]

            # Estruturação das alternativas (A até E) — aceita valores numéricos ou texto
            alternativas: list[dict[str, Any]] = []
            for letra in cls.LETRAS_ALTERNATIVAS:
                col_name = f'alt_{letra}'
                texto_alt = clean_row.get(col_name, '')
                if texto_alt:
                    letra_upper = letra.upper()
                    alternativas.append(
                        {
                            'letra': letra_upper,
                            'texto': texto_alt,
                            'correta': (correta_letra == letra_upper),
                        }
                    )

            if not tipo_raw:
                tipo_raw = 'OBJ' if alternativas else 'DIS'

            parsed_rows.append(
                {
                    'linha': row_idx,
                    'origem': origem,
                    'numero_questao': numero_questao,
                    'enunciado': enunciado,
                    'tipo': tipo_raw,
                    'disciplina': disciplina,
                    'topico': topico,
                    'dificuldade': dificuldade,
                    'tags': tags,
                    'alternativas': alternativas,
                    'correta': correta_letra,
                    'criterios_correcao': comentarios,
                    'raw': clean_row,
                }
            )

        return parsed_rows


class FileQuestaoParser:
    """
    Parser unificado que detecta automaticamente se o arquivo é Excel (.xlsx) ou CSV.
    """

    @classmethod
    def parse(cls, file_or_content: Any, filename: str = '') -> list[dict[str, Any]]:
        """
        Analisa o arquivo ou conteúdo fornecido despachando para o parser adequado.
        """
        # Detecção por nome do arquivo
        name = ''
        if filename:
            name = filename.lower()
        elif hasattr(file_or_content, 'name') and file_or_content.name:
            name = file_or_content.name.lower()

        if name.endswith(('.xlsx', '.xlsm', '.xltx', '.xltm')):
            return ExcelQuestaoParser.parse(file_or_content)

        if name.endswith('.csv'):
            return CSVQuestaoParser.parse(file_or_content)

        # Detecção por assinatura de bytes (ZIP/XLSX: PK\x03\x04)
        if isinstance(file_or_content, (bytes, bytearray)):
            if file_or_content.startswith(b'PK\x03\x04'):
                return ExcelQuestaoParser.parse(file_or_content)
            return CSVQuestaoParser.parse(file_or_content)

        # Fallback padrão: tenta Excel primeiro se for upload binário, senão CSV
        try:
            return ExcelQuestaoParser.parse(file_or_content)
        except Exception:
            return CSVQuestaoParser.parse(file_or_content)
