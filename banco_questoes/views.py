import csv
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from .forms import AlternativaFormSet, QuestaoForm
from .models import Alternativa, Disciplina, Questao, Topico
from .parser import CSVParseError, CSVQuestaoParser
from .validator import CSVQuestaoValidator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lista de questões
# ---------------------------------------------------------------------------
@login_required
def questao_list(request):
    """Lista de questões com filtros básicos e métricas."""
    questoes = Questao.objects.select_related('disciplina', 'topico').prefetch_related('tags').order_by('-criado_em')

    # Métricas agregadas
    metricas = {
        'total': questoes.count(),
        'objetivas': questoes.filter(tipo=Questao.Tipo.OBJETIVA).count(),
        'discursivas': questoes.filter(tipo=Questao.Tipo.DISCURSIVA).count(),
        'ativas': questoes.filter(ativo=True).count(),
    }

    # Filtros
    tipo = request.GET.get('tipo')
    dificuldade = request.GET.get('dificuldade')
    ativo = request.GET.get('ativo')
    q = request.GET.get('q')

    if tipo:
        questoes = questoes.filter(tipo=tipo)
    if dificuldade:
        questoes = questoes.filter(dificuldade=dificuldade)
    if ativo in ('1', 'true', 'True'):
        questoes = questoes.filter(ativo=True)
    elif ativo in ('0', 'false', 'False'):
        questoes = questoes.filter(ativo=False)
    if q:
        questoes = questoes.filter(enunciado__icontains=q)

    return render(
        request,
        'banco_questoes/questao_list.html',
        {
            'questoes': questoes,
            'metricas': metricas,
            'filtros': {
                'tipo': tipo or '',
                'dificuldade': dificuldade or '',
                'ativo': ativo or '',
                'q': q or '',
            },
            'tipos': Questao.Tipo.choices,
            'dificuldades': Questao.Dificuldade.choices,
        },
    )


# ---------------------------------------------------------------------------
# Criar questão (com formset inline)
# ---------------------------------------------------------------------------
@login_required
@permission_required('banco_questoes.add_questao', raise_exception=True)
def questao_create(request):
    """Cadastro de questão via formulário completo + formset inline de alternativas."""
    if request.method == 'POST':
        form = QuestaoForm(request.POST)
        formset = AlternativaFormSet(request.POST)

        if form.is_valid() and formset.is_valid():
            # Validação cruzada
            if form.validate_with_alternativas(formset):
                try:
                    with transaction.atomic():
                        questao = form.save()
                        # Salva alternativas apenas para questões objetivas
                        if questao.is_objetiva:
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
        form = QuestaoForm(request.POST, instance=questao)
        formset = AlternativaFormSet(request.POST, instance=questao)

        if form.is_valid() and formset.is_valid():
            if form.validate_with_alternativas(formset):
                try:
                    with transaction.atomic():
                        questao = form.save()
                        if questao.is_objetiva:
                            formset.save()
                        else:
                            # Se mudou para discursiva, remove alternativas existentes
                            questao.alternativas.all().delete()
                    messages.success(
                        request,
                        f'Questão Q{questao.pk} atualizada com sucesso!',
                    )
                    return redirect('banco_questoes:questao_list')
                except Exception as exc:
                    logger.exception('Erro ao atualizar questão: %s', exc)
                    messages.error(request, f'Erro interno ao atualizar: {exc}')
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


