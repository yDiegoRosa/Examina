"""Questão objetiva com alternativas de texto repetido é recusada (importação e formulário)."""

import pytest

from banco_questoes.importer import QuestaoImportService
from banco_questoes.utils import alternativas_repetidas


def test_descreve_as_letras_repetidas():
    assert alternativas_repetidas([('A', 'Sim'), ('B', 'Não')]) == ''
    # Maiúsculas e espaços não fazem diferença.
    assert alternativas_repetidas([('A', 'Mesmo texto'), ('B', 'Outro'), ('C', '  mesmo   TEXTO ')]) == 'A e C'
    assert alternativas_repetidas([('A', 'x'), ('B', 'x'), ('C', 'y'), ('D', 'y'), ('E', 'x')]) == 'A, B e E; C e D'
    assert alternativas_repetidas([('A', ''), ('B', '  ')]) == ''  # vazias ficam para a regra do mínimo


@pytest.mark.django_db
def test_importacao_recusa_so_a_objetiva_com_alternativas_repetidas():
    csv = (
        'disciplina,enunciado,opcao_a,opcao_b,opcao_c,gabarito\n'
        'SEG,Questão com repetidas,Alternativa gráfica,Outra,alternativa  GRÁFICA,A\n'
        'SEG,Questão normal,Sim,Não,Talvez,A\n'
    ).encode()
    resultado = QuestaoImportService.import_file(csv, filename='questoes.csv')

    assert (resultado.total_sucesso, resultado.total_rejeitadas) == (1, 1)
    recusada = next(item for item in resultado.linhas if item.status == 'REJEITADA')
    assert recusada.erros == [
        'Linha 2: Alternativas repetidas (A e C): cada alternativa precisa de um texto diferente.'
    ]
