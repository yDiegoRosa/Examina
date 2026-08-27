"""
test_duplicidade.py — Testes de detecção de duplicatas no CSVQuestaoValidator.

Cobre:
- normalize_enunciado: normalização de texto para comparação
- find_db_duplicate: busca por enunciado idêntico no banco
- validate_rows: duplicata intra-arquivo (mesmo CSV)
- validate_rows: duplicata contra banco de dados
- Pipeline CSVImportService: relatório 1 sucesso + 1 rejeitada (duplicata)
- Flag check_db_duplicates=False: duplicatas do banco ignoradas
"""

import pytest

from banco_questoes.importer import CSVImportService
from banco_questoes.validator import CSVQuestaoValidator, normalize_enunciado

# ---------------------------------------------------------------------------
# normalize_enunciado
# ---------------------------------------------------------------------------


class TestNormalizeEnunciado:
    def test_lowercase(self):
        assert normalize_enunciado('Questão ABC') == 'questão abc'

    def test_espacos_multiplos_comprimidos(self):
        assert normalize_enunciado('texto   com   espaços') == 'texto com espaços'

    def test_quebras_de_linha_viram_espaco(self):
        assert normalize_enunciado('linha1\nlinha2\n linha3') == 'linha1 linha2 linha3'

    def test_strip_bordas(self):
        assert normalize_enunciado('  texto  ') == 'texto'

    def test_string_vazia_retorna_vazia(self):
        assert normalize_enunciado('') == ''

    def test_none_como_string_vazia(self):
        assert normalize_enunciado(None) == ''  # type: ignore


# ---------------------------------------------------------------------------
# find_db_duplicate
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFindDbDuplicate:
    def test_retorna_questao_existente(self, questao_objetiva):
        """Deve encontrar questão com enunciado idêntico (case-insensitive)."""
        norm = normalize_enunciado(questao_objetiva.enunciado)
        resultado = CSVQuestaoValidator.find_db_duplicate(norm)
        assert resultado is not None
        assert resultado.pk == questao_objetiva.pk

    def test_retorna_none_para_enunciado_novo(self, db):
        """Enunciado não presente no banco deve retornar None."""
        resultado = CSVQuestaoValidator.find_db_duplicate('pergunta completamente nova que não existe no banco')
        assert resultado is None

    def test_detecta_com_variacao_de_espacos(self, questao_objetiva):
        """Enunciado com espaços extras deve ser detectado após normalização."""
        enunciado_com_espacos = '  ' + questao_objetiva.enunciado + '  '
        norm = normalize_enunciado(enunciado_com_espacos)
        resultado = CSVQuestaoValidator.find_db_duplicate(norm)
        assert resultado is not None

    def test_string_vazia_retorna_none(self, db):
        assert CSVQuestaoValidator.find_db_duplicate('') is None


# ---------------------------------------------------------------------------
# validate_rows: duplicata intra-arquivo
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestValidateRowsDuplicataArquivo:
    def _make_row(self, linha, enunciado='Enunciado válido?'):
        return {
            'linha': linha,
            'enunciado': enunciado,
            'tipo': 'OBJ',
            'disciplina': 'Computação',
            'topico': None,
            'dificuldade': 'M',
            'origem': 'ENADE',
            'numero_questao': str(linha),
            'tags': [],
            'alternativas': [
                {'letra': 'A', 'texto': 'Alt A', 'correta': False},
                {'letra': 'B', 'texto': 'Alt B', 'correta': True},
            ],
            'correta': 'B',
            'criterios_correcao': '',
        }

    def test_duas_linhas_mesmo_enunciado_segunda_rejeitada(self):
        """Segunda linha com enunciado idêntico deve ser rejeitada."""
        rows = [
            self._make_row(2, 'Enunciado repetido?'),
            self._make_row(3, 'Enunciado repetido?'),
        ]
        report = CSVQuestaoValidator.validate_rows(rows, check_db_duplicates=False)

        assert report.total_validas == 1
        assert report.total_invalidas == 1
        erros = report.linhas_invalidas[0].errors
        assert any('duplicado' in e.lower() or 'Linha 2' in e for e in erros)

    def test_enunciados_diferentes_ambos_validos(self):
        rows = [
            self._make_row(2, 'Primeira questão?'),
            self._make_row(3, 'Segunda questão diferente?'),
        ]
        report = CSVQuestaoValidator.validate_rows(rows, check_db_duplicates=False)

        assert report.total_validas == 2
        assert report.total_invalidas == 0

    def test_duplicata_normalizada_detectada(self):
        """Enunciados idênticos após normalização devem ser tratados como duplicados."""
        rows = [
            self._make_row(2, 'Questão de Teste?'),
            self._make_row(3, 'questão de teste?'),  # só difere em caixa
        ]
        report = CSVQuestaoValidator.validate_rows(rows, check_db_duplicates=False)

        assert report.total_invalidas == 1

    def test_check_db_duplicates_false_ignora_banco(self, questao_objetiva):
        """Com check_db_duplicates=False, questão já no banco deve ser aceita."""
        rows = [
            {
                'linha': 2,
                'enunciado': questao_objetiva.enunciado,  # já existe no banco
                'tipo': 'OBJ',
                'disciplina': 'Computação',
                'topico': None,
                'dificuldade': 'M',
                'origem': 'X',
                'numero_questao': '1',
                'tags': [],
                'alternativas': [
                    {'letra': 'A', 'texto': 'Alt A', 'correta': False},
                    {'letra': 'B', 'texto': 'Alt B', 'correta': True},
                ],
                'correta': 'B',
                'criterios_correcao': '',
            }
        ]
        report = CSVQuestaoValidator.validate_rows(rows, check_db_duplicates=False)
        assert report.total_validas == 1


