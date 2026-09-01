import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from simple_history.models import HistoricalRecords

from banco_questoes.models import Disciplina, Questao

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
            diff_str = (
                f'faltam {100 - soma_dificuldade}%'
                if soma_dificuldade < 100
                else f'excede em {soma_dificuldade - 100}%'
            )
            errors['percentual_facil'] = (
                f'A soma dos percentuais de dificuldade (Fácil + Médio + Difícil) deve ser exatamente 100% (atual: {soma_dificuldade}% — {diff_str}).'
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

    def calcular_matriz_metas(self):
        """
        Calcula as metas de quantidade inteira de questões por célula (Tópico × Dificuldade)
        utilizando o Método do Maior Resto (Hamilton-Hare).
        """
        from .services.matrix_calculator import calcular_metas_celulas

        topicos_data = []
        if self.pk:
            criterios = self.topicos_distribuicao.select_related('topico').all()
            for crit in criterios:
                topicos_data.append(
                    {
                        'id': crit.topico_id,
                        'nome': crit.topico.nome,
                        'percentual': crit.percentual,
                    }
                )

        return calcular_metas_celulas(
            quantidade_total=self.quantidade_questoes,
            percentual_facil=self.percentual_facil,
            percentual_medio=self.percentual_medio,
            percentual_dificil=self.percentual_dificil,
            topicos=topicos_data if topicos_data else None,
        )

    def calcular_distribuicao_dificuldade(self) -> dict[str, int]:
        """
        Calcula a quantidade de questões inteiras para cada nível de dificuldade (F, M, D)
        aplicando o Método do Maior Resto.
        """
        from .services.matrix_calculator import calcular_metas_celulas

        resultado = calcular_metas_celulas(
            quantidade_total=self.quantidade_questoes,
            percentual_facil=self.percentual_facil,
            percentual_medio=self.percentual_medio,
            percentual_dificil=self.percentual_dificil,
            topicos=None,
        )
        return resultado.totais_por_dificuldade

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

    def consultar_questoes_priorizadas(
        self,
        topico_id=None,
        dificuldade=None,
        tipo=None,
        limite_provas_recentes=5,
        excluir_ids=None,
    ):
        """
        Retorna QuerySet de questões ativas anotadas e ordenadas pela restrição do RF07:
        priorizando questões NÃO usadas (ou MENOS usadas) nas últimas 5 gerações desta máscara.
        """
        from .services.question_selector import consultar_questoes_candidatas_priorizadas

        return consultar_questoes_candidatas_priorizadas(
            mascara=self,
            topico_id=topico_id,
            dificuldade=dificuldade,
            tipo=tipo,
            limite_provas_recentes=limite_provas_recentes,
            excluir_ids=excluir_ids,
        )

    def validar_estoque(self):
        """
        Audita o banco de questões e valida a disponibilidade real para cada célula (Tópico × Dificuldade).
        """
        from .services.stock_validator import validar_disponibilidade_estoque

        return validar_disponibilidade_estoque(self)

    def gerar_prova(self, autor=None, titulo=None, numero_caderno=1, limite_provas_recentes=5):
        """
        Gera uma Prova persistida e vinculada a esta Máscara após validação rigorosa de estoque.
        """
        from .services.question_selector import gerar_prova_automatizada

        return gerar_prova_automatizada(
            mascara=self,
            autor=autor,
            titulo=titulo,
            numero_caderno=numero_caderno,
            limite_provas_recentes=limite_provas_recentes,
        )

    def selecionar_questoes_automatizada(self, limite_provas_recentes=5):
        """
        Executa a seleção automatizada de questões (RF07) para compor a prova,
        respeitando a matriz de metas por célula (tópico x dificuldade) e o histórico de uso.
        """
        from .services.question_selector import selecionar_questoes_por_matriz

        return selecionar_questoes_por_matriz(
            mascara=self,
            limite_provas_recentes=limite_provas_recentes,
        )

    @property
    def criterios_topico(self):
        """Propriedade de conveniência para a convenção de critérios de tópicos."""
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
        if self.percentual is not None and self.percentual <= 0:
            raise ValidationError({'percentual': 'O percentual do tópico selecionado deve ser maior que 0%.'})
        if self.percentual is not None and self.percentual > 100:
            raise ValidationError({'percentual': 'O percentual do tópico não pode exceder 100%.'})


class Prova(models.Model):
    """
    Avaliação acadêmica gerada a partir de uma Máscara de Prova (RF07) ou por Montagem 100% Manual.
    Rastreia cadernos de prova, data de aplicação e histórico de utilização de questões.

    Os campos ``snapshot_*`` congelam o estado exato dos parâmetros da avaliação
    e da matriz de metas no instante da geração, garantindo rastreabilidade
    e auditabilidade mesmo que os registros originais sejam alterados.
    """

    class TipoGeracao(models.TextChoices):
        AUTOMATICA = 'AUTOMATICA', 'Automatizada (Máscara / RF07)'
        MANUAL = 'MANUAL', 'Manual (Seleção Livre)'

    mascara = models.ForeignKey(
        Mascara,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='provas_geradas',
        verbose_name='máscara de prova',
    )
    disciplina = models.ForeignKey(
        Disciplina,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='provas',
        verbose_name='disciplina',
    )
    tipo_geracao = models.CharField(
        'tipo de montagem',
        max_length=20,
        choices=TipoGeracao.choices,
        default=TipoGeracao.AUTOMATICA,
    )
    titulo = models.CharField('título da prova', max_length=250)
    numero_caderno = models.PositiveIntegerField('número do caderno', default=1)
    questoes = models.ManyToManyField(
        Questao,
        through='ProvaQuestao',
        related_name='provas',
        verbose_name='questões da prova',
    )
    autor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='provas_geradas',
        verbose_name='docente responsável',
    )

    # ── Snapshots JSON (registro imutável no momento da geração) ──────────
    snapshot_mascara = models.JSONField(
        'snapshot da máscara',
        default=dict,
        blank=True,
        help_text='Parâmetros completos da máscara congelados no instante da geração.',
    )
    snapshot_criterios_dificuldade = models.JSONField(
        'snapshot dos critérios de dificuldade',
        default=list,
        blank=True,
        help_text='Critérios F/M/D com percentuais e quantidades calculadas.',
    )
    snapshot_criterios_topico = models.JSONField(
        'snapshot dos critérios de tópico',
        default=list,
        blank=True,
        help_text='Tópicos curriculares com percentuais e quantidades calculadas.',
    )
    snapshot_matriz_metas = models.JSONField(
        'snapshot da matriz de metas',
        default=dict,
        blank=True,
        help_text='Matriz 2D completa (Tópico × Dificuldade) com metas inteiras por célula.',
    )

    criado_em = models.DateTimeField('data de geração', auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ['-criado_em', 'numero_caderno']
        verbose_name = 'prova gerada'
        verbose_name_plural = 'provas geradas'

    def __str__(self):
        return f'{self.titulo} (Caderno {self.numero_caderno})'

    @property
    def disciplina_efetiva(self):
        """Retorna a disciplina associada diretamente ou através da máscara."""
        return self.disciplina or (self.mascara.disciplina if self.mascara else None)


class ProvaQuestao(models.Model):
    """
    Item/associação de uma questão a uma prova gerada específica.

    O campo ``snapshot_questao`` preserva enunciado, alternativas, tipo,
    dificuldade e demais dados da questão no instante da inclusão na prova.
    """

    prova = models.ForeignKey(
        Prova,
        on_delete=models.CASCADE,
        related_name='itens',
        verbose_name='prova',
    )
    questao = models.ForeignKey(
        Questao,
        on_delete=models.PROTECT,
        related_name='utilizacoes_provas',
        verbose_name='questão',
    )
    numero = models.PositiveIntegerField('número da questão no exame', default=1)
    ordem = models.PositiveIntegerField('ordem de exibição', default=1)
    pontuacao = models.DecimalField(
        'pontuação da questão',
        max_digits=5,
        decimal_places=2,
        default=1.00,
    )

    # ── Snapshot JSON (registro imutável da questão) ──────────────────────
    snapshot_questao = models.JSONField(
        'snapshot da questão',
        default=dict,
        blank=True,
        help_text='Dados completos da questão congelados no momento da inclusão na prova.',
    )

    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('prova', 'questao'), ('prova', 'ordem')]
        ordering = ['ordem']
        verbose_name = 'questão da prova'
        verbose_name_plural = 'questões da prova'

    def __str__(self):
        return f'{self.prova} • Questão {self.numero} (Q{self.questao_id})'


