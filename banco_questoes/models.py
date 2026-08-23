import logging

from django.db import models
from simple_history.models import HistoricalRecords
from taggit.managers import TaggableManager

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Disciplina
# ---------------------------------------------------------------------------
class Disciplina(models.Model):
    """Componente curricular (ex.: Matemática, Português)."""

    nome = models.CharField(max_length=100, unique=True)
    descricao = models.TextField('descrição', blank=True)
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['nome']
        verbose_name = 'disciplina'
        verbose_name_plural = 'disciplinas'

    def __str__(self):
        return self.nome


# ---------------------------------------------------------------------------
# Tópico
# ---------------------------------------------------------------------------
class Topico(models.Model):
    """Subdivisão temática dentro de uma disciplina."""

    disciplina = models.ForeignKey(
        Disciplina,
        on_delete=models.CASCADE,
        related_name='topicos',
    )
    nome = models.CharField(max_length=150)
    descricao = models.TextField('descrição', blank=True)
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('disciplina', 'nome')]
        ordering = ['disciplina__nome', 'nome']
        verbose_name = 'tópico'
        verbose_name_plural = 'tópicos'

    def __str__(self):
        return f'{self.disciplina.nome} — {self.nome}'


# ---------------------------------------------------------------------------
# Questão
# ---------------------------------------------------------------------------
class Questao(models.Model):
    """Questão do banco — objetiva ou discursiva."""

    class Tipo(models.TextChoices):
        OBJETIVA = 'OBJ', 'Objetiva'
        DISCURSIVA = 'DIS', 'Discursiva'

    class Dificuldade(models.TextChoices):
        FACIL = 'F', 'Fácil'
        MEDIO = 'M', 'Médio'
        DIFICIL = 'D', 'Difícil'

    tipo = models.CharField(max_length=3, choices=Tipo.choices)
    dificuldade = models.CharField(max_length=1, choices=Dificuldade.choices)
    disciplina = models.ForeignKey(
        Disciplina,
        on_delete=models.PROTECT,
        related_name='questoes',
    )
    topico = models.ForeignKey(
        Topico,
        on_delete=models.SET_NULL,
        related_name='questoes',
        null=True,
        blank=True,
    )
    enunciado = models.TextField()
    criterios_correcao = models.TextField(
        'critérios de correção',
        blank=True,
        help_text='Obrigatório para questões discursivas.',
    )
    tags = TaggableManager(blank=True, help_text='Tags de classificação livre.')
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ['-criado_em']
        verbose_name = 'questão'
        verbose_name_plural = 'questões'

    def __str__(self):
        return f'Q{self.pk} — {self.get_tipo_display()} ({self.disciplina})'

    @property
    def is_objetiva(self):
        return self.tipo == self.Tipo.OBJETIVA

    @property
    def is_discursiva(self):
        return self.tipo == self.Tipo.DISCURSIVA


# ---------------------------------------------------------------------------
# Alternativa
# ---------------------------------------------------------------------------
LETRAS_VALIDAS = ('A', 'B', 'C', 'D', 'E')


class Alternativa(models.Model):
    """Opção de resposta para questões objetivas (A–E)."""

    questao = models.ForeignKey(
        Questao,
        on_delete=models.CASCADE,
        related_name='alternativas',
    )
    letra = models.CharField(max_length=1)
    texto = models.TextField()
    correta = models.BooleanField(default=False)

    class Meta:
        unique_together = [('questao', 'letra')]
        ordering = ['letra']
        verbose_name = 'alternativa'
        verbose_name_plural = 'alternativas'
        constraints = [
            models.CheckConstraint(
                condition=models.Q(letra__in=LETRAS_VALIDAS),
                name='alternativa_letra_a_e',
            ),
        ]

    def __str__(self):
        return f'{self.letra}) {self.texto[:50]}'
