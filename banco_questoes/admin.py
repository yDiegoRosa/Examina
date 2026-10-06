from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import Alternativa, Disciplina, Questao, TentativaLogin, Topico


# ---------------------------------------------------------------------------
# Inlines
# ---------------------------------------------------------------------------
class TopicoInline(admin.TabularInline):
    model = Topico
    extra = 1
    fields = ['nome', 'descricao', 'ativo']


class AlternativaInline(admin.TabularInline):
    model = Alternativa
    extra = 4
    max_num = 5
    fields = ['letra', 'texto', 'correta']


# ---------------------------------------------------------------------------
# ModelAdmins
# ---------------------------------------------------------------------------
@admin.register(Disciplina)
class DisciplinaAdmin(admin.ModelAdmin):
    list_display = ['nome', 'ativo', 'criado_em']
    list_filter = ['ativo']
    search_fields = ['nome']
    inlines = [TopicoInline]


@admin.register(Topico)
class TopicoAdmin(admin.ModelAdmin):
    list_display = ['nome', 'disciplina', 'ativo', 'criado_em']
    list_filter = ['ativo', 'disciplina']
    search_fields = ['nome', 'disciplina__nome']


@admin.register(Questao)
class QuestaoAdmin(SimpleHistoryAdmin):
    list_display = ['__str__', 'tipo', 'dificuldade', 'disciplina', 'ativo', 'criado_em']
    list_filter = ['tipo', 'dificuldade', 'disciplina', 'ativo']
    search_fields = ['enunciado', 'disciplina__nome', 'topico__nome']
    list_select_related = ['disciplina', 'topico']
    inlines = [AlternativaInline]


@admin.register(Alternativa)
class AlternativaAdmin(admin.ModelAdmin):
    list_display = ['questao', 'letra', 'correta']
    list_filter = ['correta']
    list_select_related = ['questao']


@admin.register(TentativaLogin)
class TentativaLoginAdmin(admin.ModelAdmin):
    """Consulta das tentativas de login erradas. Apagar as de um usuário o desbloqueia."""

    list_display = ['username', 'ip', 'criado_em']
    search_fields = ['username', 'ip']
    readonly_fields = ['username', 'ip', 'criado_em']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
