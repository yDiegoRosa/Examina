"""Configuração dos cadastros acadêmicos no Django admin."""

from django.contrib import admin

from .models import Aluno, Matricula, Turma


@admin.register(Aluno)
class AlunoAdmin(admin.ModelAdmin):
    list_display = ('nome', 'matricula', 'ativo')
    list_filter = ('ativo',)
    search_fields = ('nome', 'matricula')
    readonly_fields = ('criado_em', 'atualizado_em')


@admin.register(Turma)
class TurmaAdmin(admin.ModelAdmin):
    list_display = ('nome', 'periodo', 'ativo')
    list_filter = ('periodo', 'ativo')
    search_fields = ('nome', 'periodo')
    readonly_fields = ('criado_em', 'atualizado_em')

    def get_readonly_fields(self, request, obj=None):
        campos = super().get_readonly_fields(request, obj)
        if obj is not None and obj.matriculas.exists():
            return (*campos, 'periodo')
        return campos


@admin.register(Matricula)
class MatriculaAdmin(admin.ModelAdmin):
    list_display = ('aluno', 'turma', 'ativo')
    list_filter = ('ativo', 'turma__periodo')
    search_fields = (
        'aluno__nome',
        'aluno__matricula',
        'turma__nome',
    )
    list_select_related = ('aluno', 'turma')
    readonly_fields = ('criado_em', 'atualizado_em')

    def get_readonly_fields(self, request, obj=None):
        campos = super().get_readonly_fields(request, obj)
        if obj is not None:
            return (*campos, 'aluno', 'turma')
        return campos

    def has_delete_permission(self, request, obj=None):
        return False
