"""
Atribuição de versões (cadernos A/B/C…) por aluno numa aplicação de prova, em blocos.

Distribui as ``ProvaVersao`` de uma prova entre os alunos matriculados na turma da
aplicação: na lista de alunos em ordem alfabética, cada versão recebe uma faixa contínua
("da Ana ao Diego, versão A"), e as faixas têm o mesmo tamanho — quando a divisão não é
exata, as primeiras versões recebem um aluno a mais. Assim o professor confere a
distribuição pela ata e separa os cadernos nominais em pilhas por versão.

Quem entra na turma depois da distribuição recebe versão por ``incluir_alunos_novos``,
sem alterar a dos demais (os cartões já impressos continuam valendo).
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


def _chave_alfabetica(aluno) -> tuple[str, str]:
    return (aluno.nome, aluno.matricula)


def _versoes_em_blocos(versoes_na_ordem: list[int]) -> bool:
    """Cada versão aparece numa faixa só da lista alfabética (distribuição em blocos)."""
    vistas: set[int] = set()
    anterior = None
    for versao_id in versoes_na_ordem:
        if versao_id != anterior:
            if versao_id in vistas:
                return False
            vistas.add(versao_id)
            anterior = versao_id
    return True


def incluir_alunos_novos(aplicacao: ProvaAplicacao) -> list[AtribuicaoVersao]:
    """
    Dá versão aos alunos matriculados que ainda não têm (entraram na turma depois da
    distribuição), sem mexer em nenhuma atribuição existente — vale mesmo com os cartões já
    gerados.

    Na distribuição em blocos, o aluno entra no bloco alfabético onde o nome dele cai; entre
    dois blocos, no que tem menos alunos (empate: o anterior). Assim a faixa "de Ana a Diego"
    continua valendo. Em distribuição antiga (versões alternadas), vai para a versão com
    menos alunos.

    :return: as ``AtribuicaoVersao`` criadas (sequência a partir da maior existente).
    """
    import bisect
    from collections import Counter

    from academico.models import Matricula
    from provas.models import AtribuicaoVersao

    versoes = list(aplicacao.prova.versoes.order_by('numero_versao'))
    existentes = list(aplicacao.atribuicoes.select_related('aluno'))
    if not versoes:
        return []

    com_versao = {atribuicao.aluno_id for atribuicao in existentes}
    novos = [
        matricula.aluno
        for matricula in Matricula.objects.filter(
            turma=aplicacao.turma,
            situacao=Matricula.Situacao.MATRICULADO,
            aluno__ativo=True,
        )
        .exclude(aluno_id__in=com_versao)
        .select_related('aluno')
        .order_by('aluno__nome', 'aluno__matricula')
    ]
    if not novos:
        return []

    ordem = sorted((_chave_alfabetica(a.aluno), a.versao_id) for a in existentes)
    em_blocos = bool(ordem) and _versoes_em_blocos([versao_id for _, versao_id in ordem])
    contagem = Counter(versao_id for _, versao_id in ordem)
    sequencia = max((a.sequencia for a in existentes), default=0)

    criadas: list[AtribuicaoVersao] = []
    for aluno in novos:
        chave = _chave_alfabetica(aluno)
        if em_blocos:
            posicao = bisect.bisect(ordem, chave, key=lambda item: item[0])
            vizinhos = [ordem[i][1] for i in (posicao - 1, posicao) if 0 <= i < len(ordem)]
            versao_id = min(dict.fromkeys(vizinhos), key=lambda pk: contagem[pk])
        else:
            versao_id = min((versao.pk for versao in versoes), key=lambda pk: contagem[pk])
        sequencia += 1
        criadas.append(AtribuicaoVersao(aplicacao=aplicacao, aluno=aluno, versao_id=versao_id, sequencia=sequencia))
        contagem[versao_id] += 1
        bisect.insort(ordem, (chave, versao_id), key=lambda item: item[0])

    AtribuicaoVersao.objects.bulk_create(criadas)
    return criadas


def resumo_distribuicao(aplicacao: ProvaAplicacao) -> list[dict]:
    """
    Quantos alunos receberam cada versão e, quando a versão forma uma faixa contínua na
    ordem alfabética, o primeiro e o último aluno da faixa ("de Ana a Diego").

    Aplicações distribuídas antes da regra em blocos (versões alternadas) mostram só a
    quantidade: ``primeiro``/``ultimo`` ficam vazios.
    """
    atribuicoes = sorted(
        aplicacao.atribuicoes.select_related('aluno'), key=lambda atribuicao: _chave_alfabetica(atribuicao.aluno)
    )
    posicoes: dict[int, list[int]] = {}
    for posicao, atribuicao in enumerate(atribuicoes):
        posicoes.setdefault(atribuicao.versao_id, []).append(posicao)

    resumo = []
    for versao in aplicacao.prova.versoes.order_by('numero_versao'):
        da_versao = posicoes.get(versao.pk, [])
        continua = bool(da_versao) and da_versao[-1] - da_versao[0] + 1 == len(da_versao)
        resumo.append(
            {
                'versao': versao.identificador,
                'total': len(da_versao),
                'primeiro': atribuicoes[da_versao[0]].aluno.nome if continua else '',
                'ultimo': atribuicoes[da_versao[-1]].aluno.nome if continua else '',
            }
        )
    return resumo
