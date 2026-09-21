"""Admin do Módulo 4 (Desempenho)."""

from django.contrib import admin

from .models import Desempenho


@admin.register(Desempenho)
class DesempenhoAdmin(admin.ModelAdmin):
    list_display = (
        'folha_resposta',
        'questao',
        'tipo_correcao',
        'resposta_marcada',
        'acertou',
        'nota',
        'data_correcao',
    )
    list_filter = ('tipo_correcao', 'acertou')
    search_fields = ('folha_resposta__aluno__nome', 'folha_resposta__aluno__matricula')
    raw_id_fields = ('folha_resposta', 'questao', 'corrigido_por')
    readonly_fields = ('criado_em', 'atualizado_em')
    list_select_related = ('folha_resposta', 'questao')
