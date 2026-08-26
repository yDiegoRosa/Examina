import io
from pathlib import Path
from typing import Any

import openpyxl
from django.test import TestCase

from .models import Questao
from .parser import CSVParseError, CSVQuestaoParser, ExcelQuestaoParser, FileQuestaoParser
from .validator import CSVQuestaoValidator


class CSVQuestaoParserTest(TestCase):
    """Testes unitários para o parser de leitura de CSV de questões (formato 10 colunas)."""

    def test_parse_valid_objective_question_10col(self):
        """Verifica a estruturação correta de uma questão objetiva no formato de 10 colunas."""
        csv_content = (
            'origem,questao,enunciado,opcao_a,opcao_b,opcao_c,opcao_d,opcao_e,gabarito,comentarios\n'
            'ENADE ADS 2021,26,Qual é a capital do RJ?,Niterói,Rio de Janeiro,Petrópolis,Cabo Frio,,B,Alternativa correta é B.\n'
        )
        result = CSVQuestaoParser.parse(csv_content)

        self.assertEqual(len(result), 1)
        row = result[0]
        self.assertEqual(row['linha'], 2)
        self.assertEqual(row['origem'], 'ENADE ADS 2021')
        self.assertEqual(row['numero_questao'], '26')
        self.assertEqual(row['enunciado'], 'Qual é a capital do RJ?')
        self.assertEqual(row['tipo'], 'OBJ')  # Inferido automaticamente
        self.assertEqual(row['correta'], 'B')
        self.assertEqual(row['criterios_correcao'], 'Alternativa correta é B.')
        self.assertEqual(len(row['alternativas']), 4)
        self.assertEqual(row['alternativas'][0], {'letra': 'A', 'texto': 'Niterói', 'correta': False})
        self.assertEqual(row['alternativas'][1], {'letra': 'B', 'texto': 'Rio de Janeiro', 'correta': True})

    def test_parse_valid_discursive_question_10col(self):
        """Verifica inferência automática de tipo DIS quando não há alternativas."""
        csv_content = (
            'origem,questao,enunciado,opcao_a,opcao_b,opcao_c,opcao_d,opcao_e,gabarito,comentarios\n'
            'POSCOMP 2025,15,Explique o conceito de polimorfismo em POO.,,,,,,, O aluno deve conceituar sobrescrita e sobrecarga.\n'
        )
        result = CSVQuestaoParser.parse(csv_content)

        self.assertEqual(len(result), 1)
        row = result[0]
        self.assertEqual(row['enunciado'], 'Explique o conceito de polimorfismo em POO.')
        self.assertEqual(row['tipo'], 'DIS')  # Inferido automaticamente
        self.assertEqual(row['origem'], 'POSCOMP 2025')
        self.assertEqual(row['numero_questao'], '15')
        self.assertEqual(len(row['alternativas']), 0)

    def test_parse_semicolon_delimiter_10col(self):
        """Verifica suporte automático a arquivos delimitados por ponto-e-vírgula (;)."""
        csv_content = (
            'origem;questao;enunciado;opcao_a;opcao_b;opcao_c;opcao_d;opcao_e;gabarito;comentarios\n'
            'ENADE 2022;10;2 + 2 = ?;3;4;;;;B;\n'
        )
        result = CSVQuestaoParser.parse(csv_content)

        self.assertEqual(len(result), 1)
        row = result[0]
        self.assertEqual(row['enunciado'], '2 + 2 = ?')
        self.assertEqual(row['origem'], 'ENADE 2022')
        self.assertEqual(len(row['alternativas']), 2)
        self.assertEqual(row['correta'], 'B')

    def test_parse_with_utf8_bom_10col(self):
        """Verifica remoção correta do BOM UTF-8 (\ufeff) gerado pelo Microsoft Excel."""
        csv_bytes = (
            '\ufeff'
            'origem,questao,enunciado,opcao_a,opcao_b,opcao_c,opcao_d,opcao_e,gabarito,comentarios\n'
            'ENADE 2023,5,Questão com BOM,Alt A,Alt B,,,,A,\n'
        ).encode('utf-8')

        result = CSVQuestaoParser.parse(csv_bytes)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['enunciado'], 'Questão com BOM')
        self.assertEqual(result[0]['origem'], 'ENADE 2023')

    def test_parse_sample_file(self):
        """Verifica a leitura do arquivo modelo de exemplo modelo_questoes.csv."""
        sample_path = Path(__file__).resolve().parent / 'samples' / 'modelo_questoes.csv'
        with open(sample_path, 'rb') as f:
            result = CSVQuestaoParser.parse(f)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['tipo'], 'OBJ')  # Inferido pois tem alternativas
        self.assertEqual(result[1]['tipo'], 'OBJ')  # Inferido pois tem alternativas
        self.assertEqual(result[0]['origem'], 'ENADE ADS 2021')

    def test_parse_missing_mandatory_headers_raises_error(self):
        """Verifica se erro específico é levantado quando o cabeçalho 'enunciado' está ausente."""
        csv_invalid = 'origem,questao,opcao_a\nENADE,5,Texto\n'
        with self.assertRaises(CSVParseError) as ctx:
            CSVQuestaoParser.parse(csv_invalid)

        self.assertIn('Cabeçalho do CSV incompleto', str(ctx.exception))

    def test_parse_empty_content(self):
        """Verifica retorno de lista vazia para entradas em branco."""
        self.assertEqual(CSVQuestaoParser.parse(''), [])
        self.assertEqual(CSVQuestaoParser.parse(io.StringIO('')), [])

    def test_parse_legacy_13col_format_still_works(self):
        """Verifica retrocompatibilidade: o parser aceita o formato antigo de 13 colunas."""
        csv_content = (
            'enunciado,tipo,disciplina,topico,dificuldade,tags,alt_a,alt_b,alt_c,alt_d,alt_e,correta,criterios_correcao\n'
            'Qual é 2+2?,OBJ,Matemática,Aritmética,F,soma,3,4,,,,B,\n'
        )
        result = CSVQuestaoParser.parse(csv_content)

        self.assertEqual(len(result), 1)
        row = result[0]
        self.assertEqual(row['enunciado'], 'Qual é 2+2?')
        self.assertEqual(row['tipo'], 'OBJ')
        self.assertEqual(row['disciplina'], 'Matemática')
        self.assertEqual(row['correta'], 'B')


