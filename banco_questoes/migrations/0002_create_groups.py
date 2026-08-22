"""
Data migration: cria os grupos Docente e Coordenador com suas permissões.

Docente     — acesso total a todos os models do banco de questões.
Coordenador — visualiza tudo + add/change em questões e alternativas.
"""

from django.db import migrations

# ---------------------------------------------------------------------------
# Mapeamento grupo → permissões (codenames do app banco_questoes)
# ---------------------------------------------------------------------------
GROUPS_PERMISSIONS = {
    'Docente': [
        # Disciplina — acesso total
        'add_disciplina',
        'change_disciplina',
        'delete_disciplina',
        'view_disciplina',
        # Tópico — acesso total
        'add_topico',
        'change_topico',
        'delete_topico',
        'view_topico',
        # Questão — acesso total
        'add_questao',
        'change_questao',
        'delete_questao',
        'view_questao',
        # Alternativa — acesso total
        'add_alternativa',
        'change_alternativa',
        'delete_alternativa',
        'view_alternativa',
    ],
    'Coordenador': [
        # Disciplina — somente visualização
        'view_disciplina',
        # Tópico — somente visualização
        'view_topico',
        # Questão — add/change/view (sem delete)
        'add_questao',
        'change_questao',
        'view_questao',
        # Alternativa — add/change/view (sem delete)
        'add_alternativa',
        'change_alternativa',
        'view_alternativa',
    ],
}

APP_LABEL = 'banco_questoes'


def create_groups(apps, _schema_editor):
    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')

    for group_name, codenames in GROUPS_PERMISSIONS.items():
        group, _created = Group.objects.get_or_create(name=group_name)
        perms = Permission.objects.filter(
            codename__in=codenames,
            content_type__app_label=APP_LABEL,
        )
        group.permissions.set(perms)


def remove_groups(apps, _schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.filter(name__in=GROUPS_PERMISSIONS.keys()).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('banco_questoes', '0001_initial'),
        ('auth', '__latest__'),
    ]

    operations = [
        migrations.RunPython(create_groups, remove_groups),
    ]
