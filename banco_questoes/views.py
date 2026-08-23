import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from .forms import AlternativaFormSet, QuestaoForm
from .models import Questao

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _save_questao_and_alternativas(request, form, formset):
    """Salva questão e alternativas em sequência, vinculando o formset à instância."""
    questao = form.save()
    formset.instance = questao
    formset.save()
    logger.info('Questão %s salva por %s', questao.pk, request.user)
    return questao


# ---------------------------------------------------------------------------
# Criar questão
# ---------------------------------------------------------------------------
@login_required
def questao_create(request):
    """Cadastro de nova questão com alternativas inline."""
    if request.method == 'POST':
        form = QuestaoForm(request.POST)
        formset = AlternativaFormSet(request.POST)

        form_ok = form.is_valid()
        formset_ok = formset.is_valid()
        cross_ok = form.validate_with_alternativas(formset) if form_ok and formset_ok else False

        if form_ok and formset_ok and cross_ok:
            questao = _save_questao_and_alternativas(request, form, formset)
            messages.success(request, f'Questão Q{questao.pk} cadastrada com sucesso.')
            return redirect('banco_questoes:questao_update', pk=questao.pk)
    else:
        form = QuestaoForm()
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
# Editar questão
# ---------------------------------------------------------------------------
@login_required
def questao_update(request, pk):
    """Edição de questão existente com alternativas inline."""
    questao = get_object_or_404(Questao, pk=pk)

    if request.method == 'POST':
        form = QuestaoForm(request.POST, instance=questao)
        formset = AlternativaFormSet(request.POST, instance=questao)

        form_ok = form.is_valid()
        formset_ok = formset.is_valid()
        cross_ok = form.validate_with_alternativas(formset) if form_ok and formset_ok else False

        if form_ok and formset_ok and cross_ok:
            _save_questao_and_alternativas(request, form, formset)
            messages.success(request, 'Questão atualizada com sucesso.')
            return redirect('banco_questoes:questao_update', pk=questao.pk)
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