class ExcelQuestaoParserTest(TestCase):
    """Testes unitários para o parser de planilhas Excel (.xlsx) de questões."""

    def _create_sample_workbook_bytes(self, rows: list[list[Any]]) -> bytes:
        """Helper para criar um arquivo .xlsx em memória com as linhas especificadas."""
        wb = openpyxl.Workbook()
        ws = wb.active
        for row in rows:
            ws.append(row)
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    def test_parse_valid_excel_with_numeric_options_and_newlines(self):
        """Verifica a leitura correta de planilha Excel com números, quebras de linha e colunas residuais."""
        headers = [
            'Origem',
            'Questão',
            'Enunciado',
            'Opção A',
            'Opção B',
            'Opção C',
            'Opção D',
            'Opção E',
            'Gabarito',
            'Comentários / Justificativa',
            'Coluna_K_Vazia',  # Coluna residual vazia além de J
        ]
        row_obj_text = [
            'ENADE ADS 2021',
            26,  # Número inteiro
            'Em relação ao serviço DNS,\nqual alternativa é correta?',  # Com quebra de linha
            'Opção A texto',
            'Opção B texto',
            'Opção C texto',
            'Opção D texto',
            'Opção E texto',
            'C',
            'Justificativa detalhada da questão C.',
            None,
        ]
        row_obj_calc = [
            'ENADE 2023',
            31.0,  # Float que representa inteiro
            'Calcule o valor da expressão matemática.',
            10,  # Opção numérica inteira
            20.5,  # Opção numérica decimal
            30,
            40,
            50,
            'B',
            None,  # Comentário vazio
            None,
        ]
        row_discursive = [
            'POSCOMP 2025',
            15,
            'Explique o funcionamento do algoritmo Dijkstra.',
            None,
            None,
            None,
            None,
            None,
            None,
            'Critérios: grafo ponderado, fila de prioridade.',
            None,
        ]
        empty_trailing_row = [None] * 11  # Linha residual vazia

        raw_bytes = self._create_sample_workbook_bytes(
            [
                headers,
                row_obj_text,
                row_obj_calc,
                row_discursive,
                empty_trailing_row,
            ]
        )

        result = ExcelQuestaoParser.parse(raw_bytes)

        # Deve descartar a linha residual vazia e retornar exatamente 3 questões
        self.assertEqual(len(result), 3)

        # Questão 1 (Objetiva com texto e número 26)
        q1 = result[0]
        self.assertEqual(q1['linha'], 2)
        self.assertEqual(q1['origem'], 'ENADE ADS 2021')
        self.assertEqual(q1['numero_questao'], '26')
        self.assertIn('\n', q1['enunciado'])
        self.assertEqual(q1['tipo'], 'OBJ')
        self.assertEqual(q1['correta'], 'C')
        self.assertEqual(len(q1['alternativas']), 5)
        self.assertTrue(q1['alternativas'][2]['correta'])

        # Questão 2 (Objetiva com opções numéricas convertidas para string)
        q2 = result[1]
        self.assertEqual(q2['linha'], 3)
        self.assertEqual(q2['origem'], 'ENADE 2023')
        self.assertEqual(q2['numero_questao'], '31')  # 31.0 -> '31'
        self.assertEqual(q2['tipo'], 'OBJ')
        self.assertEqual(q2['alternativas'][0]['texto'], '10')
        self.assertEqual(q2['alternativas'][1]['texto'], '20.5')
        self.assertEqual(q2['criterios_correcao'], '')  # None -> ''

        # Questão 3 (Discursiva sem alternativas)
        q3 = result[2]
        self.assertEqual(q3['linha'], 4)
        self.assertEqual(q3['tipo'], 'DIS')
        self.assertEqual(len(q3['alternativas']), 0)
        self.assertEqual(q3['criterios_correcao'], 'Critérios: grafo ponderado, fila de prioridade.')

    def test_parse_excel_missing_enunciado_header_raises_error(self):
        """Verifica se erro específico é levantado quando a coluna 'enunciado' não existe no Excel."""
        raw_bytes = self._create_sample_workbook_bytes(
            [
                ['Origem', 'Questão', 'Opção A'],
                ['ENADE', 1, 'Alt A'],
            ]
        )
        with self.assertRaises(CSVParseError) as ctx:
            ExcelQuestaoParser.parse(raw_bytes)

        self.assertIn('Cabeçalho da planilha Excel incompleto', str(ctx.exception))

    def test_file_questao_parser_dispatcher(self):
        """Verifica se FileQuestaoParser despacha corretamente com base na extensão ou conteúdo."""
        excel_bytes = self._create_sample_workbook_bytes(
            [
                ['origem', 'questao', 'enunciado', 'opcao_a', 'opcao_b', 'gabarito'],
                ['ENADE', 1, 'Pergunta teste?', 'Sim', 'Não', 'A'],
            ]
        )
        # Teste por bytes (magic bytes PK)
        res_bytes = FileQuestaoParser.parse(excel_bytes)
        self.assertEqual(len(res_bytes), 1)
        self.assertEqual(res_bytes[0]['enunciado'], 'Pergunta teste?')

        # Teste por filename CSV
        csv_str = 'enunciado,opcao_a,opcao_b,gabarito\nPergunta CSV?,A,B,A\n'
        res_csv = FileQuestaoParser.parse(csv_str, filename='questoes.csv')
        self.assertEqual(len(res_csv), 1)
        self.assertEqual(res_csv[0]['enunciado'], 'Pergunta CSV?')


class CSVQuestaoValidatorTest(TestCase):
    """Testes unitários para as regras de validação linha a linha de CSV."""

    def test_validate_valid_objective_row(self):
        """Verifica validação de uma linha objetiva completa e válida."""
        row = {
            'linha': 2,
            'origem': 'ENADE ADS 2021',
            'numero_questao': '26',
            'enunciado': 'Qual é a fórmula da água?',
            'tipo': 'OBJ',
            'disciplina': '',
            'topico': None,
            'dificuldade': '',
            'tags': [],
            'alternativas': [
                {'letra': 'A', 'texto': 'H2O', 'correta': True},
                {'letra': 'B', 'texto': 'CO2', 'correta': False},
            ],
            'correta': 'A',
            'criterios_correcao': '',
        }
        res = CSVQuestaoValidator.validate_row(row)
        self.assertTrue(res.is_valid)
        self.assertEqual(len(res.errors), 0)
        self.assertEqual(res.cleaned_data['tipo'], Questao.Tipo.OBJETIVA)
        self.assertEqual(res.cleaned_data['dificuldade'], Questao.Dificuldade.MEDIO)  # Default
        self.assertEqual(res.cleaned_data['disciplina'], 'Não Classificada')  # Default
        self.assertEqual(res.cleaned_data['origem'], 'ENADE ADS 2021')
        self.assertEqual(res.cleaned_data['numero_questao'], '26')
        self.assertEqual(len(res.cleaned_data['alternativas']), 2)

    def test_validate_valid_discursive_row(self):
        """Verifica validação de uma linha discursiva sem comentários (agora opcional)."""
        row = {
            'linha': 3,
            'origem': '',
            'numero_questao': '',
            'enunciado': 'Explique o princípio da inércia de Newton.',
            'tipo': 'DIS',
            'disciplina': '',
            'topico': None,
            'dificuldade': '',
            'tags': [],
            'alternativas': [],
            'correta': None,
            'criterios_correcao': '',
        }
        res = CSVQuestaoValidator.validate_row(row)
        self.assertTrue(res.is_valid)
        self.assertEqual(res.cleaned_data['tipo'], Questao.Tipo.DISCURSIVA)
        self.assertEqual(res.cleaned_data['dificuldade'], Questao.Dificuldade.MEDIO)

    def test_validate_missing_enunciado(self):
        """Verifica erro quando enunciado está ausente (único campo realmente obrigatório)."""
        row = {
            'linha': 4,
            'enunciado': '',
            'tipo': 'OBJ',
            'alternativas': [
                {'letra': 'A', 'texto': 'A', 'correta': True},
                {'letra': 'B', 'texto': 'B', 'correta': False},
            ],
            'correta': 'A',
        }
        res = CSVQuestaoValidator.validate_row(row)
        self.assertFalse(res.is_valid)
        self.assertTrue(any('enunciado' in err for err in res.errors))

    def test_validate_invalid_tipo_and_dificuldade(self):
        """Verifica rejeição de tipos e dificuldades explicitamente inválidos."""
        row = {
            'linha': 5,
            'enunciado': 'Enunciado de teste',
            'tipo': 'INVALIDO',
            'dificuldade': 'X',
        }
        res = CSVQuestaoValidator.validate_row(row)
        self.assertFalse(res.is_valid)
        self.assertTrue(any('Tipo de questão inválido' in err for err in res.errors))
        self.assertTrue(any('Nível de dificuldade inválido' in err for err in res.errors))

    def test_validate_objective_insufficient_alternatives(self):
        """Verifica erro em questão objetiva com menos de 2 alternativas."""
        row = {
            'linha': 6,
            'enunciado': 'Questão com 1 alternativa',
            'tipo': 'OBJ',
            'alternativas': [{'letra': 'A', 'texto': 'Única alternativa', 'correta': True}],
            'correta': 'A',
        }
        res = CSVQuestaoValidator.validate_row(row)
        self.assertFalse(res.is_valid)
        self.assertTrue(any('mínimo 2 alternativas' in err for err in res.errors))

    def test_validate_objective_correta_not_matching_alternatives(self):
        """Verifica erro quando gabarito não corresponde a nenhuma alternativa preenchida."""
        row = {
            'linha': 7,
            'enunciado': 'Questão com gabarito inexistente',
            'tipo': 'OBJ',
            'alternativas': [
                {'letra': 'A', 'texto': 'Alt A', 'correta': False},
                {'letra': 'B', 'texto': 'Alt B', 'correta': False},
            ],
            'correta': 'E',
        }
        res = CSVQuestaoValidator.validate_row(row)
        self.assertFalse(res.is_valid)
        self.assertTrue(any('não corresponde a nenhuma alternativa preenchida' in err for err in res.errors))

    def test_validate_defaults_applied_when_fields_absent(self):
        """Verifica que disciplina e dificuldade recebem valores-padrão quando ausentes."""
        row = {
            'linha': 8,
            'enunciado': 'Questão sem disciplina e dificuldade',
            'tipo': 'OBJ',
            'disciplina': '',
            'dificuldade': '',
            'alternativas': [
                {'letra': 'A', 'texto': 'A', 'correta': True},
                {'letra': 'B', 'texto': 'B', 'correta': False},
            ],
            'correta': 'A',
        }
        res = CSVQuestaoValidator.validate_row(row)
        self.assertTrue(res.is_valid)
        self.assertEqual(res.cleaned_data['disciplina'], 'Não Classificada')
        self.assertEqual(res.cleaned_data['dificuldade'], Questao.Dificuldade.MEDIO)

    def test_validate_batch_rows_report(self):
        """Verifica a geração do relatório consolidado com validate_rows."""
        rows = [
            {
                'linha': 2,
                'enunciado': 'Questão Válida',
                'tipo': 'OBJ',
                'alternativas': [
                    {'letra': 'A', 'texto': 'A', 'correta': True},
                    {'letra': 'B', 'texto': 'B', 'correta': False},
                ],
                'correta': 'A',
            },
            {
                'linha': 3,
                'enunciado': '',
                'tipo': 'OBJ',
            },
        ]
        report = CSVQuestaoValidator.validate_rows(rows)

        self.assertEqual(report.total_processadas, 2)
        self.assertEqual(report.total_validas, 1)
        self.assertEqual(report.total_invalidas, 1)
        self.assertFalse(report.is_valid)
        self.assertEqual(len(report.linhas_validas), 1)
        self.assertEqual(len(report.linhas_invalidas), 1)
        self.assertEqual(report.linhas_invalidas[0].linha, 3)

    def test_intra_file_deduplication_blocks_repeated_rows(self):
        """Verifica se linhas repetidas no mesmo CSV são detectadas e bloqueadas."""
        rows = [
            {
                'linha': 2,
                'enunciado': 'Qual é a velocidade da luz?',
                'tipo': 'OBJ',
                'alternativas': [
                    {'letra': 'A', 'texto': '300.000 km/s', 'correta': True},
                    {'letra': 'B', 'texto': '150.000 km/s', 'correta': False},
                ],
                'correta': 'A',
            },
            {
                'linha': 3,
                'enunciado': '   QUAL É A   VELOCIDADE DA LUZ?   ',
                'tipo': 'OBJ',
                'alternativas': [
                    {'letra': 'A', 'texto': '300.000 km/s', 'correta': True},
                    {'letra': 'B', 'texto': '150.000 km/s', 'correta': False},
                ],
                'correta': 'A',
            },
        ]
        report = CSVQuestaoValidator.validate_rows(rows, check_db_duplicates=False)

        self.assertEqual(report.total_processadas, 2)
        self.assertEqual(report.total_validas, 1)
        self.assertEqual(report.total_invalidas, 1)
        self.assertFalse(report.is_valid)

        invalid_row = report.linhas_invalidas[0]
        self.assertEqual(invalid_row.linha, 3)
        self.assertTrue(any('Enunciado duplicado no próprio arquivo' in err for err in invalid_row.errors))
        self.assertTrue(any('Linha 2' in err for err in invalid_row.errors))

    def test_database_deduplication_blocks_existing_question(self):
        """Verifica se questão cujo enunciado já existe no banco é bloqueada."""
        from .models import Disciplina

        disc = Disciplina.objects.create(nome='Física Geral')
        questao_existente = Questao.objects.create(
            disciplina=disc,
            tipo=Questao.Tipo.OBJETIVA,
            dificuldade=Questao.Dificuldade.MEDIO,
            enunciado='Qual é a constante gravitacional na Terra?',
        )

        rows = [
            {
                'linha': 2,
                'enunciado': '  qual é a constante gravitacional na terra?  ',
                'tipo': 'OBJ',
                'alternativas': [
                    {'letra': 'A', 'texto': '9.8 m/s²', 'correta': True},
                    {'letra': 'B', 'texto': '10.5 m/s²', 'correta': False},
                ],
                'correta': 'A',
            }
        ]
        report = CSVQuestaoValidator.validate_rows(rows, check_db_duplicates=True)

        self.assertEqual(report.total_processadas, 1)
        self.assertEqual(report.total_validas, 0)
        self.assertEqual(report.total_invalidas, 1)
        self.assertFalse(report.is_valid)

        invalid_row = report.linhas_invalidas[0]
        self.assertTrue(any('Já existe uma questão cadastrada no banco' in err for err in invalid_row.errors))
        self.assertTrue(any(f'Q{questao_existente.pk}' in err for err in invalid_row.errors))


