"""
Data migration: reaplica as permissões dos grupos Docente e Coordenador.

A 0002 atribui as permissões no momento em que roda, mas num banco NOVO as linhas de
``auth.Permission`` só são criadas pelo sinal ``post_migrate`` (depois de todas as
migrations) — os grupos nasciam com 0 permissões. Aqui as permissões do app são
criadas explicitamente antes da atribuição.

Idempotente e não destrutiva: apenas ADICIONA as permissões esperadas; permissões
extras concedidas manualmente aos grupos são mantidas. O reverso não faz nada.
"""

from django.contrib.auth.management import create_permissions
from django.db import migrations

# Mesmo mapeamento da 0002 (congelado aqui: migrations não devem importar umas às outras).
GROUPS_PERMISSIONS = {
    'Docente': [
        'add_disciplina',
        'change_disciplina',
        'delete_disciplina',
        'view_disciplina',
        'add_topico',
        'change_topico',
        'delete_topico',
        'view_topico',
        'add_questao',
        'change_questao',
        'delete_questao',
        'view_questao',
        'add_alternativa',
        'change_alternativa',
        'delete_alternativa',
        'view_alternativa',
    ],
    'Coordenador': [
        'view_disciplina',
        'view_topico',
        'add_questao',
        'change_questao',
        'view_questao',
        'add_alternativa',
        'change_alternativa',
        'view_alternativa',
    ],
}

APP_LABEL = 'banco_questoes'


def reaplicar_permissoes(apps, _schema_editor):
    # Garante que content types e permissões do app existam já neste ponto.
    app_config = apps.get_app_config(APP_LABEL)
    app_config.models_module = True
    create_permissions(app_config, apps=apps, verbosity=0)
    app_config.models_module = None

    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')

    for group_name, codenames in GROUPS_PERMISSIONS.items():
        group, _created = Group.objects.get_or_create(name=group_name)
        perms = Permission.objects.filter(codename__in=codenames, content_type__app_label=APP_LABEL)
        group.permissions.add(*perms)


class Migration(migrations.Migration):
    dependencies = [
        ('banco_questoes', '0007_add_origem_numero_questao'),
        ('auth', '__latest__'),
        ('contenttypes', '__latest__'),
    ]

    operations = [
        migrations.RunPython(reaplicar_permissoes, migrations.RunPython.noop),
    ]
