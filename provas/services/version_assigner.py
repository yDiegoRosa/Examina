"""
Atribuição de versões (cadernos A/B/C…) por aluno numa aplicação de prova, em blocos.

Distribui as ``ProvaVersao`` de uma prova entre os alunos matriculados na turma da
aplicação: na lista de alunos em ordem alfabética, cada versão recebe uma faixa contínua
("da Ana ao Diego, versão A"), e as faixas têm o mesmo tamanho — quando a divisão não é
exata, as primeiras versões recebem um aluno a mais. Assim o professor confere a
distribuição pela ata e separa os cadernos nominais em pilhas por versão.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import transaction

if TYPE_CHECKING:
    from provas.models import AtribuicaoVersao, ProvaAplicacao


def indices_em_blocos(total_alunos: int, total_versoes: int) -> list[int]:
    """
    Índice da versão de cada aluno, na ordem alfabética: blocos contínuos e equilibrados.

    Ex.: 7 alunos e 3 versões → ``[0, 0, 0, 1, 1, 2, 2]`` (A=3, B=2, C=2). Com menos
    alunos do que versões, as últimas versões ficam sem aluno.
    """
    tamanho, sobra = divmod(total_alunos, total_versoes)
    return [indice for indice in range(total_versoes) for _ in range(tamanho + (1 if indice < sobra else 0))]


def atribuir_versoes(aplicacao: ProvaAplicacao) -> list[AtribuicaoVersao]:
    """
    (Re)atribui versões aos alunos matriculados da turma da aplicação (idempotente).

    Ordena os alunos ativos com matrícula ``MATRICULADO`` por nome e divide a lista em
    blocos contínuos e equilibrados, um por versão (ver ``indices_em_blocos``). Substitui
    atribuições anteriores da mesma aplicação.

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
            'Os cartões-resposta desta aplicação já foram gerados: redistribuir as versões trocaria o '
            'caderno (e o gabarito) de alunos que já receberam o cartão.'
        )

    versoes = list(aplicacao.prova.versoes.order_by('numero_versao'))
    if not versoes:
        raise ValueError('A prova não possui versões geradas. Gere as versões (A/B/C…) antes de atribuir.')

    matriculas = list(
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
        indices = indices_em_blocos(len(matriculas), len(versoes))
        novos = [
            AtribuicaoVersao(
                aplicacao=aplicacao,
                aluno=matricula.aluno,
                versao=versoes[indice_versao],
                sequencia=posicao,
            )
            for posicao, (matricula, indice_versao) in enumerate(zip(matriculas, indices, strict=True), start=1)
        ]
        AtribuicaoVersao.objects.bulk_create(novos)
        criadas = list(aplicacao.atribuicoes.select_related('aluno', 'versao').order_by('sequencia'))

    return criadas


def resumo_distribuicao(aplicacao: ProvaAplicacao) -> list[dict]:
    """
    Quantos alunos receberam cada versão e, quando a versão forma uma faixa contínua na
    ordem alfabética, o primeiro e o último aluno da faixa ("de Ana a Diego").

    Aplicações distribuídas antes da regra em blocos (versões alternadas) mostram só a
    quantidade: ``primeiro``/``ultimo`` ficam vazios.
    """
    por_versao: dict[int, list] = {}
    for atribuicao in aplicacao.atribuicoes.select_related('aluno').order_by('sequencia'):
        por_versao.setdefault(atribuicao.versao_id, []).append(atribuicao)

    resumo = []
    for versao in aplicacao.prova.versoes.order_by('numero_versao'):
        atribuicoes = por_versao.get(versao.pk, [])
        continua = bool(atribuicoes) and (atribuicoes[-1].sequencia - atribuicoes[0].sequencia + 1 == len(atribuicoes))
        resumo.append(
            {
                'versao': versao.identificador,
                'total': len(atribuicoes),
                'primeiro': atribuicoes[0].aluno.nome if continua else '',
                'ultimo': atribuicoes[-1].aluno.nome if continua else '',
            }
        )
    return resumo