class QuestaoImportViewTest(TestCase):
    """Testes de integração da view de importação e relatório pós-upload (formato 10 colunas)."""

    def setUp(self):
        from django.contrib.auth.models import Permission, User

        self.user = User.objects.create_user(username='docente_test', password='secret_password')  # noqa: S106
        perm = Permission.objects.get(codename='add_questao')
        self.user.user_permissions.add(perm)
        self.client.login(username='docente_test', password='secret_password')  # noqa: S106

    def test_import_page_renders_get(self):
        """Verifica se a tela de upload é renderizada com sucesso via GET."""
        from django.urls import reverse

        url = reverse('banco_questoes:questao_import')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Importação de Questões')
        self.assertContains(response, 'Baixar Modelo Excel')
        self.assertContains(response, 'Baixar Modelo CSV')

    def test_import_csv_success_persists_and_renders_report(self):
        """Verifica persistência de questões no formato 10 colunas e exibição de relatório."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.urls import reverse

        csv_content = (
            'origem,questao,enunciado,opcao_a,opcao_b,opcao_c,opcao_d,opcao_e,gabarito,comentarios\n'
            'ENADE ADS 2021,26,Qual é a capital da França?,Londres,Paris,Berlim,Madrid,,B,Paris é a capital desde o século X.\n'
            'POSCOMP 2025,15,Discorra sobre a Revolução Industrial.,,,,,,, Destacar a transição da manufatura para maquinofatura.\n'
        ).encode()

        uploaded_file = SimpleUploadedFile(
            'questoes_novas.csv',
            csv_content,
            content_type='text/csv',
        )

        url = reverse('banco_questoes:questao_import')
        response = self.client.post(url, {'csv_file': uploaded_file})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Relatório Detalhado Linha a Linha')
        self.assertContains(response, 'Importadas com Sucesso')
        self.assertContains(response, 'Qual é a capital da França?')
        self.assertContains(response, 'Discorra sobre a Revolução Industrial.')

        # Confirma que foram salvas no banco
        q_obj = Questao.objects.get(enunciado__icontains='França')
        self.assertEqual(q_obj.origem, 'ENADE ADS 2021')
        self.assertEqual(q_obj.numero_questao, '26')
        self.assertEqual(Questao.objects.filter(enunciado__icontains='Revolução Industrial').count(), 1)

    def test_import_excel_success_persists_and_renders_report(self):
        """Verifica persistência de questões via upload de planilha Excel (.xlsx) e relatório."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.urls import reverse

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(
            [
                'Origem',
                'Questão',
                'Enunciado',
                'Opção A',
                'Opção B',
                'Opção C',
                'Opção D',
                'Opção E',
                'Gabarito',
                'Comentários / Justificativa',
            ]
        )
        ws.append(
            [
                'ENADE ADS 2021',
                26,
                'Qual é a capital da Itália?',
                'Milão',
                'Roma',
                'Nápoles',
                'Turim',
                'Florença',
                'B',
                'Roma é a capital.',
            ]
        )
        ws.append(
            [
                'POSCOMP 2025',
                15,
                'Explique o algoritmo Bubble Sort.',
                None,
                None,
                None,
                None,
                None,
                None,
                'Explicar trocas adjacentes e complexidade O(n^2).',
            ]
        )
        buf = io.BytesIO()
        wb.save(buf)

        uploaded_file = SimpleUploadedFile(
            'questoes_novas.xlsx',
            buf.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )

        url = reverse('banco_questoes:questao_import')
        response = self.client.post(url, {'csv_file': uploaded_file})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Relatório Detalhado Linha a Linha')
        self.assertContains(response, 'Importadas com Sucesso')
        self.assertContains(response, 'Qual é a capital da Itália?')
        self.assertContains(response, 'Explique o algoritmo Bubble Sort.')

        # Confirma no banco de dados
        q_obj = Questao.objects.get(enunciado__icontains='Itália')
        self.assertEqual(q_obj.origem, 'ENADE ADS 2021')
        self.assertEqual(q_obj.numero_questao, '26')
        self.assertEqual(q_obj.tipo, 'OBJ')
        self.assertEqual(q_obj.alternativas.count(), 5)
        self.assertEqual(q_obj.alternativas.get(correta=True).letra, 'B')

        q_dis = Questao.objects.get(enunciado__icontains='Bubble Sort')
        self.assertEqual(q_dis.tipo, 'DIS')
        self.assertEqual(q_dis.origem, 'POSCOMP 2025')

    def test_import_csv_with_inconsistencies_shows_rejection_reasons(self):
        """Verifica se linhas inválidas geram relatório com motivos claros de rejeição."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.urls import reverse

        csv_content = (
            'origem,questao,enunciado,opcao_a,opcao_b,opcao_c,opcao_d,opcao_e,gabarito,comentarios\n'
            'ENADE,1,,Alt A,Alt B,,,,A,\n'
            'ENADE,2,Questão Objetiva Sem Gabarito,Alt A,Alt B,,,,, \n'
        ).encode()

        uploaded_file = SimpleUploadedFile(
            'questoes_invalidas.csv',
            csv_content,
            content_type='text/csv',
        )

        url = reverse('banco_questoes:questao_import')
        response = self.client.post(url, {'csv_file': uploaded_file})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Rejeitadas / Inconsistentes')
        self.assertContains(response, 'O campo &quot;enunciado&quot; é obrigatório')
        self.assertContains(response, 'O campo &quot;gabarito&quot; é obrigatório')


class CSVImportServiceTest(TestCase):
    """Testes unitários dedicados ao serviço CSVImportService (formato 10 colunas)."""

    def test_import_service_commits_valid_questions_10col(self):
        """Verifica se CSVImportService comita questões no formato de 10 colunas com campos novos."""
        from .importer import CSVImportService

        csv_text = (
            'origem,questao,enunciado,opcao_a,opcao_b,opcao_c,opcao_d,opcao_e,gabarito,comentarios\n'
            'POSCOMP 2025,15,Qual a fórmula da fotossíntese?,,,,,,,Explicar fase clara e escura.\n'
            'ENADE ADS 2021,26,O que é 5 x 5?,10,20,25,30,,C,Alternativa C é correta.\n'
        )

        res = CSVImportService.import_csv(csv_text)

        self.assertEqual(res.total_processadas, 2)
        self.assertEqual(res.total_sucesso, 2)
        self.assertEqual(res.total_rejeitadas, 0)
        self.assertFalse(res.has_errors)

        # Verifica criação no banco com campos novos
        q_obj = Questao.objects.get(enunciado='O que é 5 x 5?')
        self.assertEqual(q_obj.origem, 'ENADE ADS 2021')
        self.assertEqual(q_obj.numero_questao, '26')
        self.assertEqual(q_obj.disciplina.nome, 'Não Classificada')  # Default
        self.assertEqual(q_obj.dificuldade, 'M')  # Default
        self.assertEqual(q_obj.alternativas.count(), 4)
        self.assertEqual(q_obj.alternativas.get(correta=True).letra, 'C')
        self.assertEqual(q_obj.alternativas.get(correta=True).texto, '25')

        # Verifica discursiva inferida
        q_dis = Questao.objects.get(enunciado='Qual a fórmula da fotossíntese?')
        self.assertEqual(q_dis.tipo, 'DIS')
        self.assertEqual(q_dis.origem, 'POSCOMP 2025')

    def test_import_service_commits_excel_file(self):
        """Verifica se QuestaoImportService importa diretamente planilhas Excel binárias."""
        from .importer import QuestaoImportService

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(['Origem', 'Questão', 'Enunciado', 'Opção A', 'Opção B', 'Gabarito', 'Comentários'])
        ws.append(['ENADE', 1, 'Quanto é 10 + 20?', '30', '40', 'A', '10 + 20 = 30'])
        buf = io.BytesIO()
        wb.save(buf)

        res = QuestaoImportService.import_excel(buf.getvalue())
        self.assertEqual(res.total_processadas, 1)
        self.assertEqual(res.total_sucesso, 1)
        self.assertFalse(res.has_errors)

        q = Questao.objects.get(enunciado='Quanto é 10 + 20?')
        self.assertEqual(q.alternativas.count(), 2)
        self.assertEqual(q.alternativas.get(correta=True).letra, 'A')

    def test_import_service_partial_commits_only_valid_rows(self):
        """Verifica que linhas com erro são rejeitadas sem bloquear as linhas válidas."""
        from .importer import CSVImportService

        csv_text = (
            'origem,questao,enunciado,opcao_a,opcao_b,opcao_c,opcao_d,opcao_e,gabarito,comentarios\n'
            'ENADE,1,,,,,,,,\n'
            'ENADE,2,Capital de SP?,Santos,São Paulo,Campinas,,,B,\n'
        )

        res = CSVImportService.import_csv(csv_text)

        self.assertEqual(res.total_processadas, 2)
        self.assertEqual(res.total_sucesso, 1)
        self.assertEqual(res.total_rejeitadas, 1)
        self.assertTrue(res.has_errors)

        self.assertEqual(Questao.objects.filter(enunciado='Capital de SP?').count(), 1)
        self.assertEqual(Questao.objects.count(), 1)

    def test_import_service_reimport_blocks_as_database_duplicates(self):
        """Verifica se reimportar o mesmo CSV bloqueia as questões como duplicadas no banco."""
        from .importer import CSVImportService

        csv_text = (
            'origem,questao,enunciado,opcao_a,opcao_b,opcao_c,opcao_d,opcao_e,gabarito,comentarios\n'
            'ENADE 2026,1,Questão Unica 2026,Opcao 1,Opcao 2,,,,A,\n'
        )

        res1 = CSVImportService.import_csv(csv_text)
        self.assertEqual(res1.total_sucesso, 1)

        res2 = CSVImportService.import_csv(csv_text)
        self.assertEqual(res2.total_sucesso, 0)
        self.assertEqual(res2.total_rejeitadas, 1)
        self.assertTrue(any('Já existe uma questão cadastrada no banco' in err for err in res2.linhas[0].erros))


class QuestaoFilterTest(TestCase):
    """Testes unitários para o FilterSet QuestaoFilter (django-filter)."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        from .models import Disciplina, Topico

        user_model = get_user_model()
        self.user1 = user_model.objects.create_user(username='prof_silva', password='secret_password')  # noqa: S106
        self.user2 = user_model.objects.create_user(username='prof_souza', password='secret_password')  # noqa: S106

        self.disc_mat = Disciplina.objects.create(nome='Matemática')
        self.disc_fis = Disciplina.objects.create(nome='Física')

        self.top_alg = Topico.objects.create(disciplina=self.disc_mat, nome='Álgebra')
        self.top_geo = Topico.objects.create(disciplina=self.disc_mat, nome='Geometria')

        # Questão 1: Matemática, Álgebra, Fácil, OBJ, Ativa, prof_silva, tag: 'enem'
        self.q1 = Questao.objects.create(
            disciplina=self.disc_mat,
            topico=self.top_alg,
            tipo=Questao.Tipo.OBJETIVA,
            dificuldade=Questao.Dificuldade.FACIL,
            enunciado='Calcule o valor de 2 + 2.',
            ativo=True,
            autor=self.user1,
        )
        self.q1.tags.add('enem', 'basica')

        # Questão 2: Física, Difícil, DIS, Inativa, prof_souza, tag: 'quantica'
        self.q2 = Questao.objects.create(
            disciplina=self.disc_fis,
            tipo=Questao.Tipo.DISCURSIVA,
            dificuldade=Questao.Dificuldade.DIFICIL,
            enunciado='Explique o experimento da dupla fenda.',
            criterios_correcao='Abordar dualidade onda-partícula.',
            ativo=False,
            autor=self.user2,
        )
        self.q2.tags.add('quantica', 'moderna')

    def test_filter_by_disciplina_and_topico(self):
        """Verifica filtro por disciplina e tópico."""
        from .filters import QuestaoFilter

        # Filtra por Matemática
        f = QuestaoFilter({'disciplina': self.disc_mat.pk}, queryset=Questao.objects.all())
        self.assertEqual(f.qs.count(), 1)
        self.assertEqual(f.qs.first(), self.q1)

        # Filtra por Física
        f_fis = QuestaoFilter({'disciplina': self.disc_fis.pk}, queryset=Questao.objects.all())
        self.assertEqual(f_fis.qs.count(), 1)
        self.assertEqual(f_fis.qs.first(), self.q2)

    def test_filter_by_tipo_and_dificuldade(self):
        """Verifica filtro por tipo e dificuldade."""
        from .filters import QuestaoFilter

        f_obj = QuestaoFilter({'tipo': 'OBJ'}, queryset=Questao.objects.all())
        self.assertEqual(f_obj.qs.count(), 1)
        self.assertEqual(f_obj.qs.first(), self.q1)

        f_dif = QuestaoFilter({'dificuldade': 'D'}, queryset=Questao.objects.all())
        self.assertEqual(f_dif.qs.count(), 1)
        self.assertEqual(f_dif.qs.first(), self.q2)

    def test_filter_by_tags(self):
        """Verifica filtro por tags."""
        from .filters import QuestaoFilter

        f_tag = QuestaoFilter({'tags': 'enem'}, queryset=Questao.objects.all())
        self.assertEqual(f_tag.qs.count(), 1)
        self.assertEqual(f_tag.qs.first(), self.q1)

        f_tag2 = QuestaoFilter({'tags': 'quantica'}, queryset=Questao.objects.all())
        self.assertEqual(f_tag2.qs.count(), 1)
        self.assertEqual(f_tag2.qs.first(), self.q2)

    def test_filter_by_autor(self):
        """Verifica filtro por docente criador da questão."""
        from .filters import QuestaoFilter

        f_autor = QuestaoFilter({'autor': self.user1.pk}, queryset=Questao.objects.all())
        self.assertEqual(f_autor.qs.count(), 1)
        self.assertEqual(f_autor.qs.first(), self.q1)

    def test_filter_by_ativo(self):
        """Verifica filtro por status ativo/inativo."""
        from .filters import QuestaoFilter

        f_ativas = QuestaoFilter({'ativo': 'true'}, queryset=Questao.objects.all())
        self.assertEqual(f_ativas.qs.count(), 1)
        self.assertEqual(f_ativas.qs.first(), self.q1)

        f_inativas = QuestaoFilter({'ativo': 'false'}, queryset=Questao.objects.all())
        self.assertEqual(f_inativas.qs.count(), 1)
        self.assertEqual(f_inativas.qs.first(), self.q2)

    def test_filter_by_q_text_search(self):
        """Verifica busca textual ampla no enunciado e critérios."""
        from .filters import QuestaoFilter

        # Busca por palavra no enunciado
        f_busca1 = QuestaoFilter({'q': 'dupla fenda'}, queryset=Questao.objects.all())
        self.assertEqual(f_busca1.qs.count(), 1)
        self.assertEqual(f_busca1.qs.first(), self.q2)

        # Busca por palavra nos critérios de correção
        f_busca2 = QuestaoFilter({'q': 'onda-partícula'}, queryset=Questao.objects.all())
        self.assertEqual(f_busca2.qs.count(), 1)
        self.assertEqual(f_busca2.qs.first(), self.q2)


