import contextlib
import logging
import uuid
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from academico.models import Matricula, Turma
from banco_questoes.models import Disciplina, Questao, Topico
from banco_questoes.permissions import docente_ou_coordenador_required
from banco_questoes.utils import parse_pk

from .forms import MascaraProvaForm, MascaraTopicoFormSet, ProvaAplicacaoForm, ProvaManualForm, UploadFolhasForm
from .models import (
    FolhaDigitalizada,
    FolhaResposta,
    MascaraCriterioTopico,
    MascaraProva,
    Prova,
    ProvaAplicacao,
    ProvaQuestao,
)
from .presets import construir_distribuicao_topicos_enade, preset_enade_initial
from .services import fiducial
from .services.answer_sheet_layout import build_layout, max_questoes_por_pagina
from .services.caderno_layout import (
    linhas_da_discursiva,
    numerar_por_tipo,
    ordenar_como_caderno,
    organizar_caderno,
)
from .services.caderno_service import contexto_caderno
from .services.folha_service import FOLHA_ALTERNATIVAS, FOLHA_COLUMNS, FOLHA_ID_DIGITS, aviso_discursivas
from .services.manual_generator import ProvaManualError, criar_prova_manual
from .services.pdf_generator import PDFGeneratorService
from .services.question_replacer import (
    SubstituicaoQuestaoError,
    obter_candidatas_substituicao,
    substituir_questao_prova,
)
from .services.stock_validator import EstoqueInsuficienteError
from .services.version_generator import quantidade_versoes_da_prova, tipo_do_item

logger = logging.getLogger(__name__)


@login_required
@docente_ou_coordenador_required
def prova_list(request):
    """Lista as provas persistidas, inclusive montagens manuais sem máscara."""
    provas = Prova.objects.select_related('disciplina', 'mascara__disciplina', 'autor').annotate(
        total_itens=Count('itens', distinct=True), total_versoes=Count('versoes', distinct=True)
    )
    metricas = Prova.objects.aggregate(
        total=Count('pk'),
        manuais=Count('pk', filter=Q(tipo_geracao=Prova.TipoGeracao.MANUAL)),
        automaticas=Count('pk', filter=Q(tipo_geracao=Prova.TipoGeracao.AUTOMATICA)),
    )
    busca = request.GET.get('q', '').strip()
    tipo = request.GET.get('tipo', '')
    disciplina_id = request.GET.get('disciplina', '')
    if busca:
        provas = provas.filter(titulo__icontains=busca)
    if tipo in Prova.TipoGeracao.values:
        provas = provas.filter(tipo_geracao=tipo)
    else:
        tipo = ''
    if disciplina_id.isascii() and disciplina_id.isdigit() and len(disciplina_id) <= 18:
        provas = provas.filter(
            Q(disciplina_id=disciplina_id) | Q(disciplina__isnull=True, mascara__disciplina_id=disciplina_id)
        )
    else:
        disciplina_id = ''
    paginator = Paginator(provas.order_by('-criado_em', '-pk'), 12)
    page_obj = paginator.get_page(request.GET.get('page'))
    params = request.GET.copy()
    params.pop('page', None)
    return render(
        request,
        'provas/prova_list.html',
        {
            'provas': page_obj,
            'page_obj': page_obj,
            'page_range': paginator.get_elided_page_range(page_obj.number, on_each_side=2, on_ends=1),
            'querystring': params.urlencode(),
            'metricas': metricas,
            'busca': busca,
            'tipo_selecionado': tipo,
            'disciplina_selecionada': disciplina_id,
            'disciplinas': Disciplina.objects.order_by('nome'),
        },
    )


# ---------------------------------------------------------------------------
# Listagem de Máscaras (Blueprints)
# ---------------------------------------------------------------------------
@login_required
@docente_ou_coordenador_required
def mascara_list(request):
    """Exibe o painel de Máscaras de Prova cadastradas com métricas, filtros e paginação."""
    mascaras_qs = (
        MascaraProva.objects.select_related('disciplina', 'autor')
        .prefetch_related('topicos_distribuicao__topico')
        .all()
    )

    disciplina_pk = parse_pk(request.GET.get('disciplina'))
    disciplina_id = str(disciplina_pk) if disciplina_pk else None
    if disciplina_pk:
        mascaras_qs = mascaras_qs.filter(disciplina_id=disciplina_pk)

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
@docente_ou_coordenador_required
def mascara_create(request):
    """Cria uma nova Máscara de Avaliação com matriz de dificuldade e tópicos."""
    if request.method == 'POST':
        form = MascaraProvaForm(request.POST)
        disc_id = parse_pk(request.POST.get('disciplina'))
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
        disc_param = parse_pk(request.GET.get('disciplina'))
        disciplina_init = Disciplina.objects.filter(pk=disc_param, ativo=True).first() if disc_param else None

        if request.GET.get('preset') == 'enade':
            # Pré-preenche o formulário com a estrutura de Simulado ENADE (RF — máscara ENADE).
            form = MascaraProvaForm(initial=preset_enade_initial(disciplina_init))
        else:
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
# Criação em um clique do Simulado ENADE (Componente Específico)
# ---------------------------------------------------------------------------
@login_required
@docente_ou_coordenador_required
@require_POST
def mascara_enade_create(request):
    """
    Cria, em um clique, um Simulado ENADE — Componente Específico já distribuído
    nas 5 grandes áreas do CE (30 questões = 27 objetivas + 3 discursivas).

    A máscara é criada sem disciplina fixa (abrange todo o acervo) e recebe
    automaticamente a distribuição de tópicos das áreas presentes no banco.
    O docente é levado à tela de detalhe para revisar e gerar a prova.
    """
    mascara = MascaraProva(autor=request.user, **preset_enade_initial())

    try:
        mascara.full_clean()
    except ValidationError as exc:
        messages.error(request, f'Não foi possível montar o Simulado ENADE: {exc.messages[0]}')
        return redirect('provas:mascara_list')

    with transaction.atomic():
        mascara.save()
        distribuicao = construir_distribuicao_topicos_enade()
        if distribuicao:
            MascaraCriterioTopico.objects.bulk_create(
                [
                    MascaraCriterioTopico(
                        mascara=mascara,
                        topico_id=item['topico_id'],
                        percentual=item['percentual'],
                    )
                    for item in distribuicao
                ]
            )

    if distribuicao:
        messages.success(
            request,
            f'Simulado ENADE "{mascara.titulo}" criado com {len(distribuicao)} tópicos '
            'distribuídos nas 5 áreas do Componente Específico. Revise a distribuição e gere a prova.',
        )
    else:
        messages.warning(
            request,
            f'Simulado ENADE "{mascara.titulo}" criado, mas nenhum tópico com questões foi encontrado '
            'nas áreas do Componente Específico — configure a distribuição de tópicos manualmente.',
        )

    return redirect('provas:mascara_detail', pk=mascara.pk)


