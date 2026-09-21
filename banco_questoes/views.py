import csv
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.http import HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from .filters import QuestaoFilter
from .forms import AlternativaFormSet, CSVUploadForm, QuestaoForm
from .importer import CSVImportService
from .models import Disciplina, Questao, Topico
from .parser import CSVParseError
from .permissions import (
    can_change_questao,
    docente_ou_coordenador_required,
    is_docente_ou_coordenador,
    pode_editar_questao_required,
)

logger = logging.getLogger(__name__)


def style_guide(request):
    """Kit de componentes do design system (referência viva para novas telas)."""
    return render(request, 'banco_questoes/style_guide.html')


# ---------------------------------------------------------------------------
# Lista de questões (com django-filter e paginação)
# ---------------------------------------------------------------------------
@login_required
def questao_list(request):
    """Lista de questões com filtros avançados via django-filter, métricas e paginação."""
    base_qs = (
        Questao.objects.select_related('disciplina', 'topico', 'autor').prefetch_related('tags').order_by('-criado_em')
    )

    # Aplicação do FilterSet
    questao_filter = QuestaoFilter(request.GET, queryset=base_qs)
    questoes_qs = questao_filter.qs

    # Paginação (15 itens por página)
    paginator = Paginator(questoes_qs, 15)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    page_range = paginator.get_elided_page_range(page_obj.number, on_each_side=2, on_ends=1)

    # Query string preservando filtros para navegação entre páginas
    get_params = request.GET.copy()
    get_params.pop('page', None)
    querystring = get_params.urlencode()

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
        request.GET.get(key)
        for key in (
            'disciplina',
            'topico',
            'dificuldade',
            'tipo',
            'origem',
            'ativo',
            'autor',
        )
    )

    return render(
        request,
        'banco_questoes/questao_list.html',
        {
            'filter': questao_filter,
            'questoes': page_obj,
            'page_obj': page_obj,
            'page_range': page_range,
            'querystring': querystring,
            'metricas': metricas,
            'has_active_filters': has_active_filters,
            'pode_criar': is_docente_ou_coordenador(request.user),
            'pode_editar': can_change_questao(request.user),
        },
    )


# ---------------------------------------------------------------------------
# Criar questão (com formset inline e autor)
# ---------------------------------------------------------------------------
@login_required
@docente_ou_coordenador_required
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
@pode_editar_questao_required
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
@pode_editar_questao_required
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
        messages.success(
            request,
            f'Questão Q{questao.pk} inativada. O registro foi preservado no acervo.',
        )

    return redirect('banco_questoes:questao_list')


