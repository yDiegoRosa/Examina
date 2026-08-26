import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from banco_questoes.models import Disciplina, Questao, Topico

from .forms import MascaraProvaForm, MascaraTopicoFormSet
from .models import MascaraProva
from .services.pdf_generator import PDFGeneratorService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Listagem de Máscaras (Blueprints)
# ---------------------------------------------------------------------------
@login_required
def mascara_list(request):
    """Exibe o painel de Máscaras de Prova cadastradas com métricas e filtros."""
    mascaras = (
        MascaraProva.objects.select_related('disciplina', 'autor')
        .prefetch_related('topicos_distribuicao__topico')
        .all()
    )

    disciplina_id = request.GET.get('disciplina')
    if disciplina_id:
        mascaras = mascaras.filter(disciplina_id=disciplina_id)

    disciplinas = Disciplina.objects.filter(ativo=True)

    metricas = {
        'total': mascaras.count(),
        'ativas': mascaras.filter(ativo=True).count(),
        'inativas': mascaras.filter(ativo=False).count(),
    }

    return render(
        request,
        'provas/mascara_list.html',
        {
            'mascaras': mascaras,
            'disciplinas': disciplinas,
            'disciplina_selecionada': disciplina_id,
            'metricas': metricas,
        },
    )


# ---------------------------------------------------------------------------
# Criar Máscara de Prova
# ---------------------------------------------------------------------------
@login_required
def mascara_create(request):
    """Cria uma nova Máscara de Avaliação com matriz de dificuldade e tópicos."""
    if request.method == 'POST':
        form = MascaraProvaForm(request.POST)
        formset = MascaraTopicoFormSet(request.POST)

        if form.is_valid():
            disciplina = form.cleaned_data['disciplina']
            mascara_temp = form.save(commit=False)
            formset = MascaraTopicoFormSet(
                request.POST,
                instance=mascara_temp,
                form_kwargs={'disciplina': disciplina},
            )

            if formset.is_valid():
                with transaction.atomic():
                    mascara_temp.autor = request.user
                    mascara_temp.save()

                    formset.instance = mascara_temp
                    formset.save()

                messages.success(request, f'Máscara "{mascara_temp.titulo}" criada com sucesso!')
                return redirect('provas:mascara_detail', pk=mascara_temp.pk)
    else:
        form = MascaraProvaForm()
        formset = MascaraTopicoFormSet()

    return render(
        request,
        'provas/mascara_form.html',
        {
            'form': form,
            'formset': formset,
            'titulo_pagina': 'Nova Máscara de Avaliação',
        },
    )


# ---------------------------------------------------------------------------
# Editar Máscara de Prova
# ---------------------------------------------------------------------------
@login_required
def mascara_update(request, pk):
    """Edita uma Máscara de Avaliação existente."""
    mascara = get_object_or_404(MascaraProva, pk=pk)

    if request.method == 'POST':
        form = MascaraProvaForm(request.POST, instance=mascara)
        formset = MascaraTopicoFormSet(
            request.POST,
            instance=mascara,
            form_kwargs={'disciplina': mascara.disciplina},
        )

        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                mascara = form.save()
                formset.save()

            messages.success(request, f'Máscara "{mascara.titulo}" atualizada com sucesso!')
            return redirect('provas:mascara_detail', pk=mascara.pk)
    else:
        form = MascaraProvaForm(instance=mascara)
        formset = MascaraTopicoFormSet(
            instance=mascara,
            form_kwargs={'disciplina': mascara.disciplina},
        )

    return render(
        request,
        'provas/mascara_form.html',
        {
            'form': form,
            'formset': formset,
            'mascara': mascara,
            'titulo_pagina': f'Editar Máscara: {mascara.titulo}',
        },
    )


