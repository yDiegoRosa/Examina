"""Modelos do domínio acadêmico: alunos, turmas e o vínculo (matrícula) entre eles."""

from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

periodo_validator = RegexValidator(
    regex=r'^\d{4}\.[1-2]$',
    message='Use o formato Ano.Semestre, por exemplo: 2026.2.',
)
complemento_validator = RegexValidator(
    regex=r'^[A-Z0-9]{1,10}$',
    message='Use só letras e números, sem espaços (ex.: A, B, 2).',
)


class Aluno(models.Model):
    """
    Aluno da instituição.

    A ``matricula`` é o identificador institucional único e será o elo com a folha
    de resposta na leitura óptica (RF10 — QR Code vinculando folha ↔ aluno).
    """

    nome = models.CharField('nome completo', max_length=200)
    matricula = models.CharField(
        'matrícula',
        max_length=30,
        unique=True,
        help_text='Número de matrícula institucional (único).',
    )
    ativo = models.BooleanField('ativo', default=True)

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['nome']
        verbose_name = 'aluno'
        verbose_name_plural = 'alunos'

    def __str__(self):
        return f'{self.nome} ({self.matricula})'


class Turma(models.Model):
    """
    Turma escopada por período letivo.

    Cada turma pertence a um único período e turno (ex.: ``2026.2`` + ``RSD`` + Manhã):
    o mesmo nome pode se repetir em turnos ou períodos diferentes. O histórico de
    vínculo de um aluno emerge de suas matrículas em turmas de períodos distintos.

    Na tela, a turma é cadastrada pela disciplina: o nome é a sigla dela, mais o complemento
    quando há mais de uma turma da mesma disciplina no período e turno ("SEG", "SEG-A").
    O nome continua gravado (ata, cadernos e arquivos usam ``nome``); ``disciplina`` fica vazia
    só em turmas antigas que a migration não conseguiu ligar a uma disciplina pela sigla.
    """

    class Turno(models.TextChoices):
        MANHA = 'MANHA', 'Manhã'
        NOITE = 'NOITE', 'Noite'

    periodo = models.CharField(
        'período',
        max_length=10,
        validators=[periodo_validator],
        help_text='Ano.semestre, por exemplo: 2026.2.',
    )
    nome = models.CharField(
        'nome da turma',
        max_length=120,
        help_text='Identificação da turma, por exemplo: RSD, MAC. Pode se repetir em outro turno ou período.',
    )
    # Vazio só em turmas cadastradas antes do campo existir; a tela de turma exige o turno.
    turno = models.CharField(
        'turno',
        max_length=10,
        choices=Turno.choices,
        blank=True,
        help_text='Turno das aulas da turma. Sai preenchido na ata de avaliação.',
    )
    disciplina = models.ForeignKey(
        'banco_questoes.Disciplina',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='turmas',
        verbose_name='disciplina',
    )
    complemento = models.CharField(
        'complemento',
        max_length=10,
        blank=True,
        validators=[complemento_validator],
        help_text='Só quando há mais de uma turma da mesma disciplina no período e turno (ex.: A, B).',
    )
    ativo = models.BooleanField('ativa', default=True)

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-periodo', 'nome', 'turno']
        unique_together = [('periodo', 'nome', 'turno')]
        verbose_name = 'turma'
        verbose_name_plural = 'turmas'

    def __str__(self):
        # O turno entra no nome exibido: turmas de mesmo nome e período só se distinguem por ele.
        turno = f' ({self.get_turno_display()})' if self.turno else ''
        return f'{self.periodo} — {self.nome}{turno}'

    @staticmethod
    def montar_nome(disciplina, complemento: str = '') -> str:
        """Nome da turma a partir da disciplina: "SEG", ou "SEG-A" com complemento."""
        return f'{disciplina.sigla}-{complemento}' if complemento else disciplina.sigla


class Matricula(models.Model):
    """
    Vínculo de um aluno a uma turma (histórico por período).

    A situação registra o estado do vínculo; o período é herdado da turma. Um
    aluno não pode ser matriculado duas vezes na mesma turma.
    """

    class Situacao(models.TextChoices):
        MATRICULADO = 'MATRICULADO', 'Matriculado'
        TRANCADO = 'TRANCADO', 'Trancado'
        APROVADO = 'APROVADO', 'Aprovado'
        REPROVADO = 'REPROVADO', 'Reprovado'
        CANCELADO = 'CANCELADO', 'Cancelado'

    aluno = models.ForeignKey(
        Aluno,
        on_delete=models.PROTECT,
        related_name='matriculas',
        verbose_name='aluno',
    )
    turma = models.ForeignKey(
        Turma,
        on_delete=models.PROTECT,
        related_name='matriculas',
        verbose_name='turma',
    )
    situacao = models.CharField(
        'situação',
        max_length=20,
        choices=Situacao.choices,
        default=Situacao.MATRICULADO,
    )
    data_matricula = models.DateField('data da matrícula', default=timezone.localdate)
    data_encerramento = models.DateField('data de encerramento', null=True, blank=True)
    observacao = models.TextField('observação', blank=True)

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-data_matricula', 'aluno__nome']
        unique_together = [('aluno', 'turma')]
        verbose_name = 'matrícula'
        verbose_name_plural = 'matrículas'

    def __str__(self):
        return f'{self.aluno} em {self.turma} [{self.get_situacao_display()}]'

    @property
    def ativa(self) -> bool:
        """Indica se o vínculo está atualmente ativo (aluno matriculado)."""
        return self.situacao == self.Situacao.MATRICULADO


class Docencia(models.Model):
    """
    O professor leciona uma disciplina numa turma (cadastro feito só pelo administrador).

    É o que define a "turma do professor" no acesso por perfil (``academico.acesso``): o
    professor aplica provas nas turmas das suas docências e, no desempenho, vê só os pares
    turma + disciplina delas — inclusive no Simulado ENADE, que é gerado pelo administrador.
    """

    professor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='docencias',
        verbose_name='professor',
    )
    disciplina = models.ForeignKey(
        'banco_questoes.Disciplina',
        on_delete=models.PROTECT,
        related_name='docencias',
        verbose_name='disciplina',
    )
    turma = models.ForeignKey(
        Turma,
        on_delete=models.PROTECT,
        related_name='docencias',
        verbose_name='turma',
    )
    ativo = models.BooleanField('ativa', default=True)

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['turma', 'disciplina__nome', 'professor__username']
        unique_together = [('professor', 'disciplina', 'turma')]
        verbose_name = 'docência'
        verbose_name_plural = 'docências'

    def __str__(self):
        nome = self.professor.get_full_name() or self.professor.get_username()
        return f'{nome} — {self.disciplina} — {self.turma}'


class PerfilProfessor(models.Model):
    """
    Dados da conta do professor criada em Acadêmico → Professores.

    ``troca_senha_pendente``: a conta foi criada (ou a senha redefinida) com uma senha temporária;
    até o professor escolher a dele, só a tela "Alterar senha" abre
    (``academico.middleware.TrocaDeSenhaObrigatoriaMiddleware``).
    """

    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='perfil_professor',
        verbose_name='usuário',
    )
    troca_senha_pendente = models.BooleanField('troca de senha pendente', default=True)

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'perfil de professor'
        verbose_name_plural = 'perfis de professor'

    def __str__(self):
        return self.usuario.get_username()
