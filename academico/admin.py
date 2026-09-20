"""Configuração dos cadastros acadêmicos no Django admin."""

from django.contrib import admin

from .models import Aluno, Matricula, Turma


@admin.register(Aluno)
class AlunoAdmin(admin.ModelAdmin):
    list_display = ('nome', 'matricula', 'ativo', 'criado_em')
    list_filter = ('ativo',)
    search_fields = ('nome', 'matricula')
    ordering = ('nome',)


@admin.register(Turma)
class TurmaAdmin(admin.ModelAdmin):
    list_display = ('periodo', 'nome', 'ativo', 'criado_em')
    list_filter = ('ativo', 'periodo')
    search_fields = ('nome', 'periodo')
    ordering = ('-periodo', 'nome')


@admin.register(Matricula)
class MatriculaAdmin(admin.ModelAdmin):
    list_display = ('aluno', 'turma', 'situacao', 'data_matricula', 'data_encerramento')
    list_filter = ('situacao', 'turma__periodo')
    search_fields = ('aluno__nome', 'aluno__matricula', 'turma__nome')
    autocomplete_fields = ('aluno', 'turma')
    date_hierarchy = 'data_matricula'