class QuestaoUpdateViewTest(TestCase):
    """Testes de integração da view de edição de questão (questao_update)."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Permission

        from .models import Alternativa, Disciplina

        user_model = get_user_model()
        self.docente = user_model.objects.create_user(username='docente_editor', password='secret_password')  # noqa: S106
        perm_change = Permission.objects.get(codename='change_questao')
        self.docente.user_permissions.add(perm_change)

        self.user_sem_perm = user_model.objects.create_user(username='sem_perm', password='secret_password')  # noqa: S106

        self.disc = Disciplina.objects.create(nome='Programação')
        self.questao = Questao.objects.create(
            disciplina=self.disc,
            tipo=Questao.Tipo.OBJETIVA,
            dificuldade=Questao.Dificuldade.FACIL,
            enunciado='Qual a extensão padrão de arquivos Python?',
            ativo=True,
            autor=self.docente,
        )
        self.alt_a = Alternativa.objects.create(questao=self.questao, letra='A', texto='.pt', correta=False)
        self.alt_b = Alternativa.objects.create(questao=self.questao, letra='B', texto='.py', correta=True)

    def test_questao_update_get_renders_form_and_data(self):
        """Verifica se a view de edição renderiza os dados atuais da questão."""
        from django.urls import reverse

        self.client.login(username='docente_editor', password='secret_password')  # noqa: S106
        url = reverse('banco_questoes:questao_update', kwargs={'pk': self.questao.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'Editar Questão Q{self.questao.pk}')
        self.assertContains(response, 'Qual a extensão padrão de arquivos Python?')
        self.assertContains(response, '.py')

    def test_questao_update_post_modifies_objective_question(self):
        """Verifica atualização de enunciado e alteração da alternativa correta."""
        from django.urls import reverse

        self.client.login(username='docente_editor', password='secret_password')  # noqa: S106
        url = reverse('banco_questoes:questao_update', kwargs={'pk': self.questao.pk})

        post_data = {
            'tipo': Questao.Tipo.OBJETIVA,
            'dificuldade': Questao.Dificuldade.MEDIO,
            'disciplina': self.disc.pk,
            'enunciado': 'Qual a extensão correta de arquivos Python 3?',
            'ativo': 'on',
            'tags': 'python, extensao, 2026',
            # Formset
            'alternativas-TOTAL_FORMS': '2',
            'alternativas-INITIAL_FORMS': '2',
            'alternativas-MIN_NUM_FORMS': '2',
            'alternativas-MAX_NUM_FORMS': '5',
            'alternativas-0-id': str(self.alt_a.pk),
            'alternativas-0-letra': 'A',
            'alternativas-0-texto': '.py',
            'alternativas-0-correta': 'on',  # Mudou A para correta
            'alternativas-1-id': str(self.alt_b.pk),
            'alternativas-1-letra': 'B',
            'alternativas-1-texto': '.java',
        }

        response = self.client.post(url, post_data)
        self.assertRedirects(response, reverse('banco_questoes:questao_list'))

        self.questao.refresh_from_db()
        self.assertEqual(self.questao.enunciado, 'Qual a extensão correta de arquivos Python 3?')
        self.assertEqual(self.questao.dificuldade, Questao.Dificuldade.MEDIO)
        self.assertEqual(self.questao.history.count(), 2)  # Criou histórico simple-history

        self.alt_a.refresh_from_db()
        self.alt_b.refresh_from_db()
        self.assertTrue(self.alt_a.correta)
        self.assertFalse(self.alt_b.correta)

    def test_questao_update_post_converts_objective_to_discursive(self):
        """Verifica conversão de objetiva para discursiva com deleção das alternativas."""
        from django.urls import reverse

        self.client.login(username='docente_editor', password='secret_password')  # noqa: S106
        url = reverse('banco_questoes:questao_update', kwargs={'pk': self.questao.pk})

        post_data = {
            'tipo': Questao.Tipo.DISCURSIVA,
            'dificuldade': Questao.Dificuldade.DIFICIL,
            'disciplina': self.disc.pk,
            'enunciado': 'Explique o paradigma de programação funcional em Python.',
            'criterios_correcao': 'Explicar funções puras, imutabilidade e lambda.',
            'ativo': 'on',
            'alternativas-TOTAL_FORMS': '2',
            'alternativas-INITIAL_FORMS': '2',
            'alternativas-MIN_NUM_FORMS': '2',
            'alternativas-MAX_NUM_FORMS': '5',
            'alternativas-0-id': str(self.alt_a.pk),
            'alternativas-0-letra': 'A',
            'alternativas-0-texto': '.pt',
            'alternativas-1-id': str(self.alt_b.pk),
            'alternativas-1-letra': 'B',
            'alternativas-1-texto': '.py',
        }

        response = self.client.post(url, post_data)
        self.assertRedirects(response, reverse('banco_questoes:questao_list'))

        self.questao.refresh_from_db()
        self.assertEqual(self.questao.tipo, Questao.Tipo.DISCURSIVA)
        self.assertEqual(self.questao.alternativas.count(), 0)  # Alternativas removidas

    def test_questao_update_cross_validation_blocks_objective_without_correct(self):
        """Verifica que regra cruzada (exatamente 1 correta) bloqueia atualização incorreta."""
        from django.urls import reverse

        self.client.login(username='docente_editor', password='secret_password')  # noqa: S106
        url = reverse('banco_questoes:questao_update', kwargs={'pk': self.questao.pk})

        post_data = {
            'tipo': Questao.Tipo.OBJETIVA,
            'dificuldade': Questao.Dificuldade.FACIL,
            'disciplina': self.disc.pk,
            'enunciado': 'Qual a extensão padrão de arquivos Python?',
            'ativo': 'on',
            'alternativas-TOTAL_FORMS': '2',
            'alternativas-INITIAL_FORMS': '2',
            'alternativas-MIN_NUM_FORMS': '2',
            'alternativas-MAX_NUM_FORMS': '5',
            'alternativas-0-id': str(self.alt_a.pk),
            'alternativas-0-letra': 'A',
            'alternativas-0-texto': '.pt',
            'alternativas-1-id': str(self.alt_b.pk),
            'alternativas-1-letra': 'B',
            'alternativas-1-texto': '.py',
            # Nenhuma marcada como correta
        }

        response = self.client.post(url, post_data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'devem ter exatamente 1 alternativa correta')

    def test_questao_update_permission_denied_for_unauthorized_user(self):
        """Verifica que usuário sem permissão change_questao recebe 403 Forbidden."""
        from django.urls import reverse

        self.client.login(username='sem_perm', password='secret_password')  # noqa: S106
        url = reverse('banco_questoes:questao_update', kwargs={'pk': self.questao.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 403)


class QuestaoCreateViewTest(TestCase):
    """Testes de integração da view de cadastro de questão (questao_create)."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Permission

        from .models import Disciplina

        user_model = get_user_model()
        self.docente = user_model.objects.create_user(username='docente_criador', password='secret_password')  # noqa: S106
        perm_add = Permission.objects.get(codename='add_questao')
        self.docente.user_permissions.add(perm_add)

        self.disc = Disciplina.objects.create(nome='Engenharia de Software')
        self.client.login(username='docente_criador', password='secret_password')  # noqa: S106

    def test_questao_create_discursive_without_alternatives_success(self):
        """Verifica que questão discursiva é cadastrada com sucesso sem exigir formset de alternativas."""
        from django.urls import reverse

        url = reverse('banco_questoes:questao_create')
        post_data = {
            'tipo': Questao.Tipo.DISCURSIVA,
            'dificuldade': Questao.Dificuldade.MEDIO,
            'disciplina': self.disc.pk,
            'enunciado': 'Descreva as vantagens do padrão arquitetural MVC.',
            'criterios_correcao': 'Explicar separação de responsabilidades (Model, View, Controller).',
            'tags': 'mvc, arquitetura',
            'ativo': 'on',
            # Formset management form básico sem formulários preenchidos
            'alternativas-TOTAL_FORMS': '2',
            'alternativas-INITIAL_FORMS': '0',
            'alternativas-MIN_NUM_FORMS': '0',
            'alternativas-MAX_NUM_FORMS': '5',
            'alternativas-0-letra': '',
            'alternativas-0-texto': '',
            'alternativas-1-letra': '',
            'alternativas-1-texto': '',
        }

        response = self.client.post(url, post_data)
        self.assertRedirects(response, reverse('banco_questoes:questao_list'))

        # Confirma persistência
        q = Questao.objects.get(enunciado='Descreva as vantagens do padrão arquitetural MVC.')
        self.assertEqual(q.tipo, Questao.Tipo.DISCURSIVA)
        self.assertEqual(q.dificuldade, Questao.Dificuldade.MEDIO)
        self.assertEqual(q.autor, self.docente)
        self.assertEqual(q.alternativas.count(), 0)

    def test_questao_create_objective_success(self):
        """Verifica cadastro com sucesso de questão objetiva com alternativas."""
        from django.urls import reverse

        url = reverse('banco_questoes:questao_create')
        post_data = {
            'tipo': Questao.Tipo.OBJETIVA,
            'dificuldade': Questao.Dificuldade.FACIL,
            'disciplina': self.disc.pk,
            'enunciado': 'O que significa MVC?',
            'tags': 'mvc, sigla',
            'ativo': 'on',
            'alternativas-TOTAL_FORMS': '2',
            'alternativas-INITIAL_FORMS': '0',
            'alternativas-MIN_NUM_FORMS': '0',
            'alternativas-MAX_NUM_FORMS': '5',
            'alternativas-0-letra': 'A',
            'alternativas-0-texto': 'Model-View-Controller',
            'alternativas-0-correta': 'on',
            'alternativas-1-letra': 'B',
            'alternativas-1-texto': 'Main-Virtual-Core',
        }

        response = self.client.post(url, post_data)
        self.assertRedirects(response, reverse('banco_questoes:questao_list'))

        q = Questao.objects.get(enunciado='O que significa MVC?')
        self.assertEqual(q.tipo, Questao.Tipo.OBJETIVA)
        self.assertEqual(q.alternativas.count(), 2)
        self.assertEqual(q.alternativas.get(correta=True).letra, 'A')


