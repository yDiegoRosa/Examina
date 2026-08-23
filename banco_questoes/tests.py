import io
from pathlib import Path

from django.test import TestCase

from .models import Questao
from .parser import CSVParseError, CSVQuestaoParser
from .validator import CSVQuestaoValidator


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


class CSVQuestaoValidatorTest(TestCase):
    """Testes unitários para as regras de validação linha a linha de CSV."""

    def test_validate_valid_objective_row(self):
        """Verifica validação de uma linha objetiva completa e válida."""
        row = {
            'linha': 2,
            'enunciado': 'Qual é a fórmula da água?',
            'tipo': 'OBJ',
            'disciplina': 'Química',
            'topico': 'Inorgânica',
            'dificuldade': 'F',
            'tags': ['quimica', 'agua'],
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
        self.assertEqual(res.cleaned_data['dificuldade'], Questao.Dificuldade.FACIL)
        self.assertEqual(len(res.cleaned_data['alternativas']), 2)

    def test_validate_valid_discursive_row(self):
        """Verifica validação de uma linha discursiva completa e válida."""
        row = {
            'linha': 3,
            'enunciado': 'Explique o princípio da inércia de Newton.',
            'tipo': 'DIS',
            'disciplina': 'Física',
            'topico': 'Mecânica',
            'dificuldade': 'M',
            'tags': ['fisica', 'leis-de-newton'],
            'alternativas': [],
            'correta': None,
            'criterios_correcao': 'Mencionar a tendência de manter repouso ou MRU caso força resultante seja nula.',
        }
        res = CSVQuestaoValidator.validate_row(row)
        self.assertTrue(res.is_valid)
        self.assertEqual(res.cleaned_data['tipo'], Questao.Tipo.DISCURSIVA)
        self.assertEqual(res.cleaned_data['dificuldade'], Questao.Dificuldade.MEDIO)
        self.assertTrue(bool(res.cleaned_data['criterios_correcao']))

    def test_validate_missing_mandatory_fields(self):
        """Verifica erro quando enunciado, tipo, disciplina e dificuldade faltam."""
        row = {
            'linha': 4,
            'enunciado': '',
            'tipo': '',
            'disciplina': '',
            'dificuldade': '',
        }
        res = CSVQuestaoValidator.validate_row(row)
        self.assertFalse(res.is_valid)
        self.assertTrue(any('enunciado' in err for err in res.errors))
        self.assertTrue(any('disciplina' in err for err in res.errors))
        self.assertTrue(any('tipo' in err for err in res.errors))
        self.assertTrue(any('dificuldade' in err for err in res.errors))

    def test_validate_invalid_tipo_and_dificuldade(self):
        """Verifica rejeição de tipos e dificuldades inválidos."""
        row = {
            'linha': 5,
            'enunciado': 'Enunciado de teste',
            'tipo': 'INVALIDO',
            'disciplina': 'Matemática',
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
            'disciplina': 'História',
            'dificuldade': 'F',
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
            'disciplina': 'História',
            'dificuldade': 'F',
            'alternativas': [
                {'letra': 'A', 'texto': 'Alt A', 'correta': False},
                {'letra': 'B', 'texto': 'Alt B', 'correta': False},
            ],
            'correta': 'E',  # E não está preenchida
        }
        res = CSVQuestaoValidator.validate_row(row)
        self.assertFalse(res.is_valid)
        self.assertTrue(any('não corresponde a nenhuma alternativa preenchida' in err for err in res.errors))

    def test_validate_discursive_missing_criterios(self):
        """Verifica erro quando questão discursiva não tem critérios de correção."""
        row = {
            'linha': 8,
            'enunciado': 'Discursiva sem critérios',
            'tipo': 'DIS',
            'disciplina': 'Filosofia',
            'dificuldade': 'D',
            'criterios_correcao': '   ',  # Vazio
        }
        res = CSVQuestaoValidator.validate_row(row)
        self.assertFalse(res.is_valid)
        self.assertTrue(any('Critérios de correção são obrigatórios' in err for err in res.errors))

    def test_validate_batch_rows_report(self):
        """Verifica a geração do relatório consolidado com validate_rows."""
        rows = [
            # 1 Válida
            {
                'linha': 2,
                'enunciado': 'Questão Válida',
                'tipo': 'OBJ',
                'disciplina': 'Biologia',
                'dificuldade': 'F',
                'alternativas': [
                    {'letra': 'A', 'texto': 'A', 'correta': True},
                    {'letra': 'B', 'texto': 'B', 'correta': False},
                ],
                'correta': 'A',
            },
            # 1 Inválida
            {
                'linha': 3,
                'enunciado': '',
                'tipo': 'OBJ',
                'disciplina': 'Biologia',
                'dificuldade': 'F',
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
                'disciplina': 'Física',
                'dificuldade': 'M',
                'alternativas': [
                    {'letra': 'A', 'texto': '300.000 km/s', 'correta': True},
                    {'letra': 'B', 'texto': '150.000 km/s', 'correta': False},
                ],
                'correta': 'A',
            },
            {
                'linha': 3,
                # Enunciado idêntico com espaços extras e maiúsculas variadas
                'enunciado': '   QUAL É A   VELOCIDADE DA LUZ?   ',
                'tipo': 'OBJ',
                'disciplina': 'Física',
                'dificuldade': 'M',
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
                # Enunciado idêntico ao já existente no banco (com espaços e case alterados)
                'enunciado': '  qual é a constante gravitacional na terra?  ',
                'tipo': 'OBJ',
                'disciplina': 'Física Geral',
                'dificuldade': 'M',
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
    """Testes de integração da view de importação e relatório pós-upload."""

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
        self.assertContains(response, 'Importação de Questões via CSV')
        self.assertContains(response, 'Baixar Modelo CSV')

    def test_import_csv_success_persists_and_renders_report(self):
        """Verifica persistência de questões e exibição de relatório pós-upload."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.urls import reverse

        csv_content = (
            'enunciado,tipo,disciplina,topico,dificuldade,tags,alt_a,alt_b,alt_c,alt_d,alt_e,correta,criterios_correcao\n'
            'Qual é a capital da França?,OBJ,Geografia,Europa,F,geografia;europa,Londres,Paris,Berlim,Madrid,,B,\n'
            'Discorra sobre a Revolução Industrial.,DIS,História,Idade Contemporânea,M,historia;revolucao,,,,,,,Destacar a transição da manufatura para maquinofatura.\n'
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
        self.assertEqual(Questao.objects.filter(enunciado__icontains='França').count(), 1)
        self.assertEqual(Questao.objects.filter(enunciado__icontains='Revolução Industrial').count(), 1)

    def test_import_csv_with_inconsistencies_shows_rejection_reasons(self):
        """Verifica se linhas inválidas geram relatório com motivos claros de rejeição."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.urls import reverse

        csv_content = (
            'enunciado,tipo,disciplina,topico,dificuldade,tags,alt_a,alt_b,alt_c,alt_d,alt_e,correta,criterios_correcao\n'
            'Questão Sem Disciplina,OBJ,,Geral,F,tag1,Alt A,Alt B,,,,A,\n'
            'Questão Objetiva Sem Gabarito,OBJ,Matemática,Álgebra,F,tag2,Alt A,Alt B,,,,,\n'
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
        self.assertContains(response, 'O campo &quot;disciplina&quot; é obrigatório')
        self.assertContains(response, 'O campo &quot;correta&quot; é obrigatório')


class CSVImportServiceTest(TestCase):
    """Testes unitários dedicados ao serviço CSVImportService."""

    def test_import_service_commits_valid_questions_with_relations(self):
        """Verifica se CSVImportService comita disciplinas, tópicos, tags e alternativas."""
        from .importer import CSVImportService

        csv_text = (
            'enunciado,tipo,disciplina,topico,dificuldade,tags,alt_a,alt_b,alt_c,alt_d,alt_e,correta,criterios_correcao\n'
            'Qual a fórmula da fotossíntese?,DIS,Biologia,Fisiologia,M,"bio,celular",,,,,,,Explicar fase clara e escura.\n'
            'O que é 5 x 5?,OBJ,Matemática,Aritmética,F,"matematica,tabuada",10,20,25,30,,C,\n'
        )

        res = CSVImportService.import_csv(csv_text)

        self.assertEqual(res.total_processadas, 2)
        self.assertEqual(res.total_sucesso, 2)
        self.assertEqual(res.total_rejeitadas, 0)
        self.assertFalse(res.has_errors)

        # Verifica criação no banco
        q_obj = Questao.objects.get(enunciado='O que é 5 x 5?')
        self.assertEqual(q_obj.disciplina.nome, 'Matemática')
        self.assertEqual(q_obj.topico.nome, 'Aritmética')
        self.assertEqual(q_obj.alternativas.count(), 4)
        self.assertEqual(q_obj.alternativas.get(correta=True).letra, 'C')
        self.assertEqual(q_obj.alternativas.get(correta=True).texto, '25')
        self.assertEqual(set(q_obj.tags.names()), {'matematica', 'tabuada'})

    def test_import_service_partial_commits_only_valid_rows(self):
        """Verifica que linhas com erro são rejeitadas sem bloquear as linhas válidas."""
        from .importer import CSVImportService

        csv_text = (
            'enunciado,tipo,disciplina,topico,dificuldade,tags,alt_a,alt_b,alt_c,alt_d,alt_e,correta,criterios_correcao\n'
            'Questão Sem Enunciado,,Matemática,,F,,,,,,,,,\n'  # Inválida
            'Capital de SP?,OBJ,Geografia,Sudeste,F,sp,Santos,São Paulo,Campinas,,,B,\n'  # Válida
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
            'enunciado,tipo,disciplina,topico,dificuldade,tags,alt_a,alt_b,alt_c,alt_d,alt_e,correta,criterios_correcao\n'
            'Questão Unica 2026,OBJ,Sociologia,,F,socio,Opcao 1,Opcao 2,,,,A,\n'
        )

        # 1ª Importação: Sucesso
        res1 = CSVImportService.import_csv(csv_text)
        self.assertEqual(res1.total_sucesso, 1)

        # 2ª Importação: Bloqueada por duplicidade contra o banco
        res2 = CSVImportService.import_csv(csv_text)
        self.assertEqual(res2.total_sucesso, 0)
        self.assertEqual(res2.total_rejeitadas, 1)
        self.assertTrue(any('Já existe uma questão cadastrada no banco' in err for err in res2.linhas[0].erros))
