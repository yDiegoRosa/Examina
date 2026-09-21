"""Configuração dos cadastros acadêmicos no Django admin."""

from django.contrib import admin

from .models import Aluno, Matricula, Turma


@admin.register(Aluno)
class AlunoAdmin(admin.ModelAdmin):
    list_display = ('nome', 'matricula', 'ativo', 'criado_em')
    list_filter = ('ativo',)
    search_fields = ('nome', 'matricula')
    ordering = ('nome',)
    readonly_fields = ('criado_em', 'atualizado_em')


@admin.register(Turma)
class TurmaAdmin(admin.ModelAdmin):
    list_display = ('periodo', 'nome', 'ativo', 'criado_em')
    list_filter = ('ativo', 'periodo')
    search_fields = ('nome', 'periodo')
    ordering = ('-periodo', 'nome')
    readonly_fields = ('criado_em', 'atualizado_em')

    def get_readonly_fields(self, request, obj=None):
        campos = super().get_readonly_fields(request, obj)
        if obj is not None and obj.matriculas.exists():
            return (*campos, 'periodo')
        return campos


@admin.register(Matricula)
class MatriculaAdmin(admin.ModelAdmin):
    list_display = ('aluno', 'turma', 'situacao', 'data_matricula', 'data_encerramento')
    list_filter = ('situacao', 'turma__periodo')
    search_fields = ('aluno__nome', 'aluno__matricula', 'turma__nome')
    autocomplete_fields = ('aluno', 'turma')
    date_hierarchy = 'data_matricula'
    list_select_related = ('aluno', 'turma')
    readonly_fields = ('criado_em', 'atualizado_em')

    def get_readonly_fields(self, request, obj=None):
        campos = super().get_readonly_fields(request, obj)
        if obj is not None:
            return (*campos, 'aluno', 'turma')
        return campos

    def has_delete_permission(self, request, obj=None):
        return False
