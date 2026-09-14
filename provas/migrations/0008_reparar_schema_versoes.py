"""Compatibiliza tabelas MySQL legadas com o estado já registrado pela 0006.

Alguns bancos possuem a 0006 marcada como aplicada, mas ainda usam os nomes
versao/snapshot_alternativas. A correção é apenas física: o estado das migrações
já descreve os campos atuais. Colunas extras são preservadas como opcionais.
"""

from django.db import migrations, models


def reparar_schema_versoes(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != 'mysql':
        return

    versao = apps.get_model('provas', 'ProvaVersao')
    item = apps.get_model('provas', 'ProvaVersaoItem')

    def colunas(model):
        with connection.cursor() as cursor:
            return {
                column.name: column
                for column in connection.introspection.get_table_description(cursor, model._meta.db_table)
            }

    renomes = [(versao, 'versao', 'identificador'), (item, 'snapshot_alternativas', 'alternativas_embaralhadas')]
    # Valida ambos os nomes antes de executar DDL, que não é transacional no MySQL.
    for model, antigo, novo in renomes:
        existentes = colunas(model)
        if (antigo in existentes) == (novo in existentes):
            raise RuntimeError(f'Estrutura ambígua em {model._meta.db_table}: esperado somente {antigo} ou {novo}.')

    for model, antigo, novo in renomes:
        if antigo in colunas(model):
            campo_novo = model._meta.get_field(novo)
            campo_antigo = campo_novo.clone()
            campo_antigo.set_attributes_from_name(antigo)
            campo_antigo.model = model
            if antigo == 'versao':
                campo_antigo.max_length = 5
            schema_editor.alter_field(model, campo_antigo, campo_novo)

    if 'atualizado_em' not in colunas(versao):
        schema_editor.add_field(versao, versao._meta.get_field('atualizado_em'))
        versao.objects.using(connection.alias).update(atualizado_em=models.F('criado_em'))

    # Campos legados não são mais enviados pelo ORM. Permitir NULL evita erros
    # de INSERT sem descartar identificadores de QR ou gabaritos já armazenados.
    extras = [
        (versao, 'identificador_qrcode', models.CharField(max_length=100)),
        (item, 'gabarito_letra', models.CharField(max_length=2)),
        (item, 'criado_em', models.DateTimeField()),
    ]
    for model, nome, campo_antigo in extras:
        existentes = colunas(model)
        if nome in existentes and not existentes[nome].null_ok:
            campo_antigo.set_attributes_from_name(nome)
            campo_antigo.model = model
            campo_novo = campo_antigo.clone()
            campo_novo.set_attributes_from_name(nome)
            campo_novo.model = model
            campo_novo.null = True
            schema_editor.alter_field(model, campo_antigo, campo_novo)

    for model in (versao, item):
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, model._meta.db_table)
        existentes = {tuple(constraint['columns']) for constraint in constraints.values() if constraint['unique']}
        for campos in model._meta.unique_together:
            columns = tuple(model._meta.get_field(nome).column for nome in campos)
            if columns not in existentes:
                schema_editor.alter_unique_together(model, (), (campos,))

    # O índice legado por número impõe uma regra que o modelo atual não exige.
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, item._meta.db_table)
    if any(c['unique'] and c['columns'] == ['versao_id', 'numero'] for c in constraints.values()):
        schema_editor.alter_unique_together(item, (('versao', 'numero'),), ())


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ('provas', '0007_alter_historicalmascara_disciplina_and_more'),
    ]

    operations = [
        # Irreversível: restaurar a estrutura divergente quebraria o ORM atual.
        migrations.RunPython(reparar_schema_versoes, atomic=False),
    ]
