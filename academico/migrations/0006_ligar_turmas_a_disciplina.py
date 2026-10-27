"""
Liga as turmas já cadastradas à disciplina pela sigla do nome ("FAC NOITE" → FAC).

Só liga quando o nome é a sigla sozinha ou a sigla seguida apenas do turno ("MAB", "FAC NOITE",
"MAC Manhã"): um nome de curso como "ADS-3A" não pode virar a disciplina "Tópicos em ADS (ADS)".
O nome das turmas não muda aqui (só ao editar a turma na tela, que passa a montá-lo pela
disciplina). Turma não reconhecida fica sem disciplina: o administrador escolhe ao editar.
"""

import re
import unicodedata

from django.db import migrations

SIGLA = re.compile(r'\(([^()]+)\)\s*$')
TURNOS = {'MANHA', 'TARDE', 'NOITE'}


def palavras_do_nome(nome):
    sem_acento = ''.join(c for c in unicodedata.normalize('NFKD', nome) if not unicodedata.combining(c))
    return re.findall(r'[A-Z0-9]+', sem_acento.upper())


def ligar_turmas(apps, schema_editor):
    Disciplina = apps.get_model('banco_questoes', 'Disciplina')
    Turma = apps.get_model('academico', 'Turma')

    por_sigla = {}
    for disciplina in Disciplina.objects.all():
        encontrada = SIGLA.search(disciplina.nome)
        if encontrada:
            por_sigla.setdefault(encontrada.group(1).strip().upper(), disciplina)

    for turma in Turma.objects.filter(disciplina__isnull=True):
        palavras = palavras_do_nome(turma.nome)
        if not palavras or any(p not in TURNOS for p in palavras[1:]):
            continue
        disciplina = por_sigla.get(palavras[0])
        if disciplina:
            turma.disciplina = disciplina
            turma.save(update_fields=['disciplina'])


class Migration(migrations.Migration):
    dependencies = [
        ('academico', '0005_turma_disciplina_e_perfil_professor'),
        ('banco_questoes', '0005_seed_disciplinas'),
    ]

    operations = [
        migrations.RunPython(ligar_turmas, migrations.RunPython.noop),
    ]
