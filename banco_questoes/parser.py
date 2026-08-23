import csv
import io
from typing import Any


class CSVParseError(Exception):
    """Exceção levantada quando o arquivo CSV possui problemas estruturais."""


class CSVQuestaoParser:
    """
    Parser nativo para leitura e estruturação de arquivos CSV de questões.

    Atende a especificação de 13 colunas:
    enunciado, tipo, disciplina, topico, dificuldade, tags,
    alt_a, alt_b, alt_c, alt_d, alt_e, correta, criterios_correcao
    """

    COLUNAS_OBRIGATORIAS_CABECALHO = {
        'enunciado',
        'tipo',
        'disciplina',
        'dificuldade',
    }

    LETRAS_ALTERNATIVAS = ('a', 'b', 'c', 'd', 'e')

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
            # Conta ocorrências fora de aspas
            return ';' if first_line.count(';') > first_line.count(',') else ','
        return ','

    @classmethod
    def parse(cls, file_or_content: Any) -> list[dict[str, Any]]:
        """
        Lê o arquivo CSV linha a linha e retorna lista de dicionários estruturados.

        Cada item contém:
        - linha: int (número da linha real no CSV, cabeçalho = linha 1)
        - enunciado: str
        - tipo: str ('OBJ' ou 'DIS')
        - disciplina: str
        - topico: str | None
        - dificuldade: str ('F', 'M' ou 'D')
        - tags: list[str]
        - alternativas: list[dict] [{'letra': 'A', 'texto': '...', 'correta': bool}]
        - correta: str | None ('A', 'B', 'C', 'D', 'E')
        - criterios_correcao: str
        - raw: dict original da linha
        """
        string_io = cls._to_string_io(file_or_content)
        content_sample = string_io.getvalue()

        if not content_sample:
            return []

        delimiter = cls._detect_delimiter(content_sample)
        reader = csv.DictReader(string_io, delimiter=delimiter)

        if not reader.fieldnames:
            raise CSVParseError('O arquivo CSV está vazio ou sem cabeçalho.')

        # Normaliza nomes de colunas do cabeçalho (minúsculo e sem espaços)
        normalized_headers = {name.strip().lower() for name in reader.fieldnames if name}
        missing_headers = cls.COLUNAS_OBRIGATORIAS_CABECALHO - normalized_headers

        if missing_headers:
            raise CSVParseError(f'Cabeçalho do CSV incompleto. Colunas ausentes: {", ".join(sorted(missing_headers))}')

        parsed_rows: list[dict[str, Any]] = []

        # reader.line_num inicia em 2 para a primeira linha de dados
        for line_num, raw_row in enumerate(reader, start=2):
            # Normaliza chaves e valores da linha
            clean_row = {
                (k.strip().lower() if k else ''): (v.strip() if isinstance(v, str) else '')
                for k, v in raw_row.items()
                if k
            }

            # Se a linha estiver totalmente vazia, ignora
            if not any(clean_row.values()):
                continue

            enunciado = clean_row.get('enunciado', '')
            tipo = clean_row.get('tipo', '').upper()
            disciplina = clean_row.get('disciplina', '')
            topico = clean_row.get('topico', '') or None
            dificuldade = clean_row.get('dificuldade', '').upper()
            criterios = clean_row.get('criterios_correcao', '')
            correta_letra = clean_row.get('correta', '').upper() or None

            # Processamento de tags (suporta separação por vírgula ou ponto-e-vírgula)
            raw_tags = clean_row.get('tags', '')
            tags: list[str] = []
            if raw_tags:
                tag_delimiters = [',', ';']
                tags_list = [raw_tags]
                for d in tag_delimiters:
                    tags_list = [part for item in tags_list for part in item.split(d)]
                tags = [t.strip() for t in tags_list if t.strip()]

            # Estruturação das alternativas (A até E)
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

            parsed_rows.append(
                {
                    'linha': line_num,
                    'enunciado': enunciado,
                    'tipo': tipo,
                    'disciplina': disciplina,
                    'topico': topico,
                    'dificuldade': dificuldade,
                    'tags': tags,
                    'alternativas': alternativas,
                    'correta': correta_letra,
                    'criterios_correcao': criterios,
                    'raw': clean_row,
                }
            )

        return parsed_rows