class CSVExportViewTest(TestCase):
    """Testes de integração para o download e exportação no formato de 10 colunas."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        from .models import Alternativa, Disciplina, Topico

        user_model = get_user_model()
        self.user = user_model.objects.create_user(username='docente_export', password='secret_password')  # noqa: S106
        self.client.login(username='docente_export', password='secret_password')  # noqa: S106

        self.disc_esd, _ = Disciplina.objects.get_or_create(nome='Estrutura de Dados (ESD)')
        self.disc_caw, _ = Disciplina.objects.get_or_create(nome='Construção de Aplicações WEB (CAW)')

        self.topico_arv = Topico.objects.create(disciplina=self.disc_esd, nome='Árvores Binárias')

        # Questão 1: Objetiva com origem
        self.q_obj = Questao.objects.create(
            disciplina=self.disc_esd,
            topico=self.topico_arv,
            tipo=Questao.Tipo.OBJETIVA,
            dificuldade=Questao.Dificuldade.MEDIO,
            origem='ENADE ADS 2021',
            numero_questao='26',
            enunciado='Qual a complexidade de inserção numa árvore AVL balanceada?',
            ativo=True,
            autor=self.user,
        )
        self.q_obj.tags.add('avl', 'arvores')
        Alternativa.objects.create(questao=self.q_obj, letra='A', texto='O(log n)', correta=True)
        Alternativa.objects.create(questao=self.q_obj, letra='B', texto='O(n)', correta=False)

        # Questão 2: Discursiva com comentários
        self.q_dis = Questao.objects.create(
            disciplina=self.disc_caw,
            tipo=Questao.Tipo.DISCURSIVA,
            dificuldade=Questao.Dificuldade.FACIL,
            origem='POSCOMP 2025',
            numero_questao='15',
            enunciado='Explique o ciclo de vida de uma requisição HTTP.',
            criterios_correcao='DNS, handshake TCP, requisição HTTP, resposta do servidor.',
            ativo=True,
            autor=self.user,
        )
        self.q_dis.tags.add('http', 'web')

    def test_export_csv_10col_contains_real_database_records(self):
        """Verifica se o CSV de 10 colunas exporta os registros reais incluindo origem e numero_questao."""
        from django.urls import reverse

        url = reverse('banco_questoes:download_modelo_csv')
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv; charset=utf-8')
        self.assertIn('attachment; filename="acervo_questoes_faeterj.csv"', response['Content-Disposition'])

        content = response.content.decode('utf-8')
        self.assertTrue(content.startswith('\ufeff'))

        # Verifica cabeçalho de 10 colunas
        self.assertIn('origem,questao,enunciado,opcao_a,opcao_b,opcao_c,opcao_d,opcao_e,gabarito,comentarios', content)

        # Verifica dados reais
        self.assertIn('ENADE ADS 2021', content)
        self.assertIn('26', content)
        self.assertIn('Qual a complexidade de inserção numa árvore AVL balanceada?', content)
        self.assertIn('O(log n)', content)
        self.assertIn('POSCOMP 2025', content)
        self.assertIn('Explique o ciclo de vida de uma requisição HTTP.', content)
        self.assertIn('DNS, handshake TCP, requisição HTTP, resposta do servidor.', content)

        # Verifica que campos internos NÃO estão no CSV
        self.assertNotIn('Estrutura de Dados (ESD)', content)
        self.assertNotIn('Árvores Binárias', content)

    def test_export_csv_respects_active_filters(self):
        """Verifica se o CSV exporta apenas os registros que atendem aos filtros ativos da query string."""
        from django.urls import reverse

        url = reverse('banco_questoes:download_modelo_csv')
        response = self.client.get(url, {'disciplina': self.disc_esd.pk})

        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')

        self.assertIn('Qual a complexidade de inserção numa árvore AVL balanceada?', content)
        self.assertNotIn('Explique o ciclo de vida de uma requisição HTTP.', content)


class ExcelExportViewTest(TestCase):
    """Testes de integração para a exportação de dados em planilhas Excel (.xlsx)."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        from .models import Alternativa, Disciplina, Topico

        user_model = get_user_model()
        self.user = user_model.objects.create_user(username='docente_excel', password='secret_password')  # noqa: S106
        self.client.login(username='docente_excel', password='secret_password')  # noqa: S106

        self.disc_esd, _ = Disciplina.objects.get_or_create(nome='Estrutura de Dados (ESD)')
        self.disc_caw, _ = Disciplina.objects.get_or_create(nome='Construção de Aplicações WEB (CAW)')

        self.topico_arv = Topico.objects.create(disciplina=self.disc_esd, nome='Árvores Binárias')

        # Questão 1: Objetiva com origem
        self.q_obj = Questao.objects.create(
            disciplina=self.disc_esd,
            topico=self.topico_arv,
            tipo=Questao.Tipo.OBJETIVA,
            dificuldade=Questao.Dificuldade.MEDIO,
            origem='ENADE ADS 2021',
            numero_questao='26',
            enunciado='Qual a complexidade de inserção numa árvore AVL balanceada?',
            ativo=True,
            autor=self.user,
        )
        self.q_obj.tags.add('avl', 'arvores')
        Alternativa.objects.create(questao=self.q_obj, letra='A', texto='O(log n)', correta=True)
        Alternativa.objects.create(questao=self.q_obj, letra='B', texto='O(n)', correta=False)

        # Questão 2: Discursiva com comentários
        self.q_dis = Questao.objects.create(
            disciplina=self.disc_caw,
            tipo=Questao.Tipo.DISCURSIVA,
            dificuldade=Questao.Dificuldade.FACIL,
            origem='POSCOMP 2025',
            numero_questao='15',
            enunciado='Explique o ciclo de vida de uma requisição HTTP.',
            criterios_correcao='DNS, handshake TCP, requisição HTTP, resposta do servidor.',
            ativo=True,
            autor=self.user,
        )
        self.q_dis.tags.add('http', 'web')

    def test_export_excel_status_and_content_type(self):
        """Verifica se o endpoint retorna status 200, Content-Type correto e nome do anexo .xlsx."""
        from django.urls import reverse

        url = reverse('banco_questoes:download_modelo_xlsx')
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        self.assertIn('attachment; filename="acervo_questoes_faeterj.xlsx"', response['Content-Disposition'])

    def test_export_excel_contains_real_database_records(self):
        """Verifica se o workbook .xlsx exportado contém as 10 colunas e dados reais do banco."""
        import openpyxl
        from django.urls import reverse

        url = reverse('banco_questoes:download_modelo_xlsx')
        response = self.client.get(url)

        wb = openpyxl.load_workbook(io.BytesIO(response.content))
        self.assertIn('Acervo de Questões', wb.sheetnames)
        ws = wb['Acervo de Questões']

        # Verifica cabeçalhos na linha 1
        headers = [cell.value for cell in ws[1]]
        expected_headers = [
            'origem',
            'questao',
            'enunciado',
            'opcao_a',
            'opcao_b',
            'opcao_c',
            'opcao_d',
            'opcao_e',
            'gabarito',
            'comentarios',
        ]
        self.assertEqual(headers, expected_headers)

        # Deve conter 3 linhas: cabeçalho + 2 questões
        self.assertEqual(ws.max_row, 3)

        # Linha 2: Questão HTTP (mais recente por -criado_em)
        row2 = [cell.value for cell in ws[2]]
        self.assertEqual(row2[0], 'POSCOMP 2025')
        self.assertEqual(row2[1], '15')
        self.assertEqual(row2[2], 'Explique o ciclo de vida de uma requisição HTTP.')
        self.assertEqual(row2[9], 'DNS, handshake TCP, requisição HTTP, resposta do servidor.')

        # Linha 3: Questão AVL
        row3 = [cell.value for cell in ws[3]]
        self.assertEqual(row3[0], 'ENADE ADS 2021')
        self.assertEqual(row3[1], '26')
        self.assertEqual(row3[2], 'Qual a complexidade de inserção numa árvore AVL balanceada?')
        self.assertEqual(row3[3], 'O(log n)')
        self.assertEqual(row3[8], 'A')

    def test_export_excel_respects_active_filters(self):
        """Verifica se a exportação em Excel exporta apenas registros correspondentes ao filtro GET."""
        import openpyxl
        from django.urls import reverse

        url = reverse('banco_questoes:download_modelo_xlsx')
        response = self.client.get(url, {'disciplina': self.disc_esd.pk})

        self.assertEqual(response.status_code, 200)
        wb = openpyxl.load_workbook(io.BytesIO(response.content))
        ws = wb['Acervo de Questões']

        # Deve conter apenas 2 linhas: cabeçalho + 1 questão filtrada
        self.assertEqual(ws.max_row, 2)
        row2 = [cell.value for cell in ws[2]]
        self.assertEqual(row2[0], 'ENADE ADS 2021')
        self.assertEqual(row2[2], 'Qual a complexidade de inserção numa árvore AVL balanceada?')

    def test_export_excel_empty_database_generates_sample_records(self):
        """Verifica se o acervo vazio gera planilha modelo com dados de exemplo ENADE/POSCOMP."""
        import openpyxl
        from django.urls import reverse

        # Remove todas as questões
        Questao.objects.all().delete()

        url = reverse('banco_questoes:download_modelo_xlsx')
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        wb = openpyxl.load_workbook(io.BytesIO(response.content))
        ws = wb['Acervo de Questões']

        self.assertEqual(ws.max_row, 3)
        row2 = [cell.value for cell in ws[2]]
        self.assertEqual(row2[0], 'ENADE ADS 2021')
        self.assertEqual(row2[1], '26')
        self.assertIn('DNS', row2[2])