# ---------------------------------------------------------------------------
# validate_rows: duplicata contra banco de dados
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestValidateRowsDuplicataDB:
    def test_enunciado_ja_no_banco_rejeitado(self, questao_objetiva):
        """Linha com enunciado igual ao do banco deve ser rejeitada."""
        row = {
            'linha': 2,
            'enunciado': questao_objetiva.enunciado,
            'tipo': 'OBJ',
            'disciplina': 'Computação',
            'topico': None,
            'dificuldade': 'M',
            'origem': 'X',
            'numero_questao': '1',
            'tags': [],
            'alternativas': [
                {'letra': 'A', 'texto': 'Alt A', 'correta': False},
                {'letra': 'B', 'texto': 'Alt B', 'correta': True},
            ],
            'correta': 'B',
            'criterios_correcao': '',
        }
        report = CSVQuestaoValidator.validate_rows([row], check_db_duplicates=True)

        assert report.total_invalidas == 1
        erros = report.linhas_invalidas[0].errors
        assert any('duplicada' in e.lower() for e in erros)


# ---------------------------------------------------------------------------
# Pipeline completo: CSVImportService
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCSVImportServiceDuplicidade:
    def test_importacao_com_uma_nova_e_uma_duplicata(self, user_docente):
        """Pipeline deve importar 1 questão e rejeitar a duplicata (mesmo arquivo)."""
        csv = (
            'origem,questao,enunciado,opcao_a,opcao_b,gabarito,comentarios\n'
            'ENADE,1,Qual é o protocolo de transporte?,TCP,UDP,A,TCP é confiável.\n'
            'ENADE,2,Qual é o protocolo de transporte?,TCP,UDP,A,Linha duplicada.\n'
        )
        result = CSVImportService.import_csv(csv, autor=user_docente)

        assert result.total_processadas == 2
        assert result.total_sucesso == 1
        assert result.total_rejeitadas == 1

    def test_importacao_sem_duplicatas_importa_tudo(self, user_docente):
        """CSV com 2 questões diferentes deve importar ambas com sucesso."""
        csv = (
            'origem,questao,enunciado,opcao_a,opcao_b,gabarito,comentarios\n'
            'ENADE,1,O que é TCP?,Protocolo de transporte,Protocolo de rede,A,TCP é confiável.\n'
            'POSCOMP,2,O que é UDP?,Sem conexão,Com conexão,A,UDP é sem conexão.\n'
        )
        result = CSVImportService.import_csv(csv, autor=user_docente)

        assert result.total_sucesso == 2
        assert result.total_rejeitadas == 0

    def test_reimportacao_do_mesmo_arquivo_rejeita_todos(self, user_docente):
        """Segunda importação do mesmo CSV deve rejeitar todas as linhas (já no banco)."""
        csv = (
            'origem,questao,enunciado,opcao_a,opcao_b,gabarito,comentarios\n'
            'ENADE,1,O que é HTTP?,Protocolo de aplicação,Protocolo de rede,A,HTTP usa TCP.\n'
        )
        # Primeira importação
        r1 = CSVImportService.import_csv(csv, autor=user_docente)
        assert r1.total_sucesso == 1

        # Segunda importação — mesma questão já está no banco
        r2 = CSVImportService.import_csv(csv, autor=user_docente)
        assert r2.total_sucesso == 0
        assert r2.total_rejeitadas == 1