# ---------------------------------------------------------------------------
# ProvaVersao e ProvaVersaoItem (Cadernos A / B / C com Ordem Embaralhada)
# ---------------------------------------------------------------------------
class ProvaVersao(models.Model):
    """
    Versão/Caderno específico de uma avaliação gerada (ex: Versão A, Versão B, Versão C).

    Permite a aplicação da mesma prova com permutações de ordem de questões
    e alternativas entre os estudantes, prevenindo cópias e fraudes.
    """

    prova = models.ForeignKey(
        Prova,
        on_delete=models.CASCADE,
        related_name='versoes',
        verbose_name='avaliação base',
    )
    identificador = models.CharField(
        'identificador da versão',
        max_length=10,
        help_text='Letra ou código identificador da versão (ex.: A, B, C).',
    )
    numero_versao = models.PositiveIntegerField(
        'número ordinal da versão',
        default=1,
        help_text='Índice sequencial da versão (1 para A, 2 para B, etc.).',
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('prova', 'identificador'), ('prova', 'numero_versao')]
        ordering = ['numero_versao', 'identificador']
        verbose_name = 'versão de prova'
        verbose_name_plural = 'versões de prova'

    def __str__(self):
        return f'{self.prova.titulo} — Versão {self.identificador}'

    @property
    def total_questoes(self):
        return self.itens.count()


class ProvaVersaoItem(models.Model):
    """
    Item associado a uma versão específica da prova com sua ordem de exibição
    e alternativas embaralhadas persistidas.
    """

    versao = models.ForeignKey(
        ProvaVersao,
        on_delete=models.CASCADE,
        related_name='itens',
        verbose_name='versão de prova',
    )
    prova_questao = models.ForeignKey(
        ProvaQuestao,
        on_delete=models.CASCADE,
        related_name='itens_versao',
        verbose_name='questão da prova base',
    )
    numero = models.PositiveIntegerField(
        'número da questão nesta versão',
        default=1,
    )
    ordem = models.PositiveIntegerField(
        'ordem de exibição na versão',
        default=1,
    )
    alternativas_embaralhadas = models.JSONField(
        'alternativas embaralhadas',
        default=list,
        blank=True,
        help_text='Lista de alternativas com a ordem e letras sorteadas para esta versão específica.',
    )

    class Meta:
        unique_together = [('versao', 'prova_questao'), ('versao', 'ordem')]
        ordering = ['ordem']
        verbose_name = 'item da versão de prova'
        verbose_name_plural = 'itens da versão de prova'

    def __str__(self):
        return f'Versão {self.versao.identificador} • Questão {self.numero} (PQ{self.prova_questao_id})'

    @property
    def questao(self):
        return self.prova_questao.questao

    @property
    def pontuacao(self):
        return self.prova_questao.pontuacao


# Aliases para retrocompatibilidade contínua
MascaraProva = Mascara
MascaraTopico = MascaraCriterioTopico
