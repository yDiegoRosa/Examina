import csv
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from .forms import AlternativaFormSet, QuestaoForm
from .models import Questao

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lista de questões
# ---------------------------------------------------------------------------
@login_required
def questao_list(request):
    """Lista de questões com filtros básicos."""
    qs = Questao.objects.select_related('disciplina', 'topico')

    # Filtros via query string
    tipo = request.GET.get('tipo')
    if tipo:
        qs = qs.filter(tipo=tipo)

    dificuldade = request.GET.get('dificuldade')
    if dificuldade:
        qs = qs.filter(dificuldade=dificuldade)

    disciplina_id = request.GET.get('disciplina')
    if disciplina_id:
        qs = qs.filter(disciplina_id=disciplina_id)

    ativo = request.GET.get('ativo')
    if ativo in ('1', '0'):
        qs = qs.filter(ativo=ativo == '1')

    busca = request.GET.get('q', '').strip()
    if busca:
        qs = qs.filter(enunciado__icontains=busca)

    return render(
        request,
        'banco_questoes/questao_list.html',
        {
            'questoes': qs,
            'filtros': {
                'tipo': tipo or '',
                'dificuldade': dificuldade or '',
                'disciplina': disciplina_id or '',
                'ativo': ativo or '',
                'q': busca,
            },
            'tipos': Questao.Tipo.choices,
            'dificuldades': Questao.Dificuldade.choices,
        },
    )


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
@permission_required('banco_questoes.add_questao', raise_exception=True)
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
            return redirect('banco_questoes:questao_list')
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
@permission_required('banco_questoes.change_questao', raise_exception=True)
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
            return redirect('banco_questoes:questao_list')
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
# Download do CSV Modelo
# ---------------------------------------------------------------------------
@login_required
def download_modelo_csv(_request):
    """Gera e faz o download do arquivo CSV modelo de importação de questões."""
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="modelo_importacao_questoes.csv"'

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
    writer.writerow(
        [
            'Qual é o valor de x na equação 2x + 6 = 14?',
            'OBJ',
            'Matemática',
            'Álgebra',
            'F',
            'enem;algebra;equacoes',
            'x = 3',
            'x = 4',
            'x = 5',
            'x = 6',
            'x = 7',
            'B',
            '',
        ]
    )
    writer.writerow(
        [
            'Explique o processo de fotossíntese destacando a fase clara e a fase escura.',
            'DIS',
            'Biologia',
            'Botânica',
            'M',
            'biologia;fotossintese;fisiologia',
            '',
            '',
            '',
            '',
            '',
            '',
            'O aluno deve mencionar a absorção de luz pelos pigmentos fotossintetizantes na fase clara (tilacóides) e a fixação de CO2 no Ciclo de Calvin na fase escura (estroma).',
        ]
    )

    return response
