"""
Views do cadastro acadêmico: CRUDs de Aluno e Turma, matrículas e docências.

Só o administrador cadastra e altera (alunos, turmas, matrículas, docências); o professor
consulta as turmas das suas docências e os alunos delas; o coordenador consulta tudo
(regras em ``academico.acesso``).
"""

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from banco_questoes.permissions import GRUPO_ADMINISTRADOR, administrador_required, docente_ou_coordenador_required
from banco_questoes.utils import parse_pk

from . import professores as contas
from .acesso import turmas_permitidas, ve_tudo
from .forms import AlunoForm, DocenciaForm, ImportarAlunosForm, ProfessorForm, TurmaForm
from .models import Aluno, Docencia, Matricula, Turma

# Senha temporária guardada na sessão só entre o POST e a tela que a mostra (uma vez).
_CREDENCIAIS_NA_SESSAO = 'professor_credenciais'


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
    """
    Lista alunos com busca (nome/matrícula), filtro por status e paginação. O professor vê
    só os alunos matriculados nas turmas das suas docências.
    """
    if ve_tudo(request.user):
        base_qs = Aluno.objects.all()
    else:
        base_qs = Aluno.objects.filter(matriculas__turma__in=turmas_permitidas(request.user)).distinct()
    alunos_qs = base_qs

    busca = request.GET.get('q', '').strip()
    status = request.GET.get('status', '')
    if busca:
        alunos_qs = alunos_qs.filter(Q(nome__icontains=busca) | Q(matricula__icontains=busca))
    alunos_qs = _filtrar_por_status(alunos_qs, status)

    metricas = Aluno.objects.filter(pk__in=base_qs.values('pk')).aggregate(
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
@administrador_required
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
@administrador_required
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
@administrador_required
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
    """Lista turmas com busca, filtro por status e paginação (professor: só as das suas docências)."""
    base_qs = turmas_permitidas(request.user)
    turmas_qs = base_qs.select_related('disciplina')

    busca = request.GET.get('q', '').strip()
    status = request.GET.get('status', '')
    if busca:
        turmas_qs = turmas_qs.filter(Q(nome__icontains=busca) | Q(periodo__icontains=busca))
    turmas_qs = _filtrar_por_status(turmas_qs, status)

    metricas = Turma.objects.filter(pk__in=base_qs.values('pk')).aggregate(
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
@administrador_required
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
@administrador_required
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
@administrador_required
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
    O professor só consulta (turmas das suas docências); matricular é do administrador.
    """
    turma = get_object_or_404(turmas_permitidas(request.user), pk=pk)
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


# ---------------------------------------------------------------------------
# Importação de alunos por planilha (só o administrador)
# ---------------------------------------------------------------------------
def _importar_alunos(request, turma=None):
    """Tela única das duas importações: sem ``turma`` só cadastra; com ela, também matricula."""
    from .importacao_alunos import PlanilhaInvalidaError, importar_alunos

    resultado = None
    if request.method == 'POST':
        form = ImportarAlunosForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                resultado = importar_alunos(form.cleaned_data['arquivo'], turma=turma)
            except PlanilhaInvalidaError as exc:
                form.add_error('arquivo', str(exc))
            else:
                form = ImportarAlunosForm()
    else:
        form = ImportarAlunosForm()
    return render(
        request,
        'academico/aluno_import.html',
        {'form': form, 'turma': turma, 'resultado': resultado},
    )


@login_required
@administrador_required
def aluno_importar(request):
    """Acadêmico → Alunos → Importar planilha: cadastra os alunos que ainda não existem."""
    return _importar_alunos(request)


@login_required
@administrador_required
def turma_importar_alunos(request, pk):
    """Matrículas da turma → Importar planilha: cadastra quem falta e matricula todos na turma."""
    return _importar_alunos(request, turma=get_object_or_404(Turma, pk=pk))


@login_required
@administrador_required
def aluno_modelo_planilha(request):
    """Modelo .xlsx da importação de alunos (colunas matricula e nome)."""
    from django.http import HttpResponse

    from .importacao_alunos import gerar_modelo_xlsx

    resposta = HttpResponse(
        gerar_modelo_xlsx(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    resposta['Content-Disposition'] = 'attachment; filename="modelo_alunos.xlsx"'
    return resposta


@require_POST
@login_required
@administrador_required
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
@administrador_required
def turma_desmatricular(request, pk):
    """Desmatricula (soft) um aluno: situação CANCELADO + data de encerramento; a linha permanece."""
    turma = get_object_or_404(Turma, pk=pk)
    matricula = get_object_or_404(Matricula, pk=parse_pk(request.POST.get('matricula')), turma=turma)
    matricula.situacao = Matricula.Situacao.CANCELADO
    matricula.data_encerramento = timezone.localdate()
    matricula.save(update_fields=['situacao', 'data_encerramento', 'atualizado_em'])
    messages.success(request, f'{matricula.aluno.nome} foi desmatriculado(a) da turma "{turma}".')
    return redirect('academico:turma_matriculas', pk=turma.pk)


# ---------------------------------------------------------------------------
# Docências da turma (professor × disciplina) — só o administrador
# ---------------------------------------------------------------------------
@login_required
@administrador_required
def turma_docencias(request, pk):
    """Professores e disciplinas da turma: base do acesso por perfil (``academico.acesso``)."""
    turma = get_object_or_404(Turma, pk=pk)
    if request.method == 'POST':
        form = DocenciaForm(request.POST)
        if form.is_valid():
            docencia, criada = Docencia.objects.update_or_create(
                professor=form.cleaned_data['professor'],
                disciplina=form.cleaned_data['disciplina'],
                turma=turma,
                defaults={'ativo': True},
            )
            verbo = 'cadastrada' if criada else 'reativada'
            messages.success(request, f'Docência {verbo}: {docencia}.')
            return redirect('academico:turma_docencias', pk=turma.pk)
    else:
        form = DocenciaForm(initial={'disciplina': turma.disciplina})

    docencias = turma.docencias.select_related('professor', 'disciplina').order_by('-ativo', 'disciplina__nome')
    return render(
        request,
        'academico/turma_docencias.html',
        {'turma': turma, 'docencias': docencias, 'form': form},
    )


@require_POST
@login_required
@administrador_required
def turma_docencia_toggle(request, pk):
    """Encerra (ou reativa) uma docência da turma; a linha fica no histórico."""
    turma = get_object_or_404(Turma, pk=pk)
    docencia = get_object_or_404(Docencia, pk=parse_pk(request.POST.get('docencia')), turma=turma)
    docencia.ativo = not docencia.ativo
    docencia.save(update_fields=['ativo', 'atualizado_em'])
    estado = 'reativada' if docencia.ativo else 'encerrada'
    messages.success(request, f'Docência {estado}: {docencia}.')
    return redirect('academico:turma_docencias', pk=turma.pk)


# ---------------------------------------------------------------------------
# Professores (contas do grupo Docente) — só o administrador
# ---------------------------------------------------------------------------
def _professores():
    """
    Contas do grupo Docente, menos as de administração (superusuário ou grupo Administrador):
    essas não aparecem na aba nem podem ter a senha redefinida ou ser desativadas por aqui.
    """
    return (
        get_user_model()
        .objects.filter(groups__name=contas.GRUPO_DOCENTE)
        .exclude(is_superuser=True)
        .exclude(groups__name=GRUPO_ADMINISTRADOR)
        .distinct()
    )


def _guardar_credenciais(request, usuario, senha, acao):
    request.session[_CREDENCIAIS_NA_SESSAO] = {'pk': usuario.pk, 'senha': senha, 'acao': acao}


@login_required
@administrador_required
def professor_list(request):
    """Professores (grupo Docente) com busca por nome/login, filtro por situação e paginação."""
    base_qs = _professores()
    professores_qs = base_qs.select_related('perfil_professor').annotate(
        turmas=Count('docencias', filter=Q(docencias__ativo=True), distinct=True)
    )
    busca = request.GET.get('q', '').strip()
    status = request.GET.get('status', '')
    if busca:
        professores_qs = professores_qs.filter(
            Q(first_name__icontains=busca) | Q(last_name__icontains=busca) | Q(username__icontains=busca)
        )
    if status == 'ativo':
        professores_qs = professores_qs.filter(is_active=True)
    elif status == 'inativo':
        professores_qs = professores_qs.filter(is_active=False)

    metricas = base_qs.aggregate(
        total=Count('pk', distinct=True),
        ativos=Count('pk', filter=Q(is_active=True), distinct=True),
        aguardando=Count('pk', filter=Q(perfil_professor__troca_senha_pendente=True), distinct=True),
    )
    page_obj, page_range, querystring = _paginar(
        request, professores_qs.order_by('first_name', 'last_name', 'username')
    )
    return render(
        request,
        'academico/professor_list.html',
        {
            'professores': page_obj,
            'page_obj': page_obj,
            'page_range': page_range,
            'querystring': querystring,
            'busca': busca,
            'status_selecionado': status,
            'metricas': metricas,
        },
    )


@login_required
@administrador_required
def professor_create(request):
    """Cria a conta do professor (login nome.sobrenome) e mostra a senha temporária uma vez."""
    if request.method == 'POST':
        form = ProfessorForm(request.POST)
        if form.is_valid():
            usuario, senha = contas.criar_professor(form.cleaned_data['nome_completo'], form.cleaned_data['email'])
            _guardar_credenciais(request, usuario, senha, 'criado')
            return redirect('academico:professor_credenciais')
    else:
        form = ProfessorForm()
    return render(request, 'academico/professor_form.html', {'form': form, 'titulo_pagina': 'Novo Professor'})


@login_required
@administrador_required
def professor_update(request, pk):
    """Edita nome e e-mail (o login não muda)."""
    usuario = get_object_or_404(_professores(), pk=pk)
    if request.method == 'POST':
        form = ProfessorForm(request.POST)
        if form.is_valid():
            usuario.first_name, usuario.last_name = contas.partes_do_nome(form.cleaned_data['nome_completo'])
            usuario.email = form.cleaned_data['email']
            usuario.save(update_fields=['first_name', 'last_name', 'email'])
            messages.success(request, f'Professor "{usuario.get_full_name()}" atualizado.')
            return redirect('academico:professor_list')
    else:
        form = ProfessorForm(initial={'nome_completo': usuario.get_full_name(), 'email': usuario.email})
    return render(
        request,
        'academico/professor_form.html',
        {'form': form, 'professor': usuario, 'titulo_pagina': f'Editar Professor: {usuario.get_full_name()}'},
    )


@require_POST
@login_required
@administrador_required
def professor_redefinir_senha(request, pk):
    """Gera nova senha temporária; o professor terá de trocá-la no próximo login."""
    usuario = get_object_or_404(_professores(), pk=pk)
    _guardar_credenciais(request, usuario, contas.redefinir_senha(usuario), 'redefinida')
    return redirect('academico:professor_credenciais')


@require_POST
@login_required
@administrador_required
def professor_toggle_ativo(request, pk):
    """Desativa (não consegue mais entrar) ou reativa a conta; os dados dele ficam."""
    usuario = get_object_or_404(_professores(), pk=pk)
    if usuario == request.user:
        messages.error(request, 'Você não pode desativar a própria conta.')
        return redirect('academico:professor_list')
    usuario.is_active = not usuario.is_active
    usuario.save(update_fields=['is_active'])
    estado = 'reativada' if usuario.is_active else 'desativada'
    messages.success(request, f'Conta de "{usuario.get_full_name() or usuario.username}" {estado}.')
    return redirect('academico:professor_list')


@login_required
@administrador_required
def professor_credenciais(request):
    """Mostra login e senha temporária uma única vez (a sessão esquece a senha em seguida)."""
    dados = request.session.pop(_CREDENCIAIS_NA_SESSAO, None)
    if not dados:
        messages.info(request, 'A senha temporária só é mostrada uma vez. Use "Redefinir senha" para gerar outra.')
        return redirect('academico:professor_list')
    usuario = get_object_or_404(_professores(), pk=dados['pk'])
    return render(
        request,
        'academico/professor_credenciais.html',
        {'professor': usuario, 'senha': dados['senha'], 'acao': dados['acao']},
    )
