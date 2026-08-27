from django.contrib import admin

from .models import Mascara, MascaraCriterioDificuldade, MascaraCriterioTopico


class MascaraCriterioDificuldadeInline(admin.TabularInline):
    model = MascaraCriterioDificuldade
    extra = 0
    can_delete = False
    readonly_fields = ['dificuldade', 'percentual', 'quantidade_questoes']


class MascaraCriterioTopicoInline(admin.TabularInline):
    model = MascaraCriterioTopico
    extra = 1


@admin.register(Mascara)
class MascaraAdmin(admin.ModelAdmin):
    list_display = [
        'titulo',
        'disciplina',
        'modalidade',
        'quantidade_questoes',
        'valor_total',
        'ativo',
        'autor',
        'criado_em',
    ]
    list_filter = ['disciplina', 'modalidade', 'ativo', 'criado_em']
    search_fields = ['titulo', 'descricao', 'disciplina__nome']
    inlines = [MascaraCriterioDificuldadeInline, MascaraCriterioTopicoInline]


@admin.register(MascaraCriterioDificuldade)
class MascaraCriterioDificuldadeAdmin(admin.ModelAdmin):
    list_display = ['mascara', 'dificuldade', 'percentual', 'quantidade_questoes']
    list_filter = ['dificuldade', 'mascara__disciplina']
    search_fields = ['mascara__titulo']


@admin.register(MascaraCriterioTopico)
class MascaraCriterioTopicoAdmin(admin.ModelAdmin):
    list_display = ['mascara', 'topico', 'percentual', 'quantidade_questoes']
    list_filter = ['mascara__disciplina']
    search_fields = ['mascara__titulo', 'topico__nome']
