"""
Unifica disciplinas duplicadas nas oficiais da matriz da FAETERJ-Rio (tabela do cliente, 2026-10-26).

Uma disciplina fora da lista oficial cujo nome (sem a sigla) é igual ao de uma oficial — ex.:
"Estrutura de Dados (EDA)", criada por uma importação com a sigla errada, duplicando "Estrutura de
Dados (ESD)" — tem tudo movido para a oficial (questões, tópicos, máscaras, provas, turmas e
docências) e é desativada (não é apagada: o histórico de versões das questões aponta para ela).
Disciplinas sem par, como "Não Classificada", ficam como estão.

Tópico com o mesmo nome de um da oficial é movido com o sufixo " (de SIGLA)" em vez de ser
fundido: uma máscara não pode ter dois critérios para o mesmo tópico, e assim nada se perde.
"""

import re
import unicodedata

from django.db import migrations

OFICIAIS = [
    'Organização de Computadores (ORG)',
    'Matemática para Computação (MAC)',
    'Fundamentos de Algoritmos de Computação (FAC)',
    'Matemática Básica (MAB)',
    'Introdução à Análise de Sistemas (IAS)',
    'Língua Portuguesa (LPO)',
    'Interface Homem-Máquina (IHM)',
    'Técnicas e Paradigmas Humanos (TPH)',
    'Engenharia de Requisitos (REQ)',
    'Construção de Aplicações WEB (CAW)',
    'Fundamentos de Programação (FPR)',
    'Língua Estrangeira (LES)',
    'Métodos e Processos Administrativos (MPA)',
    'Fundamentos de Sistemas Operacionais (SOP)',
    'Cálculo (CAL)',
    'Programação Orientada a Objetos Básica (POB)',
    'Projeto de Banco de Dados (PBD)',
    'Desenvolvimento de Tecnologias WEB (DAW)',
    'Álgebra (ALG)',
    'Estrutura de Dados (ESD)',
    'Fundamentos de Redes e Sistemas Distribuídos (RSD)',
    'Programação Orientada a Objetos Avançada (POA)',
    'Utilização de Banco de Dados e SQL (UBD)',
    'Modelagem de Sistemas (MOD)',
    'Segurança da Informação (SEG)',
    'Estatística e Probabilidade (EST)',
    'Tópicos em ADS (ADS)',
    'Metodologia da Pesquisa (MET)',
    'Empreendedorismo e Inovação (EMP)',
    'Programação de Scripts de Banco de Dados (SBD)',
    'Projeto de Sistemas (PJS)',
    'Programação de Dispositivos Móveis (PDM)',
    'Gerência e Projeto de Sistemas (GPS)',
    'Tópicos Avançados (TAV)',
]

_SUFIXO_SIGLA = re.compile(r'\s*\(([^()]+)\)\s*$')


def _nome_base(nome):
    """ "Estrutura de Dados (EDA)" → "estrutura de dados" (sem sigla, acentos e maiúsculas)."""
    sem_sigla = _SUFIXO_SIGLA.sub('', nome)
    sem_acento = ''.join(c for c in unicodedata.normalize('NFKD', sem_sigla) if not unicodedata.combining(c))
    return ' '.join(sem_acento.casefold().split())


def _sigla(nome):
    encontrada = _SUFIXO_SIGLA.search(nome)
    return encontrada.group(1).strip() if encontrada else nome[:20]


def _mover(apps, origem, destino):
    Topico = apps.get_model('banco_questoes', 'Topico')
    Questao = apps.get_model('banco_questoes', 'Questao')
    Mascara = apps.get_model('provas', 'Mascara')
    Prova = apps.get_model('provas', 'Prova')
    Turma = apps.get_model('academico', 'Turma')
    Docencia = apps.get_model('academico', 'Docencia')

    for topico in Topico.objects.filter(disciplina=origem):
        if Topico.objects.filter(disciplina=destino, nome=topico.nome).exists():
            topico.nome = f'{topico.nome} (de {_sigla(origem.nome)})'[:150]
        topico.disciplina = destino
        topico.save(update_fields=['disciplina', 'nome'])

    for modelo in (Questao, Mascara, Prova, Turma):
        modelo.objects.filter(disciplina=origem).update(disciplina=destino)

    for docencia in Docencia.objects.filter(disciplina=origem):
        igual = Docencia.objects.filter(
            professor_id=docencia.professor_id, disciplina=destino, turma_id=docencia.turma_id
        )
        if igual.exists():
            if docencia.ativo:
                igual.update(ativo=True)
            docencia.delete()
        else:
            docencia.disciplina = destino
            docencia.save(update_fields=['disciplina'])


def unificar(apps, schema_editor):
    Disciplina = apps.get_model('banco_questoes', 'Disciplina')
    oficiais = {_nome_base(d.nome): d for d in Disciplina.objects.filter(nome__in=OFICIAIS)}
    for disciplina in Disciplina.objects.exclude(nome__in=OFICIAIS):
        destino = oficiais.get(_nome_base(disciplina.nome))
        if destino is None:
            continue
        _mover(apps, disciplina, destino)
        disciplina.ativo = False
        disciplina.save(update_fields=['ativo'])


class Migration(migrations.Migration):
    dependencies = [
        ('banco_questoes', '0009_tentativa_login'),
        ('academico', '0006_ligar_turmas_a_disciplina'),
        ('provas', '0014_valor_total_prova_manual'),
    ]

    operations = [
        migrations.RunPython(unificar, migrations.RunPython.noop),
    ]
