"""
Ata de Avaliação de uma aplicação de prova: a lista impressa que circula na sala, no
modelo da ata emitida pelo sistema acadêmico da faculdade (Coruja).

Uma linha por aluno — nº, matrícula, nome e a versão (caderno) que ele recebe — com as
colunas de assinatura de presença, assinatura de recebimento e nota em branco, para
preenchimento à mão. O curso sai preenchido com o curso padrão, a disciplina só pelo nome e
o turno vem da turma (em branco em turmas antigas, sem turno); a grade não existe no
Examina e sai como campo em branco.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import Q
from django.utils import timezone

from .pdf_generator import PDFGeneratorService

if TYPE_CHECKING:
    from provas.models import ProvaAplicacao

# O Examina não cadastra curso (turmas e disciplinas não têm esse vínculo): a ata usa o
# curso atendido pelo sistema.
CURSO_PADRAO = 'Análise e Desenvolvimento de Sistemas'


def linhas_ata_avaliacao(aplicacao: ProvaAplicacao) -> list[dict]:
    """
    Alunos da ata em ordem alfabética, numerados a partir de 1.

    Entram os matriculados ativos da turma (mesma regra da distribuição de versões) e
    quem já tem cartão nesta aplicação mas saiu da turma depois — o cartão impresso existe
    e o aluno pode comparecer. A versão é a do cartão (a que está impressa nele); sem
    cartão, a da distribuição; sem nenhuma das duas, fica vazia.
    """
    from academico.models import Aluno, Matricula

    versao_do_cartao = dict(aplicacao.folhas.values_list('aluno_id', 'prova_versao__identificador'))
    versao_distribuida = dict(aplicacao.atribuicoes.values_list('aluno_id', 'versao__identificador'))

    alunos = (
        Aluno.objects.filter(
            Q(
                ativo=True,
                matriculas__turma=aplicacao.turma,
                matriculas__situacao=Matricula.Situacao.MATRICULADO,
            )
            | Q(pk__in=versao_do_cartao.keys())
        )
        .distinct()
        .order_by('nome', 'matricula')
    )

    return [
        {
            'numero': numero,
            'matricula': aluno.matricula,
            'nome': aluno.nome,
            'versao': versao_do_cartao.get(aluno.pk) or versao_distribuida.get(aluno.pk, ''),
        }
        for numero, aluno in enumerate(alunos, start=1)
    ]


def contexto_ata_avaliacao(aplicacao: ProvaAplicacao) -> dict:
    """Contexto do template ``provas/pdf_ata_avaliacao.html`` para a aplicação."""
    prova = aplicacao.prova
    disciplina = prova.disciplina_efetiva
    professor = ''
    if prova.autor:
        professor = prova.autor.get_full_name() or prova.autor.username

    return {
        'prova_titulo': prova.titulo,
        'curso': CURSO_PADRAO,
        'periodo': aplicacao.turma.periodo,
        'turma_nome': aplicacao.turma.nome,
        'turno': aplicacao.turma.get_turno_display() if aplicacao.turma.turno else '',
        'disciplina_nome': disciplina.nome if disciplina else '',
        'professor': professor,
        'data_aplicacao': aplicacao.data_aplicacao.strftime('%d/%m/%Y') if aplicacao.data_aplicacao else '',
        'emitido_em': timezone.localdate().strftime('%d/%m/%Y'),
        # Prova de caderno único (ex.: simulado ENADE): a coluna repetiria a mesma versão.
        'mostrar_versao': prova.versoes.count() > 1,
        'linhas': linhas_ata_avaliacao(aplicacao),
    }


def render_ata_pdf(aplicacao: ProvaAplicacao) -> bytes:
    """Renderiza o PDF da Ata de Avaliação da aplicação."""
    return PDFGeneratorService.render_pdf_from_template(
        'provas/pdf_ata_avaliacao.html',
        context=contexto_ata_avaliacao(aplicacao),
    )