# ---------------------------------------------------------------------------
# Editar Máscara de Prova
# ---------------------------------------------------------------------------
@login_required
@docente_ou_coordenador_required
def mascara_update(request, pk):
    """Edita uma Máscara de Avaliação existente."""
    mascara = get_object_or_404(MascaraProva, pk=pk)

    if request.method == 'POST':
        form = MascaraProvaForm(request.POST, instance=mascara)
        disc_id = parse_pk(request.POST.get('disciplina'))
        disciplina_post = Disciplina.objects.filter(pk=disc_id, ativo=True).first() if disc_id else None
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
@docente_ou_coordenador_required
def mascara_detail(request, pk):
    """Visualiza a matriz de critérios da máscara e realiza diagnóstico de estoque no acervo."""
    mascara = get_object_or_404(
        MascaraProva.objects.select_related('disciplina', 'autor').prefetch_related('topicos_distribuicao__topico'),
        pk=pk,
    )

    distribuicao_dif = mascara.calcular_distribuicao_dificuldade()

    # Diagnóstico de estoque real no acervo de questões
    questoes_base = Questao.objects.filter(ativo=True)
    if mascara.disciplina_id:
        questoes_base = questoes_base.filter(disciplina=mascara.disciplina)

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
@docente_ou_coordenador_required
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
@docente_ou_coordenador_required
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
        topico_filtro = parse_pk(request.GET.get('topico'))
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

    # Seleção e pontos informados (preservados na tela se a validação falhar).
    selecionadas: set[int] = set()
    pontos_informados: dict[int, str] = {}

    if request.method == 'POST':
        form = ProvaManualForm(request.POST)
        questoes_selecionadas_ids = request.POST.getlist('questoes_selecionadas')
        ids_int = [int(qid) for qid in questoes_selecionadas_ids if qid.isdigit()]
        selecionadas = set(ids_int)
        pontos_informados = {qid: request.POST.get(f'pontuacao_{qid}', '').strip() for qid in ids_int}

        if form.is_valid():
            if not questoes_selecionadas_ids:
                messages.error(request, 'A avaliação não pode ficar vazia. Selecione ao menos uma questão no acervo.')
            else:
                try:
                    disciplina = form.cleaned_data['disciplina']
                    titulo = form.cleaned_data['titulo']
                    mascara_selecionada = form.cleaned_data.get('mascara') or mascara_guia

                    prova = criar_prova_manual(
                        titulo=titulo,
                        disciplina=disciplina,
                        questoes_ids=ids_int,
                        autor=request.user,
                        mascara=mascara_selecionada,
                        # Campo em branco = pontuação padrão (1,00).
                        pontuacoes={qid: valor for qid, valor in pontos_informados.items() if valor},
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

    for questao in questoes_disponiveis:
        questao.selecionada = questao.pk in selecionadas
        questao.pontuacao_informada = pontos_informados.get(questao.pk) or '1.00'

    return render(
        request,
        'provas/prova_manual_form.html',
        {
            'form': form,
            'escala_nota': mascara_guia.valor_total if mascara_guia else Decimal('10'),
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
@docente_ou_coordenador_required
def prova_revisao(request, pk):
    """Exibe a tela de revisão da seleção com indicadores meta vs. atual por célula."""
    prova = get_object_or_404(
        Prova.objects.select_related('mascara', 'mascara__disciplina', 'disciplina', 'autor'),
        pk=pk,
    )
    itens = list(
        prova.itens.select_related('questao', 'questao__topico', 'questao__disciplina')
        .prefetch_related('questao__alternativas')
        .order_by('ordem')
    )
    numerar_por_tipo(itens, tipo_do_item)  # rótulos "Q05" / "D01", como no caderno

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
    total_atual = len(itens)

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
            'total_topicos': sum(topico['topico_id'] is not None for topico in topicos_ordenados),
            'total_meta': total_meta,
            'total_atual': total_atual,
            'versoes': prova.versoes.annotate(total_itens=Count('itens')).order_by('numero_versao', 'identificador'),
            'versoes_distribuidas': prova.versoes_distribuidas,
            'quantidade_versoes': quantidade_versoes_da_prova(prova),
        },
    )


# ---------------------------------------------------------------------------
# Troca Individual de Questão (Ajuste Fino na Tela de Revisão)
# ---------------------------------------------------------------------------
@login_required
@docente_ou_coordenador_required
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
@docente_ou_coordenador_required
def item_substituir_questao(request, item_pk):
    """
    Substitui a questão de um item da prova por uma nova questão selecionada,
    validando integridade da célula e atualizando os snapshots JSON.
    """
    item = get_object_or_404(
        ProvaQuestao.objects.select_related('prova', 'prova__mascara', 'questao'),
        pk=item_pk,
    )
    nova_questao_id = parse_pk(request.POST.get('nova_questao_id'))
    if not nova_questao_id:
        return JsonResponse(
            {'success': False, 'error': 'ID da nova questão é obrigatório e deve ser válido.'}, status=400
        )

    nova_questao = get_object_or_404(Questao, pk=nova_questao_id)

    try:
        substituir_questao_prova(item=item, nova_questao=nova_questao, autor=request.user)
        msg = f'Questão #{item.numero} substituída com sucesso pela questão Q{nova_questao.pk}!'
        if item.itens_versao.exists():
            msg += ' Os cadernos A/B/C foram atualizados — baixe-os novamente antes de imprimir.'

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
@docente_ou_coordenador_required
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
@docente_ou_coordenador_required
def api_topicos_e_estoque(request):
    """
    Retorna tópicos da disciplina selecionada e contagem de questões
    disponíveis no banco por nível de dificuldade para alimentar a UI em tempo real.
    """
    disciplina_id = parse_pk(request.GET.get('disciplina_id'))
    if not disciplina_id:
        return JsonResponse({'error': 'Parâmetro disciplina_id obrigatório (numérico).'}, status=400)

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


def _int_param(request, nome, default, minimo, maximo):
    """Lê e limita um parâmetro inteiro de GET dentro de [minimo, maximo]."""
    try:
        valor = int(request.GET.get(nome, default))
    except (TypeError, ValueError):
        valor = default
    return max(minimo, min(maximo, valor))


@login_required
@docente_ou_coordenador_required
def folha_resposta_demo(request):
    """
    Demonstração do cartão-resposta com dados fictícios — mesmo formato do cartão
    nominal real (``folha_service.render_folha_pdf``): sem matriz de matrícula, QR no
    formato ``EXM1F|<token>`` (token fictício, não corresponde a nenhum aluno) e aviso
    das discursivas. Útil para imprimir um cartão de teste; não toca no fluxo real.

    Parâmetros GET opcionais: ``q`` (nº de objetivas), ``disc`` (nº de discursivas, só
    para o aviso), ``a`` (alternativas), ``col`` (colunas), ``versao`` e ``id`` (dígitos
    da matriz de matrícula — 0 no cartão atual; > 0 só para inspecionar o layout antigo).
    """
    columns = _int_param(request, 'col', FOLHA_COLUMNS, 1, 4)
    num_questoes = _int_param(request, 'q', 30, 1, max_questoes_por_pagina(columns))
    num_discursivas = _int_param(request, 'disc', 0, 0, 20)
    num_alternativas = _int_param(request, 'a', FOLHA_ALTERNATIVAS, 2, 5)
    id_digits = _int_param(request, 'id', FOLHA_ID_DIGITS, 0, 12)
    versao_ident = (request.GET.get('versao') or 'A').upper()[:10]

    try:
        geometry = build_layout(
            num_questoes=num_questoes,
            num_alternativas=num_alternativas,
            id_digits=id_digits,
            columns=columns,
        )
    except ValueError as exc:
        return HttpResponse(f'Parâmetros inválidos para o cartão: {exc}', status=400)

    marcadores_info = fiducial.render_marcadores(geometry)
    marcadores = [
        {
            'corner': m.corner,
            'left': m.left,
            'top': m.top,
            'size': m.size,
            'tipo': marcadores_info[m.corner]['tipo'],
            'data_uri': marcadores_info[m.corner]['data_uri'],
        }
        for m in geometry.markers
    ]

    try:
        qr_data_uri = fiducial.gerar_qr_data_uri(fiducial.montar_payload_folha(uuid.uuid4()))
    except fiducial.FiducialError:
        qr_data_uri = None

    context = {
        'geo': geometry,
        'marcadores': marcadores,
        'qr_data_uri': qr_data_uri,
        'instituicao': 'FAETERJ-Rio',
        'prova_titulo': 'Avaliação de exemplo (demonstração do cartão-resposta)',
        'disciplina_nome': 'Disciplina de exemplo',
        'versao_ident': versao_ident,
        'data_aplicacao': '',
        'aluno_nome': 'Aluno(a) de exemplo',
        'aluno_matricula': '00000000',
        'instrucoes': [],
        'aviso_discursivas': aviso_discursivas(num_discursivas),
    }

    pdf_bytes = PDFGeneratorService.render_pdf_from_template(
        'provas/pdf_folha_resposta.html',
        context=context,
    )

    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = 'inline; filename="cartao_resposta_demo.pdf"'
    return response


# ---------------------------------------------------------------------------
# RF10 — Exportação de folhas-resposta nominais (uma por aluno) em ZIP
# ---------------------------------------------------------------------------
@login_required
@docente_ou_coordenador_required
def aplicacao_folhas_zip(request, pk):
    """
    Gera e entrega em ZIP uma folha-resposta nominal (PDF) por aluno matriculado da
    turma da aplicação — cada folha com nome/matrícula e o QR do seu ``qr_token``.

    Se a aplicação ainda não tiver versões atribuídas, a distribuição em blocos é executada
    automaticamente antes de gerar as folhas.
    """
    import io
    import zipfile

    from django.utils.text import slugify

    from .services.folha_service import gerar_folhas_da_aplicacao, render_folha_pdf

    aplicacao = get_object_or_404(ProvaAplicacao.objects.select_related('prova', 'turma'), pk=pk)

    try:
        folhas = gerar_folhas_da_aplicacao(aplicacao)
    except ValueError as exc:
        return HttpResponse(
            f'Não foi possível gerar os cartões: {exc}',
            status=400,
            content_type='text/plain; charset=utf-8',
        )

    if not folhas:
        return HttpResponse(
            'Nenhum aluno matriculado (ativo) nesta turma para gerar cartões.',
            status=400,
            content_type='text/plain; charset=utf-8',
        )

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        for folha in folhas:
            try:
                pdf_bytes = render_folha_pdf(folha)
            except ValueError as exc:  # versão sem questões objetivas
                logger.warning('Folha pk=%s ignorada na exportação: %s', folha.pk, exc)
                continue
            slug = slugify(folha.aluno.nome) or 'aluno'
            matricula = slugify(folha.aluno.matricula) or str(folha.aluno.pk)
            nome_arquivo = f'{matricula}_{slug}_versao_{folha.prova_versao.identificador}.pdf'
            zf.writestr(nome_arquivo, pdf_bytes)

    zip_buffer.seek(0)
    filename = f'cartoes_aplicacao_{aplicacao.pk}.zip'
    response = HttpResponse(zip_buffer.getvalue(), content_type='application/zip')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@login_required
@docente_ou_coordenador_required
def aplicacao_cadernos_zip(request, pk):
    """
    Cadernos nominais da aplicação em ZIP: um PDF por aluno, com nome e matrícula na capa,
    na versão do seu cartão-resposta. ``?versao=A`` limita o ZIP aos alunos dessa versão.

    Gera os cartões-resposta que faltarem (mesma regra do ZIP de cartões): a versão de cada
    caderno é a do cartão, e a redistribuição das versões fica travada a partir daqui.
    Os arquivos são numerados na ordem alfabética (``01_versao_A_…``); como as versões são
    distribuídas em blocos, a numeração já agrupa os cadernos por versão.
    """
    import io
    import zipfile

    from django.utils.text import slugify

    from .services.caderno_service import render_cadernos_nominais
    from .services.folha_service import gerar_folhas_da_aplicacao
    from .services.pdf_generator import PDFGenerationError

    aplicacao = get_object_or_404(
        ProvaAplicacao.objects.select_related(
            'prova', 'prova__mascara', 'prova__mascara__disciplina', 'prova__disciplina', 'prova__autor', 'turma'
        ),
        pk=pk,
    )

    try:
        folhas = gerar_folhas_da_aplicacao(aplicacao)
    except ValueError as exc:
        return HttpResponse(
            f'Não foi possível gerar os cadernos: {exc}',
            status=400,
            content_type='text/plain; charset=utf-8',
        )

    if not folhas:
        return HttpResponse(
            'Nenhum aluno matriculado (ativo) nesta turma para gerar cadernos.',
            status=400,
            content_type='text/plain; charset=utf-8',
        )

    folhas.sort(key=lambda folha: (folha.aluno.nome, folha.aluno.matricula))
    digitos = max(2, len(str(len(folhas))))
    numero_da_folha = {folha.pk: numero for numero, folha in enumerate(folhas, start=1)}

    versao_ident = (request.GET.get('versao') or '').strip().upper()
    if versao_ident:
        folhas = [folha for folha in folhas if folha.prova_versao.identificador.upper() == versao_ident]
        if not folhas:
            return HttpResponse(
                f'Nenhum aluno desta aplicação recebeu a versão {versao_ident}.',
                status=400,
                content_type='text/plain; charset=utf-8',
            )

    zip_buffer = io.BytesIO()
    try:
        with zipfile.ZipFile(zip_buffer, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
            for folha, pdf_bytes in render_cadernos_nominais(aplicacao, folhas):
                numero = str(numero_da_folha[folha.pk]).zfill(digitos)
                slug = slugify(folha.aluno.nome) or 'aluno'
                matricula = slugify(folha.aluno.matricula) or str(folha.aluno.pk)
                zf.writestr(f'{numero}_versao_{folha.prova_versao.identificador}_{matricula}_{slug}.pdf', pdf_bytes)
    except PDFGenerationError as exc:
        logger.error('Erro ao gerar os cadernos nominais da aplicação pk=%s: %s', pk, exc, exc_info=True)
        return HttpResponse(
            f'Erro ao gerar os cadernos: {exc}',
            status=500,
            content_type='text/plain; charset=utf-8',
        )

    sufixo = f'_versao_{versao_ident}' if versao_ident else ''
    filename = f'cadernos_aplicacao_{aplicacao.pk}{sufixo}.zip'
    response = HttpResponse(zip_buffer.getvalue(), content_type='application/zip')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@login_required
@docente_ou_coordenador_required
def aplicacao_ata_pdf(request, pk):
    """
    Ata de Avaliação da aplicação (PDF): lista dos alunos com a versão de cada um e as
    colunas de assinatura de presença, de recebimento e de nota em branco, no modelo da
    ata do sistema acadêmico da faculdade. Não altera nada (não gera cartões nem versões).
    """
    from django.utils.text import slugify

    from .services.ata_service import render_ata_pdf

    aplicacao = get_object_or_404(
        ProvaAplicacao.objects.select_related('prova', 'prova__mascara', 'prova__autor', 'turma'), pk=pk
    )

    try:
        pdf_bytes = render_ata_pdf(aplicacao)
    except Exception as exc:  # noqa: BLE001
        logger.error('Erro ao gerar a ata da aplicação pk=%s: %s', pk, exc, exc_info=True)
        return HttpResponse(
            f'Erro ao gerar o PDF da ata: {exc}',
            status=500,
            content_type='text/plain; charset=utf-8',
        )

    turma = aplicacao.turma
    base = slugify(f'ata {turma.periodo} {turma.nome} {turma.get_turno_display()} {aplicacao.prova.titulo}')
    filename = f'{(base or f"ata-aplicacao-{aplicacao.pk}")[:120]}.pdf'
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    return response


# ---------------------------------------------------------------------------
# Upload em lote de folhas escaneadas (fila de entrada do OMR)
# ---------------------------------------------------------------------------
@login_required
@docente_ou_coordenador_required
def aplicacao_upload_folhas(request, pk):
    """
    Recebe o upload em lote de imagens (fotos de celular ou digitalizações) de folhas
    de uma aplicação, criando uma ``FolhaDigitalizada`` PENDENTE por imagem para o
    processamento posterior pelo leitor óptico (OMR). Não faz OMR agora.
    """
    aplicacao = get_object_or_404(ProvaAplicacao.objects.select_related('prova', 'turma'), pk=pk)

    if request.method == 'POST':
        form = UploadFolhasForm(request.POST, request.FILES)
        if form.is_valid():
            imagens = form.cleaned_data['imagens']
            for imagem in imagens:
                FolhaDigitalizada.objects.create(
                    prova_aplicacao=aplicacao,
                    imagem=imagem,
                    enviado_por=request.user,
                )
            messages.success(
                request,
                f'{len(imagens)} imagem(ns) enviada(s) para processamento (OMR).',
            )
            return redirect('provas:aplicacao_upload_folhas', pk=aplicacao.pk)
    else:
        form = UploadFolhasForm()

    from desempenho.models import Desempenho

    digitalizacoes = aplicacao.digitalizacoes.order_by('-criado_em')
    erros = aplicacao.digitalizacoes.filter(status=FolhaDigitalizada.Status.ERRO).count()
    inconclusivas = Desempenho.objects.filter(
        folha_resposta__prova_aplicacao=aplicacao,
        tipo_correcao=Desempenho.TipoCorrecao.AUTOMATICA,
        resposta_marcada__in=['', '*'],
    ).count()
    return render(
        request,
        'provas/aplicacao_upload_folhas.html',
        {
            'aplicacao': aplicacao,
            'form': form,
            'digitalizacoes': digitalizacoes,
            'total': digitalizacoes.count(),
            'pendencias_revisao': erros + inconclusivas,
        },
    )


# ---------------------------------------------------------------------------
# Correção automática (OMR) das folhas digitalizadas de uma aplicação (RF11)
# ---------------------------------------------------------------------------
@require_POST
@login_required
@docente_ou_coordenador_required
def aplicacao_corrigir_folhas(request, pk):
    """
    Executa a leitura óptica (OMR) das folhas digitalizadas PENDENTES da aplicação:
    identifica a folha pelo QR, corrige a perspectiva, lê as bolhas, compara com o
    gabarito da versão e grava o resultado em ``Desempenho`` (RF11).
    """
    from .services.correcao import corrigir_folha_digitalizada

    aplicacao = get_object_or_404(ProvaAplicacao, pk=pk)

    pendentes = list(aplicacao.digitalizacoes.filter(status=FolhaDigitalizada.Status.PENDENTE).order_by('criado_em'))
    if not pendentes:
        messages.info(request, 'Não há cartões pendentes para corrigir.')
        return redirect('provas:aplicacao_upload_folhas', pk=aplicacao.pk)

    processadas = 0
    erros = 0
    total_acertos = 0
    for digitalizacao in pendentes:
        try:
            resultado = corrigir_folha_digitalizada(digitalizacao, usuario=request.user)
        except Exception as exc:  # noqa: BLE001
            logger.error('Erro inesperado ao corrigir digitalização %s: %s', digitalizacao.pk, exc, exc_info=True)
            digitalizacao.status = FolhaDigitalizada.Status.ERRO
            digitalizacao.mensagem = f'Erro inesperado: {exc}'
            digitalizacao.save(update_fields=['status', 'mensagem', 'atualizado_em'])
            erros += 1
            continue

        if resultado.sucesso:
            processadas += 1
            total_acertos += resultado.acertos
        else:
            erros += 1

    if processadas:
        resumo = f'{processadas} cartão(ões) corrigido(s) automaticamente ({total_acertos} acerto(s) no total).'
        if erros:
            resumo += f' {erros} com erro — verifique a coluna Resultado.'
        messages.success(request, resumo)
    else:
        messages.warning(request, f'Nenhum cartão corrigido. {erros} com erro — verifique a coluna Resultado.')

    return redirect('provas:aplicacao_upload_folhas', pk=aplicacao.pk)


# ---------------------------------------------------------------------------
# Fila de revisão manual de falhas do OMR
# ---------------------------------------------------------------------------
@login_required
@docente_ou_coordenador_required
def aplicacao_revisao_manual(request, pk):
    """
    Lista os casos que a leitura automática não resolveu, para correção manual:
    digitalizações com erro (QR ilegível / deskew) e questões inconclusivas
    (em branco ou dupla marcação) ainda com ``tipo_correcao='AUTOMATICA'``.
    """
    from desempenho.models import Desempenho

    aplicacao = get_object_or_404(ProvaAplicacao.objects.select_related('prova', 'turma'), pk=pk)

    digitalizacoes_erro = aplicacao.digitalizacoes.filter(status=FolhaDigitalizada.Status.ERRO).order_by('-criado_em')
    inconclusivas = (
        Desempenho.objects.filter(
            folha_resposta__prova_aplicacao=aplicacao,
            tipo_correcao=Desempenho.TipoCorrecao.AUTOMATICA,
            resposta_marcada__in=['', '*'],
        )
        .select_related('folha_resposta__aluno', 'questao')
        .order_by('folha_resposta__aluno__nome', 'questao_id')
    )
    folhas = aplicacao.folhas.select_related('aluno').order_by('aluno__nome')

    return render(
        request,
        'provas/aplicacao_revisao_manual.html',
        {
            'aplicacao': aplicacao,
            'digitalizacoes_erro': digitalizacoes_erro,
            'inconclusivas': inconclusivas,
            'folhas': folhas,
            'letras': ['A', 'B', 'C', 'D', 'E'],
        },
    )


@require_POST
@login_required
@docente_ou_coordenador_required
def desempenho_revisao_manual(request, pk):
    """Aplica a correção manual de uma questão inconclusiva da fila de revisão."""
    from desempenho.models import Desempenho

    from .services.correcao import corrigir_questao_manual

    desempenho = get_object_or_404(Desempenho.objects.select_related('folha_resposta'), pk=pk)
    aplicacao_pk = desempenho.folha_resposta.prova_aplicacao_id
    resposta = request.POST.get('resposta', '')

    try:
        corrigir_questao_manual(desempenho, resposta, usuario=request.user)
        rotulo = resposta.strip().upper() or 'em branco'
        messages.success(request, f'Questão corrigida manualmente (resposta: {rotulo}).')
    except ValueError as exc:
        messages.error(request, str(exc))

    return redirect('provas:aplicacao_revisao_manual', pk=aplicacao_pk)


@require_POST
@login_required
@docente_ou_coordenador_required
def digitalizacao_vincular_folha(request, pk):
    """Vincula manualmente uma folha a uma digitalização com erro e reprocessa (OMR)."""
    from .services.correcao import revincular_e_reprocessar

    digitalizacao = get_object_or_404(FolhaDigitalizada.objects.select_related('prova_aplicacao'), pk=pk)
    aplicacao_pk = digitalizacao.prova_aplicacao_id

    folha_pk = parse_pk(request.POST.get('folha_id'))
    folha = FolhaResposta.objects.filter(pk=folha_pk, prova_aplicacao_id=aplicacao_pk).first() if folha_pk else None
    if folha is None:
        messages.error(request, 'Selecione um cartão válido para vincular.')
        return redirect('provas:aplicacao_revisao_manual', pk=aplicacao_pk)

    resultado = revincular_e_reprocessar(digitalizacao, folha, usuario=request.user)
    if resultado.sucesso:
        messages.success(request, f'Cartão de {folha.aluno.nome} vinculado e reprocessado: {resultado.mensagem}')
    else:
        messages.warning(request, f'Cartão vinculado, mas o reprocessamento falhou: {resultado.mensagem}')

    return redirect('provas:aplicacao_revisao_manual', pk=aplicacao_pk)


# ---------------------------------------------------------------------------
# Lançamento manual de notas por aluno (discursivas + fallback objetivo)
# ---------------------------------------------------------------------------
@login_required
@docente_ou_coordenador_required
def aplicacao_folhas(request, pk):
    """Lista as folhas (alunos) de uma aplicação com atalho para o lançamento manual de notas."""
    from desempenho.models import Desempenho

    aplicacao = get_object_or_404(ProvaAplicacao.objects.select_related('prova', 'turma'), pk=pk)

    if request.method == 'POST' and request.POST.get('acao') == 'gerar':
        from .services.folha_service import gerar_folhas_da_aplicacao

        try:
            folhas = gerar_folhas_da_aplicacao(aplicacao)
            messages.success(request, f'{len(folhas)} cartão(ões) gerado(s)/atualizado(s).')
        except Exception as exc:  # noqa: BLE001
            messages.error(request, f'Não foi possível gerar os cartões: {exc}')
        return redirect('provas:aplicacao_folhas', pk=aplicacao.pk)

    folhas = list(aplicacao.folhas.select_related('aluno', 'prova_versao').order_by('aluno__nome'))
    resumo = {
        row['folha_resposta_id']: row
        for row in Desempenho.objects.filter(folha_resposta__prova_aplicacao=aplicacao)
        .values('folha_resposta_id')
        .annotate(total=Count('id'), acertos=Count('id', filter=Q(acertou=True)))
    }
    linhas = [
        {
            'folha': folha,
            'total': resumo.get(folha.id, {}).get('total', 0),
            'acertos': resumo.get(folha.id, {}).get('acertos', 0),
        }
        for folha in folhas
    ]

    return render(
        request,
        'provas/aplicacao_folhas.html',
        {'aplicacao': aplicacao, 'linhas': linhas},
    )


@login_required
@docente_ou_coordenador_required
def folha_lancamento_manual(request, pk):
    """
    Lançamento manual de notas de uma folha (aluno): notas 0–10 de discursivas
    (``acertou = nota >= corte``) e resposta das objetivas (fallback sem scan).
    """
    from .services.correcao import (
        itens_versao_para_lancamento,
        lancar_nota_discursiva,
        lancar_objetiva_manual,
        recalcular_acertou_discursivas,
    )

    folha = get_object_or_404(
        FolhaResposta.objects.select_related('aluno', 'prova_versao', 'prova_aplicacao__prova'),
        pk=pk,
    )
    aplicacao = folha.prova_aplicacao

    if request.method == 'POST':
        corte = aplicacao.nota_corte
        corte_raw = (request.POST.get('nota_corte') or '').strip().replace(',', '.')
        if corte_raw:
            try:
                novo_corte = Decimal(corte_raw)
            except InvalidOperation:
                novo_corte = None
            if novo_corte is None or not (Decimal('0') <= novo_corte <= Decimal('10')):
                messages.error(request, 'Nota de corte inválida (use um valor entre 0 e 10).')
            else:
                if novo_corte != aplicacao.nota_corte:
                    aplicacao.nota_corte = novo_corte
                    aplicacao.save(update_fields=['nota_corte', 'atualizado_em'])
                    recalcular_acertou_discursivas(aplicacao, novo_corte)
                corte = novo_corte

        lancados = 0
        erros = 0
        for linha in itens_versao_para_lancamento(folha):
            questao = linha['questao']
            if linha['tipo'] == 'OBJ':
                valor = request.POST.get(f'resp_{questao.id}', '__skip__')
                if valor == '__skip__':
                    continue
                try:
                    lancar_objetiva_manual(folha, questao, '' if valor == 'BRANCO' else valor, usuario=request.user)
                    lancados += 1
                except ValueError:
                    erros += 1
            else:
                bruto = (request.POST.get(f'nota_{questao.id}') or '').strip()
                if not bruto:
                    continue
                try:
                    lancar_nota_discursiva(folha, questao, bruto, corte, usuario=request.user)
                    lancados += 1
                except ValueError:
                    erros += 1

        if lancados:
            resumo = f'{lancados} lançamento(s) salvo(s).'
            if erros:
                resumo += f' {erros} com erro (valor inválido).'
            messages.success(request, resumo)
        elif erros:
            messages.error(request, f'{erros} lançamento(s) com erro (valor inválido).')
        else:
            messages.info(request, 'Nenhum lançamento informado.')
        return redirect('provas:folha_lancamento_manual', pk=folha.pk)

    return render(
        request,
        'provas/folha_lancamento_manual.html',
        {
            'folha': folha,
            'aplicacao': aplicacao,
            'linhas': itens_versao_para_lancamento(folha),
            'letras': ['A', 'B', 'C', 'D', 'E'],
        },
    )


# ---------------------------------------------------------------------------
# Geração / Regeneração de Versões da Prova (A, B, C...)
# ---------------------------------------------------------------------------
@require_POST
@login_required
@docente_ou_coordenador_required
def prova_gerar_versoes_view(request, pk):
    """
    (Re)Gera os cadernos de prova (Versões A, B, C…) com permutações de questões e
    alternativas. A quantidade padrão vem da máscara da prova (1 = caderno único,
    como no Simulado ENADE); ``quantidade_versoes`` no POST a sobrescreve (1–8).
    """
    from provas.services.version_generator import VersoesDistribuidasError, gerar_versoes_prova

    prova = get_object_or_404(Prova.objects.select_related('mascara'), pk=pk)
    qtd = quantidade_versoes_da_prova(prova)
    with contextlib.suppress(TypeError, ValueError):
        qtd = max(1, min(8, int(request.POST.get('quantidade_versoes', qtd))))

    try:
        versoes = gerar_versoes_prova(prova=prova, quantidade_versoes=qtd, substituir_existentes=True)
    except VersoesDistribuidasError as err:
        messages.error(request, f'Não foi possível regerar os cadernos: {err}')
        return redirect('provas:prova_revisao', pk=prova.pk)
    if len(versoes) == 1:
        messages.success(request, 'Caderno único gerado com sucesso (Versão A), sem embaralhamento.')
    else:
        nomes_versoes = ', '.join(f'Versão {v.identificador}' for v in versoes)
        messages.success(
            request,
            f'Cadernos de avaliação gerados com sucesso: {nomes_versoes} ({len(versoes)} versões com questões e alternativas embaralhadas)!',
        )
    return redirect('provas:prova_revisao', pk=prova.pk)


# ---------------------------------------------------------------------------
# Geração do PDF da Capa Padronizada da Prova (Módulo 3)
# ---------------------------------------------------------------------------
@login_required
@docente_ou_coordenador_required
def prova_capa_pdf(request, pk):
    """
    Gera o PDF da capa padronizada de uma prova gerada ou de uma versão específica.
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

    versao_ident = request.GET.get('versao')
    versao = None
    if versao_ident:
        versao = prova.versoes.filter(identificador__iexact=versao_ident).first()

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

    total_questoes = prova.itens.count()

    identificador_display = f'Versão {versao.identificador}' if versao else 'Versão única'

    context = {
        'prova': prova,
        'versao': versao,
        'identificador_display': identificador_display,
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

    versao_slug = f'_versao_{versao.identificador}' if versao else f'_caderno{prova.numero_caderno}'
    filename = f'capa_prova_{prova.pk}{versao_slug}.pdf'
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    return response


# ---------------------------------------------------------------------------
# Geração do PDF do Caderno de Prova / Prova Completa (Suporte a Versões A/B/C)
# ---------------------------------------------------------------------------
@login_required
@docente_ou_coordenador_required
def prova_pdf(request, pk):
    """
    Gera o PDF formatado do Caderno de Prova com todas as questões.
    Suporta versões específicas (ex.: ?versao=A, ?versao=B, ?versao=C).
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

    versao_ident = request.GET.get('versao')
    versao = None
    if versao_ident:
        versao = prova.versoes.filter(identificador__iexact=versao_ident).first()
    elif prova.versoes.exists():
        versao = prova.versoes.order_by('numero_versao').first()

    incluir_capa = request.GET.get('incluir_capa', '1') in ('1', 'true', 'True')
    incluir_gabarito = request.GET.get('incluir_gabarito', '0') in ('1', 'true', 'True')

    # Layout ENADE (discursivas antes das objetivas) forçado por ?layout=enade ou detectado
    # para máscaras de Simulado ENADE; senão, o layout institucional FAETERJ padrão.
    template_pdf, context = contexto_caderno(
        prova,
        versao,
        incluir_capa=incluir_capa,
        incluir_gabarito=incluir_gabarito,
        layout=request.GET.get('layout') or '',
        turma=request.GET.get('turma') or '',
        turno=request.GET.get('turno') or '',
        data_aplicacao=request.GET.get('data') or '',
        instrucoes_extras=request.GET.get('instrucoes_extras') or '',
    )

    try:
        pdf_bytes = PDFGeneratorService.render_pdf_from_template(
            template_name=template_pdf,
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

    versao_slug = f'_versao_{versao.identificador}' if versao else f'_caderno{prova.numero_caderno}'
    filename = f'prova_{prova.pk}{versao_slug}_{tipo_str}.pdf'
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    return response


# ---------------------------------------------------------------------------
# Geração do PDF do Gabarito Oficial por Versão / Caderno da Prova
# ---------------------------------------------------------------------------
@login_required
@docente_ou_coordenador_required
def prova_gabarito_pdf(request, pk):
    """
    Gera o PDF do Gabarito Oficial específico para a versão/caderno da prova.
    Respeita rigorosamente a permutação de questões e alternativas da versão solicitada.
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

    versao_ident = request.GET.get('versao')
    versao = None
    if versao_ident:
        versao = prova.versoes.filter(identificador__iexact=versao_ident).first()
    elif prova.versoes.exists():
        versao = prova.versoes.order_by('numero_versao').first()

    disciplina = prova.disciplina_efetiva
    mascara = prova.mascara
    snap = prova.snapshot_mascara or {}

    valor_total = None
    modalidade = None

    if mascara:
        valor_total = mascara.valor_total
        modalidade = mascara.get_modalidade_display()
    elif snap:
        valor_total = snap.get('valor_total')
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

    resumo_itens = []
    itens_detalhados = []

    if versao:
        itens_versao_qs = versao.itens.select_related(
            'prova_questao__questao',
            'prova_questao__questao__topico',
            'prova_questao__questao__disciplina',
        ).order_by('ordem')
        for iv in itens_versao_qs:
            q = iv.prova_questao.questao
            snap_q = iv.prova_questao.snapshot_questao or {}
            tipo = snap_q.get('tipo') or q.tipo
            tipo_display = snap_q.get('tipo_display') or q.get_tipo_display()
            topico_nome = snap_q.get('topico_nome') or (q.topico.nome if q.topico else '')
            origem = snap_q.get('origem') or q.origem
            origem_numero = snap_q.get('numero_questao') or q.numero_questao
            dificuldade_display = snap_q.get('dificuldade_display') or q.get_dificuldade_display()
            enunciado = snap_q.get('enunciado') or q.enunciado
            criterios_correcao = snap_q.get('criterios_correcao') or q.criterios_correcao

            letra_correta = ''
            alternativas_lista = list(iv.alternativas_embaralhadas)
            if tipo == 'OBJ':
                for alt in alternativas_lista:
                    if alt.get('correta'):
                        letra_correta = alt.get('letra', '')
                        break
            else:
                letra_correta = 'DIS'

            resumo_itens.append(
                {
                    'numero': iv.numero,
                    'tipo': tipo,
                    'letra_correta': letra_correta,
                    'pontuacao': iv.pontuacao,
                }
            )

            itens_detalhados.append(
                {
                    'numero': iv.numero,
                    'tipo': tipo,
                    'tipo_display': tipo_display,
                    'pontuacao': iv.pontuacao,
                    'topico_nome': topico_nome,
                    'origem': origem,
                    'origem_numero': origem_numero,
                    'dificuldade_display': dificuldade_display,
                    'enunciado': enunciado,
                    'letra_correta': letra_correta,
                    'alternativas': alternativas_lista,
                    'criterios_correcao': criterios_correcao,
                }
            )
    else:
        # Fallback para prova sem versões
        itens_qs = (
            prova.itens.select_related('questao', 'questao__topico', 'questao__disciplina')
            .prefetch_related('questao__alternativas')
            .order_by('ordem')
        )
        for idx, item in enumerate(itens_qs, start=1):
            num_exibicao = item.numero or idx
            q = item.questao
            snap_q = item.snapshot_questao or {}
            tipo = snap_q.get('tipo') or q.tipo
            tipo_display = snap_q.get('tipo_display') or q.get_tipo_display()
            topico_nome = snap_q.get('topico_nome') or (q.topico.nome if q.topico else '')
            origem = snap_q.get('origem') or q.origem
            origem_numero = snap_q.get('numero_questao') or q.numero_questao
            dificuldade_display = snap_q.get('dificuldade_display') or q.get_dificuldade_display()
            enunciado = snap_q.get('enunciado') or q.enunciado
            criterios_correcao = snap_q.get('criterios_correcao') or q.criterios_correcao

            letra_correta = ''
            alternativas_lista = []
            if tipo == 'OBJ':
                snap_alts = snap_q.get('alternativas')
                if snap_alts:
                    for a in snap_alts:
                        alt_dict = {
                            'letra': a.get('letra', ''),
                            'texto': a.get('texto', ''),
                            'correta': a.get('correta', False),
                        }
                        alternativas_lista.append(alt_dict)
                        if alt_dict['correta']:
                            letra_correta = alt_dict['letra']
                else:
                    for a in q.alternativas.all():
                        alt_dict = {
                            'letra': a.letra,
                            'texto': a.texto,
                            'correta': a.correta,
                        }
                        alternativas_lista.append(alt_dict)
                        if alt_dict['correta']:
                            letra_correta = alt_dict['letra']
            else:
                letra_correta = 'DIS'

            resumo_itens.append(
                {
                    'numero': num_exibicao,
                    'tipo': tipo,
                    'letra_correta': letra_correta,
                    'pontuacao': item.pontuacao,
                }
            )

            itens_detalhados.append(
                {
                    'numero': num_exibicao,
                    'tipo': tipo,
                    'tipo_display': tipo_display,
                    'pontuacao': item.pontuacao,
                    'topico_nome': topico_nome,
                    'origem': origem,
                    'origem_numero': origem_numero,
                    'dificuldade_display': dificuldade_display,
                    'enunciado': enunciado,
                    'letra_correta': letra_correta,
                    'alternativas': alternativas_lista,
                    'criterios_correcao': criterios_correcao,
                }
            )

    identificador_display = f'Versão {versao.identificador}' if versao else 'Versão única'
    # Mesma ordem e numeração do caderno: discursivas primeiro (D01…), objetivas Q01…QN.
    resumo_itens = ordenar_como_caderno(resumo_itens)
    itens_detalhados = ordenar_como_caderno(itens_detalhados)

    context = {
        'prova': prova,
        'versao': versao,
        'identificador_display': identificador_display,
        'disciplina_nome': disciplina.nome if disciplina else None,
        'docente_nome': docente_nome,
        'turma': snap.get('turma') or request.GET.get('turma') or '',
        'turno': snap.get('turno') or request.GET.get('turno') or '',
        'data_aplicacao': request.GET.get('data') or '',
        'valor_total': valor_total,
        'modalidade': modalidade,
        'total_questoes': len(resumo_itens),
        'resumo_itens': resumo_itens,
        'itens_detalhados': itens_detalhados,
    }

    try:
        pdf_bytes = PDFGeneratorService.render_pdf_from_template(
            template_name='provas/pdf_gabarito_versao.html',
            context=context,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error('Erro ao gerar Gabarito PDF da prova pk=%s: %s', pk, exc, exc_info=True)
        return HttpResponse(
            f'Erro ao gerar o PDF do gabarito: {exc}',
            status=500,
            content_type='text/plain; charset=utf-8',
        )

    versao_slug = f'_versao_{versao.identificador}' if versao else f'_caderno{prova.numero_caderno}'
    filename = f'gabarito_prova_{prova.pk}{versao_slug}.pdf'
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    return response


# ---------------------------------------------------------------------------
# Download do Pacote Completo ZIP (Todas as Versões A, B, C + Gabaritos)
# ---------------------------------------------------------------------------
@login_required
@docente_ou_coordenador_required
def prova_pacote_zip(request, pk):
    """
    Gera dinamicamente e entrega em arquivo ZIP todos os cadernos de prova
    e gabaritos oficiais correspondentes para todas as versões geradas (A, B, C...).
    """
    import io
    import zipfile

    prova = get_object_or_404(
        Prova.objects.select_related(
            'mascara',
            'mascara__disciplina',
            'disciplina',
            'autor',
        ),
        pk=pk,
    )

    versoes = list(prova.versoes.prefetch_related('itens').order_by('numero_versao'))
    if not versoes and prova.itens.exists():
        from provas.services.version_generator import gerar_versoes_prova

        versoes = gerar_versoes_prova(prova=prova, quantidade_versoes=quantidade_versoes_da_prova(prova))

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

    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        for versao in versoes:
            itens_versao_qs = versao.itens.select_related(
                'prova_questao__questao',
                'prova_questao__questao__topico',
                'prova_questao__questao__disciplina',
            ).order_by('ordem')

            itens_render = []
            resumo_itens = []
            itens_detalhados = []

            for iv in itens_versao_qs:
                q = iv.prova_questao.questao
                snap_q = iv.prova_questao.snapshot_questao or {}
                tipo = snap_q.get('tipo') or q.tipo
                tipo_display = snap_q.get('tipo_display') or q.get_tipo_display()
                topico_nome = snap_q.get('topico_nome') or (q.topico.nome if q.topico else '')
                origem = snap_q.get('origem') or q.origem
                origem_numero = snap_q.get('numero_questao') or q.numero_questao
                dificuldade_display = snap_q.get('dificuldade_display') or q.get_dificuldade_display()
                enunciado = snap_q.get('enunciado') or q.enunciado
                criterios_correcao = snap_q.get('criterios_correcao') or q.criterios_correcao

                letra_correta = ''
                alternativas_lista = list(iv.alternativas_embaralhadas)
                if tipo == 'OBJ':
                    for alt in alternativas_lista:
                        if alt.get('correta'):
                            letra_correta = alt.get('letra', '')
                            break
                else:
                    letra_correta = 'DIS'

                itens_render.append(
                    {
                        'numero': iv.numero,
                        'ordem': iv.ordem,
                        'pontuacao': iv.pontuacao,
                        'questao': q,
                        'snapshot_questao': snap_q,
                        'alternativas_versao': alternativas_lista,
                    }
                )

                resumo_itens.append(
                    {
                        'numero': iv.numero,
                        'tipo': tipo,
                        'letra_correta': letra_correta,
                        'pontuacao': iv.pontuacao,
                    }
                )

                itens_detalhados.append(
                    {
                        'numero': iv.numero,
                        'tipo': tipo,
                        'tipo_display': tipo_display,
                        'pontuacao': iv.pontuacao,
                        'topico_nome': topico_nome,
                        'origem': origem,
                        'origem_numero': origem_numero,
                        'dificuldade_display': dificuldade_display,
                        'enunciado': enunciado,
                        'letra_correta': letra_correta,
                        'alternativas': alternativas_lista,
                        'criterios_correcao': criterios_correcao,
                    }
                )

            identificador_display = f'Versão {versao.identificador}'
            # Caderno em blocos e gabarito na mesma ordem/numeração (ver services.caderno_layout).
            itens_render, blocos = organizar_caderno(itens_render)
            resumo_itens = ordenar_como_caderno(resumo_itens)
            itens_detalhados = ordenar_como_caderno(itens_detalhados)

            context_prova = {
                'prova': prova,
                'versao': versao,
                'identificador_display': identificador_display,
                'itens': itens_render,
                'itens_render': itens_render,
                'blocos': blocos,
                'disciplina_nome': disciplina.nome if disciplina else None,
                'docente_nome': docente_nome,
                'turma': snap.get('turma') or request.GET.get('turma') or '',
                'turno': snap.get('turno') or request.GET.get('turno') or '',
                'data_aplicacao': request.GET.get('data') or '',
                'valor_total': valor_total,
                'tempo_minutos': tempo_minutos,
                'modalidade': modalidade,
                'total_questoes': len(itens_render),
                'instrucoes_extras': request.GET.get('instrucoes_extras') or '',
                'incluir_capa': True,
                'incluir_gabarito': False,
                'linhas_discursiva': linhas_da_discursiva(prova),
            }

            context_gabarito = {
                'prova': prova,
                'versao': versao,
                'identificador_display': identificador_display,
                'disciplina_nome': disciplina.nome if disciplina else None,
                'docente_nome': docente_nome,
                'turma': snap.get('turma') or request.GET.get('turma') or '',
                'turno': snap.get('turno') or request.GET.get('turno') or '',
                'data_aplicacao': request.GET.get('data') or '',
                'valor_total': valor_total,
                'modalidade': modalidade,
                'total_questoes': len(resumo_itens),
                'resumo_itens': resumo_itens,
                'itens_detalhados': itens_detalhados,
            }

            try:
                pdf_prova = PDFGeneratorService.render_pdf_from_template(
                    template_name='provas/pdf_prova_completa.html',
                    context=context_prova,
                )
                zf.writestr(f'Caderno_Versao_{versao.identificador}.pdf', pdf_prova)

                pdf_gabarito = PDFGeneratorService.render_pdf_from_template(
                    template_name='provas/pdf_gabarito_versao.html',
                    context=context_gabarito,
                )
                zf.writestr(f'Gabarito_Oficial_Versao_{versao.identificador}.pdf', pdf_gabarito)
            except Exception as exc:
                logger.error('Erro ao empacotar PDFs da versão %s no ZIP: %s', versao.identificador, exc, exc_info=True)

    zip_buffer.seek(0)
    filename = f'pacote_avaliacao_{prova.pk}_todas_versoes.zip'
    response = HttpResponse(zip_buffer.getvalue(), content_type='application/zip')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


# ---------------------------------------------------------------------------
# Aplicações de prova (fora do admin): listagem, cadastro e painel da aplicação
# ---------------------------------------------------------------------------
@login_required
@docente_ou_coordenador_required
def aplicacao_list(request):
    """Lista as aplicações (prova × turma × data) com o progresso das folhas, busca e filtro por turma."""
    aplicacoes = ProvaAplicacao.objects.select_related('prova', 'turma').annotate(
        total_folhas=Count('folhas', distinct=True),
        folhas_lidas=Count('folhas', filter=Q(folhas__status=FolhaResposta.Status.LIDA), distinct=True),
    )
    busca = request.GET.get('q', '').strip()
    turma_id = request.GET.get('turma', '')
    if busca:
        aplicacoes = aplicacoes.filter(
            Q(prova__titulo__icontains=busca) | Q(turma__nome__icontains=busca) | Q(turma__periodo__icontains=busca)
        )
    if turma_id.isascii() and turma_id.isdigit() and len(turma_id) <= 18:
        aplicacoes = aplicacoes.filter(turma_id=turma_id)
    else:
        turma_id = ''

    paginator = Paginator(aplicacoes.order_by('-data_aplicacao', '-pk'), 12)
    page_obj = paginator.get_page(request.GET.get('page'))
    params = request.GET.copy()
    params.pop('page', None)
    return render(
        request,
        'provas/aplicacao_list.html',
        {
            'aplicacoes': page_obj,
            'page_obj': page_obj,
            'page_range': paginator.get_elided_page_range(page_obj.number, on_each_side=2, on_ends=1),
            'querystring': params.urlencode(),
            'busca': busca,
            'turma_selecionada': turma_id,
            'turmas': Turma.objects.order_by('-periodo', 'nome'),
            'total': ProvaAplicacao.objects.count(),
        },
    )


@login_required
@docente_ou_coordenador_required
def aplicacao_create(request):
    """Cadastra uma aplicação de prova a uma turma (aceita ``?prova=<pk>`` para pré-selecionar a prova)."""
    if request.method == 'POST':
        form = ProvaAplicacaoForm(request.POST)
        if form.is_valid():
            aplicacao = form.save()
            messages.success(
                request,
                f'Aplicação cadastrada: {aplicacao}. Próximo passo: distribuir as versões e gerar os cartões-resposta.',
            )
            return redirect('provas:aplicacao_detail', pk=aplicacao.pk)
    else:
        prova_id = request.GET.get('prova', '')
        initial = {'prova': prova_id} if prova_id.isascii() and prova_id.isdigit() else None
        form = ProvaAplicacaoForm(initial=initial)

    return render(request, 'provas/aplicacao_form.html', {'form': form})


@login_required
@docente_ou_coordenador_required
def aplicacao_detail(request, pk):
    """
    Painel da aplicação: situação de cada etapa (versões → folhas → digitalizações →
    revisão → notas) e atalhos para todas as ações do fluxo de correção.
    """
    from desempenho.models import Desempenho

    from .services.version_assigner import resumo_distribuicao

    aplicacao = get_object_or_404(ProvaAplicacao.objects.select_related('prova', 'turma'), pk=pk)

    folhas_por_status = dict(aplicacao.folhas.values_list('status').annotate(total=Count('id')))
    digitalizacoes_por_status = dict(aplicacao.digitalizacoes.values_list('status').annotate(total=Count('id')))
    desempenhos = Desempenho.objects.filter(folha_resposta__prova_aplicacao=aplicacao)
    inconclusivas = desempenhos.filter(
        tipo_correcao=Desempenho.TipoCorrecao.AUTOMATICA,
        resposta_marcada__in=['', '*'],
    ).count()
    dig_erro = digitalizacoes_por_status.get(FolhaDigitalizada.Status.ERRO, 0)
    total_folhas = sum(folhas_por_status.values())

    return render(
        request,
        'provas/aplicacao_detail.html',
        {
            'aplicacao': aplicacao,
            'versoes': aplicacao.prova.versoes.order_by('numero_versao'),
            'matriculados': Matricula.objects.filter(
                turma=aplicacao.turma, situacao=Matricula.Situacao.MATRICULADO, aluno__ativo=True
            ).count(),
            'total_atribuicoes': aplicacao.atribuicoes.count(),
            'resumo_versoes': resumo_distribuicao(aplicacao),
            'total_folhas': total_folhas,
            'folhas_lidas': folhas_por_status.get(FolhaResposta.Status.LIDA, 0),
            'dig_pendentes': digitalizacoes_por_status.get(FolhaDigitalizada.Status.PENDENTE, 0),
            'dig_processadas': digitalizacoes_por_status.get(FolhaDigitalizada.Status.PROCESSADA, 0),
            'dig_erro': dig_erro,
            'pendencias_revisao': dig_erro + inconclusivas,
            'total_desempenhos': desempenhos.count(),
            'redistribuicao_bloqueada': total_folhas > 0,
        },
    )


@require_POST
@login_required
@docente_ou_coordenador_required
def aplicacao_atribuir_versoes(request, pk):
    """Distribui as versões em blocos aos alunos matriculados — bloqueado após gerar as folhas."""
    from .services.version_assigner import atribuir_versoes

    aplicacao = get_object_or_404(ProvaAplicacao.objects.select_related('prova', 'turma'), pk=pk)
    try:
        atribuicoes = atribuir_versoes(aplicacao)
    except ValueError as exc:
        messages.error(request, f'Não foi possível distribuir as versões: {exc}')
    else:
        if atribuicoes:
            messages.success(request, f'{len(atribuicoes)} aluno(s) receberam versão (em blocos, na ordem alfabética).')
        else:
            messages.warning(request, 'Nenhum aluno matriculado (ativo) nesta turma para receber versão.')
    return redirect('provas:aplicacao_detail', pk=aplicacao.pk)


# ---------------------------------------------------------------------------
# Notas finais da turma numa aplicação + exportação (XLSX/CSV)
# ---------------------------------------------------------------------------
SITUACAO_NOTA_LABEL = {
    'completa': 'Completa',
    'incompleta': 'Incompleta (há questões sem correção)',
    'anulada': 'Cartão anulado',
    'sem_itens': 'Sem questões',
}
# Prefixos que planilhas interpretam como fórmula (injeção de fórmula / CSV injection).
PREFIXOS_FORMULA = ('=', '+', '-', '@', '\t', '\r')


def _tabela_notas(boletim):
    """Cabeçalho e linhas (valores crus) da planilha de notas."""
    cabecalho = [
        'Matrícula',
        'Aluno',
        'Versão',
        'Acertos (objetivas)',
        'Total de objetivas',
        'Discursivas corrigidas',
        'Total de discursivas',
        'Pontos obtidos',
        'Pontos possíveis',
        f'Nota final (0–{boletim["escala"].normalize():f})',
        'Situação',
    ]
    linhas = [
        [
            linha['matricula'],
            linha['aluno'],
            linha['versao'],
            linha['acertos_objetivas'],
            linha['total_objetivas'],
            linha['discursivas_corrigidas'],
            linha['total_discursivas'],
            linha['pontos_obtidos'],
            linha['pontos_possiveis'],
            linha['nota'],
            SITUACAO_NOTA_LABEL.get(linha['situacao'], linha['situacao']),
        ]
        for linha in boletim['linhas']
    ]
    return cabecalho, linhas


def _nome_arquivo_notas(aplicacao):
    from django.utils.text import slugify

    turma = aplicacao.turma
    base = slugify(f'notas {aplicacao.prova.titulo} {turma.periodo} {turma.nome} {turma.get_turno_display()}')
    return (base or f'notas-aplicacao-{aplicacao.pk}')[:120]


def _xlsx_notas(aplicacao, boletim):
    import io

    from openpyxl import Workbook
    from openpyxl.styles import Font

    cabecalho, linhas = _tabela_notas(boletim)
    workbook = Workbook()
    planilha = workbook.active
    planilha.title = 'Notas'
    planilha.append(cabecalho)
    for celula in planilha[1]:
        celula.font = Font(bold=True)
    for valores in linhas:
        planilha.append(valores)
        for celula in planilha[planilha.max_row]:
            # Texto começando com "=", "+"… viraria fórmula: força o tipo texto.
            if isinstance(celula.value, str) and celula.value.startswith(PREFIXOS_FORMULA):
                celula.data_type = 's'
    planilha.freeze_panes = 'A2'
    for coluna, largura in zip('ABCDEFGHIJK', (14, 38, 8, 12, 12, 12, 12, 12, 12, 14, 30), strict=True):
        planilha.column_dimensions[coluna].width = largura

    buffer = io.BytesIO()
    workbook.save(buffer)
    resposta = HttpResponse(
        buffer.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    resposta['Content-Disposition'] = f'attachment; filename="{_nome_arquivo_notas(aplicacao)}.xlsx"'
    return resposta


def _csv_notas(aplicacao, boletim):
    import csv

    def _celula(valor):
        if valor is None:
            return ''
        if isinstance(valor, Decimal):
            return str(valor).replace('.', ',')  # decimal pt-BR (separador de colunas é ";")
        if isinstance(valor, str) and valor.startswith(PREFIXOS_FORMULA):
            return f"'{valor}"
        return valor

    cabecalho, linhas = _tabela_notas(boletim)
    resposta = HttpResponse(content_type='text/csv; charset=utf-8')
    resposta['Content-Disposition'] = f'attachment; filename="{_nome_arquivo_notas(aplicacao)}.csv"'
    resposta.write('\N{BYTE ORDER MARK}')  # BOM p/ acentos no Excel
    escritor = csv.writer(resposta, delimiter=';')
    escritor.writerow(cabecalho)
    for valores in linhas:
        escritor.writerow([_celula(valor) for valor in valores])
    return resposta


@login_required
@docente_ou_coordenador_required
def aplicacao_notas(request, pk):
    """
    Notas finais da turma na aplicação (``desempenho.services.notas_da_aplicacao``) e
    exportação da planilha: ``?formato=xlsx`` ou ``?formato=csv``.
    """
    from desempenho.services import notas_da_aplicacao

    aplicacao = get_object_or_404(ProvaAplicacao.objects.select_related('prova', 'prova__mascara', 'turma'), pk=pk)
    boletim = notas_da_aplicacao(aplicacao)

    formato = (request.GET.get('formato') or '').lower()
    if formato == 'xlsx':
        return _xlsx_notas(aplicacao, boletim)
    if formato == 'csv':
        return _csv_notas(aplicacao, boletim)

    return render(request, 'provas/aplicacao_notas.html', {'aplicacao': aplicacao, **boletim})