# ---------------------------------------------------------------------------
# Detalhes & Diagnóstico de Estoque da Máscara
# ---------------------------------------------------------------------------
@login_required
def mascara_detail(request, pk):
    """Visualiza a matriz de critérios da máscara e realiza diagnóstico de estoque no acervo."""
    mascara = get_object_or_404(
        MascaraProva.objects.select_related('disciplina', 'autor').prefetch_related('topicos_distribuicao__topico'),
        pk=pk,
    )

    distribuicao_dif = mascara.calcular_distribuicao_dificuldade()

    # Diagnóstico de estoque real no acervo de questões
    questoes_base = Questao.objects.filter(disciplina=mascara.disciplina, ativo=True)

    if mascara.modalidade == MascaraProva.Modalidade.OBJETIVA:
        questoes_base = questoes_base.filter(tipo=Questao.Tipo.OBJETIVA)
    elif mascara.modalidade == MascaraProva.Modalidade.DISCURSIVA:
        questoes_base = questoes_base.filter(tipo=Questao.Tipo.DISCURSIVA)

    total_acervo = questoes_base.count()
    estoque_facil = questoes_base.filter(dificuldade=Questao.Dificuldade.FACIL).count()
    estoque_medio = questoes_base.filter(dificuldade=Questao.Dificuldade.MEDIO).count()
    estoque_dificil = questoes_base.filter(dificuldade=Questao.Dificuldade.DIFICIL).count()

    diagnostico = {
        'total_necessario': mascara.quantidade_questoes,
        'total_disponivel': total_acervo,
        'suficiente': total_acervo >= mascara.quantidade_questoes,
        'facil': {
            'demanda': distribuicao_dif['F'],
            'estoque': estoque_facil,
            'ok': estoque_facil >= distribuicao_dif['F'],
        },
        'medio': {
            'demanda': distribuicao_dif['M'],
            'estoque': estoque_medio,
            'ok': estoque_medio >= distribuicao_dif['M'],
        },
        'dificil': {
            'demanda': distribuicao_dif['D'],
            'estoque': estoque_dificil,
            'ok': estoque_dificil >= distribuicao_dif['D'],
        },
    }

    return render(
        request,
        'provas/mascara_detail.html',
        {
            'mascara': mascara,
            'distribuicao_dif': distribuicao_dif,
            'diagnostico': diagnostico,
        },
    )


# ---------------------------------------------------------------------------
# Alternar Status Ativo / Inativo
# ---------------------------------------------------------------------------
@require_POST
@login_required
def mascara_toggle_ativo(request, pk):
    """Alterna o status ativo/inativo de uma máscara."""
    mascara = get_object_or_404(MascaraProva, pk=pk)
    mascara.ativo = not mascara.ativo
    mascara.save(update_fields=['ativo', 'atualizado_em'])

    status_str = 'ativada' if mascara.ativo else 'desativada'
    messages.info(request, f'Máscara "{mascara.titulo}" {status_str} com sucesso.')
    return redirect('provas:mascara_list')


# ---------------------------------------------------------------------------
# API AJAX: Tópicos e Diagnóstico em Tempo Real por Disciplina
# ---------------------------------------------------------------------------
@login_required
def api_topicos_e_estoque(request):
    """
    Retorna tópicos da disciplina selecionada e contagem de questões
    disponíveis no banco por nível de dificuldade para alimentar a UI em tempo real.
    """
    disciplina_id = request.GET.get('disciplina_id')
    if not disciplina_id:
        return JsonResponse({'error': 'Parâmetro disciplina_id obrigatório.'}, status=400)

    disciplina = get_object_or_404(Disciplina, pk=disciplina_id, ativo=True)
    topicos = Topico.objects.filter(disciplina=disciplina, ativo=True).values('id', 'nome')

    # Contagem de questões no acervo por dificuldade
    questoes = Questao.objects.filter(disciplina=disciplina, ativo=True)
    contagens = questoes.aggregate(
        total=Count('id'),
        facil=Count('id', filter=Q(dificuldade=Questao.Dificuldade.FACIL)),
        medio=Count('id', filter=Q(dificuldade=Questao.Dificuldade.MEDIO)),
        dificil=Count('id', filter=Q(dificuldade=Questao.Dificuldade.DIFICIL)),
        objetivas=Count('id', filter=Q(tipo=Questao.Tipo.OBJETIVA)),
        discursivas=Count('id', filter=Q(tipo=Questao.Tipo.DISCURSIVA)),
    )

    return JsonResponse(
        {
            'disciplina_id': disciplina.pk,
            'disciplina_nome': disciplina.nome,
            'topicos': list(topicos),
            'estoque': contagens,
        }
    )


# ---------------------------------------------------------------------------
# View de Teste PDF
# ---------------------------------------------------------------------------
@login_required
def pdf_teste_view(request):
    """View de demonstração para validação do WeasyPrint."""
    questoes = Questao.objects.filter(ativo=True).prefetch_related('alternativas')[:5]

    context = {
        'titulo': 'Avaliação Modelo • Teste de Impressão WeasyPrint',
        'disciplina': 'Construção de Aplicações Web (CAW)',
        'docente': request.user.get_full_name() or request.user.username,
        'turma': 'ADS-2026.2',
        'turno': 'Noite',
        'data': '26/08/2026',
        'questoes': questoes,
        'incluir_gabarito': True,
    }

    pdf_bytes = PDFGeneratorService.render_pdf_from_template(
        'provas/pdf_template_teste.html',
        context=context,
    )

    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = 'inline; filename="avaliacao_teste_faeterj.pdf"'
    return response