# ---------------------------------------------------------------------------
# Importação em Lote via CSV & Relatório de Inconsistências
# ---------------------------------------------------------------------------
@login_required
@permission_required('banco_questoes.add_questao', raise_exception=True)
def questao_import(request):
    """View para upload de CSV e exibição do relatório de inconsistências linha a linha."""
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
            parsed_rows = CSVQuestaoParser.parse(csv_file)
        except CSVParseError as err:
            messages.error(request, f'Erro ao ler o arquivo CSV: {err}')
            return render(request, 'banco_questoes/questao_import.html', {'titulo': 'Importação em Lote'})

        if not parsed_rows:
            messages.warning(request, 'O arquivo CSV enviado não contém linhas de dados.')
            return render(request, 'banco_questoes/questao_import.html', {'titulo': 'Importação em Lote'})

        validation_report = CSVQuestaoValidator.validate_rows(parsed_rows, check_db_duplicates=True)
        resultados_linhas = []

        for row in parsed_rows:
            line_num = row['linha']
            val_res = next((r for r in validation_report.linhas_validas if r.linha == line_num), None)

            if val_res and val_res.is_valid:
                data = val_res.cleaned_data
                try:
                    with transaction.atomic():
                        disc, _ = Disciplina.objects.get_or_create(nome=data['disciplina'])
                        topico_obj = None
                        if data.get('topico'):
                            topico_obj, _ = Topico.objects.get_or_create(disciplina=disc, nome=data['topico'])

                        questao = Questao.objects.create(
                            disciplina=disc,
                            topico=topico_obj,
                            tipo=data['tipo'],
                            dificuldade=data['dificuldade'],
                            enunciado=data['enunciado'],
                            criterios_correcao=data.get('criterios_correcao', ''),
                            ativo=True,
                        )

                        if data.get('tags'):
                            questao.tags.add(*data['tags'])

                        if data['tipo'] == Questao.Tipo.OBJETIVA:
                            for alt in data.get('alternativas', []):
                                Alternativa.objects.create(
                                    questao=questao,
                                    letra=alt['letra'],
                                    texto=alt['texto'],
                                    correta=alt['correta'],
                                )

                        resultados_linhas.append(
                            {
                                'linha': line_num,
                                'status': 'SUCESSO',
                                'enunciado': data['enunciado'],
                                'tipo': data['tipo'],
                                'disciplina': data['disciplina'],
                                'questao_id': questao.pk,
                                'erros': [],
                            }
                        )
                except Exception as exc:
                    logger.exception('Erro ao persistir questão da linha %s: %s', line_num, exc)
                    resultados_linhas.append(
                        {
                            'linha': line_num,
                            'status': 'REJEITADA',
                            'enunciado': row.get('enunciado', ''),
                            'tipo': row.get('tipo', ''),
                            'disciplina': row.get('disciplina', ''),
                            'questao_id': None,
                            'erros': [f'Erro interno de persistência: {exc}'],
                        }
                    )
            else:
                inval_res = next((r for r in validation_report.linhas_invalidas if r.linha == line_num), None)
                erros = inval_res.errors if inval_res else ['Linha com inconsistências não identificadas.']
                resultados_linhas.append(
                    {
                        'linha': line_num,
                        'status': 'REJEITADA',
                        'enunciado': row.get('enunciado', ''),
                        'tipo': row.get('tipo', ''),
                        'disciplina': row.get('disciplina', ''),
                        'questao_id': None,
                        'erros': erros,
                    }
                )

        total_sucesso = sum(1 for r in resultados_linhas if r['status'] == 'SUCESSO')
        total_rejeitadas = sum(1 for r in resultados_linhas if r['status'] == 'REJEITADA')

        relatorio = {
            'total_processadas': len(resultados_linhas),
            'total_sucesso': total_sucesso,
            'total_rejeitadas': total_rejeitadas,
            'linhas': resultados_linhas,
        }

        if total_sucesso > 0:
            messages.success(
                request,
                f'Importação processada: {total_sucesso} questão(ões) importada(s) com sucesso.',
            )
        if total_rejeitadas > 0:
            messages.warning(
                request,
                f'Atenção: {total_rejeitadas} questão(ões) foram rejeitadas devido a inconsistências/duplicidade.',
            )

    return render(
        request,
        'banco_questoes/questao_import.html',
        {
            'relatorio': relatorio,
            'titulo': 'Importação em Lote de Questões',
        },
    )
