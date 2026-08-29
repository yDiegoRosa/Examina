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

from .forms import MascaraProvaForm, MascaraTopicoFormSet, ProvaManualForm
from .models import MascaraProva, Prova, ProvaQuestao
from .services.manual_generator import ProvaManualError, criar_prova_manual
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
        mascara.provas_geradas.select_related('autor')
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
# Montagem 100% Manual de Prova (Seleção Livre no Acervo)
# ---------------------------------------------------------------------------
@login_required
def prova_manual_create(request):
    """
    Interface para montagem 100% manual de avaliações (seleção livre no acervo).
    Permite escolher disciplina, filtrar questões e selecionar exatamente quais questões
    comporão o exame, com suporte a máscara guia para validação de meta quantitativa.
    """
    mascara_id = request.GET.get('mascara') or request.POST.get('mascara')
    mascara_guia = None

    if mascara_id:
        try:
            mascara_guia = MascaraProva.objects.select_related('disciplina').get(pk=mascara_id, ativo=True)
            disciplina_selecionada = mascara_guia.disciplina
        except (MascaraProva.DoesNotExist, ValueError):
            mascara_guia = None
            disciplina_selecionada = None
    else:
        disciplina_id = request.GET.get('disciplina') or request.POST.get('disciplina')
        disciplina_selecionada = None
        if disciplina_id:
            try:
                disciplina_selecionada = Disciplina.objects.get(pk=disciplina_id, ativo=True)
            except (Disciplina.DoesNotExist, ValueError):
                disciplina_selecionada = None

    questoes_disponiveis = []
    topicos_disciplina = []

    if disciplina_selecionada:
        topicos_disciplina = list(Topico.objects.filter(disciplina=disciplina_selecionada, ativo=True).order_by('nome'))
        qs = (
            Questao.objects.filter(disciplina=disciplina_selecionada, ativo=True)
            .select_related('topico', 'disciplina')
            .prefetch_related('alternativas')
            .order_by('topico__nome', 'dificuldade', 'pk')
        )

        # Filtros adicionais via GET
        topico_filtro = request.GET.get('topico')
        if topico_filtro:
            qs = qs.filter(topico_id=topico_filtro)

        dificuldade_filtro = request.GET.get('dificuldade')
        if dificuldade_filtro:
            qs = qs.filter(dificuldade=dificuldade_filtro)

        tipo_filtro = request.GET.get('tipo')
        if tipo_filtro:
            qs = qs.filter(tipo=tipo_filtro)

        busca = request.GET.get('busca')
        if busca:
            qs = qs.filter(Q(enunciado__icontains=busca) | Q(origem__icontains=busca))

        questoes_disponiveis = list(qs)

    if request.method == 'POST':
        form = ProvaManualForm(request.POST)
        questoes_selecionadas_ids = request.POST.getlist('questoes_selecionadas')

        if form.is_valid():
            if not questoes_selecionadas_ids:
                messages.error(request, 'A avaliação não pode ficar vazia. Selecione ao menos uma questão no acervo.')
            else:
                try:
                    ids_int = [int(qid) for qid in questoes_selecionadas_ids if qid.isdigit()]
                    disciplina = form.cleaned_data['disciplina']
                    titulo = form.cleaned_data['titulo']
                    numero_caderno = form.cleaned_data['numero_caderno']
                    mascara_selecionada = form.cleaned_data.get('mascara') or mascara_guia

                    prova = criar_prova_manual(
                        titulo=titulo,
                        disciplina=disciplina,
                        questoes_ids=ids_int,
                        autor=request.user,
                        numero_caderno=numero_caderno,
                        mascara=mascara_selecionada,
                    )
                    messages.success(
                        request,
                        f'Avaliação manual "{prova.titulo}" criada com sucesso contendo {prova.itens.count()} questões!',
                    )
                    return redirect('provas:prova_revisao', pk=prova.pk)
                except ProvaManualError as err:
                    err_msg = err.messages[0] if hasattr(err, 'messages') and err.messages else str(err)
                    messages.error(request, f'Erro na validação da avaliação: {err_msg}')
    else:
        initial_data = {}
        if disciplina_selecionada:
            initial_data['disciplina'] = disciplina_selecionada
        if mascara_guia:
            initial_data['mascara'] = mascara_guia
            initial_data['titulo'] = f'{mascara_guia.titulo} (Manual)'
        form = ProvaManualForm(initial=initial_data)

    return render(
        request,
        'provas/prova_manual_form.html',
        {
            'form': form,
            'mascara_guia': mascara_guia,
            'disciplina_selecionada': disciplina_selecionada,
            'topicos_disciplina': topicos_disciplina,
            'questoes_disponiveis': questoes_disponiveis,
            'total_disponiveis': len(questoes_disponiveis),
        },
    )


