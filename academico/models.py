"""Modelos do domínio acadêmico: alunos, turmas e matrículas."""

from django.db import models


class Aluno(models.Model):
    """Aluno cadastrado na instituição."""

    nome = models.CharField(max_length=200)
    matricula = models.CharField('matrícula', max_length=30, unique=True)
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['nome', 'matricula']
        verbose_name = 'aluno'
        verbose_name_plural = 'alunos'

    def __str__(self):
        return f'{self.nome} ({self.matricula})'


class Turma(models.Model):
    """Turma da instituição em um determinado período letivo."""

    nome = models.CharField(max_length=150)
    periodo = models.CharField(
        'período letivo',
        max_length=20,
        help_text='Exemplo: 2026.2',
    )
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['nome', 'periodo']
        verbose_name = 'turma'
        verbose_name_plural = 'turmas'
        constraints = [
            models.UniqueConstraint(
                fields=['nome', 'periodo'],
                name='academico_turma_nome_periodo_unicos',
            ),
        ]

    def __str__(self):
        return f'{self.nome} — {self.periodo}'


class Matricula(models.Model):
    """Vínculo de um aluno com uma turma de determinado período."""

    aluno = models.ForeignKey(
        Aluno,
        on_delete=models.PROTECT,
        related_name='matriculas',
    )
    turma = models.ForeignKey(
        Turma,
        on_delete=models.PROTECT,
        related_name='matriculas',
    )
    ativo = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['turma__periodo', 'turma__nome', 'aluno__nome']
        verbose_name = 'matrícula'
        verbose_name_plural = 'matrículas'
        constraints = [
            models.UniqueConstraint(
                fields=['aluno', 'turma'],
                name='academico_matricula_aluno_turma_unicos',
            ),
        ]

    def __str__(self):
        return f'{self.aluno} — {self.turma}'
