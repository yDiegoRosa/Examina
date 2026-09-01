"""
test_parser.py — Testes unitários do CSVQuestaoParser e FileQuestaoParser.

Cobre:
- Estruturação de questões objetivas e discursivas (10 colunas)
- Detecção automática de delimitador (vírgula / ponto-e-vírgula)
- Decodificação BOM UTF-8
- Validação de cabeçalho obrigatório (enunciado)
- Aliases de cabeçalhos alternativos
- Inferência automática de tipo (OBJ/DIS)
- Utilitário clean_cell_value
- Dispatcher FileQuestaoParser por extensão
"""

import pytest

from banco_questoes.parser import (
    CSVParseError,
    CSVQuestaoParser,
    FileQuestaoParser,
    clean_cell_value,
)

# ---------------------------------------------------------------------------
# clean_cell_value
# ---------------------------------------------------------------------------


class TestCleanCellValue:
    def test_none_retorna_string_vazia(self):
        assert clean_cell_value(None) == ''

    def test_float_inteiro_sem_decimal(self):
        assert clean_cell_value(26.0) == '26'

    def test_float_com_decimal(self):
        assert clean_cell_value(12.5) == '12.5'

    def test_int_vira_string(self):
        assert clean_cell_value(7) == '7'

    def test_str_com_espacos_bordas_removidos(self):
        assert clean_cell_value('  texto  ') == 'texto'

    def test_bool_true(self):
        assert clean_cell_value(True) == 'True'

    def test_bool_false(self):
        assert clean_cell_value(False) == 'False'


# ---------------------------------------------------------------------------
# CSVQuestaoParser — questão objetiva 10 colunas
# ---------------------------------------------------------------------------


class TestCSVQuestaoParserObjetiva:
    def test_parse_estrutura_basica(self):
        """Verifica campos obrigatórios extraídos corretamente."""
        csv = (
            'origem,questao,enunciado,opcao_a,opcao_b,opcao_c,opcao_d,opcao_e,gabarito,comentarios\n'
            'ENADE 2021,26,Qual é a capital do RJ?,Niterói,Rio de Janeiro,Petrópolis,Cabo Frio,,B,Alt B correta.\n'
        )
        result = CSVQuestaoParser.parse(csv)

        assert len(result) == 1
        row = result[0]
        assert row['linha'] == 2
        assert row['origem'] == 'ENADE 2021'
        assert row['numero_questao'] == '26'
        assert row['enunciado'] == 'Qual é a capital do RJ?'
        assert row['correta'] == 'B'
        assert row['criterios_correcao'] == 'Alt B correta.'

    def test_parse_tipo_inferido_obj_quando_ha_alternativas(self):
        """Tipo deve ser inferido como OBJ quando há alternativas preenchidas."""
        csv = (
            'origem,questao,enunciado,opcao_a,opcao_b,opcao_c,opcao_d,opcao_e,gabarito,comentarios\n'
            'X,1,Enunciado?,Alt A,Alt B,,,, A,\n'
        )
        result = CSVQuestaoParser.parse(csv)
        assert result[0]['tipo'] == 'OBJ'

    def test_parse_alternativas_estruturadas(self):
        """Alternativas devem ter letra, texto e flag correta corretos."""
        csv = (
            'origem,questao,enunciado,opcao_a,opcao_b,opcao_c,opcao_d,opcao_e,gabarito,comentarios\n'
            'X,1,Questão?,Alpha,Beta,Gamma,,,B,\n'
        )
        result = CSVQuestaoParser.parse(csv)
        alts = result[0]['alternativas']

        assert len(alts) == 3
        assert alts[0] == {'letra': 'A', 'texto': 'Alpha', 'correta': False}
        assert alts[1] == {'letra': 'B', 'texto': 'Beta', 'correta': True}
        assert alts[2] == {'letra': 'C', 'texto': 'Gamma', 'correta': False}

    def test_parse_linha_vazia_ignorada(self):
        """Linhas completamente vazias devem ser descartadas."""
        csv = (
            'origem,questao,enunciado,opcao_a,opcao_b,gabarito,comentarios\n'
            'X,1,Questão?,Alt A,Alt B,A,\n'
            ',,,,,, \n'  # linha com apenas espaços
            'Y,2,Outra?,Alt C,Alt D,B,\n'
        )
        result = CSVQuestaoParser.parse(csv)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# CSVQuestaoParser — questão discursiva
# ---------------------------------------------------------------------------


class TestCSVQuestaoParserDiscursiva:
    def test_parse_tipo_inferido_dis_sem_alternativas(self):
        """Tipo deve ser inferido como DIS quando não há alternativas preenchidas."""
        csv = (
            'origem,questao,enunciado,opcao_a,opcao_b,opcao_c,opcao_d,opcao_e,gabarito,comentarios\n'
            'POSCOMP,5,Explique polimorfismo.,,,,,,, Sobrescrita e sobrecarga.\n'
        )
        result = CSVQuestaoParser.parse(csv)
        row = result[0]
        assert row['tipo'] == 'DIS'
        assert len(row['alternativas']) == 0
        assert row['correta'] is None

    def test_parse_comentarios_preservados(self):
        csv = 'enunciado,comentarios\nDiscuta Big O.,O(n log n) para QuickSort.\n'
        result = CSVQuestaoParser.parse(csv)
        assert result[0]['criterios_correcao'] == 'O(n log n) para QuickSort.'


# ---------------------------------------------------------------------------
# CSVQuestaoParser — detecção de delimitador
# ---------------------------------------------------------------------------