# ---------------------------------------------------------------------------
# Revisão da Seleção — Meta vs. Atual (Tópico × Dificuldade)
# ---------------------------------------------------------------------------
@login_required
def prova_revisao(request, pk):
    """Exibe a tela de revisão da seleção com indicadores meta vs. atual por célula."""
    prova = get_object_or_404(
        Prova.objects.select_related('mascara', 'mascara__disciplina', 'disciplina', 'autor'),
        pk=pk,
    )
    itens = (
        prova.itens.select_related('questao', 'questao__topico', 'questao__disciplina')
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
        comparativo_celulas.append(
            {
                'topico_id': topico_id,
                'topico_nome': topico_nomes.get(topico_id, 'Sem tópico'),
                'dificuldade': dif,
                'dificuldade_display': dif_display_map.get(dif, dif),
                'meta': meta,
                'atual': atual,
                'status': 'ok' if atual == meta else 'divergente',
            }
        )

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
            topicos_ordenados.append(
                {
                    'topico_id': tid,
                    'topico_nome': c['topico_nome'],
                    'meta': meta_t,
                    'atual': atual_t,
                    'status': 'ok' if atual_t == meta_t else 'divergente',
                }
            )

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
        linhas_comparativo.append(
            {
                'topico_nome': top_info['topico_nome'],
                'celulas': celulas_linha,
                'total_meta': top_info['meta'],
                'total_atual': top_info['atual'],
                'status': top_info['status'],
            }
        )

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
        ProvaQuestao.objects.select_related(
            'prova', 'prova__mascara', 'questao', 'questao__topico', 'questao__disciplina'
        ),
        pk=item_pk,
    )
    candidatas_qs = obter_candidatas_substituicao(item)

    candidatas_data = []
    for q in candidatas_qs:
        alternativas = [
            {'letra': alt.letra, 'texto': alt.texto, 'correta': alt.correta} for alt in q.alternativas.all()
        ]
        candidatas_data.append(
            {
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
            }
        )

    questao_atual = item.questao
    return JsonResponse(
        {
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
        }
    )


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

        is_ajax = request.headers.get(
            'x-requested-with'
        ) == 'XMLHttpRequest' or 'application/json' in request.headers.get('Accept', '')
        if is_ajax:
            return JsonResponse({'success': True, 'message': msg})

        messages.success(request, msg)
        return redirect('provas:prova_revisao', pk=item.prova_id)
    except SubstituicaoQuestaoError as err:
        error_msg = str(err)
        is_ajax = request.headers.get(
            'x-requested-with'
        ) == 'XMLHttpRequest' or 'application/json' in request.headers.get('Accept', '')
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


