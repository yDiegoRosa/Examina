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
    """Testes de integração para o download e exportação de dados reais em CSV."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        from .models import Alternativa, Disciplina, Topico

        user_model = get_user_model()
        self.user = user_model.objects.create_user(username='docente_export', password='secret_password')  # noqa: S106
        self.client.login(username='docente_export', password='secret_password')  # noqa: S106

        self.disc_esd, _ = Disciplina.objects.get_or_create(nome='Estrutura de Dados (ESD)')
        self.disc_caw, _ = Disciplina.objects.get_or_create(nome='Construção de Aplicações WEB (CAW)')

        self.topico_arv = Topico.objects.create(disciplina=self.disc_esd, nome='Árvores Binárias')

        # Questão 1: Objetiva real
        self.q_obj = Questao.objects.create(
            disciplina=self.disc_esd,
            topico=self.topico_arv,
            tipo=Questao.Tipo.OBJETIVA,
            dificuldade=Questao.Dificuldade.MEDIO,
            enunciado='Qual a complexidade de inserção numa árvore AVL balanceada?',
            ativo=True,
            autor=self.user,
        )
        self.q_obj.tags.add('avl', 'arvores')
        Alternativa.objects.create(questao=self.q_obj, letra='A', texto='O(log n)', correta=True)
        Alternativa.objects.create(questao=self.q_obj, letra='B', texto='O(n)', correta=False)

        # Questão 2: Discursiva real
        self.q_dis = Questao.objects.create(
            disciplina=self.disc_caw,
            tipo=Questao.Tipo.DISCURSIVA,
            dificuldade=Questao.Dificuldade.FACIL,
            enunciado='Explique o ciclo de vida de uma requisição HTTP.',
            criterios_correcao='DNS, handshake TCP, requisição HTTP, resposta do servidor.',
            ativo=True,
            autor=self.user,
        )
        self.q_dis.tags.add('http', 'web')

    def test_export_csv_contains_real_database_records(self):
        """Verifica se o CSV exporta os registros reais cadastrados no banco de dados."""
        from django.urls import reverse

        url = reverse('banco_questoes:download_modelo_csv')
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv; charset=utf-8')
        self.assertIn('attachment; filename="acervo_questoes_faeterj.csv"', response['Content-Disposition'])

        content = response.content.decode('utf-8')
        # Verifica cabeçalho BOM UTF-8
        self.assertTrue(content.startswith('\ufeff'))

        # Verifica presença das questões reais
        self.assertIn('Qual a complexidade de inserção numa árvore AVL balanceada?', content)
        self.assertIn('Estrutura de Dados (ESD)', content)
        self.assertIn('Árvores Binárias', content)
        self.assertIn('O(log n)', content)
        self.assertIn('Explique o ciclo de vida de uma requisição HTTP.', content)
        self.assertIn('Construção de Aplicações WEB (CAW)', content)
        self.assertIn('DNS, handshake TCP, requisição HTTP, resposta do servidor.', content)

    def test_export_csv_respects_active_filters(self):
        """Verifica se o CSV exporta apenas os registros que atendem aos filtros ativos da query string."""
        from django.urls import reverse

        url = reverse('banco_questoes:download_modelo_csv')
        # Filtra apenas pela disciplina ESD
        response = self.client.get(url, {'disciplina': self.disc_esd.pk})

        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')

        self.assertIn('Qual a complexidade de inserção numa árvore AVL balanceada?', content)
        self.assertNotIn('Explique o ciclo de vida de uma requisição HTTP.', content)


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
