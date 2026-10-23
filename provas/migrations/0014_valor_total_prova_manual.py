"""
Provas montadas à mão sem máscara: o valor total passa a ser a soma dos pontos das questões
(a nota do aluno é a soma dos pontos obtidos, não mais convertida para 0–10). As provas novas
já gravam ``valor_total`` no snapshot; aqui ele é preenchido nas antigas, para a nota e a capa
ficarem coerentes. Só altera provas manuais sem máscara que ainda não têm o valor.
"""

from django.db import migrations
from django.db.models import Sum


def _manuais_sem_mascara(modelo_prova):
    for prova in modelo_prova.objects.filter(tipo_geracao='MANUAL'):
        snap = prova.snapshot_mascara or {}
        if snap.get('mascara_id') is None and not prova.mascara_id:
            yield prova, snap


def preencher_valor_total(apps, schema_editor):
    Prova = apps.get_model('provas', 'Prova')
    ProvaQuestao = apps.get_model('provas', 'ProvaQuestao')
    for prova, snap in _manuais_sem_mascara(Prova):
        if snap.get('valor_total'):
            continue
        soma = ProvaQuestao.objects.filter(prova=prova).aggregate(total=Sum('pontuacao'))['total']
        if soma:
            prova.snapshot_mascara = {**snap, 'valor_total': str(soma)}
            prova.save(update_fields=['snapshot_mascara'])


def remover_valor_total(apps, schema_editor):
    Prova = apps.get_model('provas', 'Prova')
    for prova, snap in _manuais_sem_mascara(Prova):
        if 'valor_total' in snap:
            prova.snapshot_mascara = {k: v for k, v in snap.items() if k != 'valor_total'}
            prova.save(update_fields=['snapshot_mascara'])


class Migration(migrations.Migration):
    dependencies = [
        ('provas', '0013_mascara_quantidade_versoes'),
    ]

    operations = [
        migrations.RunPython(preencher_valor_total, remover_valor_total),
    ]