# ---------------------------------------------------------------------------
# Geração do PDF da Capa Padronizada da Prova (Módulo 3)
# ---------------------------------------------------------------------------
@login_required
def prova_capa_pdf(request, pk):
    """
    Gera o PDF da capa padronizada de uma prova gerada.

    Renderiza o template `pdf_capa_prova.html` via WeasyPrint com todos os
    metadados institucionais da avaliação. O placeholder do QR Code fica
    reservado para integração futura pelo Módulo 3.
    """
    prova = get_object_or_404(
        Prova.objects.select_related(
            'mascara',
            'mascara__disciplina',
            'disciplina',
            'autor',
        ),
        pk=pk,
    )

    # Disciplina efetiva (direta na prova ou via máscara)
    disciplina = prova.disciplina_efetiva

    # Parâmetros da máscara (se existir snapshot ou objeto vivo)
    mascara = prova.mascara
    snap = prova.snapshot_mascara or {}

    valor_total = None
    tempo_minutos = None
    modalidade = None

    if mascara:
        valor_total = mascara.valor_total
        tempo_minutos = mascara.tempo_estimado_minutos
        modalidade = mascara.get_modalidade_display()
    elif snap:
        valor_total = snap.get('valor_total')
        tempo_minutos = snap.get('tempo_estimado_minutos')
        modalidade_code = snap.get('modalidade', '')
        # Mapa legível
        _modalidade_map = {
            'OBJ': 'Somente Objetivas',
            'DIS': 'Somente Discursivas',
            'MISTA': 'Mista (Objetivas e Discursivas)',
        }
        modalidade = _modalidade_map.get(modalidade_code, modalidade_code)

    # Nome do docente
    docente_nome = None
    if prova.autor:
        docente_nome = prova.autor.get_full_name() or prova.autor.username

    # Total de questões da prova
    total_questoes = prova.itens.count()

    context = {
        'prova': prova,
        'disciplina_nome': disciplina.nome if disciplina else None,
        'docente_nome': docente_nome,
        'turma': snap.get('turma') or request.GET.get('turma') or '',
        'turno': snap.get('turno') or request.GET.get('turno') or '',
        'data_aplicacao': request.GET.get('data') or '',
        'valor_total': valor_total,
        'tempo_minutos': tempo_minutos,
        'modalidade': modalidade,
        'total_questoes': total_questoes,
        'instrucoes_extras': request.GET.get('instrucoes_extras') or '',
    }

    try:
        pdf_bytes = PDFGeneratorService.render_pdf_from_template(
            template_name='provas/pdf_capa_prova.html',
            context=context,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error('Erro ao gerar capa PDF da prova pk=%s: %s', pk, exc, exc_info=True)
        return HttpResponse(
            f'Erro ao gerar o PDF da capa: {exc}',
            status=500,
            content_type='text/plain; charset=utf-8',
        )

    filename = f'capa_prova_{prova.pk}_caderno{prova.numero_caderno}.pdf'
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    return response


# ---------------------------------------------------------------------------
# Geração do PDF do Caderno de Prova / Prova Completa (Formatação de Questões)
# ---------------------------------------------------------------------------
@login_required
def prova_pdf(request, pk):
    """
    Gera o PDF formatado do Caderno de Prova com todas as questões.

    Suporta parâmetros query opcionais:
    - `incluir_capa`: '1' (padrão se não informado ou '0' para somente questões)
    - `incluir_gabarito`: '1' para anexar a página de espelho de gabarito e critérios
    - `data`: data de aplicação customizada
    - `turma`: identificação de turma
    - `turno`: turno da aplicação
    - `instrucoes_extras`: orientações adicionais aos estudantes
    """
    prova = get_object_or_404(
        Prova.objects.select_related(
            'mascara',
            'mascara__disciplina',
            'disciplina',
            'autor',
        ),
        pk=pk,
    )

    itens = (
        prova.itens.select_related('questao', 'questao__topico', 'questao__disciplina')
        .prefetch_related('questao__alternativas')
        .order_by('ordem')
    )

    disciplina = prova.disciplina_efetiva
    mascara = prova.mascara
    snap = prova.snapshot_mascara or {}

    valor_total = None
    tempo_minutos = None
    modalidade = None

    if mascara:
        valor_total = mascara.valor_total
        tempo_minutos = mascara.tempo_estimado_minutos
        modalidade = mascara.get_modalidade_display()
    elif snap:
        valor_total = snap.get('valor_total')
        tempo_minutos = snap.get('tempo_estimado_minutos')
        modalidade_code = snap.get('modalidade', '')
        _modalidade_map = {
            'OBJ': 'Somente Objetivas',
            'DIS': 'Somente Discursivas',
            'MISTA': 'Mista (Objetivas e Discursivas)',
        }
        modalidade = _modalidade_map.get(modalidade_code, modalidade_code)

    docente_nome = None
    if prova.autor:
        docente_nome = prova.autor.get_full_name() or prova.autor.username

    # Parâmetros de customização via GET
    # Por padrão, gera a avaliação completa com capa
    param_capa = request.GET.get('incluir_capa', '1')
    incluir_capa = param_capa in ('1', 'true', 'True')

    param_gabarito = request.GET.get('incluir_gabarito', '0')
    incluir_gabarito = param_gabarito in ('1', 'true', 'True')

    context = {
        'prova': prova,
        'itens': itens,
        'disciplina_nome': disciplina.nome if disciplina else None,
        'docente_nome': docente_nome,
        'turma': snap.get('turma') or request.GET.get('turma') or '',
        'turno': snap.get('turno') or request.GET.get('turno') or '',
        'data_aplicacao': request.GET.get('data') or '',
        'valor_total': valor_total,
        'tempo_minutos': tempo_minutos,
        'modalidade': modalidade,
        'total_questoes': itens.count(),
        'instrucoes_extras': request.GET.get('instrucoes_extras') or '',
        'incluir_capa': incluir_capa,
        'incluir_gabarito': incluir_gabarito,
    }

    try:
        pdf_bytes = PDFGeneratorService.render_pdf_from_template(
            template_name='provas/pdf_prova_completa.html',
            context=context,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error('Erro ao gerar PDF da prova pk=%s: %s', pk, exc, exc_info=True)
        return HttpResponse(
            f'Erro ao gerar o PDF da avaliação: {exc}',
            status=500,
            content_type='text/plain; charset=utf-8',
        )

    tipo_str = 'completa' if incluir_capa else 'questoes'
    if incluir_gabarito:
        tipo_str += '_com_gabarito'

    filename = f'prova_{prova.pk}_caderno{prova.numero_caderno}_{tipo_str}.pdf'
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    return response
