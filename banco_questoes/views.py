import csv
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.db import transaction
from django.http import HttpResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render

from .filters import QuestaoFilter
from .forms import AlternativaFormSet, QuestaoForm
from .importer import CSVImportService
from .models import Questao
from .parser import CSVParseError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lista de questões (com django-filter)
# ---------------------------------------------------------------------------
@login_required
def questao_list(request):
    """Lista de questões com filtros avançados via django-filter e métricas."""
    base_qs = (
        Questao.objects.select_related('disciplina', 'topico', 'autor').prefetch_related('tags').order_by('-criado_em')
    )

    # Aplicação do FilterSet
    questao_filter = QuestaoFilter(request.GET, queryset=base_qs)
    questoes = questao_filter.qs

    # Métricas globais do acervo
    all_questoes = Questao.objects.all()
    metricas = {
        'total': all_questoes.count(),
        'objetivas': all_questoes.filter(tipo=Questao.Tipo.OBJETIVA).count(),
        'discursivas': all_questoes.filter(tipo=Questao.Tipo.DISCURSIVA).count(),
        'ativas': all_questoes.filter(ativo=True).count(),
    }

    # Verifica se há filtros ativos aplicados pelo usuário
    has_active_filters = any(
        request.GET.get(key) for key in ('disciplina', 'topico', 'dificuldade', 'tipo', 'tags', 'ativo', 'autor', 'q')
    )

    return render(
        request,
        'banco_questoes/questao_list.html',
        {
            'filter': questao_filter,
            'questoes': questoes,
            'metricas': metricas,
            'has_active_filters': has_active_filters,
        },
    )


# ---------------------------------------------------------------------------
# Criar questão (com formset inline e autor)
# ---------------------------------------------------------------------------
@login_required
@permission_required('banco_questoes.add_questao', raise_exception=True)
def questao_create(request):
    """Cadastro de questão via formulário completo + formset inline de alternativas."""
    if request.method == 'POST':
        form = QuestaoForm(request.POST, request.FILES)
        formset = AlternativaFormSet(request.POST)

        if form.is_valid():
            is_obj = form.cleaned_data.get('tipo') == Questao.Tipo.OBJETIVA
            formset_valid = formset.is_valid() if is_obj else True
            cross_valid = form.validate_with_alternativas(formset) if is_obj else True

            if formset_valid and cross_valid:
                try:
                    with transaction.atomic():
                        questao = form.save(commit=False)
                        questao.autor = request.user
                        questao.save()
                        form.save_m2m()

                        if is_obj:
                            formset.instance = questao
                            formset.save()

                    messages.success(
                        request,
                        f'Questão Q{questao.pk} ({questao.get_tipo_display()}) cadastrada com sucesso!',
                    )
                    return redirect('banco_questoes:questao_list')
                except Exception as exc:
                    logger.exception('Erro ao salvar questão: %s', exc)
                    messages.error(request, f'Erro interno ao cadastrar questão: {exc}')
            else:
                messages.error(
                    request,
                    'Por favor, corrija os erros apontados no formulário de alternativas.',
                )
        else:
            messages.error(
                request,
                'Por favor, corrija os erros apontados no formulário.',
            )
    else:
        form = QuestaoForm(initial={'tipo': Questao.Tipo.OBJETIVA})
        formset = AlternativaFormSet()

    return render(
        request,
        'banco_questoes/questao_form.html',
        {
            'form': form,
            'formset': formset,
            'titulo': 'Nova Questão',
        },
    )


