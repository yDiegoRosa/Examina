from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.urls import reverse

from .models import (
    AtribuicaoVersao,
    FolhaResposta,
    Mascara,
    MascaraCriterioDificuldade,
    MascaraCriterioTopico,
    Prova,
    ProvaAplicacao,
    ProvaQuestao,
)
from .services.version_assigner import atribuir_versoes


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


class ProvaQuestaoInline(admin.TabularInline):
    model = ProvaQuestao
    extra = 0
    readonly_fields = ['numero', 'ordem', 'questao', 'pontuacao', 'criado_em']


@admin.register(Prova)
class ProvaAdmin(admin.ModelAdmin):
    list_display = ['titulo', 'mascara', 'numero_caderno', 'autor', 'criado_em']
    list_filter = ['mascara__disciplina', 'criado_em']
    search_fields = ['titulo', 'mascara__titulo']
    inlines = [ProvaQuestaoInline]


class AtribuicaoVersaoInline(admin.TabularInline):
    model = AtribuicaoVersao
    extra = 0
    can_delete = False
    readonly_fields = ['sequencia', 'aluno', 'versao', 'criado_em']
    ordering = ['sequencia']


@admin.register(ProvaAplicacao)
class ProvaAplicacaoAdmin(admin.ModelAdmin):
    list_display = ['prova', 'turma', 'data_aplicacao', 'total_atribuicoes', 'criado_em']
    list_filter = ['data_aplicacao', 'turma__periodo']
    search_fields = ['prova__titulo', 'turma__nome', 'turma__periodo']
    autocomplete_fields = ['prova', 'turma']
    date_hierarchy = 'data_aplicacao'
    inlines = [AtribuicaoVersaoInline]
    actions = ['acao_atribuir_versoes', 'acao_exportar_folhas']

    @admin.display(description='Atribuições')
    def total_atribuicoes(self, obj):
        return obj.atribuicoes.count()

    @admin.action(description='Exportar folhas-resposta nominais (ZIP)')
    def acao_exportar_folhas(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(
                request,
                'Selecione exatamente uma aplicação para exportar as folhas.',
                level=messages.WARNING,
            )
            return None
        aplicacao = queryset.first()
        return HttpResponseRedirect(reverse('provas:aplicacao_folhas_zip', args=[aplicacao.pk]))

    @admin.action(description='Atribuir versões (round-robin)')
    def acao_atribuir_versoes(self, request, queryset):
        total_ok = 0
        for aplicacao in queryset:
            try:
                atribuicoes = atribuir_versoes(aplicacao)
            except ValueError as exc:
                self.message_user(request, f'{aplicacao}: {exc}', level=messages.ERROR)
                continue
            total_ok += 1
            self.message_user(
                request,
                f'{aplicacao}: {len(atribuicoes)} aluno(s) receberam versão.',
                level=messages.SUCCESS,
            )
        if total_ok == 0 and not queryset:
            self.message_user(request, 'Nenhuma aplicação selecionada.', level=messages.WARNING)


@admin.register(AtribuicaoVersao)
class AtribuicaoVersaoAdmin(admin.ModelAdmin):
    list_display = ['aplicacao', 'sequencia', 'aluno', 'versao']
    list_filter = ['versao__identificador', 'aplicacao__turma__periodo']
    search_fields = ['aluno__nome', 'aluno__matricula', 'aplicacao__prova__titulo']
    autocomplete_fields = ['aplicacao', 'aluno']


@admin.register(FolhaResposta)
class FolhaRespostaAdmin(admin.ModelAdmin):
    list_display = ['aluno', 'prova_aplicacao', 'prova_versao', 'status', 'qr_token']
    list_filter = ['status', 'prova_versao__identificador', 'prova_aplicacao__turma__periodo']
    search_fields = ['aluno__nome', 'aluno__matricula', 'qr_token']
    autocomplete_fields = ['prova_aplicacao', 'aluno']
    readonly_fields = ['qr_token', 'criado_em', 'atualizado_em']
    list_select_related = ['aluno', 'prova_aplicacao', 'prova_versao']
