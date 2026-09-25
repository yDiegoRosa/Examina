"""Views do cadastro acadêmico: CRUDs de Aluno e Turma."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from banco_questoes.permissions import docente_ou_coordenador_required
from banco_questoes.utils import parse_pk

from .forms import AlunoForm, TurmaForm
from .models import Aluno, Matricula, Turma


def _paginar(request, queryset, por_pagina=10):
    """Aplica paginação padrão e devolve (page_obj, page_range, querystring)."""
    paginator = Paginator(queryset, por_pagina)
    page_obj = paginator.get_page(request.GET.get('page'))
    page_range = paginator.get_elided_page_range(page_obj.number, on_each_side=2, on_ends=1)
    get_params = request.GET.copy()
    get_params.pop('page', None)
    return page_obj, page_range, get_params.urlencode()


def _filtrar_por_status(queryset, status):
    """Filtra por 'ativo'/'inativo'; qualquer outro valor não filtra."""
    if status == 'ativo':
        return queryset.filter(ativo=True)
    if status == 'inativo':
        return queryset.filter(ativo=False)
    return queryset


# ---------------------------------------------------------------------------
# CRUD de Aluno
# ---------------------------------------------------------------------------
@login_required
@docente_ou_coordenador_required
def aluno_list(request):
    """Lista alunos com busca (nome/matrícula), filtro por status e paginação."""
    alunos_qs = Aluno.objects.all()

    busca = request.GET.get('q', '').strip()
    status = request.GET.get('status', '')
    if busca:
        alunos_qs = alunos_qs.filter(Q(nome__icontains=busca) | Q(matricula__icontains=busca))
    alunos_qs = _filtrar_por_status(alunos_qs, status)

    metricas = Aluno.objects.aggregate(
        total=Count('pk'),
        ativos=Count('pk', filter=Q(ativo=True)),
        inativos=Count('pk', filter=Q(ativo=False)),
    )

    page_obj, page_range, querystring = _paginar(request, alunos_qs)
    return render(
        request,
        'academico/aluno_list.html',
        {
            'alunos': page_obj,
            'page_obj': page_obj,
            'page_range': page_range,
            'querystring': querystring,
            'busca': busca,
            'status_selecionado': status,
            'metricas': metricas,
        },
    )


@login_required
@docente_ou_coordenador_required
def aluno_create(request):
    """Cria um novo aluno."""
    if request.method == 'POST':
        form = AlunoForm(request.POST)
        if form.is_valid():
            aluno = form.save()
            messages.success(request, f'Aluno "{aluno.nome}" cadastrado com sucesso!')
            return redirect('academico:aluno_list')
    else:
        form = AlunoForm()

    return render(
        request,
        'academico/aluno_form.html',
        {'form': form, 'titulo_pagina': 'Novo Aluno'},
    )


@login_required
@docente_ou_coordenador_required
def aluno_update(request, pk):
    """Edita um aluno existente."""
    aluno = get_object_or_404(Aluno, pk=pk)
    if request.method == 'POST':
        form = AlunoForm(request.POST, instance=aluno)
        if form.is_valid():
            aluno = form.save()
            messages.success(request, f'Aluno "{aluno.nome}" atualizado com sucesso!')
            return redirect('academico:aluno_list')
    else:
        form = AlunoForm(instance=aluno)

    return render(
        request,
        'academico/aluno_form.html',
        {'form': form, 'aluno': aluno, 'titulo_pagina': f'Editar Aluno: {aluno.nome}'},
    )


@require_POST
@login_required
@docente_ou_coordenador_required
def aluno_toggle_ativo(request, pk):
    """Alterna o status ativo/inativo do aluno."""
    aluno = get_object_or_404(Aluno, pk=pk)
    aluno.ativo = not aluno.ativo
    aluno.save(update_fields=['ativo', 'atualizado_em'])
    estado = 'ativado' if aluno.ativo else 'inativado'
    messages.success(request, f'Aluno "{aluno.nome}" {estado}.')
    return redirect('academico:aluno_list')


# ---------------------------------------------------------------------------
# CRUD de Turma
# ---------------------------------------------------------------------------
@login_required
@docente_ou_coordenador_required
def turma_list(request):
    """Lista turmas com busca (nome/período), filtro por status e paginação."""
    turmas_qs = Turma.objects.all()

    busca = request.GET.get('q', '').strip()
    status = request.GET.get('status', '')
    if busca:
        turmas_qs = turmas_qs.filter(Q(nome__icontains=busca) | Q(periodo__icontains=busca))
    turmas_qs = _filtrar_por_status(turmas_qs, status)

    metricas = Turma.objects.aggregate(
        total=Count('pk'),
        ativas=Count('pk', filter=Q(ativo=True)),
        inativas=Count('pk', filter=Q(ativo=False)),
    )

    page_obj, page_range, querystring = _paginar(request, turmas_qs)
    return render(
        request,
        'academico/turma_list.html',
        {
            'turmas': page_obj,
            'page_obj': page_obj,
            'page_range': page_range,
            'querystring': querystring,
            'busca': busca,
            'status_selecionado': status,
            'metricas': metricas,
        },
    )


@login_required
@docente_ou_coordenador_required
def turma_create(request):
    """Cria uma nova turma."""
    if request.method == 'POST':
        form = TurmaForm(request.POST)
        if form.is_valid():
            turma = form.save()
            messages.success(request, f'Turma "{turma}" cadastrada com sucesso!')
            return redirect('academico:turma_list')
    else:
        form = TurmaForm()

    return render(
        request,
        'academico/turma_form.html',
        {'form': form, 'titulo_pagina': 'Nova Turma'},
    )


@login_required
@docente_ou_coordenador_required
def turma_update(request, pk):
    """Edita uma turma existente."""
    turma = get_object_or_404(Turma, pk=pk)
    if request.method == 'POST':
        form = TurmaForm(request.POST, instance=turma)
        if form.is_valid():
            turma = form.save()
            messages.success(request, f'Turma "{turma}" atualizada com sucesso!')
            return redirect('academico:turma_list')
    else:
        form = TurmaForm(instance=turma)

    return render(
        request,
        'academico/turma_form.html',
        {'form': form, 'turma': turma, 'titulo_pagina': f'Editar Turma: {turma}'},
    )


@require_POST
@login_required
@docente_ou_coordenador_required
def turma_toggle_ativo(request, pk):
    """Alterna o status ativo/inativo da turma."""
    turma = get_object_or_404(Turma, pk=pk)
    turma.ativo = not turma.ativo
    turma.save(update_fields=['ativo', 'atualizado_em'])
    estado = 'ativada' if turma.ativo else 'inativada'
    messages.success(request, f'Turma "{turma}" {estado}.')
    return redirect('academico:turma_list')


# ---------------------------------------------------------------------------
# Tela de Matrícula (vínculo Aluno × Turma), preservando histórico
# ---------------------------------------------------------------------------
@login_required
@docente_ou_coordenador_required
def turma_matriculas(request, pk):
    """
    Gerencia o elenco de uma turma: mostra as matrículas (com situação) e os alunos
    ativos disponíveis para matricular. Desmatricular é *soft* (preserva o histórico).
    """
    turma = get_object_or_404(Turma, pk=pk)
    matriculas = turma.matriculas.select_related('aluno').order_by('aluno__nome')
    matriculados_ids = turma.matriculas.filter(situacao=Matricula.Situacao.MATRICULADO).values_list(
        'aluno_id', flat=True
    )
    alunos_disponiveis = Aluno.objects.filter(ativo=True).exclude(pk__in=matriculados_ids).order_by('nome')

    return render(
        request,
        'academico/turma_matriculas.html',
        {
            'turma': turma,
            'matriculas': matriculas,
            'alunos_disponiveis': alunos_disponiveis,
        },
    )


@require_POST
@login_required
@docente_ou_coordenador_required
def turma_matricular(request, pk):
    """Matricula (ou reativa) os alunos selecionados na turma."""
    turma = get_object_or_404(Turma, pk=pk)
    ids = [aluno_pk for aluno_pk in map(parse_pk, request.POST.getlist('alunos')) if aluno_pk]
    alunos = Aluno.objects.filter(pk__in=ids, ativo=True)

    total = 0
    for aluno in alunos:
        Matricula.objects.update_or_create(
            aluno=aluno,
            turma=turma,
            defaults={
                'situacao': Matricula.Situacao.MATRICULADO,
                'data_matricula': timezone.localdate(),
                'data_encerramento': None,
            },
        )
        total += 1

    if total:
        messages.success(request, f'{total} aluno(s) matriculado(s) na turma "{turma}".')
    else:
        messages.info(request, 'Nenhum aluno selecionado para matrícula.')
    return redirect('academico:turma_matriculas', pk=turma.pk)


@require_POST
@login_required
@docente_ou_coordenador_required
def turma_desmatricular(request, pk):
    """Desmatricula (soft) um aluno: situação CANCELADO + data de encerramento; a linha permanece."""
    turma = get_object_or_404(Turma, pk=pk)
    matricula = get_object_or_404(Matricula, pk=parse_pk(request.POST.get('matricula')), turma=turma)
    matricula.situacao = Matricula.Situacao.CANCELADO
    matricula.data_encerramento = timezone.localdate()
    matricula.save(update_fields=['situacao', 'data_encerramento', 'atualizado_em'])
    messages.success(request, f'{matricula.aluno.nome} foi desmatriculado(a) da turma "{turma}".')
    return redirect('academico:turma_matriculas', pk=turma.pk)
