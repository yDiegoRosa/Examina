from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render

from banco_questoes.models import Questao

from .services.pdf_generator import PDFGeneratorService


@login_required
def prova_list(request):
    """View inicial para listagem de provas e avaliações do Módulo 2."""
    return render(
        request,
        'provas/prova_list.html',
        {
            'titulo': 'Módulo 2 • Provas e Avaliações',
        },
    )


@login_required
def pdf_teste_view(request):
    """
    View de teste para validação da compilação e layout de impressão com WeasyPrint.

    Renderiza uma avaliação acadêmica de demonstração contendo questões objetivas
    e discursivas do acervo, além do espelho de correção.
    """
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