class AuthenticationViewsTest(TestCase):
    """Testes de integração para as rotas e templates de login e logout."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username='docente_login_test',
            password='my_secret_password',  # noqa: S106
        )

    def test_login_page_renders_get(self):
        """Verifica se a tela de login renderiza com sucesso e com identidade visual."""
        from django.urls import reverse

        url = reverse('login')
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'registration/login.html')
        self.assertContains(response, 'Examina')
        self.assertContains(response, 'FAETERJ-Rio')
        self.assertContains(response, 'Entrar no Examina')
        self.assertContains(response, 'id_username')
        self.assertContains(response, 'id_password')

    def test_unauthenticated_access_redirects_to_login(self):
        """Garante que funções internas e rota raiz exigem autenticação prévia."""
        from django.urls import reverse

        # Teste na rota raiz '/'
        resp_root = self.client.get('/')
        self.assertEqual(resp_root.status_code, 302)
        self.assertIn('/login/', resp_root.url)

        # Teste em '/questoes/'
        resp_list = self.client.get(reverse('banco_questoes:questao_list'))
        self.assertEqual(resp_list.status_code, 302)
        self.assertIn('/login/', resp_list.url)

        # Teste em '/questoes/nova/'
        resp_create = self.client.get(reverse('banco_questoes:questao_create'))
        self.assertEqual(resp_create.status_code, 302)
        self.assertIn('/login/', resp_create.url)

        # Teste em '/questoes/importar/'
        resp_import = self.client.get(reverse('banco_questoes:questao_import'))
        self.assertEqual(resp_import.status_code, 302)
        self.assertIn('/login/', resp_import.url)

    def test_login_post_success_redirects_to_questoes(self):
        """Verifica autenticação com credenciais corretas e redirecionamento."""
        from django.urls import reverse

        url = reverse('login')
        post_data = {
            'username': 'docente_login_test',
            'password': 'my_secret_password',  # noqa: S106
        }

        response = self.client.post(url, post_data)
        self.assertRedirects(response, reverse('banco_questoes:questao_list'))

        # Confirma que a sessão foi autenticada
        self.assertEqual(int(self.client.session['_auth_user_id']), self.user.pk)

    def test_login_post_invalid_credentials_shows_error(self):
        """Verifica falha de autenticação com senha incorreta e exibição de alerta."""
        from django.urls import reverse

        url = reverse('login')
        post_data = {
            'username': 'docente_login_test',
            'password': 'wrong_password',  # noqa: S106
        }

        response = self.client.post(url, post_data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Usuário ou senha incorretos')

    def test_logout_post_redirects_to_login(self):
        """Verifica logout via POST e redirecionamento para tela de login."""
        from django.urls import reverse

        self.client.login(username='docente_login_test', password='my_secret_password')  # noqa: S106
        url = reverse('logout')

        response = self.client.post(url)
        self.assertRedirects(response, reverse('login'))


class QuestaoMediaSupportTest(TestCase):
    """Testes para o suporte a texto auxiliar e imagem de apoio nas questões."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Permission

        from .models import Disciplina

        user_model = get_user_model()
        self.docente = user_model.objects.create_user(username='docente_media', password='secret_password')  # noqa: S106
        for perm_name in ('add_questao', 'change_questao'):
            perm = Permission.objects.get(codename=perm_name)
            self.docente.user_permissions.add(perm)

        self.client.login(username='docente_media', password='secret_password')  # noqa: S106
        self.disc, _ = Disciplina.objects.get_or_create(nome='Organização de Computadores (ORG)')

    def test_create_questao_with_texto_auxiliar_and_image(self):
        """Verifica cadastro com sucesso de questão contendo texto auxiliar e imagem anexada."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.urls import reverse

        tiny_gif = (
            b'GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00'
            b'!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;'
        )
        imagem_upload = SimpleUploadedFile('circuito.gif', tiny_gif, content_type='image/gif')

        url = reverse('banco_questoes:questao_create')
        post_data = {
            'tipo': Questao.Tipo.OBJETIVA,
            'dificuldade': Questao.Dificuldade.MEDIO,
            'disciplina': self.disc.pk,
            'texto_auxiliar': 'Considere a arquitetura Von Neumann apresentada no diagrama a seguir.',
            'imagem': imagem_upload,
            'enunciado': 'Qual componente é responsável pelo controle de ciclo de instrução?',
            'ativo': 'on',
            'alternativas-TOTAL_FORMS': '2',
            'alternativas-INITIAL_FORMS': '0',
            'alternativas-MIN_NUM_FORMS': '0',
            'alternativas-MAX_NUM_FORMS': '5',
            'alternativas-0-letra': 'A',
            'alternativas-0-texto': 'Unidade de Controle (UC)',
            'alternativas-0-correta': 'on',
            'alternativas-1-letra': 'B',
            'alternativas-1-texto': 'Unidade Lógica e Aritmética (ULA)',
        }

        response = self.client.post(url, post_data)
        self.assertRedirects(response, reverse('banco_questoes:questao_list'))

        q = Questao.objects.get(enunciado='Qual componente é responsável pelo controle de ciclo de instrução?')
        self.assertEqual(q.texto_auxiliar, 'Considere a arquitetura Von Neumann apresentada no diagrama a seguir.')
        self.assertTrue(bool(q.imagem))
        self.assertIn('circuito', q.imagem.name)

        # Limpeza do arquivo criado no teste
        if q.imagem:
            q.imagem.delete(save=False)

    def test_update_questao_modify_texto_auxiliar_and_clear_image(self):
        """Verifica atualização de questão modificando texto auxiliar e removendo imagem."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.urls import reverse

        from .models import Alternativa

        tiny_gif = (
            b'GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00'
            b'!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;'
        )
        imagem_upload = SimpleUploadedFile('diagrama_inicial.gif', tiny_gif, content_type='image/gif')

        questao = Questao.objects.create(
            disciplina=self.disc,
            tipo=Questao.Tipo.OBJETIVA,
            dificuldade=Questao.Dificuldade.FACIL,
            texto_auxiliar='Texto original',
            imagem=imagem_upload,
            enunciado='Pergunta inicial sobre registradores.',
            ativo=True,
            autor=self.docente,
        )
        alt1 = Alternativa.objects.create(questao=questao, letra='A', texto='PC', correta=True)
        alt2 = Alternativa.objects.create(questao=questao, letra='B', texto='MAR', correta=False)

        url = reverse('banco_questoes:questao_update', kwargs={'pk': questao.pk})
        post_data = {
            'tipo': Questao.Tipo.OBJETIVA,
            'dificuldade': Questao.Dificuldade.FACIL,
            'disciplina': self.disc.pk,
            'texto_auxiliar': 'Texto auxiliar revisado e enriquecido.',
            'imagem-clear': 'on',  # Solicita remoção da imagem
            'enunciado': 'Pergunta inicial sobre registradores.',
            'ativo': 'on',
            'alternativas-TOTAL_FORMS': '2',
            'alternativas-INITIAL_FORMS': '2',
            'alternativas-MIN_NUM_FORMS': '0',
            'alternativas-MAX_NUM_FORMS': '5',
            'alternativas-0-id': str(alt1.pk),
            'alternativas-0-letra': 'A',
            'alternativas-0-texto': 'PC',
            'alternativas-0-correta': 'on',
            'alternativas-1-id': str(alt2.pk),
            'alternativas-1-letra': 'B',
            'alternativas-1-texto': 'MAR',
        }

        response = self.client.post(url, post_data)
        self.assertRedirects(response, reverse('banco_questoes:questao_list'))

        questao.refresh_from_db()
        self.assertEqual(questao.texto_auxiliar, 'Texto auxiliar revisado e enriquecido.')
        self.assertFalse(bool(questao.imagem))  # Imagem removida com sucesso


