import random

from django.db.models import Case, Count, IntegerField, Q, QuerySet, Value, When

from banco_questoes.models import Questao


def consultar_questoes_candidatas_priorizadas(
    mascara=None,
    disciplina=None,
    topico_id: int | None = None,
    dificuldade: str | None = None,
    tipo: str | None = None,
    limite_provas_recentes: int = 5,
    excluir_ids: set[int] | list[int] | None = None,
) -> QuerySet[Questao]:
    """
    Retorna QuerySet de questões ativas anotadas e ordenadas pela restrição do RF07:
    priorizando questões NÃO usadas (ou MENOS usadas) nas últimas 5 gerações desta máscara.

    Estratégia de Priorização:
    1. Preferência a exames oficiais (ENADE / POSCOMP) quando `mascara.priorizar_enade=True`.
    2. Menor frequência de uso recente (`vezes_usada_recentemente ASC`: 0 usos > 1 uso > 2 usos).
    3. Aleatorização controlada (`?`) para desempate entre questões com mesmo histórico de uso.

    Args:
        mascara: Instância de Mascara de Avaliação (opcional).
        disciplina: Instância de Disciplina (opcional se mascara for informada).
        topico_id: ID do tópico curricular (opcional).
        dificuldade: Nível de dificuldade ('F', 'M', 'D', opcional).
        tipo: Tipo de questão ('OBJ', 'DIS', opcional).
        limite_provas_recentes: Quantidade de avaliações anteriores a considerar (padrão: 5).
        excluir_ids: Lista/set de IDs de questões a excluir (ex.: já selecionadas no mesmo exame).

    Returns:
        QuerySet[Questao] anotado com `vezes_usada_recentemente` e ordenado por prioridade.
    """
    from provas.models import Prova

    disc = disciplina or (mascara.disciplina if mascara else None)

    # 1. Identifica os IDs das últimas N provas geradas desta mesma máscara
    if mascara:
        ultimas_provas_ids = list(
            Prova.objects.filter(mascara=mascara)
            .order_by('-criado_em')
            .values_list('id', flat=True)[:limite_provas_recentes]
        )
        priorizar_enade = getattr(mascara, 'priorizar_enade', False)
        modalidade = getattr(mascara, 'modalidade', None)
    else:
        ultimas_provas_ids = []
        priorizar_enade = False
        modalidade = None

    # 2. Base de questões ativas da disciplina
    qs = Questao.objects.filter(ativo=True)
    if disc:
        qs = qs.filter(disciplina=disc)

    # 3. Filtros estruturais e pedagógicos
    if topico_id is not None:
        qs = qs.filter(topico_id=topico_id)

    if dificuldade:
        qs = qs.filter(dificuldade=dificuldade)

    if tipo:
        qs = qs.filter(tipo=tipo)
    elif modalidade == 'OBJ':
        qs = qs.filter(tipo=Questao.Tipo.OBJETIVA)
    elif modalidade == 'DIS':
        qs = qs.filter(tipo=Questao.Tipo.DISCURSIVA)

    if excluir_ids:
        qs = qs.exclude(id__in=excluir_ids)

    # 4. Anotação de utilizações nas últimas 5 provas e pontuação ENADE
    if ultimas_provas_ids:
        filtro_provas_recentes = Q(utilizacoes_provas__prova_id__in=ultimas_provas_ids)
    else:
        # Se não há provas anteriores, a contagem é 0 para todas as questões
        filtro_provas_recentes = Q(pk__in=[])

    if priorizar_enade:
        enade_expr = Case(
            When(origem__icontains='enade', then=Value(1)),
            When(origem__icontains='poscomp', then=Value(1)),
            default=Value(0),
            output_field=IntegerField(),
        )
    else:
        enade_expr = Value(0, output_field=IntegerField())

    qs = qs.annotate(
        vezes_usada_recentemente=Count(
            'utilizacoes_provas',
            filter=filtro_provas_recentes,
        ),
        is_enade=enade_expr,
    )

    # 5. Ordenação priorizada
    if priorizar_enade:
        return qs.order_by('-is_enade', 'vezes_usada_recentemente', '?')

    return qs.order_by('vezes_usada_recentemente', '?')


