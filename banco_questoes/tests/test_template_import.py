"""
test_template_import.py — Round-trip do template de importação (RF02).

Garante que o arquivo modelo gerado por download_modelo_csv / download_modelo_xlsx
(novo formato de 15 colunas, com classificação) é reimportável sem erros e cria as
questões devidamente classificadas (disciplina, tópico, tipo, dificuldade, tags).

Esses testes protegem contra "drift" entre o cabeçalho do template e as linhas de
exemplo/exportação: se as colunas saírem de ordem, o round-trip falha.
"""

import pytest
from django.urls import reverse

from banco_questoes.importer import QuestaoImportService
from banco_questoes.models import Questao
from banco_questoes.parser import FileQuestaoParser


@pytest.fixture
def cliente_logado(client, django_user_model):
    """Client autenticado (as views de download exigem apenas login)."""
    user = django_user_model.objects.create_user(username='exportador', password='testpass123')  # noqa: S106
    client.force_login(user)
    return client


@pytest.mark.django_db
def test_template_csv_reimportavel_sem_erros(cliente_logado):
    resp = cliente_logado.get(reverse('banco_questoes:download_modelo_csv'))
    assert resp.status_code == 200

    resultado = QuestaoImportService.import_file(resp.content, filename='modelo.csv')

    assert resultado.total_processadas == 3
    assert resultado.total_sucesso == 3
    assert resultado.total_rejeitadas == 0

    # Classificação preservada no round-trip
    assert Questao.objects.filter(
        disciplina__nome='Fundamentos de Redes e Sistemas Distribuídos (RSD)', tipo='OBJ'
    ).exists()
    assert Questao.objects.filter(disciplina__nome='Fundamentos de Programação (FPR)', dificuldade='D').exists()

    discursiva = Questao.objects.get(tipo='DIS')
    assert discursiva.disciplina.nome == 'Gerência e Projeto de Sistemas (GPS)'
    assert discursiva.alternativas.count() == 0
    assert discursiva.criterios_correcao  # critérios vêm da coluna "comentarios"

    objetiva = Questao.objects.get(disciplina__nome='Fundamentos de Redes e Sistemas Distribuídos (RSD)')
    assert objetiva.alternativas.count() == 5
    assert objetiva.alternativas.get(correta=True).letra == 'C'
    assert objetiva.tags.count() == 2


@pytest.mark.django_db
def test_template_xlsx_reimportavel_sem_erros(cliente_logado):
    resp = cliente_logado.get(reverse('banco_questoes:download_modelo_xlsx'))
    assert resp.status_code == 200

    resultado = QuestaoImportService.import_file(resp.content, filename='modelo.xlsx')

    assert resultado.total_processadas == 3
    assert resultado.total_sucesso == 3
    assert resultado.total_rejeitadas == 0
    assert Questao.objects.count() == 3


def test_cabecalho_15_colunas_mapeia_classificacao():
    """O parser mapeia as colunas de classificação do novo cabeçalho para os campos canônicos."""
    conteudo = (
        'origem,questao,tipo,disciplina,topico,dificuldade,enunciado,'
        'opcao_a,opcao_b,opcao_c,opcao_d,opcao_e,gabarito,tags,comentarios\n'
        'ENADE,1,OBJ,Redes,DNS,M,Qual a função do DNS?,'
        'Resolver nomes,Rotear pacotes,,,,A,redes; dns,Comentário explicativo.\n'
    )
    linhas = FileQuestaoParser.parse(conteudo, filename='x.csv')

    assert len(linhas) == 1
    linha = linhas[0]
    assert linha['tipo'] == 'OBJ'
    assert linha['disciplina'] == 'Redes'
    assert linha['topico'] == 'DNS'
    assert linha['dificuldade'] == 'M'
    assert linha['tags'] == ['redes', 'dns']
    assert len(linha['alternativas']) == 2
