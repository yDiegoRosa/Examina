import io
from pathlib import Path

from django.test import TestCase

from .parser import CSVParseError, CSVQuestaoParser


class CSVQuestaoParserTest(TestCase):
    """Testes unitários para o parser de leitura de CSV de questões."""

    def test_parse_valid_objective_question(self):
        """Verifica a estruturação correta de uma questão objetiva."""
        csv_content = (
            'enunciado,tipo,disciplina,topico,dificuldade,tags,alt_a,alt_b,alt_c,alt_d,alt_e,correta,criterios_correcao\n'
            'Qual é a capital do RJ?,OBJ,Geografia,Sudeste,F,rio;geografia,Niterói,Rio de Janeiro,Petrópolis,Cabo Frio,,B,\n'
        )
        result = CSVQuestaoParser.parse(csv_content)

        self.assertEqual(len(result), 1)
        row = result[0]
        self.assertEqual(row['linha'], 2)
        self.assertEqual(row['enunciado'], 'Qual é a capital do RJ?')
        self.assertEqual(row['tipo'], 'OBJ')
        self.assertEqual(row['disciplina'], 'Geografia')
        self.assertEqual(row['topico'], 'Sudeste')
        self.assertEqual(row['dificuldade'], 'F')
        self.assertEqual(row['tags'], ['rio', 'geografia'])
        self.assertEqual(row['correta'], 'B')
        self.assertEqual(len(row['alternativas']), 4)
        self.assertEqual(row['alternativas'][0], {'letra': 'A', 'texto': 'Niterói', 'correta': False})
        self.assertEqual(row['alternativas'][1], {'letra': 'B', 'texto': 'Rio de Janeiro', 'correta': True})
        self.assertEqual(row['alternativas'][2], {'letra': 'C', 'texto': 'Petrópolis', 'correta': False})
        self.assertEqual(row['alternativas'][3], {'letra': 'D', 'texto': 'Cabo Frio', 'correta': False})

    def test_parse_valid_discursive_question(self):
        """Verifica a estruturação correta de uma questão discursiva."""
        csv_content = (
            'enunciado,tipo,disciplina,topico,dificuldade,tags,alt_a,alt_b,alt_c,alt_d,alt_e,correta,criterios_correcao\n'
            '"Explique o conceito de polimorfismo em POO.",DIS,Programação,POO,M,"poo,computacao",,,,,,,O aluno deve conceituar sobrescrita e sobrecarga.\n'
        )
        result = CSVQuestaoParser.parse(csv_content)

        self.assertEqual(len(result), 1)
        row = result[0]
        self.assertEqual(row['enunciado'], 'Explique o conceito de polimorfismo em POO.')
        self.assertEqual(row['tipo'], 'DIS')
        self.assertEqual(row['disciplina'], 'Programação')
        self.assertEqual(row['topico'], 'POO')
        self.assertEqual(row['dificuldade'], 'M')
        self.assertEqual(row['tags'], ['poo', 'computacao'])
        self.assertEqual(row['criterios_correcao'], 'O aluno deve conceituar sobrescrita e sobrecarga.')
        self.assertEqual(len(row['alternativas']), 0)

    def test_parse_semicolon_delimiter(self):
        """Verifica suporte automático a arquivos delimitados por ponto-e-vírgula (;)."""
        csv_content = (
            'enunciado;tipo;disciplina;topico;dificuldade;tags;alt_a;alt_b;alt_c;alt_d;alt_e;correta;criterios_correcao\n'
            '2 + 2 = ?;OBJ;Matemática;;F;soma;3;4;;;;B;\n'
        )
        result = CSVQuestaoParser.parse(csv_content)

        self.assertEqual(len(result), 1)
        row = result[0]
        self.assertEqual(row['enunciado'], '2 + 2 = ?')
        self.assertEqual(row['disciplina'], 'Matemática')
        self.assertIsNone(row['topico'])
        self.assertEqual(len(row['alternativas']), 2)
        self.assertEqual(row['correta'], 'B')

    def test_parse_with_utf8_bom(self):
        """Verifica remoção correta do BOM UTF-8 (\ufeff) gerado pelo Microsoft Excel."""
        csv_bytes = (
            '\ufeff'
            'enunciado,tipo,disciplina,topico,dificuldade,tags,alt_a,alt_b,alt_c,alt_d,alt_e,correta,criterios_correcao\n'
            'Questão com BOM,OBJ,História,Brasil,M,historia,Alt A,Alt B,,,A,\n'
        ).encode('utf-8')

        result = CSVQuestaoParser.parse(csv_bytes)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['enunciado'], 'Questão com BOM')

    def test_parse_sample_file(self):
        """Verifica a leitura do arquivo modelo de exemplo modelo_questoes.csv."""
        sample_path = Path(__file__).resolve().parent / 'samples' / 'modelo_questoes.csv'
        with open(sample_path, 'rb') as f:
            result = CSVQuestaoParser.parse(f)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['tipo'], 'OBJ')
        self.assertEqual(result[1]['tipo'], 'DIS')

    def test_parse_missing_mandatory_headers_raises_error(self):
        """Verifica se erro específico é levantado quando cabeçalhos obrigatórios estão ausentes."""
        csv_invalid = 'enunciado,disciplina,tags\nTexto,Matemática,tag1\n'
        with self.assertRaises(CSVParseError) as ctx:
            CSVQuestaoParser.parse(csv_invalid)

        self.assertIn('Cabeçalho do CSV incompleto', str(ctx.exception))

    def test_parse_empty_content(self):
        """Verifica retorno de lista vazia para entradas em branco."""
        self.assertEqual(CSVQuestaoParser.parse(''), [])
        self.assertEqual(CSVQuestaoParser.parse(io.StringIO('')), [])
