import logging
from collections import Counter
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction

from banco_questoes.models import Disciplina, Questao
from provas.models import Mascara, Prova, ProvaQuestao

from .caderno_layout import grupo_da_questao, numeros_por_tipo
from .snapshot_builder import construir_snapshot_questao

logger = logging.getLogger(__name__)


class ProvaManualError(ValidationError):
    """Exceção levantada quando a montagem manual de prova é inválida."""


PONTUACAO_PADRAO = Decimal('1.00')
PONTUACAO_MAXIMA = Decimal('999.99')  # limite de ProvaQuestao.pontuacao (5 dígitos, 2 decimais)


def validar_pontuacao(valor) -> Decimal:
    """
    Pontuação de uma questão da prova: aceita número ou texto com vírgula/ponto ("1,5").
    Deve ser maior que zero e caber em ``ProvaQuestao.pontuacao``; arredonda para 2 casas.

    :raises ProvaManualError: valor vazio, inválido, zero/negativo ou acima do limite.
    """
    try:
        pontuacao = Decimal(str(valor).strip().replace(',', '.'))
    except (InvalidOperation, ValueError) as exc:
        raise ProvaManualError(f'Pontuação inválida: "{valor}".') from exc
    if not pontuacao.is_finite() or pontuacao <= 0:
        raise ProvaManualError(f'A pontuação de cada questão deve ser maior que zero (recebido: "{valor}").')
    pontuacao = pontuacao.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    if pontuacao <= 0 or pontuacao > PONTUACAO_MAXIMA:
        raise ProvaManualError(f'A pontuação de cada questão deve ficar entre 0,01 e {PONTUACAO_MAXIMA} pontos.')
    return pontuacao


