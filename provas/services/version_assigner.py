"""
Atribuição round-robin de versões (cadernos A/B/C…) por aluno numa aplicação de prova.

Distribui as ``ProvaVersao`` de uma prova entre os alunos matriculados na turma da
aplicação, ciclando as versões sobre a lista de alunos ordenada por nome. Como a
atribuição é linear (não circular), alunos adjacentes na lista nunca recebem a mesma
versão quando há ≥2 versões — atendendo ao objetivo de "evitar vizinhos com prova
idêntica".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import transaction

if TYPE_CHECKING:
    from provas.models import AtribuicaoVersao, ProvaAplicacao


def atribuir_versoes(aplicacao: ProvaAplicacao) -> list[AtribuicaoVersao]:
    """
    (Re)atribui versões aos alunos matriculados da turma da aplicação (idempotente).

    Ordena os alunos ativos com matrícula ``MATRICULADO`` por nome e cicla as versões
    da prova (round-robin). Substitui atribuições anteriores da mesma aplicação.

    :param aplicacao: instância de ``ProvaAplicacao``.
    :return: lista de ``AtribuicaoVersao`` criadas, na ordem da sequência.
    :raises ValueError: se a prova não possuir versões geradas ou se as folhas-resposta
        da aplicação já foram geradas (redistribuir trocaria o caderno de quem já
        recebeu a folha — e o gabarito usado na correção).
    """
    from academico.models import Matricula
    from provas.models import AtribuicaoVersao

    if aplicacao.folhas.exists():
        raise ValueError(
            'As folhas-resposta desta aplicação já foram geradas: redistribuir as versões trocaria o '
            'caderno (e o gabarito) de alunos que já receberam a folha.'
        )

    versoes = list(aplicacao.prova.versoes.order_by('numero_versao'))
    if not versoes:
        raise ValueError('A prova não possui versões geradas. Gere as versões (A/B/C…) antes de atribuir.')

    matriculas = (
        Matricula.objects.filter(
            turma=aplicacao.turma,
            situacao=Matricula.Situacao.MATRICULADO,
            aluno__ativo=True,
        )
        .select_related('aluno')
        .order_by('aluno__nome', 'aluno__matricula')
    )

    criadas: list[AtribuicaoVersao] = []
    with transaction.atomic():
        aplicacao.atribuicoes.all().delete()
        total_versoes = len(versoes)
        novos = [
            AtribuicaoVersao(
                aplicacao=aplicacao,
                aluno=matricula.aluno,
                versao=versoes[indice % total_versoes],
                sequencia=indice + 1,
            )
            for indice, matricula in enumerate(matriculas)
        ]
        AtribuicaoVersao.objects.bulk_create(novos)
        criadas = list(aplicacao.atribuicoes.select_related('aluno', 'versao').order_by('sequencia'))

    return criadas