class QuestaoToggleAtivoViewTests(TestCase):
    """Testes de integração da view questao_toggle_ativo (soft delete)."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Permission

        from .models import Disciplina

        user_model = get_user_model()

        # Usuário com permissão de edição
        self.editor = user_model.objects.create_user(username='editor_toggle', password='secret_password')  # noqa: S106
        perm_change = Permission.objects.get(codename='change_questao')
        self.editor.user_permissions.add(perm_change)

        # Usuário sem permissão
        self.sem_perm = user_model.objects.create_user(username='leitor_toggle', password='secret_password')  # noqa: S106

        disc = Disciplina.objects.create(nome='Lógica de Programação')

        self.questao_ativa = Questao.objects.create(
            disciplina=disc,
            tipo=Questao.Tipo.OBJETIVA,
            dificuldade=Questao.Dificuldade.FACIL,
            enunciado='Questão ativa para soft delete.',
            ativo=True,
            autor=self.editor,
        )
        self.questao_inativa = Questao.objects.create(
            disciplina=disc,
            tipo=Questao.Tipo.DISCURSIVA,
            dificuldade=Questao.Dificuldade.MEDIO,
            enunciado='Questão inativa para reativação.',
            ativo=False,
            autor=self.editor,
        )

    def _url(self, pk):
        from django.urls import reverse

        return reverse('banco_questoes:questao_toggle_ativo', kwargs={'pk': pk})

    def test_sem_permissao_retorna_403(self):
        """Usuário sem change_questao não pode inativar/reativar — deve receber 403."""
        self.client.login(username='leitor_toggle', password='secret_password')  # noqa: S106
        response = self.client.post(self._url(self.questao_ativa.pk))
        self.assertEqual(response.status_code, 403)

    def test_inativar_questao_ativa(self):
        """POST em questão ativa inverte ativo para False e redireciona para a lista."""
        from django.urls import reverse

        self.client.login(username='editor_toggle', password='secret_password')  # noqa: S106
        response = self.client.post(self._url(self.questao_ativa.pk))

        self.assertRedirects(response, reverse('banco_questoes:questao_list'))
        self.questao_ativa.refresh_from_db()
        self.assertFalse(self.questao_ativa.ativo)

    def test_reativar_questao_inativa(self):
        """POST em questão inativa inverte ativo para True e redireciona para a lista."""
        from django.urls import reverse

        self.client.login(username='editor_toggle', password='secret_password')  # noqa: S106
        response = self.client.post(self._url(self.questao_inativa.pk))

        self.assertRedirects(response, reverse('banco_questoes:questao_list'))
        self.questao_inativa.refresh_from_db()
        self.assertTrue(self.questao_inativa.ativo)

    def test_get_retorna_405(self):
        """GET na URL de toggle deve retornar 405 Method Not Allowed."""
        self.client.login(username='editor_toggle', password='secret_password')  # noqa: S106
        response = self.client.get(self._url(self.questao_ativa.pk))
        self.assertEqual(response.status_code, 405)

    def test_historico_registra_mudanca_de_ativo(self):
        """A mudança de ativo deve gerar um novo registro no histórico da questão."""
        self.client.login(username='editor_toggle', password='secret_password')  # noqa: S106

        historico_antes = self.questao_ativa.history.count()
        self.client.post(self._url(self.questao_ativa.pk))

        self.questao_ativa.refresh_from_db()
        self.assertEqual(self.questao_ativa.history.count(), historico_antes + 1)

        ultima_revisao = self.questao_ativa.history.first()
        self.assertFalse(ultima_revisao.ativo)
        self.assertEqual(ultima_revisao.history_user, self.editor)
