import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from banco_questoes.models import Disciplina, Questao, Topico

from .forms import MascaraProvaForm, MascaraTopicoFormSet
from .models import MascaraProva, Prova, ProvaQuestao
from .services.pdf_generator import PDFGeneratorService
from .services.question_replacer import (
    SubstituicaoQuestaoError,
    obter_candidatas_substituicao,
    substituir_questao_prova,
)
from .services.stock_validator import EstoqueInsuficienteError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Listagem de Máscaras (Blueprints)
# ---------------------------------------------------------------------------
@login_required
def mascara_list(request):
    """Exibe o painel de Máscaras de Prova cadastradas com métricas, filtros e paginação."""
    mascaras_qs = (
        MascaraProva.objects.select_related('disciplina', 'autor')
        .prefetch_related('topicos_distribuicao__topico')
        .all()
    )

    disciplina_id = request.GET.get('disciplina')
    if disciplina_id:
        mascaras_qs = mascaras_qs.filter(disciplina_id=disciplina_id)

    disciplinas = Disciplina.objects.filter(ativo=True)

    metricas = {
        'total': mascaras_qs.count(),
        'ativas': mascaras_qs.filter(ativo=True).count(),
        'inativas': mascaras_qs.filter(ativo=False).count(),
    }

    # Paginação (10 itens por página)
    paginator = Paginator(mascaras_qs, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    page_range = paginator.get_elided_page_range(page_obj.number, on_each_side=2, on_ends=1)

    # Query string preservando filtros para navegação entre páginas
    get_params = request.GET.copy()
    get_params.pop('page', None)
    querystring = get_params.urlencode()

    return render(
        request,
        'provas/mascara_list.html',
        {
            'mascaras': page_obj,
            'page_obj': page_obj,
            'page_range': page_range,
            'querystring': querystring,
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
        disc_id = request.POST.get('disciplina')
        disciplina_post = Disciplina.objects.filter(pk=disc_id, ativo=True).first() if disc_id else None
        formset = MascaraTopicoFormSet(request.POST, disciplina=disciplina_post)

        if form.is_valid():
            disciplina = form.cleaned_data['disciplina']
            mascara_temp = form.save(commit=False)
            formset = MascaraTopicoFormSet(
                request.POST,
                instance=mascara_temp,
                disciplina=disciplina,
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
        disc_param = request.GET.get('disciplina')
        disciplina_init = Disciplina.objects.filter(pk=disc_param, ativo=True).first() if disc_param else None
        form = MascaraProvaForm(initial={'disciplina': disciplina_init} if disciplina_init else None)
        formset = MascaraTopicoFormSet(disciplina=disciplina_init)

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
        disc_id = request.POST.get('disciplina')
        disciplina_post = Disciplina.objects.filter(pk=disc_id, ativo=True).first() if disc_id else mascara.disciplina
        formset = MascaraTopicoFormSet(
            request.POST,
            instance=mascara,
            disciplina=disciplina_post,
        )

        if form.is_valid():
            disciplina = form.cleaned_data['disciplina']
            formset = MascaraTopicoFormSet(
                request.POST,
                instance=mascara,
                disciplina=disciplina,
            )

            if formset.is_valid():
                with transaction.atomic():
                    mascara = form.save()
                    formset.save()

                messages.success(request, f'Máscara "{mascara.titulo}" atualizada com sucesso!')
                return redirect('provas:mascara_detail', pk=mascara.pk)
    else:
        form = MascaraProvaForm(instance=mascara)
        formset = MascaraTopicoFormSet(
            instance=mascara,
            disciplina=mascara.disciplina,
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

    matriz_metas = mascara.calcular_matriz_metas()
    relatorio_estoque = mascara.validar_estoque()

    # Últimas provas geradas a partir desta máscara
    provas_recentes = (
        mascara.provas_geradas
        .select_related('autor')
        .annotate(total_questoes=Count('itens'))
        .order_by('-criado_em')[:5]
    )

    return render(
        request,
        'provas/mascara_detail.html',
        {
            'mascara': mascara,
            'distribuicao_dif': distribuicao_dif,
            'matriz_metas': matriz_metas,
            'diagnostico': diagnostico,
            'relatorio_estoque': relatorio_estoque,
            'provas_recentes': provas_recentes,
        },
    )


# ---------------------------------------------------------------------------
# Geração Automatizada de Prova (com Bloqueio por Insuficiência)
# ---------------------------------------------------------------------------
@require_POST
@login_required
def mascara_gerar_prova(request, pk):
    """
    Executa a geração e persistência de uma avaliação a partir da máscara.
    Bloqueia a operação e notifica detalhadamente caso haja déficit de estoque no banco.
    """
    mascara = get_object_or_404(MascaraProva, pk=pk)

    try:
        prova = mascara.gerar_prova(autor=request.user)
        messages.success(
            request,
            f'Avaliação "{prova.titulo}" gerada com sucesso contendo {prova.itens.count()} questões selecionadas!',
        )
        return redirect('provas:prova_revisao', pk=prova.pk)
    except EstoqueInsuficienteError as err:
        messages.error(
            request,
            f'⚠️ Geração bloqueada: {err.mensagem}',
        )
        return redirect('provas:mascara_detail', pk=mascara.pk)


# ---------------------------------------------------------------------------
# Revisão da Seleção — Meta vs. Atual (Tópico × Dificuldade)
# ---------------------------------------------------------------------------
@login_required
def prova_revisao(request, pk):
    """Exibe a tela de revisão da seleção com indicadores meta vs. atual por célula."""
    prova = get_object_or_404(
        Prova.objects.select_related('mascara', 'mascara__disciplina', 'autor'),
        pk=pk,
    )
    itens = (
        prova.itens
        .select_related('questao', 'questao__topico', 'questao__disciplina')
        .prefetch_related('questao__alternativas')
        .order_by('ordem')
    )

    # --- Metas a partir dos snapshots JSON (instante da geração) ---
    snap_matriz = prova.snapshot_matriz_metas or {}
    snap_celulas = snap_matriz.get('celulas', [])

    # Indexar metas por (topico_id, dificuldade)
    metas_por_celula = {}
    for cel in snap_celulas:
        chave = (cel.get('topico_id'), cel.get('dificuldade'))
        metas_por_celula[chave] = cel.get('quantidade', 0)

    # --- Distribuição real das questões selecionadas ---
    atual_por_celula = {}
    for item in itens:
        q = item.questao
        topico_id = q.topico_id
        dif = q.dificuldade
        chave = (topico_id, dif)
        atual_por_celula[chave] = atual_por_celula.get(chave, 0) + 1

    # --- Construção do comparativo por célula ---
    todas_chaves = set(metas_por_celula.keys()) | set(atual_por_celula.keys())

    # Mapas de nomes
    topico_nomes = {}
    for cel in snap_celulas:
        topico_nomes[cel.get('topico_id')] = cel.get('topico_nome', 'Sem tópico')
    for item in itens:
        q = item.questao
        if q.topico_id and q.topico_id not in topico_nomes:
            topico_nomes[q.topico_id] = q.topico.nome if q.topico else 'Sem tópico'

    dif_display_map = {'F': 'Fácil', 'M': 'Médio', 'D': 'Difícil'}

    comparativo_celulas = []
    for chave in sorted(todas_chaves, key=lambda c: (c[0] or 0, c[1] or '')):
        topico_id, dif = chave
        meta = metas_por_celula.get(chave, 0)
        atual = atual_por_celula.get(chave, 0)
        comparativo_celulas.append({
            'topico_id': topico_id,
            'topico_nome': topico_nomes.get(topico_id, 'Sem tópico'),
            'dificuldade': dif,
            'dificuldade_display': dif_display_map.get(dif, dif),
            'meta': meta,
            'atual': atual,
            'status': 'ok' if atual == meta else 'divergente',
        })

    # --- Resumo por dificuldade ---
    resumo_dificuldade = {}
    for d_code in ['F', 'M', 'D']:
        meta_d = sum(c['meta'] for c in comparativo_celulas if c['dificuldade'] == d_code)
        atual_d = sum(c['atual'] for c in comparativo_celulas if c['dificuldade'] == d_code)
        resumo_dificuldade[d_code] = {
            'display': dif_display_map[d_code],
            'meta': meta_d,
            'atual': atual_d,
            'status': 'ok' if atual_d == meta_d else 'divergente',
        }

    # --- Resumo por tópico ---
    topicos_ordenados = []
    topicos_vistos = set()
    for c in comparativo_celulas:
        tid = c['topico_id']
        if tid not in topicos_vistos:
            topicos_vistos.add(tid)
            meta_t = sum(x['meta'] for x in comparativo_celulas if x['topico_id'] == tid)
            atual_t = sum(x['atual'] for x in comparativo_celulas if x['topico_id'] == tid)
            topicos_ordenados.append({
                'topico_id': tid,
                'topico_nome': c['topico_nome'],
                'meta': meta_t,
                'atual': atual_t,
                'status': 'ok' if atual_t == meta_t else 'divergente',
            })

    total_meta = snap_matriz.get('total_questoes', 0)
    total_atual = itens.count()

    # Estruturar comparativo em formato de linhas para a tabela
    linhas_comparativo = []
    for top_info in topicos_ordenados:
        tid = top_info['topico_id']
        celulas_linha = {}
        for d_code in ['F', 'M', 'D']:
            match = [c for c in comparativo_celulas if c['topico_id'] == tid and c['dificuldade'] == d_code]
            if match:
                celulas_linha[d_code] = match[0]
            else:
                celulas_linha[d_code] = {'meta': 0, 'atual': 0, 'status': 'ok'}
        linhas_comparativo.append({
            'topico_nome': top_info['topico_nome'],
            'celulas': celulas_linha,
            'total_meta': top_info['meta'],
            'total_atual': top_info['atual'],
            'status': top_info['status'],
        })

    return render(
        request,
        'provas/prova_revisao.html',
        {
            'prova': prova,
            'mascara': prova.mascara,
            'itens': itens,
            'comparativo_celulas': comparativo_celulas,
            'linhas_comparativo': linhas_comparativo,
            'resumo_dificuldade': resumo_dificuldade,
            'resumo_topicos': topicos_ordenados,
            'total_meta': total_meta,
            'total_atual': total_atual,
        },
    )


# ---------------------------------------------------------------------------
# Troca Individual de Questão (Ajuste Fino na Tela de Revisão)
# ---------------------------------------------------------------------------
@login_required
def item_candidatas_substituicao(request, item_pk):
    """
    Retorna JSON com a lista de questões candidatas para substituir a questão de um item da prova.
    Filtra estritamente pelo mesmo tópico e dificuldade, ordenando por prioridade RF07.
    """
    item = get_object_or_404(
        ProvaQuestao.objects.select_related('prova', 'prova__mascara', 'questao', 'questao__topico', 'questao__disciplina'),
        pk=item_pk,
    )
    candidatas_qs = obter_candidatas_substituicao(item)

    candidatas_data = []
    for q in candidatas_qs:
        alternativas = [
            {'letra': alt.letra, 'texto': alt.texto, 'correta': alt.correta}
            for alt in q.alternativas.all()
        ]
        candidatas_data.append({
            'id': q.pk,
            'enunciado': q.enunciado,
            'dificuldade': q.dificuldade,
            'dificuldade_display': q.get_dificuldade_display(),
            'tipo': q.tipo,
            'tipo_display': q.get_tipo_display(),
            'topico_id': q.topico_id,
            'topico_nome': q.topico.nome if q.topico else 'Sem tópico',
            'origem': q.origem or '',
            'numero_questao': q.numero_questao or '',
            'vezes_usada_recentemente': getattr(q, 'vezes_usada_recentemente', 0),
            'is_enade': bool(getattr(q, 'is_enade', 0)),
            'alternativas': alternativas,
        })

    questao_atual = item.questao
    return JsonResponse({
        'item_id': item.pk,
        'item_numero': item.numero,
        'questao_atual': {
            'id': questao_atual.pk,
            'enunciado': questao_atual.enunciado,
            'dificuldade': questao_atual.dificuldade,
            'dificuldade_display': questao_atual.get_dificuldade_display(),
            'topico_nome': questao_atual.topico.nome if questao_atual.topico else 'Sem tópico',
            'origem': questao_atual.origem or '',
        },
        'total_candidatas': len(candidatas_data),
        'candidatas': candidatas_data,
    })


@require_POST
@login_required
def item_substituir_questao(request, item_pk):
    """
    Substitui a questão de um item da prova por uma nova questão selecionada,
    validando integridade da célula e atualizando os snapshots JSON.
    """
    item = get_object_or_404(
        ProvaQuestao.objects.select_related('prova', 'prova__mascara', 'questao'),
        pk=item_pk,
    )
    nova_questao_id = request.POST.get('nova_questao_id')
    if not nova_questao_id:
        return JsonResponse({'success': False, 'error': 'ID da nova questão é obrigatório.'}, status=400)

    nova_questao = get_object_or_404(Questao, pk=nova_questao_id)

    try:
        substituir_questao_prova(item=item, nova_questao=nova_questao, autor=request.user)
        msg = f'Questão #{item.numero} substituída com sucesso pela questão Q{nova_questao.pk}!'

        is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest' or 'application/json' in request.headers.get('Accept', '')
        if is_ajax:
            return JsonResponse({'success': True, 'message': msg})

        messages.success(request, msg)
        return redirect('provas:prova_revisao', pk=item.prova_id)
    except SubstituicaoQuestaoError as err:
        error_msg = str(err)
        is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest' or 'application/json' in request.headers.get('Accept', '')
        if is_ajax:
            return JsonResponse({'success': False, 'error': error_msg}, status=400)

        messages.error(request, f'Erro na substituição: {error_msg}')
        return redirect('provas:prova_revisao', pk=item.prova_id)


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
