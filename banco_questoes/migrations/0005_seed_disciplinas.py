from django.db import migrations

DISCIPLINAS = [
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


def popular_disciplinas(apps, _schema_editor):
    Disciplina = apps.get_model('banco_questoes', 'Disciplina')
    for nome in DISCIPLINAS:
        Disciplina.objects.get_or_create(nome=nome, defaults={'ativo': True})


def reverter_disciplinas(apps, _schema_editor):
    Disciplina = apps.get_model('banco_questoes', 'Disciplina')
    Disciplina.objects.filter(nome__in=DISCIPLINAS).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('banco_questoes', '0004_historicalquestao_autor_questao_autor'),
    ]

    operations = [
        migrations.RunPython(popular_disciplinas, reverter_disciplinas),
    ]
