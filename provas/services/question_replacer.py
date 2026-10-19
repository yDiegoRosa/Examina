import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import QuerySet

from banco_questoes.models import Questao

from .question_selector import consultar_questoes_candidatas_priorizadas
from .snapshot_builder import construir_snapshot_questao
from .version_generator import sincronizar_itens_versao, tipo_do_item

logger = logging.getLogger(__name__)


class SubstituicaoQuestaoError(ValidationError):
    """Exceção levantada quando uma substituição de questão viola regras pedagógicas ou de integridade."""


def obter_candidatas_substituicao(
    item,
    limite: int = 15,
    autor_questoes=None,
) -> QuerySet[Questao]:
    """
    Retorna questões candidatas ativas para substituir um item específico de uma prova.

    As candidatas são estritamente filtradas para a mesma célula pedagógica:
    - Mesma Disciplina
    - Mesmo Tópico Curricular
    - Mesmo Nível de Dificuldade (Fácil, Médio ou Difícil)
    - Mesmo Tipo (se modalidade restrita)
    - Exclusão de todas as questões já selecionadas no mesmo exame

    A ordenação segue o RF07: menor uso nas últimas 5 gerações da máscara + destaque ENADE.

    Args:
        item: Instância de ProvaQuestao.
        limite: Quantidade máxima de sugestões a retornar.
        autor_questoes: se informado, usa só as questões desse autor (professor monta provas
            só com as próprias questões — ``academico.acesso.autor_do_acervo``); ``None`` = acervo todo.

    Returns:
        QuerySet[Questao] anotado com `vezes_usada_recentemente` e ordenado por prioridade.
    """
    prova = item.prova
    mascara = prova.mascara
    disciplina = prova.disciplina_efetiva
    questao_atual = item.questao

    # IDs de todas as questões que já fazem parte da avaliação atual
    ids_existentes_na_prova = set(prova.questoes.values_list('id', flat=True))

    candidatas = consultar_questoes_candidatas_priorizadas(
        mascara=mascara,
        disciplina=disciplina,
        topico_id=questao_atual.topico_id,
        dificuldade=questao_atual.dificuldade,
        tipo=questao_atual.tipo,
        excluir_ids=ids_existentes_na_prova,
        autor_questoes=autor_questoes,
    ).prefetch_related('alternativas', 'topico', 'disciplina')

    return candidatas[:limite]


def substituir_questao_prova(
    item,
    nova_questao: Questao,
    autor=None,
):
    """
    Efetiva a substituição atômica de uma questão em um item da prova (ProvaQuestao),
    garantindo que a nova questão atenda aos mesmos critérios da célula e preservando
    o snapshot imutável.

    Se a prova já tiver versões (A/B/C) geradas, os itens de versão do item trocado são
    reconstruídos com as alternativas da nova questão. Se as versões já foram
    distribuídas a alunos, a troca é bloqueada.

    Args:
        item: Instância de ProvaQuestao a ser alterada.
        nova_questao: Nova instância de Questao a ser vinculada.
        autor: Usuário autenticado que realizou a alteração (opcional para logs).

    Returns:
        ProvaQuestao atualizado.

    Raises:
        SubstituicaoQuestaoError: Caso a nova questão viole regras de célula ou integridade.
    """
    prova = item.prova
    disciplina = prova.disciplina_efetiva
    questao_antiga = item.questao

    # 0. Prova travada: versões já distribuídas a alunos (folhas/atribuições)
    if prova.versoes_distribuidas:
        raise SubstituicaoQuestaoError(
            'Esta prova já foi aplicada: suas versões estão vinculadas a alunos (cartões-resposta ou '
            'atribuições). A troca de questões está bloqueada para preservar gabaritos e notas.'
        )

    # 1. Validação de status ativo
    if not nova_questao.ativo:
        raise SubstituicaoQuestaoError('A questão selecionada está inativa no banco de dados.')

    # 2. Validação de disciplina
    if disciplina and nova_questao.disciplina_id != disciplina.pk:
        raise SubstituicaoQuestaoError(
            f'A questão Q{nova_questao.pk} pertence à disciplina "{nova_questao.disciplina}", '
            f'mas a avaliação exige "{disciplina}".',
        )

    # 2b. Mesmo tipo (objetiva ↔ objetiva, discursiva ↔ discursiva): trocar o tipo mudaria
    #     a quantidade de bolhas do cartão-resposta e a numeração das objetivas no caderno.
    if nova_questao.tipo != tipo_do_item(item):
        raise SubstituicaoQuestaoError(
            'A questão de substituição deve ser do mesmo tipo da original (objetiva por objetiva, '
            'discursiva por discursiva).',
        )

    # 3. Validação de tópico curricular
    if nova_questao.topico_id != questao_antiga.topico_id:
        topico_antigo_nome = questao_antiga.topico.nome if questao_antiga.topico else 'Sem tópico'
        topico_novo_nome = nova_questao.topico.nome if nova_questao.topico else 'Sem tópico'
        raise SubstituicaoQuestaoError(
            f'A questão de substituição deve pertencer ao mesmo tópico da original '
            f'({topico_antigo_nome}), mas pertence a "{topico_novo_nome}".',
        )

    # 4. Validação de dificuldade (para não corromper a matriz da máscara)
    if nova_questao.dificuldade != questao_antiga.dificuldade:
        raise SubstituicaoQuestaoError(
            f'A questão de substituição deve ter dificuldade {questao_antiga.get_dificuldade_display()} '
            f'({questao_antiga.dificuldade}), mas possui {nova_questao.get_dificuldade_display()} ({nova_questao.dificuldade}).',
        )

    # 5. Validação de duplicidade na mesma prova
    if prova.questoes.filter(pk=nova_questao.pk).exclude(pk=questao_antiga.pk).exists():
        raise SubstituicaoQuestaoError(
            f'A questão Q{nova_questao.pk} já está presente nesta mesma avaliação.',
        )

    # 6. Efetivação atômica com congelamento do novo snapshot
    with transaction.atomic():
        item.questao = nova_questao
        item.snapshot_questao = construir_snapshot_questao(nova_questao)
        item.save(update_fields=['questao', 'snapshot_questao'])
        # Mantém os cadernos A/B/C coerentes com a nova questão (alternativas + gabarito)
        sincronizar_itens_versao(item)

        logger.info(
            'Questão #%d da prova #%d substituída com sucesso: Q%d -> Q%d por %s',
            item.numero,
            prova.pk,
            questao_antiga.pk,
            nova_questao.pk,
            autor or 'sistema',
        )

    return item