def criar_prova_manual(
    titulo: str,
    disciplina: Disciplina | None = None,
    questoes_ids: list[int] | None = None,
    autor=None,
    numero_caderno: int = 1,
    mascara: Mascara | None = None,
    pontuacoes: dict[int, Decimal | str] | None = None,
) -> Prova:
    """
    Cria e persiste uma avaliação por montagem 100% manual (seleção livre no acervo),
    sem intervenção de algoritmos de sorteio ou máscaras prévias, ou utilizando uma
    máscara guia como referência de meta quantitativa.

    Validações rígidas aplicadas:
    1. A prova não pode ficar vazia (quantidade > 0).
    2. Se houver máscara guia vinculada, a quantidade selecionada deve bater exatamente
       com a quantidade definida na máscara (len(questoes) == mascara.quantidade_questoes).
    3. Todas as questões selecionadas devem pertencer à disciplina informada e estar ativas.
    4. Não pode haver questões duplicadas.

    Args:
        titulo: Nome / cabeçalho da avaliação.
        disciplina: Instância de Disciplina (opcional se mascara for informada).
        questoes_ids: Lista de IDs das questões selecionadas no acervo.
        autor: Usuário autenticado que criou a prova.
        numero_caderno: Número do caderno da avaliação (padrão 1).
        mascara: Instância opcional de Mascara de Avaliação guia.
        pontuacoes: Pontuação por ID de questão, definida pelo docente na montagem. Questão
            sem valor informado vale ``PONTUACAO_PADRAO`` (1,00).

    Returns:
        Instância de Prova criada e persistida.

    Raises:
        ProvaManualError: Caso viole qualquer regra de validação quantitativa ou pedagógica.
    """
    if not titulo or not titulo.strip():
        raise ProvaManualError('O título da avaliação é obrigatório.')

    if mascara:
        disciplina = mascara.disciplina
    elif not disciplina:
        raise ProvaManualError('A disciplina da avaliação é obrigatória.')

    questoes_ids = questoes_ids or []

    # 1. Validação: Impedir prova vazia
    if not questoes_ids:
        raise ProvaManualError('A avaliação não pode ficar vazia. Selecione ao menos uma questão para compor a prova.')

    # Remove duplicidades preservando ordem
    ids_unicos = list(dict.fromkeys(questoes_ids))

    # 2. Validação: Total deve bater exatamente com a quantidade definida na máscara
    if mascara and len(ids_unicos) != mascara.quantidade_questoes:
        raise ProvaManualError(
            f'A máscara "{mascara.titulo}" exige exatamente {mascara.quantidade_questoes} questão(ões), '
            f'mas você selecionou {len(ids_unicos)}.',
        )

    # 3. Busca e validação de existência e status ativo
    questoes = list(
        Questao.objects.filter(pk__in=ids_unicos, ativo=True)
        .select_related('disciplina', 'topico')
        .prefetch_related('alternativas')
    )

    if len(questoes) != len(ids_unicos):
        ids_encontrados = {q.pk for q in questoes}
        faltantes = set(ids_unicos) - ids_encontrados
        raise ProvaManualError(f'As seguintes questões não foram encontradas ou estão inativas: {faltantes}')

    # 4. Validação de pertinência de disciplina
    for q in questoes:
        if q.disciplina_id != disciplina.pk:
            raise ProvaManualError(
                f'A questão Q{q.pk} pertence à disciplina "{q.disciplina.nome}", '
                f'mas a avaliação exige "{disciplina.nome}".',
            )

    # 5. Pontuação de cada questão (definida pelo docente; sem valor, PONTUACAO_PADRAO)
    pontuacoes = pontuacoes or {}
    pontuacao_por_questao = {
        qid: validar_pontuacao(pontuacoes[qid]) if qid in pontuacoes else PONTUACAO_PADRAO for qid in ids_unicos
    }

    # Ordena as instâncias de questão conforme a ordem fornecida em ids_unicos
    questoes_map = {q.pk: q for q in questoes}
    questoes_ordenadas = [questoes_map[qid] for qid in ids_unicos]
    # Ordem do caderno em blocos (discursivas, objetivas longas, objetivas curtas — ver
    # caderno_layout), mantendo a ordem escolhida pelo docente dentro de cada bloco.
    questoes_ordenadas.sort(key=grupo_da_questao)

    # Constrói snapshots consolidados da avaliação
    total_questoes = len(questoes_ordenadas)
    contagem_dif = Counter(q.dificuldade for q in questoes_ordenadas)
    contagem_topico = Counter(q.topico_id for q in questoes_ordenadas)
    contagem_celula = Counter((q.topico_id, q.dificuldade) for q in questoes_ordenadas)

    dif_display_map = {'F': 'Fácil', 'M': 'Médio', 'D': 'Difícil'}

    snapshot_criterios_dif = [
        {
            'dificuldade': d,
            'dificuldade_display': dif_display_map.get(d, d),
            'percentual': round((contagem_dif[d] / total_questoes) * 100, 1) if total_questoes else 0,
            'quantidade': contagem_dif[d],
        }
        for d in ['F', 'M', 'D']
    ]

    snapshot_criterios_top = []
    topicos_vistos = {}
    for q in questoes_ordenadas:
        if q.topico_id and q.topico_id not in topicos_vistos:
            topicos_vistos[q.topico_id] = q.topico.nome if q.topico else 'Sem tópico'

    for tid, tnome in topicos_vistos.items():
        qtd = contagem_topico[tid]
        snapshot_criterios_top.append(
            {
                'topico_id': tid,
                'topico_nome': tnome,
                'percentual': round((qtd / total_questoes) * 100, 1) if total_questoes else 0,
                'quantidade': qtd,
            }
        )

    celulas_matriz = []
    for (tid, dif), qtd in contagem_celula.items():
        celulas_matriz.append(
            {
                'topico_id': tid,
                'topico_nome': topicos_vistos.get(tid, 'Sem tópico'),
                'dificuldade': dif,
                'dificuldade_display': dif_display_map.get(dif, dif),
                'quantidade': qtd,
                'percentual_composto': round((qtd / total_questoes) * 100, 1) if total_questoes else 0,
                'cota_exata': float(qtd),
                'resto': 0.0,
            }
        )

    snapshot_matriz = {
        'total_questoes': total_questoes,
        'totais_por_dificuldade': dict(contagem_dif),
        'celulas': celulas_matriz,
    }

    if mascara:
        snapshot_mascara = {
            'mascara_id': mascara.pk,
            'titulo': mascara.titulo,
            'disciplina_id': disciplina.pk,
            'disciplina_nome': disciplina.nome,
            'quantidade_questoes': total_questoes,
            'modalidade': mascara.modalidade,
            'modalidade_display': mascara.get_modalidade_display(),
            'valor_total': str(mascara.valor_total),
            'tipo_geracao': 'MANUAL',
            'embaralhar_questoes': mascara.embaralhar_questoes,
            'embaralhar_alternativas': mascara.embaralhar_alternativas,
            'priorizar_enade': mascara.priorizar_enade,
            'quantidade_versoes': mascara.quantidade_versoes,
        }
    else:
        snapshot_mascara = {
            'mascara_id': None,
            'titulo': titulo,
            'disciplina_id': disciplina.pk,
            'disciplina_nome': disciplina.nome,
            'quantidade_questoes': total_questoes,
            'modalidade': 'MANUAL',
            'modalidade_display': 'Montagem Manual (Seleção Livre)',
            'tipo_geracao': 'MANUAL',
            'embaralhar_questoes': False,
            'embaralhar_alternativas': False,
            'priorizar_enade': False,
        }

    with transaction.atomic():
        prova = Prova.objects.create(
            titulo=titulo,
            disciplina=disciplina,
            tipo_geracao=Prova.TipoGeracao.MANUAL,
            mascara=mascara,
            numero_caderno=numero_caderno,
            autor=autor,
            snapshot_mascara=snapshot_mascara,
            snapshot_criterios_dificuldade=snapshot_criterios_dif,
            snapshot_criterios_topico=snapshot_criterios_top,
            snapshot_matriz_metas=snapshot_matriz,
        )

        # Número contado dentro do tipo: objetivas 1..N (as do cartão) e discursivas 1..k.
        numeros = numeros_por_tipo(q.tipo for q in questoes_ordenadas)
        itens_para_criar = []
        for idx, (q, numero) in enumerate(zip(questoes_ordenadas, numeros, strict=True), start=1):
            itens_para_criar.append(
                ProvaQuestao(
                    prova=prova,
                    questao=q,
                    numero=numero,
                    ordem=idx,
                    pontuacao=pontuacao_por_questao[q.pk],
                    snapshot_questao=construir_snapshot_questao(q),
                )
            )

        ProvaQuestao.objects.bulk_create(itens_para_criar)

        # Gera as versões (cadernos) persistidas — quantidade definida pela máscara
        from provas.services.version_generator import gerar_versoes_prova, quantidade_versoes_da_prova

        gerar_versoes_prova(prova=prova, quantidade_versoes=quantidade_versoes_da_prova(prova))

        logger.info(
            'Prova manual #%d ("%s") criada com sucesso contendo %d questões (Máscara: %s) por %s',
            prova.pk,
            prova.titulo,
            total_questoes,
            mascara.pk if mascara else 'Nenhuma',
            autor or 'sistema',
        )

    return prova
