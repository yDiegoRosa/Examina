"""Views do Módulo 4 — dashboard de fragilidades/forças (RF13) e psicometria (RF14)."""

import csv
from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render

from academico.models import Turma
from provas.models import ProvaAplicacao

from .services import (
    indice_facilidade_por_questao,
    questoes_fora_do_esperado,
    taxa_erro_por_dificuldade,
    taxa_erro_por_topico,
)

# Nº de tópicos exibidos no gráfico (os de maior erro); a tabela mostra todos.
TOPICO_CHART_LIMITE = 15


def _parse_data(valor):
    try:
        return datetime.strptime(valor, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None


@login_required
def dashboard_fragilidades(request):
    """
    Dashboard de fragilidades/forças: taxa de erro por tópico e por dificuldade,
    com filtros por turma, prova/aplicação e período (intervalo de datas).
    """
    turmas = Turma.objects.all().order_by('periodo', 'nome')
    aplicacoes = ProvaAplicacao.objects.select_related('prova', 'turma').order_by('-data_aplicacao')

    turma_id = request.GET.get('turma') or ''
    aplicacao_id = request.GET.get('aplicacao') or ''
    data_inicio = _parse_data(request.GET.get('data_inicio'))
    data_fim = _parse_data(request.GET.get('data_fim'))

    turma = turmas.filter(pk=turma_id).first() if turma_id else None
    aplicacao = aplicacoes.filter(pk=aplicacao_id).first() if aplicacao_id else None

    filtros = {'turma': turma, 'aplicacao': aplicacao, 'data_inicio': data_inicio, 'data_fim': data_fim}
    por_topico = taxa_erro_por_topico(**filtros)
    por_dificuldade = taxa_erro_por_dificuldade(**filtros)

    total_registros = sum(linha['total'] for linha in por_dificuldade)
    total_erros = sum(linha['erros'] for linha in por_dificuldade)
    taxa_media = (total_erros / total_registros) if total_registros else 0.0

    top_topicos = por_topico[:TOPICO_CHART_LIMITE]
    chart_topico = {
        'labels': [linha['topico'] for linha in top_topicos],
        'taxas': [round(linha['taxa_erro'] * 100, 1) for linha in top_topicos],
    }
    chart_dificuldade = {
        'labels': [linha['dificuldade_display'] for linha in por_dificuldade],
        'taxas': [round(linha['taxa_erro'] * 100, 1) for linha in por_dificuldade],
    }

    context = {
        'turmas': turmas,
        'aplicacoes': aplicacoes,
        'filtro_turma': turma_id,
        'filtro_aplicacao': aplicacao_id,
        'filtro_data_inicio': request.GET.get('data_inicio') or '',
        'filtro_data_fim': request.GET.get('data_fim') or '',
        'por_topico': por_topico,
        'por_dificuldade': por_dificuldade,
        'total_registros': total_registros,
        'total_erros': total_erros,
        'taxa_media_pct': round(taxa_media * 100, 1),
        'n_topicos': len(por_topico),
        'chart_topico': chart_topico,
        'chart_dificuldade': chart_dificuldade,
    }
    return render(request, 'desempenho/dashboard.html', context)


# ---------------------------------------------------------------------------
# Psicometria básica (RF14): índice de facilidade por questão + outliers + export
# ---------------------------------------------------------------------------
def _param_int(request, nome, padrao, minimo=1):
    try:
        return max(minimo, int(request.GET.get(nome)))
    except (TypeError, ValueError):
        return padrao


def _param_float(request, nome, padrao):
    try:
        valor = float(request.GET.get(nome))
    except (TypeError, ValueError):
        return padrao
    return min(1.0, max(0.0, valor))


def _csv_psicometria(itens):
    resposta = HttpResponse(content_type='text/csv; charset=utf-8')
    resposta['Content-Disposition'] = 'attachment; filename="psicometria_questoes.csv"'
    resposta.write('﻿')  # BOM p/ acentos no Excel
    escritor = csv.writer(resposta, delimiter=';')
    escritor.writerow(
        ['Questão', 'Enunciado', 'Disciplina', 'Tópico', 'Dificuldade cadastrada',
         'Respostas', 'Acertos', 'Índice de facilidade', 'Classificação']
    )
    for i in itens:
        escritor.writerow(
            [
                i['questao_id'],
                (i['enunciado'] or '').replace('\n', ' ')[:160],
                i['disciplina'] or '',
                i['topico'],
                i['dificuldade_cadastrada'],
                i['total_respostas'],
                i['acertos'],
                f'{i["indice_facilidade"]:.2f}',
                i['classe_label'],
            ]
        )
    return resposta


def _pdf_psicometria(context):
    from provas.services.pdf_generator import PDFGenerationError, PDFGeneratorService

    try:
        pdf = PDFGeneratorService.render_pdf_from_template('desempenho/pdf_psicometria.html', context)
    except PDFGenerationError as exc:
        return HttpResponse(f'Erro ao gerar o PDF: {exc}', status=500, content_type='text/plain; charset=utf-8')
    resposta = HttpResponse(pdf, content_type='application/pdf')
    resposta['Content-Disposition'] = 'attachment; filename="psicometria_questoes.pdf"'
    return resposta


@login_required
def psicometria(request):
    """
    Relatório de psicometria básica (RF14): índice de facilidade por questão
    (média global de `acertou` em todas as aplicações), destaque das questões com
    índice fora do esperado e exportação em CSV/PDF.
    """
    min_respostas = _param_int(request, 'min_respostas', 5)
    limite_baixo = _param_float(request, 'limite_baixo', 0.20)
    limite_alto = _param_float(request, 'limite_alto', 0.90)

    itens = indice_facilidade_por_questao(min_respostas=min_respostas)
    fora = questoes_fora_do_esperado(
        limite_baixo=limite_baixo, limite_alto=limite_alto, min_respostas=min_respostas
    )

    context = {
        'itens': itens,
        'fora': fora,
        'min_respostas': min_respostas,
        'limite_baixo': limite_baixo,
        'limite_alto': limite_alto,
        'total_questoes': len(itens),
        'total_fora': len(fora),
        'gerado_em': datetime.now(),
    }

    formato = (request.GET.get('formato') or '').lower()
    if formato == 'csv':
        return _csv_psicometria(itens)
    if formato == 'pdf':
        return _pdf_psicometria(context)

    return render(request, 'desempenho/psicometria.html', context)
