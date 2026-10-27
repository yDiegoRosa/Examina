"""
Remove a "nota de corte das discursivas" da aplicação (pedido do cliente, 2026-10-27).

A discursiva passa a contar pela nota, proporcional, também nos relatórios de desempenho (já
contava assim na nota final): "acertou" deixa de existir para ela. Aqui o ``acertou`` das
discursivas já lançadas — que vinha da nota de corte — é limpo; a nota continua gravada.
Desfazer recria o campo (padrão 6) e recalcula ``acertou = nota >= corte``.
"""

from django.db import migrations


def limpar_acertou_das_discursivas(apps, schema_editor):
    Desempenho = apps.get_model('desempenho', 'Desempenho')
    Desempenho.objects.filter(nota__isnull=False).update(acertou=None)


def recalcular_pelo_corte(apps, schema_editor):
    Desempenho = apps.get_model('desempenho', 'Desempenho')
    for desempenho in Desempenho.objects.filter(nota__isnull=False).select_related('folha_resposta__prova_aplicacao'):
        corte = desempenho.folha_resposta.prova_aplicacao.nota_corte
        desempenho.acertou = desempenho.nota >= corte
        desempenho.save(update_fields=['acertou'])


class Migration(migrations.Migration):
    dependencies = [
        ('provas', '0014_valor_total_prova_manual'),
        ('desempenho', '0003_historico_desempenho'),
    ]

    operations = [
        migrations.RunPython(limpar_acertou_das_discursivas, recalcular_pelo_corte),
        migrations.RemoveField(
            model_name='provaaplicacao',
            name='nota_corte',
        ),
    ]