# ---------------------------------------------------------------------------
# Editar questão (com formset inline)
# ---------------------------------------------------------------------------
@login_required
@permission_required('banco_questoes.change_questao', raise_exception=True)
def questao_update(request, pk):
    """Edição de questão existente com histórico automático via simple-history."""
    questao = get_object_or_404(Questao, pk=pk)

    if request.method == 'POST':
        form = QuestaoForm(request.POST, request.FILES, instance=questao)
        formset = AlternativaFormSet(request.POST, instance=questao)

        if form.is_valid():
            is_obj = form.cleaned_data.get('tipo') == Questao.Tipo.OBJETIVA
            formset_valid = formset.is_valid() if is_obj else True
            cross_valid = form.validate_with_alternativas(formset) if is_obj else True

            if formset_valid and cross_valid:
                try:
                    with transaction.atomic():
                        questao = form.save(commit=False)
                        questao._history_user = request.user
                        questao.save()
                        form.save_m2m()

                        if is_obj:
                            formset.save()
                        else:
                            questao.alternativas.all().delete()

                    messages.success(
                        request,
                        f'Questão Q{questao.pk} atualizada com sucesso!',
                    )
                    return redirect('banco_questoes:questao_list')
                except Exception as exc:
                    logger.exception('Erro ao atualizar questão Q%s: %s', pk, exc)
                    messages.error(request, f'Erro interno ao atualizar questão: {exc}')
            else:
                messages.error(
                    request,
                    'Por favor, corrija os erros apontados no formulário de alternativas.',
                )
        else:
            messages.error(
                request,
                'Por favor, corrija os erros apontados no formulário.',
            )
    else:
        form = QuestaoForm(instance=questao)
        formset = AlternativaFormSet(instance=questao)

    return render(
        request,
        'banco_questoes/questao_form.html',
        {
            'form': form,
            'formset': formset,
            'titulo': f'Editar Questão Q{questao.pk}',
            'questao': questao,
        },
    )


# ---------------------------------------------------------------------------
# Inativar / Reativar questão (soft delete — ativo=False)
# ---------------------------------------------------------------------------
@login_required
@permission_required('banco_questoes.change_questao', raise_exception=True)
def questao_toggle_ativo(request, pk):
    """Inverte o flag `ativo` da questão (inativa ou reativa) sem excluí-la.

    Aceita apenas POST para evitar ativações/inativações acidentais via GET.
    A mudança é registrada no histórico do django-simple-history com o usuário
    que executou a ação.
    """
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    questao = get_object_or_404(Questao, pk=pk)
    questao.ativo = not questao.ativo
    questao._history_user = request.user
    questao.save(update_fields=['ativo', 'atualizado_em'])

    if questao.ativo:
        messages.success(request, f'Questão Q{questao.pk} reativada com sucesso.')
    else:
        messages.success(request, f'Questão Q{questao.pk} inativada. O registro foi preservado no acervo.')

    return redirect('banco_questoes:questao_list')


