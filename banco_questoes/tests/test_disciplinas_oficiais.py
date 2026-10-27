"""
Disciplinas oficiais (tabela da matriz FAETERJ-Rio): a importação reconhece o nome ou a sigla e não
cria disciplinas; a migration 0010 unifica duplicatas (ex.: "Estrutura de Dados (EDA)") na oficial.
"""

import importlib

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model

from academico.models import Docencia, Turma
from banco_questoes.disciplinas import CatalogoDisciplinas
from banco_questoes.importer import QuestaoImportService
from banco_questoes.models import Disciplina, Questao, Topico

pytestmark = pytest.mark.django_db

SEG = 'Segurança da Informação (SEG)'


@pytest.mark.parametrize(
    'texto',
    [
        'Segurança da Informação (SEG)',
        'Segurança da Informação',
        'seguranca da informacao',
        'SEG',
        'seg',
        '4SEG',
        ' 4seg ',
        'Segurança da Informação (4SEG)',
    ],
)
def test_reconhece_a_disciplina_pelo_nome_ou_pela_sigla(texto):
    assert CatalogoDisciplinas().resolver(texto).nome == SEG


@pytest.mark.parametrize('texto', ['', 'Segurança', 'XYZ', 'Redes de Computadores', 'Não Classificada'])
def test_texto_fora_da_tabela_nao_e_reconhecido(texto):
    Disciplina.objects.get_or_create(nome='Não Classificada')  # existe em bancos antigos, mas não é oficial
    assert CatalogoDisciplinas().resolver(texto) is None


def test_disciplina_inativa_nao_e_reconhecida():
    Disciplina.objects.filter(nome=SEG).update(ativo=False)
    assert CatalogoDisciplinas().resolver('SEG') is None


def test_as_34_disciplinas_da_tabela_sao_reconhecidas_pela_sigla():
    siglas = 'ORG MAC FAC MAB IAS LPO IHM TPH REQ CAW FPR LES MPA SOP CAL POB PBD DAW ALG ESD RSD POA UBD MOD SEG EST ADS MET EMP SBD PJS PDM GPS TAV'
    catalogo = CatalogoDisciplinas()
    assert all(catalogo.resolver(sigla) for sigla in siglas.split())


def test_importacao_nao_cria_disciplina_e_explica_a_recusa():
    antes = Disciplina.objects.count()
    csv = (
        'disciplina,enunciado,opcao_a,opcao_b,gabarito\n'
        '4SEG,Pela sigla com período,Sim,Não,A\n'
        'Computação,Disciplina fora da tabela,Sim,Não,A\n'
        ',Sem disciplina,Sim,Não,A\n'
    ).encode()

    resultado = QuestaoImportService.import_file(csv, filename='questoes.csv')

    assert (resultado.total_sucesso, resultado.total_rejeitadas) == (1, 2)
    assert Disciplina.objects.count() == antes
    assert Questao.objects.get().disciplina.nome == SEG
    erros = [erro for linha in resultado.linhas for erro in linha.erros]
    assert any('"Computação" não está na lista oficial' in erro for erro in erros)
    assert any('Informe a disciplina' in erro for erro in erros)


def _unificar():
    migracao = importlib.import_module('banco_questoes.migrations.0010_unificar_disciplinas_duplicadas')
    migracao.unificar(apps, None)


def test_migration_unifica_a_duplicata_na_disciplina_oficial():
    esd = Disciplina.objects.get(nome='Estrutura de Dados (ESD)')
    eda = Disciplina.objects.create(nome='Estrutura de Dados (EDA)')
    autor = get_user_model().objects.create_user('autor_eda')
    topico_novo = Topico.objects.create(disciplina=eda, nome='Pilhas e Filas')
    Topico.objects.create(disciplina=esd, nome='Árvores')
    topico_repetido = Topico.objects.create(disciplina=eda, nome='Árvores')
    questao = Questao.objects.create(
        disciplina=eda, topico=topico_repetido, tipo='DIS', dificuldade='M', enunciado='Q da EDA', autor=autor
    )
    turma = Turma.objects.create(periodo='2026.2', nome='EDA', turno=Turma.Turno.NOITE, disciplina=eda)
    Docencia.objects.create(professor=autor, disciplina=eda, turma=turma)

    _unificar()

    questao.refresh_from_db()
    topico_novo.refresh_from_db()
    topico_repetido.refresh_from_db()
    turma.refresh_from_db()
    eda.refresh_from_db()
    assert questao.disciplina == esd
    assert topico_novo.disciplina == esd
    # tópico de mesmo nome não é fundido (a máscara não aceita dois critérios iguais): ganha sufixo
    assert (topico_repetido.disciplina, topico_repetido.nome) == (esd, 'Árvores (de EDA)')
    assert questao.topico == topico_repetido
    assert turma.disciplina == esd
    assert Docencia.objects.filter(professor=autor, disciplina=esd, turma=turma).exists()
    assert not eda.ativo


def test_migration_nao_mexe_no_que_nao_tem_par_na_tabela():
    avulsa = Disciplina.objects.create(nome='Não Classificada')
    _unificar()
    avulsa.refresh_from_db()
    assert avulsa.ativo
    assert Disciplina.objects.filter(nome='Estrutura de Dados (ESD)', ativo=True).exists()


def test_migration_junta_docencia_repetida():
    esd = Disciplina.objects.get(nome='Estrutura de Dados (ESD)')
    eda = Disciplina.objects.create(nome='Estrutura de Dados (EDA)')
    professor = get_user_model().objects.create_user('prof_repetido')
    turma = Turma.objects.create(periodo='2026.2', nome='ESD', turno=Turma.Turno.MANHA, disciplina=esd)
    Docencia.objects.create(professor=professor, disciplina=esd, turma=turma, ativo=False)
    Docencia.objects.create(professor=professor, disciplina=eda, turma=turma, ativo=True)

    _unificar()

    docencias = Docencia.objects.filter(professor=professor, turma=turma)
    assert docencias.count() == 1
    assert docencias.get().disciplina == esd
    assert docencias.get().ativo