# ---------------------------------------------------------------------------
# Download / Exportação do Acervo em CSV (Formato 15 Colunas — Padrão Cliente + Classificação)
# ---------------------------------------------------------------------------
@login_required
def download_modelo_csv(request):
    """
    Exporta o acervo de questões no formato CSV padrão com 15 colunas:
    origem, questao, tipo, disciplina, topico, dificuldade, enunciado,
    opcao_a, opcao_b, opcao_c, opcao_d, opcao_e, gabarito, tags, comentarios.

    As colunas de classificação (tipo/disciplina/topico/dificuldade/tags) são opcionais
    para o parser, mas recomendadas: sem elas as questões entram como "Não Classificada"/
    Médio, o que inviabiliza a geração de provas por máscara.

    Caso existam filtros na requisição GET, exporta o conjunto filtrado.
    Se não houver questões cadastradas, gera registros modelo estilo ENADE/POSCOMP.
    """
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="acervo_questoes_faeterj.csv"'

    # BOM UTF-8 para compatibilidade direta com Microsoft Excel em PT-BR
    response.write('\ufeff')

    writer = csv.writer(response)
    writer.writerow(
        [
            'origem',
            'questao',
            'tipo',
            'disciplina',
            'topico',
            'dificuldade',
            'enunciado',
            'opcao_a',
            'opcao_b',
            'opcao_c',
            'opcao_d',
            'opcao_e',
            'gabarito',
            'tags',
            'comentarios',
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

            writer.writerow(
                [
                    q.origem or '',
                    q.numero_questao or '',
                    q.tipo,
                    q.disciplina.nome,
                    q.topico.nome if q.topico else '',
                    q.dificuldade,
                    q.enunciado,
                    alt_map.get('A', ''),
                    alt_map.get('B', ''),
                    alt_map.get('C', ''),
                    alt_map.get('D', ''),
                    alt_map.get('E', ''),
                    correta_letra,
                    ', '.join(t.name for t in q.tags.all()),
                    q.criterios_correcao or '',
                ]
            )
    else:
        # Exemplos modelo no estilo ENADE / POSCOMP (2 objetivas + 1 discursiva)
        writer.writerow(
            [
                'ENADE ADS 2021',
                '26',
                'OBJ',
                'Redes de Computadores',
                'DNS',
                'M',
                'Em relação ao serviço DNS (Domain Name System), considere as seguintes afirmações e assinale a alternativa correta.',
                'O DNS utiliza exclusivamente o protocolo TCP para todas as suas consultas.',
                'O servidor DNS raiz é responsável por armazenar todos os registros de domínio da Internet.',
                'O processo de resolução de nomes pode envolver consultas recursivas e iterativas.',
                'O DNS não suporta balanceamento de carga entre servidores.',
                'Registros do tipo MX são utilizados para mapear endereços IP em nomes de domínio.',
                'C',
                'redes; dns',
                'A alternativa C está correta porque o DNS utiliza consultas recursivas (o resolver solicita ao servidor que resolva completamente) e iterativas (o servidor retorna referências para outros servidores).',
            ]
        )
        writer.writerow(
            [
                'POSCOMP 2025',
                '15',
                'OBJ',
                'Programação',
                'Linguagem C',
                'D',
                'Considere o seguinte trecho de código em C:\n\nint x = 5;\nint y = ++x + x++;\nprintf("%d %d", x, y);\n\nQual será a saída do programa?',
                '7 12',
                '7 13',
                '6 12',
                '7 11',
                '6 11',
                'A',
                'c; operadores',
                'O pré-incremento (++x) incrementa x para 6 antes da avaliação. Em seguida, x++ usa o valor 6 e depois incrementa para 7. Assim, y = 6 + 6 = 12 e x = 7. Saída: 7 12.',
            ]
        )
        # Exemplo de questão DISCURSIVA: opções e gabarito vazios; critérios em "comentarios".
        writer.writerow(
            [
                'ENADE ADS 2021',
                '4',
                'DIS',
                'Engenharia de Software',
                'Processos de Software',
                'M',
                'Explique as diferenças entre os modelos de processo em cascata e ágil, destacando vantagens e limitações de cada abordagem.',
                '',
                '',
                '',
                '',
                '',
                '',
                'engenharia-de-software; processos-ageis',
                'Espera-se que o candidato contraste a previsibilidade e a documentação extensa do modelo cascata com a natureza iterativa e adaptativa do ágil (ex.: Scrum, XP), citando vantagens e limitações de cada um.',
            ]
        )

    return response


# ---------------------------------------------------------------------------
# Download / Exportação do Acervo em Excel (.xlsx — Formato 15 Colunas)
# ---------------------------------------------------------------------------
@login_required
def download_modelo_xlsx(request):
    """
    Exporta o acervo de questões no formato Microsoft Excel (.xlsx) com 15 colunas:
    origem, questao, tipo, disciplina, topico, dificuldade, enunciado,
    opcao_a, opcao_b, opcao_c, opcao_d, opcao_e, gabarito, tags, comentarios.

    Inclui estilização institucional (cabeçalho azul marinho, larguras automáticas,
    alinhamentos e quebra de texto) e respeita os filtros ativos da requisição GET.
    """
    import io

    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Acervo de Questões'

    headers = [
        'origem',
        'questao',
        'tipo',
        'disciplina',
        'topico',
        'dificuldade',
        'enunciado',
        'opcao_a',
        'opcao_b',
        'opcao_c',
        'opcao_d',
        'opcao_e',
        'gabarito',
        'tags',
        'comentarios',
    ]
    ws.append(headers)

    # Estilos para cabeçalho
    header_font = Font(name='Segoe UI', size=11, bold=True, color='FFFFFF')
    header_fill = PatternFill(start_color='002B49', end_color='002B49', fill_type='solid')
    header_align = Alignment(horizontal='center', vertical='center', wrap_text=True)

    thin_border = Border(
        left=Side(style='thin', color='D0D5DD'),
        right=Side(style='thin', color='D0D5DD'),
        top=Side(style='thin', color='D0D5DD'),
        bottom=Side(style='thin', color='D0D5DD'),
    )

    ws.row_dimensions[1].height = 26
    for col_num in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_num)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align
        cell.border = thin_border

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

    data_font = Font(name='Segoe UI', size=10)
    data_align_left = Alignment(horizontal='left', vertical='top', wrap_text=True)
    data_align_center = Alignment(horizontal='center', vertical='top', wrap_text=True)

    if questoes.exists():
        for q in questoes:
            alt_map = {alt.letra: alt.texto for alt in q.alternativas.all()}
            correta_letra = next((alt.letra for alt in q.alternativas.all() if alt.correta), '')

            row_data = [
                q.origem or '',
                q.numero_questao or '',
                q.tipo,
                q.disciplina.nome,
                q.topico.nome if q.topico else '',
                q.dificuldade,
                q.enunciado,
                alt_map.get('A', ''),
                alt_map.get('B', ''),
                alt_map.get('C', ''),
                alt_map.get('D', ''),
                alt_map.get('E', ''),
                correta_letra,
                ', '.join(t.name for t in q.tags.all()),
                q.criterios_correcao or '',
            ]
            ws.append(row_data)
    else:
        # Exemplos modelo no estilo ENADE / POSCOMP (2 objetivas + 1 discursiva)
        ws.append(
            [
                'ENADE ADS 2021',
                '26',
                'OBJ',
                'Redes de Computadores',
                'DNS',
                'M',
                'Em relação ao serviço DNS (Domain Name System), considere as seguintes afirmações e assinale a alternativa correta.',
                'O DNS utiliza exclusivamente o protocolo TCP para todas as suas consultas.',
                'O servidor DNS raiz é responsável por armazenar todos os registros de domínio da Internet.',
                'O processo de resolução de nomes pode envolver consultas recursivas e iterativas.',
                'O DNS não suporta balanceamento de carga entre servidores.',
                'Registros do tipo MX são utilizados para mapear endereços IP em nomes de domínio.',
                'C',
                'redes; dns',
                'A alternativa C está correta porque o DNS utiliza consultas recursivas (o resolver solicita ao servidor que resolva completamente) e iterativas (o servidor retorna referências para outros servidores).',
            ]
        )
        ws.append(
            [
                'POSCOMP 2025',
                '15',
                'OBJ',
                'Programação',
                'Linguagem C',
                'D',
                'Considere o seguinte trecho de código em C:\n\nint x = 5;\nint y = ++x + x++;\nprintf("%d %d", x, y);\n\nQual será a saída do programa?',
                '7 12',
                '7 13',
                '6 12',
                '7 11',
                '6 11',
                'A',
                'c; operadores',
                'O pré-incremento (++x) incrementa x para 6 antes da avaliação. Em seguida, x++ usa o valor 6 e depois incrementa para 7. Assim, y = 6 + 6 = 12 e x = 7. Saída: 7 12.',
            ]
        )
        # Exemplo de questão DISCURSIVA: opções e gabarito vazios; critérios em "comentarios".
        ws.append(
            [
                'ENADE ADS 2021',
                '4',
                'DIS',
                'Engenharia de Software',
                'Processos de Software',
                'M',
                'Explique as diferenças entre os modelos de processo em cascata e ágil, destacando vantagens e limitações de cada abordagem.',
                '',
                '',
                '',
                '',
                '',
                '',
                'engenharia-de-software; processos-ageis',
                'Espera-se que o candidato contraste a previsibilidade e a documentação extensa do modelo cascata com a natureza iterativa e adaptativa do ágil (ex.: Scrum, XP), citando vantagens e limitações de cada um.',
            ]
        )

    # Aplica estilos e bordas nas linhas de dados
    for row_idx in range(2, ws.max_row + 1):
        ws.row_dimensions[row_idx].height = 36
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.font = data_font
            cell.border = thin_border
            # Alinha centralizado colunas de código/gabarito e à esquerda as de texto
            if col_idx in (1, 2, 3, 6, 13):
                cell.alignment = data_align_center
            else:
                cell.alignment = data_align_left

    # Configuração de larguras de colunas personalizadas para excelente legibilidade
    col_widths = {
        'A': 18,  # origem
        'B': 12,  # questao
        'C': 8,  # tipo
        'D': 24,  # disciplina
        'E': 24,  # topico
        'F': 12,  # dificuldade
        'G': 50,  # enunciado
        'H': 32,  # opcao_a
        'I': 32,  # opcao_b
        'J': 32,  # opcao_c
        'K': 32,  # opcao_d
        'L': 32,  # opcao_e
        'M': 12,  # gabarito
        'N': 22,  # tags
        'O': 45,  # comentarios
    }
    for col_letter, width in col_widths.items():
        ws.column_dimensions[col_letter].width = width

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    response = HttpResponse(
        output.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = 'attachment; filename="acervo_questoes_faeterj.xlsx"'
    return response


# ---------------------------------------------------------------------------
# Importação em Lote via Excel (.xlsx) e CSV & Commit (RF02)
# ---------------------------------------------------------------------------
@login_required
@docente_ou_coordenador_required
def questao_import(request):
    """View para upload de planilhas Excel (.xlsx) ou arquivos CSV, commit e relatório de inconsistências."""
    relatorio = None

    if request.method == 'POST':
        upload_form = CSVUploadForm(request.POST, request.FILES)

        if not upload_form.is_valid():
            # Mensagem de erro vinda de clean_arquivo() (extensão, tamanho, magic bytes)
            for error in upload_form.errors.get('arquivo', []):
                messages.error(request, error)
            return render(
                request,
                'banco_questoes/questao_import.html',
                {'titulo': 'Importação em Lote', 'upload_form': upload_form},
            )

        arquivo = upload_form.cleaned_data['arquivo']

        try:
            relatorio = CSVImportService.import_file(arquivo, filename=arquivo.name, autor=request.user)
        except CSVParseError as err:
            logger.warning('Erro de formato durante importação: %s', err)
            messages.error(request, f'Erro ao ler o arquivo: {err}')
            return render(
                request,
                'banco_questoes/questao_import.html',
                {'titulo': 'Importação em Lote', 'upload_form': CSVUploadForm()},
            )
        except Exception as exc:
            logger.exception('Erro inesperado durante importação: %s', exc)
            messages.error(request, f'Ocorreu um erro interno durante o processamento: {exc}')
            return render(
                request,
                'banco_questoes/questao_import.html',
                {'titulo': 'Importação em Lote', 'upload_form': CSVUploadForm()},
            )

        if relatorio.total_processadas == 0:
            messages.warning(request, 'O arquivo enviado não contém linhas de dados para importação.')
            return render(
                request,
                'banco_questoes/questao_import.html',
                {'titulo': 'Importação em Lote', 'upload_form': CSVUploadForm()},
            )

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
    else:
        upload_form = CSVUploadForm()

    return render(
        request,
        'banco_questoes/questao_import.html',
        {
            'relatorio': relatorio,
            'titulo': 'Importação em Lote de Questões',
            'upload_form': upload_form,
        },
    )


# ---------------------------------------------------------------------------
# Histórico de Alterações de uma Questão (simple_history diff view)
# ---------------------------------------------------------------------------

# Campos internos do simple_history que não devem aparecer no diff
_HISTORY_META_FIELDS = frozenset(
    [
        'history_date',
        'history_change_reason',
        'history_user',
        'history_user_id',
        'history_type',
        'history_id',
        'atualizado_em',
    ]
)

# Mapeamento de nomes de campo técnicos → rótulos legíveis em português
_FIELD_LABELS = {
    'tipo': 'Tipo',
    'dificuldade': 'Dificuldade',
    'disciplina_id': 'Disciplina',
    'topico_id': 'Tópico',
    'origem': 'Origem / Exame',
    'numero_questao': 'Nº da Questão',
    'texto_auxiliar': 'Texto Auxiliar',
    'imagem': 'Imagem de Apoio',
    'enunciado': 'Enunciado',
    'criterios_correcao': 'Critérios de Correção',
    'ativo': 'Status (Ativo)',
    'autor_id': 'Autor',
}

# Rótulos legíveis para o tipo de ação do simple_history
_ACTION_LABELS = {
    '+': ('Criação', 'history-action-create'),
    '~': ('Edição', 'history-action-edit'),
    '-': ('Exclusão', 'history-action-delete'),
}


@login_required
def questao_history(request, pk):
    """Exibe o histórico de alterações de uma questão com diffs campo a campo.

    Para cada versão registrada pelo simple_history, compara com a versão
    imediatamente anterior e exibe quais campos mudaram (valor antigo → novo),
    quem realizou a alteração e quando.
    """
    questao = get_object_or_404(Questao, pk=pk)
    registros = questao.history.all().order_by('-history_date')

    versoes = []
    registros_list = list(registros)

    for idx, record in enumerate(registros_list):
        autor = record.history_user
        action_label, action_css = _ACTION_LABELS.get(record.history_type, ('Alteração', 'history-action-edit'))

        diffs = []

        # Versão anterior existe → calcula diff
        if idx < len(registros_list) - 1:
            previous = registros_list[idx + 1]
            delta = record.diff_against(previous)
            for change in delta.changes:
                if change.field in _HISTORY_META_FIELDS:
                    continue
                diffs.append(
                    {
                        'campo': _FIELD_LABELS.get(change.field, change.field),
                        'campo_raw': change.field,
                        'old': change.old,
                        'new': change.new,
                    }
                )
        else:
            # Primeira versão (criação): mostra todos os campos preenchidos
            for field_name, label in _FIELD_LABELS.items():
                try:
                    value = getattr(record, field_name, None)
                except Exception:
                    value = None
                if value not in (None, '', False):
                    diffs.append(
                        {
                            'campo': label,
                            'campo_raw': field_name,
                            'old': None,
                            'new': value,
                        }
                    )

        versoes.append(
            {
                'record': record,
                'diffs': diffs,
                'autor': autor,
                'data': record.history_date,
                'action_label': action_label,
                'action_css': action_css,
                'is_creation': idx == len(registros_list) - 1,
            }
        )

    logger.debug('Histórico da questão Q%s: %d versão(ões) carregada(s).', pk, len(versoes))

    return render(
        request,
        'banco_questoes/questao_history.html',
        {
            'questao': questao,
            'versoes': versoes,
            'titulo': f'Histórico — Q{questao.pk}',
        },
    )


# ---------------------------------------------------------------------------
# API / AJAX: Tópicos por Disciplina
# ---------------------------------------------------------------------------
@login_required
def api_topicos_por_disciplina(request):
    """Retorna JSON com tópicos ativos de uma disciplina específica."""
    disciplina_id = request.GET.get('disciplina_id')
    if not disciplina_id:
        return JsonResponse({'topicos': []})

    disciplina = get_object_or_404(Disciplina, pk=disciplina_id, ativo=True)
    topicos = list(Topico.objects.filter(disciplina=disciplina, ativo=True).values('id', 'nome').order_by('nome'))
    return JsonResponse(
        {
            'disciplina_id': disciplina.pk,
            'disciplina_nome': disciplina.nome,
            'topicos': topicos,
        }
    )
