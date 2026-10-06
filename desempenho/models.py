"""Modelos do Módulo 4 (Desempenho): resultado da correção por questão."""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from simple_history.models import HistoricalRecords


class Desempenho(models.Model):
    """
    Resultado da correção de uma questão numa folha-resposta (base do Módulo 4).

    Uma linha por (folha, questão). Preenchido pela correção automática (OMR) para
    questões objetivas ou manualmente (discursivas / revisão do docente).
    """

    class TipoCorrecao(models.TextChoices):
        AUTOMATICA = 'AUTOMATICA', 'Automática (OMR)'
        MANUAL = 'MANUAL', 'Manual'

    folha_resposta = models.ForeignKey(
        'provas.FolhaResposta',
        on_delete=models.CASCADE,
        related_name='desempenhos',
        verbose_name='folha de resposta',
    )
    questao = models.ForeignKey(
        'banco_questoes.Questao',
        on_delete=models.PROTECT,
        related_name='desempenhos',
        verbose_name='questão',
    )
    tipo_correcao = models.CharField(
        'tipo de correção',
        max_length=20,
        choices=TipoCorrecao.choices,
        default=TipoCorrecao.AUTOMATICA,
    )
    resposta_marcada = models.CharField(
        'resposta marcada',
        max_length=10,
        blank=True,
        help_text='Alternativa(s) marcada(s) pelo aluno; vazio para em branco ou questão discursiva.',
    )
    acertou = models.BooleanField('acertou', null=True, blank=True)
    nota = models.DecimalField(
        'nota',
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('10'))],
        help_text='Nota de 0 a 10 (usada nas discursivas / correção manual).',
    )
    corrigido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='correcoes_desempenho',
        verbose_name='corrigido por',
    )
    data_correcao = models.DateTimeField('data da correção', null=True, blank=True)

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    # Histórico de cada alteração (quem, quando e o valor anterior): notas lançadas e
    # corrigidas à mão ficam rastreáveis num pedido de revisão. Consulta pelo admin.
    history = HistoricalRecords()

    class Meta:
        ordering = ['folha_resposta', 'questao']
        unique_together = [('folha_resposta', 'questao')]
        verbose_name = 'desempenho'
        verbose_name_plural = 'desempenhos'

    def __str__(self):
        marcado = self.resposta_marcada or '—'
        return f'{self.folha_resposta} • Q{self.questao_id} • marcou {marcado}'
