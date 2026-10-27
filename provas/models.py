import logging
import uuid

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
        null=True,
        blank=True,
        help_text='Opcional. Sem disciplina, a máscara considera questões de todas as disciplinas.',
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
    quantidade_versoes = models.PositiveSmallIntegerField(
        'quantidade de cadernos (versões)',
        default=3,
        validators=[MinValueValidator(1), MaxValueValidator(8)],
        help_text='Cadernos gerados para cada prova (A, B, C…). Use 1 para caderno único, como no Simulado ENADE.',
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
        escopo = self.disciplina.nome if self.disciplina else 'Todas as disciplinas'
        return f'{self.titulo} ({escopo})'

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

    def montar_plano_selecao(self):
        """
        Monta o plano de seleção da prova (lista de ``BaldeSelecao``), respeitando
        a modalidade e, na modalidade MISTA, o split objetivas/discursivas.

        É a fonte única consumida pela seleção automatizada (RF07) e pela
        validação de estoque, garantindo que ambas usem exatamente a mesma
        distribuição de questões por (tópico, dificuldade, tipo).
        """
        from .services.matrix_calculator import montar_baldes_selecao

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

        return montar_baldes_selecao(
            modalidade=self.modalidade,
            quantidade_questoes=self.quantidade_questoes,
            quantidade_objetivas=self.quantidade_objetivas,
            quantidade_discursivas=self.quantidade_discursivas,
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
        autor_questoes=None,
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
            autor_questoes=autor_questoes,
        )

    def validar_estoque(self, autor_questoes=None):
        """
        Audita o banco de questões e valida a disponibilidade real para cada célula (Tópico × Dificuldade).
        """
        from .services.stock_validator import validar_disponibilidade_estoque

        return validar_disponibilidade_estoque(self, autor_questoes=autor_questoes)

    def gerar_prova(self, autor=None, titulo=None, numero_caderno=1, limite_provas_recentes=5, autor_questoes=None):
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
            autor_questoes=autor_questoes,
        )

    def selecionar_questoes_automatizada(self, limite_provas_recentes=5, autor_questoes=None):
        """
        Executa a seleção automatizada de questões (RF07) para compor a prova,
        respeitando a matriz de metas por célula (tópico x dificuldade) e o histórico de uso.
        """
        from .services.question_selector import selecionar_questoes_por_matriz

        return selecionar_questoes_por_matriz(
            mascara=self,
            limite_provas_recentes=limite_provas_recentes,
            autor_questoes=autor_questoes,
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
    # Legado (anterior às versões A/B/C…): não é mais exibido — PDFs e cartões identificam
    # o caderno pela versão. Mantido no banco por compatibilidade.
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
        return self.titulo

    @property
    def disciplina_efetiva(self):
        """Retorna a disciplina associada diretamente ou através da máscara."""
        return self.disciplina or (self.mascara.disciplina if self.mascara else None)

    @property
    def versoes_distribuidas(self):
        """
        Indica se alguma versão desta prova já foi distribuída a alunos (atribuição de
        versão ou folha-resposta gerada). A partir daí a prova fica travada: regerar
        versões ou trocar questões invalidaria gabaritos, folhas impressas e notas.
        """
        return (
            AtribuicaoVersao.objects.filter(versao__prova=self).exists()
            or FolhaResposta.objects.filter(prova_versao__prova=self).exists()
        )


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


# ---------------------------------------------------------------------------
# ProvaAplicacao e AtribuicaoVersao (vínculo Prova × Turma + versão por aluno)
# ---------------------------------------------------------------------------
class ProvaAplicacao(models.Model):
    """
    Aplicação de uma prova a uma turma numa data específica (o "evento" de avaliação).

    Base da atribuição de versões por aluno, dos cartões-resposta nominais com QR, da
    correção (OMR/manual) e das notas da turma.
    """

    prova = models.ForeignKey(
        Prova,
        on_delete=models.PROTECT,
        related_name='aplicacoes',
        verbose_name='prova',
    )
    turma = models.ForeignKey(
        'academico.Turma',
        on_delete=models.PROTECT,
        related_name='aplicacoes',
        verbose_name='turma',
    )
    data_aplicacao = models.DateField('data de aplicação')
    observacao = models.TextField('observação', blank=True)
    # Sem nota de corte (removida em 2026-10-27): a discursiva conta pela nota, proporcional, na
    # nota final e nos relatórios de desempenho — ver desempenho.services.FRACAO_ACERTO.

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-data_aplicacao', 'turma__nome']
        unique_together = [('prova', 'turma', 'data_aplicacao')]
        verbose_name = 'aplicação de prova'
        verbose_name_plural = 'aplicações de prova'

    def __str__(self):
        return f'{self.prova.titulo} — {self.turma} ({self.data_aplicacao})'


class AtribuicaoVersao(models.Model):
    """
    Versão (caderno A/B/C…) atribuída a um aluno numa aplicação de prova.

    Gerada em blocos (ver ``provas.services.version_assigner``): na ordem alfabética,
    cada versão recebe uma faixa contínua de alunos, com faixas de tamanho equilibrado.
    ``sequencia`` é a posição do aluno nessa ordem; quem entra na turma depois recebe as
    sequências seguintes (``incluir_alunos_novos``). O rótulo do campo ainda cita o
    round-robin, regra anterior; trocá-lo exige migration.
    """

    aplicacao = models.ForeignKey(
        ProvaAplicacao,
        on_delete=models.CASCADE,
        related_name='atribuicoes',
        verbose_name='aplicação',
    )
    aluno = models.ForeignKey(
        'academico.Aluno',
        on_delete=models.PROTECT,
        related_name='atribuicoes_versao',
        verbose_name='aluno',
    )
    versao = models.ForeignKey(
        ProvaVersao,
        on_delete=models.PROTECT,
        related_name='atribuicoes',
        verbose_name='versão',
    )
    sequencia = models.PositiveIntegerField('sequência (ordem do round-robin)', default=1)

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['aplicacao', 'sequencia']
        unique_together = [('aplicacao', 'aluno')]
        verbose_name = 'atribuição de versão'
        verbose_name_plural = 'atribuições de versão'

    def __str__(self):
        return f'{self.aluno} → Versão {self.versao.identificador} (#{self.sequencia})'


class FolhaResposta(models.Model):
    """
    Folha-resposta física, nominal, de um aluno numa aplicação de prova (RF10).

    Vincula o aluno a uma versão específica e carrega um ``qr_token`` único, impresso
    como QR na folha. O leitor óptico (OMR) resolve a folha por esse token para saber
    de quem é e qual gabarito aplicar.
    """

    class Status(models.TextChoices):
        GERADA = 'GERADA', 'Gerada'
        IMPRESSA = 'IMPRESSA', 'Impressa'
        LIDA = 'LIDA', 'Lida (corrigida)'
        ANULADA = 'ANULADA', 'Anulada'

    prova_aplicacao = models.ForeignKey(
        ProvaAplicacao,
        on_delete=models.CASCADE,
        related_name='folhas',
        verbose_name='aplicação de prova',
    )
    aluno = models.ForeignKey(
        'academico.Aluno',
        on_delete=models.PROTECT,
        related_name='folhas_resposta',
        verbose_name='aluno',
    )
    prova_versao = models.ForeignKey(
        ProvaVersao,
        on_delete=models.PROTECT,
        related_name='folhas',
        verbose_name='versão',
    )
    qr_token = models.UUIDField('token do QR', default=uuid.uuid4, unique=True, editable=False)
    status = models.CharField(
        'status',
        max_length=20,
        choices=Status.choices,
        default=Status.GERADA,
    )

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['prova_aplicacao', 'aluno__nome']
        unique_together = [('prova_aplicacao', 'aluno')]
        verbose_name = 'folha de resposta'
        verbose_name_plural = 'folhas de resposta'

    def __str__(self):
        return f'Folha de {self.aluno} — Versão {self.prova_versao.identificador}'


class FolhaDigitalizada(models.Model):
    """
    Imagem de uma folha-resposta escaneada/fotografada, enviada em lote para uma
    aplicação de prova — fila de entrada do leitor óptico (OMR).

    A ``folha_resposta`` fica nula até o OMR ler o QR e resolver de quem é a folha.
    """

    class Status(models.TextChoices):
        PENDENTE = 'PENDENTE', 'Pendente'
        PROCESSADA = 'PROCESSADA', 'Processada'
        ERRO = 'ERRO', 'Erro'

    prova_aplicacao = models.ForeignKey(
        ProvaAplicacao,
        on_delete=models.CASCADE,
        related_name='digitalizacoes',
        verbose_name='aplicação de prova',
    )
    imagem = models.ImageField('imagem da folha', upload_to='folhas_digitalizadas/%Y/%m/')
    status = models.CharField(
        'status',
        max_length=20,
        choices=Status.choices,
        default=Status.PENDENTE,
    )
    folha_resposta = models.ForeignKey(
        FolhaResposta,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='digitalizacoes',
        verbose_name='folha-resposta identificada',
    )
    mensagem = models.TextField('mensagem do processamento', blank=True)
    enviado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='folhas_enviadas',
        verbose_name='enviado por',
    )

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-criado_em']
        verbose_name = 'folha digitalizada'
        verbose_name_plural = 'folhas digitalizadas'

    def __str__(self):
        return f'Digitalização #{self.pk} — {self.prova_aplicacao} [{self.get_status_display()}]'


# Aliases para retrocompatibilidade contínua
MascaraProva = Mascara
MascaraTopico = MascaraCriterioTopico