# ---------------------------------------------------------------------------
# Download / Exportação do Acervo em CSV
# ---------------------------------------------------------------------------
@login_required
def download_modelo_csv(request):
    """
    Exporta o acervo de questões no formato CSV padrão com 13 colunas.

    Caso existam filtros na requisição GET, exporta o conjunto filtrado.
    Se não houver questões cadastradas, gera registros modelo baseados nas
    disciplinas reais da FAETERJ-Rio.
    """
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="acervo_questoes_faeterj.csv"'

    # BOM UTF-8 para compatibilidade direta com Microsoft Excel em PT-BR
    response.write('\ufeff')

    writer = csv.writer(response)
    writer.writerow(
        [
            'enunciado',
            'tipo',
            'disciplina',
            'topico',
            'dificuldade',
            'tags',
            'alt_a',
            'alt_b',
            'alt_c',
            'alt_d',
            'alt_e',
            'correta',
            'criterios_correcao',
        ]
    )

    base_qs = (
        Questao.objects.select_related('disciplina', 'topico')
        .prefetch_related('tags', 'alternativas')
        .order_by('-criado_em')
    )

    # Aplica filtros se houver parâmetros GET
    if request.GET:
        questao_filter = QuestaoFilter(request.GET, queryset=base_qs)
        questoes = questao_filter.qs
    else:
        questoes = base_qs

    if questoes.exists():
        for q in questoes:
            alt_map = {alt.letra: alt.texto for alt in q.alternativas.all()}
            correta_letra = next((alt.letra for alt in q.alternativas.all() if alt.correta), '')
            tags_str = ';'.join(tag.name for tag in q.tags.all())

            writer.writerow(
                [
                    q.enunciado,
                    q.tipo,
                    q.disciplina.nome,
                    q.topico.nome if q.topico else '',
                    q.dificuldade,
                    tags_str,
                    alt_map.get('A', ''),
                    alt_map.get('B', ''),
                    alt_map.get('C', ''),
                    alt_map.get('D', ''),
                    alt_map.get('E', ''),
                    correta_letra,
                    q.criterios_correcao or '',
                ]
            )
    else:
        # Exemplo modelo baseado na grade da FAETERJ
        writer.writerow(
            [
                'Em uma árvore binária de busca (BST), qual é a complexidade temporal média para busca?',
                'OBJ',
                'Estrutura de Dados (ESD)',
                'Árvores Binárias',
                'M',
                'arvores;bst;complexidade',
                'O(1)',
                'O(log n)',
                'O(n)',
                'O(n log n)',
                'O(n^2)',
                'B',
                '',
            ]
        )
        writer.writerow(
            [
                'Explique o funcionamento do protocolo HTTP e diferencie os métodos GET e POST.',
                'DIS',
                'Construção de Aplicações WEB (CAW)',
                'Protocolo HTTP',
                'F',
                'web;http;get;post',
                '',
                '',
                '',
                '',
                '',
                '',
                'O aluno deve conceituar requisição/resposta stateless, indicar que GET trafega dados na URL/idempotente e POST no corpo da requisição/criação de recursos.',
            ]
        )

    return response


# ---------------------------------------------------------------------------
# Importação em Lote via CSV & Commit (RF02)
# ---------------------------------------------------------------------------
@login_required
@permission_required('banco_questoes.add_questao', raise_exception=True)
def questao_import(request):
    """View para upload, commit e relatório de inconsistências linha a linha."""
    relatorio = None

    if request.method == 'POST':
        csv_file = request.FILES.get('csv_file')
        if not csv_file:
            messages.error(request, 'Selecione um arquivo CSV para realizar a importação.')
            return render(request, 'banco_questoes/questao_import.html', {'titulo': 'Importação em Lote'})

        if not csv_file.name.lower().endswith('.csv'):
            messages.error(request, 'Formato inválido. O arquivo deve possuir a extensão .csv.')
            return render(request, 'banco_questoes/questao_import.html', {'titulo': 'Importação em Lote'})

        try:
            relatorio = CSVImportService.import_csv(csv_file, autor=request.user)
        except CSVParseError as err:
            logger.warning('Erro de formato CSV durante importação: %s', err)
            messages.error(request, f'Erro ao ler o arquivo CSV: {err}')
            return render(request, 'banco_questoes/questao_import.html', {'titulo': 'Importação em Lote'})
        except Exception as exc:
            logger.exception('Erro inesperado durante importação de CSV: %s', exc)
            messages.error(request, f'Ocorreu um erro interno durante o processamento: {exc}')
            return render(request, 'banco_questoes/questao_import.html', {'titulo': 'Importação em Lote'})

        if relatorio.total_processadas == 0:
            messages.warning(request, 'O arquivo CSV enviado não contém linhas de dados para importação.')
            return render(request, 'banco_questoes/questao_import.html', {'titulo': 'Importação em Lote'})

        if relatorio.total_sucesso > 0:
            messages.success(
                request,
                f'Importação concluída: {relatorio.total_sucesso} questão(ões) importada(s) e comitadas no acervo.',
            )
        if relatorio.total_rejeitadas > 0:
            messages.warning(
                request,
                f'Atenção: {relatorio.total_rejeitadas} questão(ões) foram rejeitadas devido a inconsistências/duplicidade.',
            )

    return render(
        request,
        'banco_questoes/questao_import.html',
        {
            'relatorio': relatorio,
            'titulo': 'Importação em Lote de Questões',
        },
    )