class TestCSVDelimitador:
    def test_delimitador_ponto_e_virgula(self):
        csv = 'origem;questao;enunciado;opcao_a;opcao_b;gabarito;comentarios\nENADE;1;2+2=?;3;4;B;\n'
        result = CSVQuestaoParser.parse(csv)
        assert len(result) == 1
        assert result[0]['enunciado'] == '2+2=?'
        assert result[0]['correta'] == 'B'

    def test_delimitador_virgula_padrao(self):
        csv = 'enunciado,opcao_a,opcao_b,gabarito\nQuestão?,Alt A,Alt B,A\n'
        result = CSVQuestaoParser.parse(csv)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# CSVQuestaoParser — BOM UTF-8 e encoding
# ---------------------------------------------------------------------------


class TestCSVEncoding:
    def test_bom_utf8_removido(self):
        """BOM \\ufeff gerado pelo Excel deve ser ignorado."""
        csv_bytes = (
            '\ufeff'
            'origem,questao,enunciado,opcao_a,opcao_b,gabarito,comentarios\n'
            'ENADE 2023,5,Questão com BOM,Alt A,Alt B,A,\n'
        ).encode()

        result = CSVQuestaoParser.parse(csv_bytes)
        assert len(result) == 1
        assert result[0]['enunciado'] == 'Questão com BOM'
        assert result[0]['origem'] == 'ENADE 2023'

    def test_bytes_latin1_decodificados(self):
        """Arquivo latin-1 deve ser decodificado sem erro."""
        csv_bytes = 'enunciado,opcao_a,opcao_b,gabarito\nQuest\xe3o?,Alt A,Alt B,B\n'  # \xe3 = ã em latin-1
        result = CSVQuestaoParser.parse(csv_bytes.encode('latin-1'))
        assert len(result) == 1


# ---------------------------------------------------------------------------
# CSVQuestaoParser — validação de cabeçalho
# ---------------------------------------------------------------------------


class TestCSVCabecalho:
    def test_sem_coluna_enunciado_levanta_erro(self):
        """Header sem 'enunciado' deve levantar CSVParseError."""
        csv = 'origem,questao,opcao_a,gabarito\nX,1,Alt A,A\n'
        with pytest.raises(CSVParseError, match='enunciado'):
            CSVQuestaoParser.parse(csv)

    def test_csv_vazio_retorna_lista_vazia(self):
        """String completamente vazia deve retornar []."""
        result = CSVQuestaoParser.parse('')
        assert result == []

    def test_csv_apenas_header_sem_dados_retorna_lista_vazia(self):
        """CSV com cabeçalho mas sem linhas de dados deve retornar []."""
        csv = 'origem,questao,enunciado,opcao_a,opcao_b,gabarito,comentarios\n'
        result = CSVQuestaoParser.parse(csv)
        assert result == []


# ---------------------------------------------------------------------------
# CSVQuestaoParser — aliases de cabeçalhos
# ---------------------------------------------------------------------------


class TestCSVAliases:
    def test_alias_opcao_a_mapeado(self):
        """'opção a' deve ser mapeado para alt_a."""
        csv = 'enunciado,opção a,opção b,gabarito\nQuestão?,Texto A,Texto B,A\n'
        result = CSVQuestaoParser.parse(csv)
        alts = result[0]['alternativas']
        assert alts[0]['letra'] == 'A'
        assert alts[0]['texto'] == 'Texto A'

    def test_alias_alternativa_b_mapeado(self):
        """'alternativa b' deve ser mapeado para alt_b."""
        csv = 'enunciado,alternativa a,alternativa b,gabarito\nPergunta?,Resp A,Resp B,B\n'
        result = CSVQuestaoParser.parse(csv)
        alts = result[0]['alternativas']
        assert alts[1]['letra'] == 'B'
        assert alts[1]['texto'] == 'Resp B'

    def test_alias_correta_mapeado_para_gabarito(self):
        """'correta' como nome de coluna deve ser mapeada para gabarito."""
        csv = 'enunciado,opcao_a,opcao_b,correta\nQuestão?,Alt A,Alt B,B\n'
        result = CSVQuestaoParser.parse(csv)
        assert result[0]['correta'] == 'B'

    def test_alias_numero_questao_variantes(self):
        """'questão', 'questao' e 'nº' devem mapear para numero_questao."""
        for col in ('questão', 'questao', 'nº'):
            csv = f'enunciado,{col}\nPergunta?,42\n'
            result = CSVQuestaoParser.parse(csv)
            assert result[0]['numero_questao'] == '42', f'Falhou para alias: {col}'


# ---------------------------------------------------------------------------
# FileQuestaoParser — dispatcher por extensão
# ---------------------------------------------------------------------------


class TestFileQuestaoParserDispatcher:
    def test_dispatch_csv_por_extensao(self):
        """FileQuestaoParser deve despachar para CSVQuestaoParser para .csv."""
        csv = 'enunciado,opcao_a,opcao_b,gabarito\nQuestão?,Alt A,Alt B,A\n'
        result = FileQuestaoParser.parse(csv.encode(), filename='questoes.csv')
        assert len(result) == 1
        assert result[0]['enunciado'] == 'Questão?'

    def test_dispatch_xlsx_por_assinatura_bytes(self):
        """Bytes iniciando com PK\\x03\\x04 devem ser tratados como Excel (CSVParseError ao falhar)."""
        pk_bytes = b'PK\x03\x04' + b'\x00' * 100
        # Bytes corrompidos → ExcelQuestaoParser falha → CSVParseError levantada
        with pytest.raises(CSVParseError):
            FileQuestaoParser.parse(pk_bytes)
