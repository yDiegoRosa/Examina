"""Views do Módulo 4 — dashboard de fragilidades/forças da turma (RF13)."""

from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from academico.models import Turma
from provas.models import ProvaAplicacao

from .services import taxa_erro_por_dificuldade, taxa_erro_por_topico

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