def selecionar_questoes_por_matriz(
    mascara,
    limite_provas_recentes: int = 5,
) -> list[Questao]:
    """
    Executa a seleção automatizada de questões para compor uma avaliação conforme o RF07,
    respeitando as quantidades inteiras calculadas na Matriz de Metas (Tópico × Dificuldade)
    e priorizando itens com menor uso nas últimas 5 gerações.

    Args:
        mascara: Instância de Mascara de Avaliação.
        limite_provas_recentes: Quantidade de gerações anteriores para controle de repetição.

    Returns:
        Lista de instâncias de Questao selecionadas sem duplicatas.
    """
    matriz = mascara.calcular_matriz_metas()
    questoes_selecionadas: list[Questao] = []
    ids_selecionados: set[int] = set()

    for celula in matriz.celulas:
        qtd_necessaria = celula.quantidade
        if qtd_necessaria <= 0:
            continue

        candidatas = list(
            consultar_questoes_candidatas_priorizadas(
                mascara=mascara,
                topico_id=celula.topico_id,
                dificuldade=celula.dificuldade,
                limite_provas_recentes=limite_provas_recentes,
                excluir_ids=ids_selecionados,
            )[:qtd_necessaria]
        )

        for q in candidatas:
            questoes_selecionadas.append(q)
            ids_selecionados.add(q.id)

    # Se a máscara solicitar embaralhamento geral das questões
    if mascara.embaralhar_questoes:
        random.shuffle(questoes_selecionadas)

    return questoes_selecionadas


def gerar_prova_automatizada(
    mascara,
    autor=None,
    titulo: str | None = None,
    numero_caderno: int = 1,
    limite_provas_recentes: int = 5,
):
    """
    Gera uma avaliação acadêmica persistida (Prova e ProvaQuestao) a partir de uma Máscara.

    Regra de Integridade e Bloqueio:
    - Valida o estoque por célula antes de qualquer comit no banco de dados.
    - Lança `EstoqueInsuficienteError` se faltar qualquer questão, impedindo provas incompletas.
    - Executa a persistência dentro de uma transação atômica (`transaction.atomic`).
    - Congela snapshots JSON da máscara, critérios e questões no instante da geração.

    Args:
        mascara: Instância de Mascara.
        autor: Usuário docente solicitante.
        titulo: Título customizado da avaliação (opcional).
        numero_caderno: Número do caderno de prova (padrão: 1).
        limite_provas_recentes: Janela deslizante de histórico de repetições (padrão: 5).

    Returns:
        Instância de Prova persistida com todas as suas questões vinculadas.

    Raises:
        EstoqueInsuficienteError: Caso o acervo não possua questões suficientes para todas as células.
    """
    from django.db import transaction

    from provas.models import Prova, ProvaQuestao
    from provas.services.snapshot_builder import (
        construir_snapshot_criterios_dificuldade,
        construir_snapshot_criterios_topico,
        construir_snapshot_mascara,
        construir_snapshot_matriz_metas,
        construir_snapshot_questao,
    )
    from provas.services.stock_validator import (
        EstoqueInsuficienteError,
        validar_disponibilidade_estoque,
    )

    # 1. Validação prévia de disponibilidade por célula
    relatorio = validar_disponibilidade_estoque(mascara)
    if not relatorio.esta_valido:
        raise EstoqueInsuficienteError(relatorio)

    # 2. Construção dos snapshots da máscara (antes da transação para não segurar lock)
    snap_mascara = construir_snapshot_mascara(mascara)
    snap_criterios_dif = construir_snapshot_criterios_dificuldade(mascara)
    snap_criterios_top = construir_snapshot_criterios_topico(mascara)
    snap_matriz = construir_snapshot_matriz_metas(mascara)

    # 3. Seleção e persistência atômica
    with transaction.atomic():
        questoes = selecionar_questoes_por_matriz(
            mascara=mascara,
            limite_provas_recentes=limite_provas_recentes,
        )

        if len(questoes) < mascara.quantidade_questoes:
            # Revalidação de segurança caso tenha ocorrido concorrência
            raise EstoqueInsuficienteError(relatorio)

        titulo_prova = titulo or f'{mascara.titulo} (Caderno {numero_caderno})'

        prova = Prova.objects.create(
            mascara=mascara,
            titulo=titulo_prova,
            numero_caderno=numero_caderno,
            autor=autor or mascara.autor,
            snapshot_mascara=snap_mascara,
            snapshot_criterios_dificuldade=snap_criterios_dif,
            snapshot_criterios_topico=snap_criterios_top,
            snapshot_matriz_metas=snap_matriz,
        )

        # Cálculo da pontuação proporcional por item
        pontuacao_item = round(mascara.valor_total / mascara.quantidade_questoes, 2)

        itens_prova = [
            ProvaQuestao(
                prova=prova,
                questao=q,
                numero=idx + 1,
                ordem=idx + 1,
                pontuacao=pontuacao_item,
                snapshot_questao=construir_snapshot_questao(q),
            )
            for idx, q in enumerate(questoes)
        ]
        ProvaQuestao.objects.bulk_create(itens_prova)

        # Gera versões A/B/C persistidas com permutações de questões e alternativas
        from provas.services.version_generator import gerar_versoes_prova

        gerar_versoes_prova(prova=prova, quantidade_versoes=3)

    return prova


