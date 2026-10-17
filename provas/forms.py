from types import SimpleNamespace

from django import forms
from django.conf import settings
from django.forms import inlineformset_factory

from academico.models import Turma
from banco_questoes.models import Disciplina, Topico

from .models import Mascara, MascaraCriterioTopico, Prova, ProvaAplicacao
from .services.caderno_layout import eh_mascara_enade


class _MultipleFileInput(forms.ClearableFileInput):
    """Input de arquivo que aceita múltiplos arquivos."""

    allow_multiple_selected = True


class MultipleImageField(forms.ImageField):
    """
    Campo de upload de **múltiplas** imagens.

    Valida cada arquivo como imagem real (via Pillow, herdado de ``ImageField``) e
    limita o tamanho de cada uma a ``settings.IMAGE_UPLOAD_MAX_SIZE_MB`` (padrão 10 MB).
    Retorna sempre uma lista de arquivos.
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault(
            'widget',
            _MultipleFileInput(
                attrs={
                    'class': 'form-input form-file-input',
                    'accept': 'image/*',
                    'multiple': True,
                    'capture': 'environment',
                },
            ),
        )
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        if not data:
            if self.required:
                raise forms.ValidationError('Envie pelo menos uma imagem.')
            return []

        arquivos = data if isinstance(data, (list, tuple)) else [data]
        single_clean = super().clean
        limpos = [single_clean(arquivo, initial) for arquivo in arquivos]

        max_mb = getattr(settings, 'IMAGE_UPLOAD_MAX_SIZE_MB', 10)
        limite = max_mb * 1024 * 1024
        for arquivo in limpos:
            if arquivo and arquivo.size > limite:
                raise forms.ValidationError(f'Cada imagem deve ter no máximo {max_mb} MB.')
        return limpos


class UploadFolhasForm(forms.Form):
    """Upload em lote de folhas escaneadas/fotografadas de uma aplicação."""

    imagens = MultipleImageField(label='Imagens dos cartões-resposta (fotos do celular ou digitalizações)')


class ProvaAplicacaoForm(forms.ModelForm):
    """
    Cadastro de uma aplicação (prova × turma × data) fora do admin.

    Só aceita provas que já tenham versões (A/B/C…) — sem elas não há como distribuir
    cadernos nem gerar folhas-resposta — e turmas ativas. Com ``usuario``, recorta pelo
    acesso por perfil: o professor só aplica a própria prova numa turma em que leciona a
    disciplina dela (``academico.acesso.pode_aplicar``).
    """

    def __init__(self, *args, usuario=None, **kwargs):
        from academico.acesso import provas_visiveis, turmas_permitidas

        super().__init__(*args, **kwargs)
        self.usuario = usuario
        if usuario is None or _eh_administrador(usuario):
            provas = Prova.objects.all()
        else:  # coordenador também: só aplica as provas dele
            provas = provas_visiveis(usuario).filter(autor=usuario)
        self.fields['prova'].queryset = provas.filter(versoes__isnull=False).distinct().order_by('-criado_em', '-pk')
        self.fields['prova'].help_text = 'Somente provas com versões (cadernos) geradas aparecem na lista.'
        turmas = Turma.objects.all() if usuario is None else turmas_permitidas(usuario)
        self.fields['turma'].queryset = turmas.filter(ativo=True).order_by('-periodo', 'nome', 'turno')

    def clean(self):
        from academico.acesso import pode_aplicar

        cleaned = super().clean()
        prova, turma = cleaned.get('prova'), cleaned.get('turma')
        if self.usuario is not None and prova and turma and not pode_aplicar(self.usuario, prova, turma):
            self.add_error('turma', 'Você só pode aplicar a sua prova numa turma em que leciona a disciplina dela.')
        return cleaned

    class Meta:
        model = ProvaAplicacao
        fields = ['prova', 'turma', 'data_aplicacao', 'nota_corte', 'observacao']
        labels = {
            'prova': 'Prova',
            'turma': 'Turma',
            'data_aplicacao': 'Data da aplicação',
            'nota_corte': 'Nota de corte das discursivas (0–10)',
            'observacao': 'Observação',
        }
        widgets = {
            'prova': forms.Select(attrs={'class': 'form-select'}),
            'turma': forms.Select(attrs={'class': 'form-select'}),
            'data_aplicacao': forms.DateInput(attrs={'class': 'form-input', 'type': 'date'}, format='%Y-%m-%d'),
            'nota_corte': forms.NumberInput(attrs={'class': 'form-input', 'step': '0.5', 'min': '0', 'max': '10'}),
            'observacao': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 3}),
        }


def _eh_administrador(usuario) -> bool:
    from academico.acesso import eh_administrador

    return eh_administrador(usuario)


class MascaraForm(forms.ModelForm):
    """
    Formulário principal para criação e edição de Máscara de Prova (Blueprint).
    """

    class Meta:
        model = Mascara
        fields = [
            'titulo',
            'descricao',
            'disciplina',
            'quantidade_questoes',
            'modalidade',
            'quantidade_objetivas',
            'quantidade_discursivas',
            'percentual_facil',
            'percentual_medio',
            'percentual_dificil',
            'valor_total',
            'tempo_estimado_minutos',
            'embaralhar_questoes',
            'embaralhar_alternativas',
            'priorizar_enade',
            'quantidade_versoes',
            'ativo',
        ]
        labels = {
            'titulo': 'Título da Máscara',
            'descricao': 'Descrição / Observações Pedagógicas (Opcional)',
            'disciplina': 'Disciplina (opcional)',
            'quantidade_questoes': 'Quantidade Total de Questões',
            'modalidade': 'Modalidade das Questões',
            'quantidade_objetivas': 'Qtd. de Questões Objetivas',
            'quantidade_discursivas': 'Qtd. de Questões Discursivas',
            'percentual_facil': '% Nível Fácil',
            'percentual_medio': '% Nível Médio',
            'percentual_dificil': '% Nível Difícil',
            'valor_total': 'Valor Total da Prova (Pontos)',
            'tempo_estimado_minutos': 'Tempo Estimado (Minutos)',
            'embaralhar_questoes': 'Embaralhar ordem das questões nos cadernos',
            'embaralhar_alternativas': 'Embaralhar alternativas (A-E) das questões objetivas',
            'priorizar_enade': 'Priorizar questões do ENADE / POSCOMP',
            'quantidade_versoes': 'Quantidade de Cadernos (Versões)',
            'ativo': 'Máscara ativa para geração de provas',
        }
        widgets = {
            'titulo': forms.TextInput(
                attrs={
                    'class': 'form-input',
                    'placeholder': 'Ex.: AV1 Oficial — Estrutura de Dados 2026.2',
                },
            ),
            'descricao': forms.Textarea(
                attrs={
                    'class': 'form-textarea',
                    'rows': 2,
                    'placeholder': 'Observações, ementa ou foco pedagógico da avaliação…',
                },
            ),
            'disciplina': forms.Select(attrs={'class': 'form-select', 'id': 'id_disciplina_select'}),
            'quantidade_questoes': forms.NumberInput(
                attrs={'class': 'form-input', 'min': 1, 'max': 50, 'id': 'id_total_questoes'},
            ),
            'modalidade': forms.Select(attrs={'class': 'form-select', 'id': 'id_modalidade_select'}),
            'quantidade_objetivas': forms.NumberInput(
                attrs={'class': 'form-input', 'min': 0, 'id': 'id_qtd_objetivas'},
            ),
            'quantidade_discursivas': forms.NumberInput(
                attrs={'class': 'form-input', 'min': 0, 'id': 'id_qtd_discursivas'},
            ),
            'percentual_facil': forms.NumberInput(
                attrs={'class': 'form-input', 'min': 0, 'max': 100, 'id': 'id_input_facil'},
            ),
            'percentual_medio': forms.NumberInput(
                attrs={'class': 'form-input', 'min': 0, 'max': 100, 'id': 'id_input_medio'},
            ),
            'percentual_dificil': forms.NumberInput(
                attrs={'class': 'form-input', 'min': 0, 'max': 100, 'id': 'id_input_dificil'},
            ),
            'valor_total': forms.NumberInput(
                attrs={'class': 'form-input', 'step': '0.50', 'min': '0.50'},
            ),
            'tempo_estimado_minutos': forms.NumberInput(
                attrs={'class': 'form-input', 'min': 10, 'step': 5},
            ),
            'embaralhar_questoes': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'embaralhar_alternativas': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'priorizar_enade': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'quantidade_versoes': forms.NumberInput(attrs={'class': 'form-input', 'min': 1, 'max': 8}),
            'ativo': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
        }

    def __init__(self, *args, usuario=None, **kwargs):
        from academico.acesso import disciplinas_permitidas, ve_tudo

        super().__init__(*args, **kwargs)
        self.usuario = usuario
        self.fields['disciplina'].queryset = Disciplina.objects.filter(ativo=True)
        # Opcional no POST: se omitido, mantém o valor atual (ou o padrão de 3 numa máscara nova).
        self.fields['quantidade_versoes'].required = False

        self.fields['disciplina'].required = False
        self.fields['disciplina'].empty_label = 'Todas as disciplinas (simulado integrado)'
        self.fields[
            'disciplina'
        ].help_text = (
            'Deixe em branco para criar um simulado integrado e identificar dificuldades por tópico em todo o acervo.'
        )
        if usuario is not None and not ve_tudo(usuario):
            # Professor: só as disciplinas que leciona, e a máscara tem que ter uma.
            self.fields['disciplina'].queryset = disciplinas_permitidas(usuario)
            self.fields['disciplina'].required = True
            self.fields['disciplina'].empty_label = 'Selecione a disciplina...'
            self.fields['disciplina'].help_text = 'Disciplinas em que você leciona (cadastradas pelo administrador).'

    def clean_quantidade_versoes(self):
        valor = self.cleaned_data.get('quantidade_versoes')
        if valor is None:
            return self.instance.quantidade_versoes or Mascara._meta.get_field('quantidade_versoes').default
        return valor

    def clean(self):
        cleaned = super().clean()
        facil = cleaned.get('percentual_facil') or 0
        medio = cleaned.get('percentual_medio') or 0
        dificil = cleaned.get('percentual_dificil') or 0
        soma = facil + medio + dificil

        if soma != 100:
            diff_str = f'faltam {100 - soma}%' if soma < 100 else f'excede em {soma - 100}%'
            self.add_error(
                'percentual_facil',
                f'A soma dos percentuais de dificuldade (Fácil + Médio + Difícil) deve ser exatamente 100% (atual: {soma}% — {diff_str}).',
            )

        # Simulado ENADE é sempre caderno único (a geração também força 1 — version_generator).
        mascara_enade = SimpleNamespace(titulo=cleaned.get('titulo'), priorizar_enade=cleaned.get('priorizar_enade'))
        if eh_mascara_enade(mascara_enade) and self.usuario is not None and not _eh_administrador(self.usuario):
            self.add_error('titulo', 'Só o administrador cria o Simulado ENADE (prioridade ENADE e "ENADE" no título).')
        if eh_mascara_enade(mascara_enade):
            if self.data.get(self.add_prefix('quantidade_versoes')) in (None, ''):
                cleaned['quantidade_versoes'] = 1  # omitido: assume caderno único, sem erro
            elif (cleaned.get('quantidade_versoes') or 1) > 1:
                self.add_error(
                    'quantidade_versoes',
                    'O Simulado ENADE tem caderno único: a quantidade de cadernos (versões) deve ser 1.',
                )

        modalidade = cleaned.get('modalidade')
        total = cleaned.get('quantidade_questoes') or 0
        qtd_obj = cleaned.get('quantidade_objetivas') or 0
        qtd_dis = cleaned.get('quantidade_discursivas') or 0

        if modalidade == Mascara.Modalidade.MISTA and (qtd_obj + qtd_dis != total):
            self.add_error(
                'quantidade_questoes',
                f'Na modalidade mista, a soma de objetivas ({qtd_obj}) e discursivas ({qtd_dis}) deve ser igual ao total de questões ({total}).',
            )

        return cleaned


class MascaraCriterioTopicoForm(forms.ModelForm):
    """Formulário para distribuição percentual de um tópico curricular específico."""

    class Meta:
        model = MascaraCriterioTopico
        fields = ['topico', 'percentual', 'quantidade_questoes']
        widgets = {
            'topico': forms.Select(attrs={'class': 'form-select topico-select'}),
            'percentual': forms.NumberInput(
                attrs={'class': 'form-input topico-percentual-input', 'min': 0, 'max': 100},
            ),
            'quantidade_questoes': forms.NumberInput(
                attrs={'class': 'form-input topico-qtd-input', 'readonly': 'readonly'},
            ),
        }

    def __init__(self, *args, **kwargs):
        disciplina = kwargs.pop('disciplina', None)
        super().__init__(*args, **kwargs)
        if not disciplina and self.instance and getattr(self.instance, 'mascara_id', None):
            disciplina = getattr(self.instance.mascara, 'disciplina', None)

        if disciplina:
            self.fields['topico'].queryset = Topico.objects.filter(disciplina=disciplina, ativo=True).order_by('nome')
        else:
            self.fields['topico'].queryset = Topico.objects.filter(ativo=True).order_by('nome')


class BaseMascaraCriterioTopicoFormSet(forms.BaseInlineFormSet):
    """Validação customizada do inline formset de critérios de tópicos."""

    def __init__(self, *args, **kwargs):
        self.disciplina = kwargs.pop('disciplina', None)
        super().__init__(*args, **kwargs)

    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        if self.disciplina:
            kwargs['disciplina'] = self.disciplina
        elif self.instance and getattr(self.instance, 'disciplina_id', None):
            kwargs['disciplina'] = self.instance.disciplina
        return kwargs

    def clean(self):
        super().clean()
        if any(self.errors):
            return

        total_percentual = 0
        topicos_vistos = set()
        active_forms = 0

        # Identifica a disciplina vinculada à máscara
        disc_vinculada = self.disciplina or (
            self.instance.disciplina if getattr(self.instance, 'disciplina_id', None) else None
        )

        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get('DELETE'):
                continue

            topico = form.cleaned_data.get('topico')
            percentual = form.cleaned_data.get('percentual')

            if topico and disc_vinculada and topico.disciplina_id != disc_vinculada.pk:
                form.add_error(
                    'topico',
                    f'O tópico "{topico.nome}" não pertence à disciplina selecionada ({disc_vinculada.nome}).',
                )

            if topico and (percentual is None or percentual <= 0):
                form.add_error('percentual', 'Informe um percentual maior que 0% para o tópico selecionado.')

            if not topico and percentual and percentual > 0:
                form.add_error('topico', 'Selecione um tópico correspondente ao percentual informado.')

            if topico and percentual and percentual > 0:
                if topico.pk in topicos_vistos:
                    form.add_error('topico', f'O tópico "{topico.nome}" já foi selecionado nesta máscara.')
                topicos_vistos.add(topico.pk)
                total_percentual += percentual
                active_forms += 1

        if active_forms > 0 and total_percentual != 100:
            diff_str = (
                f'faltam {100 - total_percentual}%'
                if total_percentual < 100
                else f'excede em {total_percentual - 100}%'
            )
            raise forms.ValidationError(
                f'A soma dos percentuais dos tópicos selecionados deve ser exatamente 100% (atual: {total_percentual}% — {diff_str}).'
            )


MascaraCriterioTopicoFormSet = inlineformset_factory(
    Mascara,
    MascaraCriterioTopico,
    form=MascaraCriterioTopicoForm,
    formset=BaseMascaraCriterioTopicoFormSet,
    extra=0,
    can_delete=True,
)


# Aliases para retrocompatibilidade
MascaraProvaForm = MascaraForm
MascaraTopicoForm = MascaraCriterioTopicoForm
MascaraTopicoFormSet = MascaraCriterioTopicoFormSet
BaseMascaraTopicoFormSet = BaseMascaraCriterioTopicoFormSet


class ProvaManualForm(forms.Form):
    """
    Formulário para parametrização inicial da Montagem 100% Manual de Prova. Com
    ``usuario``, oferece só as disciplinas que ele leciona e as máscaras dele.
    """

    def __init__(self, *args, usuario=None, **kwargs):
        from academico.acesso import disciplinas_permitidas, mascaras_visiveis

        super().__init__(*args, **kwargs)
        if usuario is not None:
            self.fields['disciplina'].queryset = disciplinas_permitidas(usuario)
            self.fields['mascara'].queryset = mascaras_visiveis(usuario).filter(ativo=True)

    titulo = forms.CharField(
        label='Título da Avaliação',
        max_length=250,
        widget=forms.TextInput(
            attrs={
                'class': 'form-input',
                'placeholder': 'Ex.: AV2 Suplementar — Banco de Dados II 2026.2',
                'id': 'id_titulo_manual',
            },
        ),
    )
    disciplina = forms.ModelChoiceField(
        label='Disciplina Curricular',
        queryset=Disciplina.objects.filter(ativo=True),
        empty_label='Selecione a disciplina...',
        widget=forms.Select(
            attrs={
                'class': 'form-select',
                'id': 'id_disciplina_manual',
            },
        ),
    )
    mascara = forms.ModelChoiceField(
        label='Máscara Guia de Referência (Opcional)',
        queryset=Mascara.objects.filter(ativo=True),
        required=False,
        empty_label='Sem máscara (Seleção 100% Livre)...',
        widget=forms.Select(
            attrs={
                'class': 'form-select',
                'id': 'id_mascara_manual',
            },
        ),
    )
