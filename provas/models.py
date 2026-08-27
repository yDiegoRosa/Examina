import logging
import math

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from simple_history.models import HistoricalRecords

from banco_questoes.models import Questao

logger = logging.getLogger(__name__)


class Mascara(models.Model):
    """
    Máscara de Prova (Blueprint / Matriz de Critérios).

    Define as diretrizes pedagógicas e quantitativas para geração automatizada
    de avaliações acadêmicas a partir do Banco de Questões da FAETERJ-Rio.
    """

    class Modalidade(models.TextChoices):
        OBJETIVA = 'OBJ', 'Somente Objetivas'
        DISCURSIVA = 'DIS', 'Somente Discursivas'
        MISTA = 'MISTA', 'Mista (Objetivas e Discursivas)'

    titulo = models.CharField(
        'título da máscara',
        max_length=200,
        help_text='Identificação clara da máscara (ex.: AV1 Oficial — Estrutura de Dados 2026.2).',
    )
    descricao = models.TextField(
        'descrição / observações',
        blank=True,
        help_text='Orientações adicionais sobre o objetivo pedagógico ou contexto da avaliação.',
    )
    disciplina = models.ForeignKey(
        'banco_questoes.Disciplina',
        on_delete=models.PROTECT,
        related_name='mascaras',
        verbose_name='disciplina',
    )
    quantidade_questoes = models.PositiveIntegerField(
        'quantidade total de questões',
        default=10,
        validators=[MinValueValidator(1), MaxValueValidator(50)],
        help_text='Número total de itens que comporão a prova.',
    )
    modalidade = models.CharField(
        'modalidade das questões',
        max_length=10,
        choices=Modalidade.choices,
        default=Modalidade.OBJETIVA,
    )
    quantidade_objetivas = models.PositiveIntegerField(
        'quantidade de objetivas',
        default=10,
        help_text='Preenchido automaticamente ou ajustado na modalidade mista.',
    )
    quantidade_discursivas = models.PositiveIntegerField(
        'quantidade de discursivas',
        default=0,
        help_text='Preenchido automaticamente ou ajustado na modalidade mista.',
    )

    # Distribuição por Nível de Dificuldade (%)
    percentual_facil = models.PositiveIntegerField(
        '% Fácil',
        default=40,
        validators=[MaxValueValidator(100)],
        help_text='Percentual de questões de nível fácil.',
    )
    percentual_medio = models.PositiveIntegerField(
        '% Médio',
        default=40,
        validators=[MaxValueValidator(100)],
        help_text='Percentual de questões de nível médio.',
    )
    percentual_dificil = models.PositiveIntegerField(
        '% Difícil',
        default=20,
        validators=[MaxValueValidator(100)],
        help_text='Percentual de questões de nível difícil.',
    )

    # Parâmetros Avaliativos
    valor_total = models.DecimalField(
        'valor total da prova',
        max_digits=5,
        decimal_places=2,
        default=10.00,
        help_text='Pontuação total máxima da avaliação (ex.: 10.00 pts).',
    )
    tempo_estimado_minutos = models.PositiveIntegerField(
        'tempo estimado (minutos)',
        default=100,
        blank=True,
        null=True,
        help_text='Duração sugerida para aplicação da prova em minutos.',
    )

    # Regras do Algoritmo de Sorteio
    embaralhar_questoes = models.BooleanField(
        'embaralhar questões',
        default=True,
        help_text='Embaralha a ordem das questões ao gerar múltiplos cadernos de prova.',
    )
    embaralhar_alternativas = models.BooleanField(
        'embaralhar alternativas',
        default=True,
        help_text='Embaralha as alternativas (A-E) das questões objetivas em cada caderno.',
    )
    priorizar_enade = models.BooleanField(
        'priorizar questões do ENADE / POSCOMP',
        default=False,
        help_text='Dá preferência a questões cadastradas com origem de exames acadêmicos oficiais.',
    )

    # Metadados e Auditoria
    autor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='mascaras_criadas',
        verbose_name='docente criador',
    )
    ativo = models.BooleanField('máscara ativa', default=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ['-criado_em']
        verbose_name = 'máscara de prova'
        verbose_name_plural = 'máscaras de prova'

    def __str__(self):
        return f'{self.titulo} ({self.disciplina.nome})'

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Sincroniza critérios normalizados de dificuldade
        self.sincronizar_criterios_dificuldade()

    def clean(self):
        super().clean()
        errors = {}

        # 1. Validação da soma de dificuldade (deve fechar em 100%)
        soma_dificuldade = self.percentual_facil + self.percentual_medio + self.percentual_dificil
        if soma_dificuldade != 100:
            errors['percentual_facil'] = (
                f'A soma dos percentuais de dificuldade deve ser exatamente 100% (atual: {soma_dificuldade}%).'
            )

        # 2. Validação da modalidade e contagens
        if self.modalidade == self.Modalidade.OBJETIVA:
            self.quantidade_objetivas = self.quantidade_questoes
            self.quantidade_discursivas = 0
        elif self.modalidade == self.Modalidade.DISCURSIVA:
            self.quantidade_discursivas = self.quantidade_questoes
            self.quantidade_objetivas = 0
        elif self.modalidade == self.Modalidade.MISTA:
            if self.quantidade_objetivas + self.quantidade_discursivas != self.quantidade_questoes:
                errors['quantidade_questoes'] = (
                    f'Na modalidade mista, a soma de objetivas ({self.quantidade_objetivas}) e '
                    f'discursivas ({self.quantidade_discursivas}) deve ser igual ao total '
                    f'de questões ({self.quantidade_questoes}).'
                )

        if errors:
            raise ValidationError(errors)

    def calcular_distribuicao_dificuldade(self) -> dict[str, int]:
        """
        Calcula a quantidade exata de questões inteiras para cada nível de dificuldade,
        aplicando ajuste proporcional de arredondamento para garantir a soma exata.
        """
        total = self.quantidade_questoes
        qtd_facil = math.floor((total * self.percentual_facil) / 100)
        qtd_medio = math.floor((total * self.percentual_medio) / 100)
        qtd_dificil = math.floor((total * self.percentual_dificil) / 100)

        # Distribui resíduo de arredondamento no maior percentual
        residuo = total - (qtd_facil + qtd_medio + qtd_dificil)
        if residuo > 0:
            if self.percentual_facil >= self.percentual_medio and self.percentual_facil >= self.percentual_dificil:
                qtd_facil += residuo
            elif self.percentual_medio >= self.percentual_dificil:
                qtd_medio += residuo
            else:
                qtd_dificil += residuo

        return {
            'F': qtd_facil,
            'M': qtd_medio,
            'D': qtd_dificil,
        }

    def sincronizar_criterios_dificuldade(self):
        """
        Garante a sincronização dos registros normalizados em MascaraCriterioDificuldade.
        """
        distribuicao = self.calcular_distribuicao_dificuldade()
        criterios_map = [
            (Questao.Dificuldade.FACIL, self.percentual_facil, distribuicao['F']),
            (Questao.Dificuldade.MEDIO, self.percentual_medio, distribuicao['M']),
            (Questao.Dificuldade.DIFICIL, self.percentual_dificil, distribuicao['D']),
        ]

        for dif_code, perc, qtd in criterios_map:
            MascaraCriterioDificuldade.objects.update_or_create(
                mascara=self,
                dificuldade=dif_code,
                defaults={'percentual': perc, 'quantidade_questoes': qtd},
            )

    @property
    def criterios_topico(self):
        """Propriedade de conveniência para a convenção de critérios de tópicos."""
        return self.topicos_distribuicao

    @property
    def topicos_distribuicao_compat(self):
        """Compatibilidade."""
        return self.topicos_distribuicao


class MascaraCriterioDificuldade(models.Model):
    """
    Critério normalizado de distribuição por nível de dificuldade em uma Máscara de Prova.
    """

    mascara = models.ForeignKey(
        Mascara,
        on_delete=models.CASCADE,
        related_name='criterios_dificuldade',
        verbose_name='máscara de prova',
    )
    dificuldade = models.CharField(
        'nível de dificuldade',
        max_length=1,
        choices=Questao.Dificuldade.choices,
    )
    percentual = models.PositiveIntegerField(
        '% de dificuldade',
        default=0,
        validators=[MaxValueValidator(100)],
        help_text='Percentual da avaliação dedicado a este nível de dificuldade.',
    )
    quantidade_questoes = models.PositiveIntegerField(
        'quantidade calculada de questões',
        default=0,
        help_text='Número exato de questões calculadas para esta dificuldade.',
    )

    class Meta:
        unique_together = [('mascara', 'dificuldade')]
        ordering = ['dificuldade']
        verbose_name = 'critério de dificuldade'
        verbose_name_plural = 'critérios de dificuldade'

    def __str__(self):
        return f'{self.mascara.titulo} • Dificuldade {self.get_dificuldade_display()} ({self.percentual}%)'


class MascaraCriterioTopico(models.Model):
    """
    Critério normalizado de distribuição por tópico curricular em uma Máscara de Prova.
    """

    mascara = models.ForeignKey(
        Mascara,
        on_delete=models.CASCADE,
        related_name='topicos_distribuicao',
        verbose_name='máscara de prova',
    )
    topico = models.ForeignKey(
        'banco_questoes.Topico',
        on_delete=models.CASCADE,
        related_name='mascaras_criterio_topico',
        verbose_name='tópico curricular',
    )
    percentual = models.PositiveIntegerField(
        '% do tópico',
        default=0,
        validators=[MaxValueValidator(100)],
        help_text='Percentual da prova dedicado a este tópico.',
    )
    quantidade_questoes = models.PositiveIntegerField(
        'quantidade estimada de questões',
        default=0,
        help_text='Número de questões calculadas para este tópico.',
    )

    class Meta:
        unique_together = [('mascara', 'topico')]
        ordering = ['topico__nome']
        verbose_name = 'critério de tópico'
        verbose_name_plural = 'critérios de tópicos'

    def __str__(self):
        return f'{self.mascara.titulo} • {self.topico.nome} ({self.percentual}%)'

    def clean(self):
        super().clean()
        topico = getattr(self, 'topico', None)
        mascara = getattr(self, 'mascara', None)
        if topico and mascara:
            disc_mascara_id = getattr(mascara, 'disciplina_id', None)
            if disc_mascara_id and topico.disciplina_id != disc_mascara_id:
                raise ValidationError(f'O tópico "{topico.nome}" não pertence à disciplina da máscara.')


# Aliases para retrocompatibilidade contínua
MascaraProva = Mascara
MascaraTopico = MascaraCriterioTopico
