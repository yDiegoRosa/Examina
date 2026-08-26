from django import forms
from django.forms import inlineformset_factory

from .models import Alternativa, Questao


# ---------------------------------------------------------------------------
# Alternativa — Form + FormSet
# ---------------------------------------------------------------------------
class AlternativaForm(forms.ModelForm):
    """Form para cada alternativa individual com widgets Vanilla CSS."""

    class Meta:
        model = Alternativa
        fields = ['letra', 'texto', 'correta']
        widgets = {
            'letra': forms.Select(
                choices=[('', '---')] + [(letra, letra) for letra in 'ABCDE'],
                attrs={'class': 'form-select select-letter'},
            ),
            'texto': forms.Textarea(
                attrs={
                    'class': 'form-textarea textarea-sm',
                    'rows': 2,
                    'placeholder': 'Digite o conteúdo desta alternativa…',
                },
            ),
            'correta': forms.CheckboxInput(
                attrs={'class': 'form-checkbox'},
            ),
        }


AlternativaFormSet = inlineformset_factory(
    Questao,
    Alternativa,
    form=AlternativaForm,
    min_num=0,
    max_num=5,
    extra=2,
    validate_min=False,
    can_delete=True,
)


# ---------------------------------------------------------------------------
# Questão — ModelForm com validação condicional
# ---------------------------------------------------------------------------
class QuestaoForm(forms.ModelForm):
    """
    ModelForm para cadastro de questões.

    Validação condicional:
    - Objetiva → exige pelo menos 2 alternativas e exatamente 1 correta
      (verificado via ``validate_with_alternativas``).
    - Discursiva → exige ``criterios_correcao`` preenchido.
    """

    class Meta:
        model = Questao
        fields = [
            'origem',
            'numero_questao',
            'tipo',
            'dificuldade',
            'disciplina',
            'topico',
            'texto_auxiliar',
            'imagem',
            'enunciado',
            'criterios_correcao',
            'tags',
            'ativo',
        ]
        labels = {
            'origem': 'Origem / Exame (Opcional)',
            'numero_questao': 'Nº da Questão no Exame (Opcional)',
            'tipo': 'Tipo de questão',
            'dificuldade': 'Nível de dificuldade',
            'disciplina': 'Disciplina',
            'topico': 'Tópico curricular',
            'texto_auxiliar': 'Texto auxiliar / Contextualização (Opcional)',
            'imagem': 'Imagem / Diagrama de apoio (Opcional)',
            'enunciado': 'Enunciado da questão',
            'criterios_correcao': 'Comentários / Justificativa do Gabarito',
            'tags': 'Tags e palavras-chave',
            'ativo': 'Questão ativa no acervo',
        }
        widgets = {
            'origem': forms.TextInput(
                attrs={
                    'class': 'form-input',
                    'placeholder': 'Ex.: ENADE ADS 2021, POSCOMP 2025',
                },
            ),
            'numero_questao': forms.TextInput(
                attrs={
                    'class': 'form-input',
                    'placeholder': 'Ex.: 26, 15',
                },
            ),
            'tipo': forms.Select(attrs={'class': 'form-select'}),
            'dificuldade': forms.Select(attrs={'class': 'form-select'}),
            'disciplina': forms.Select(attrs={'class': 'form-select'}),
            'topico': forms.Select(attrs={'class': 'form-select'}),
            'texto_auxiliar': forms.Textarea(
                attrs={
                    'class': 'form-textarea',
                    'rows': 3,
                    'placeholder': 'Texto motivador, trecho de código, contextualização ou artigo base…',
                },
            ),
            'imagem': forms.ClearableFileInput(
                attrs={
                    'class': 'form-input form-file-input',
                    'accept': 'image/*',
                },
            ),
            'enunciado': forms.Textarea(
                attrs={
                    'class': 'form-textarea',
                    'rows': 4,
                    'placeholder': 'Redija aqui o enunciado completo da questão…',
                },
            ),
            'criterios_correcao': forms.Textarea(
                attrs={
                    'class': 'form-textarea',
                    'rows': 3,
                    'placeholder': 'Justificativa analítica fundamentando o gabarito e detalhando eventuais erros nas demais alternativas…',
                },
            ),
            'ativo': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'tags' in self.fields:
            self.fields['tags'].widget.attrs.update(
                {
                    'class': 'form-input',
                    'placeholder': 'Separe por vírgula. Ex.: enem, algoritmos, 2026',
                }
            )

    # ------------------------------------------------------------------
    # Field-level validation
    # ------------------------------------------------------------------
    def clean_topico(self):
        """Garante que o tópico pertence à disciplina selecionada."""
        topico = self.cleaned_data.get('topico')
        disciplina = self.cleaned_data.get('disciplina')

        if topico and disciplina and topico.disciplina_id != disciplina.pk:
            raise forms.ValidationError('O tópico selecionado não pertence à disciplina informada.')
        return topico

    # ------------------------------------------------------------------
    # Form-level validation
    # ------------------------------------------------------------------
    def clean(self):
        cleaned = super().clean()
        tipo = cleaned.get('tipo')

        if tipo == Questao.Tipo.DISCURSIVA and not cleaned.get('criterios_correcao', '').strip():
            self.add_error(
                'criterios_correcao',
                'Critérios de correção são obrigatórios para questões discursivas.',
            )

        return cleaned

    # ------------------------------------------------------------------
    # Validação cruzada com o formset de alternativas
    # ------------------------------------------------------------------
    def validate_with_alternativas(self, alternativa_formset):
        """
        Deve ser chamado pela view após ``form.is_valid()`` e
        ``formset.is_valid()`` para aplicar regras cruzadas.

        Retorna True se tudo válido, False caso contrário (erros são
        adicionados ao form).
        """
        tipo = self.cleaned_data.get('tipo')

        if tipo != Questao.Tipo.OBJETIVA:
            return True

        # Conta apenas forms preenchidos e não marcados para deleção
        filled = [f for f in alternativa_formset.forms if f.cleaned_data and not f.cleaned_data.get('DELETE')]

        if len(filled) < 2:
            self.add_error(
                None,
                'Questões objetivas precisam de no mínimo 2 alternativas.',
            )
            return False

        corretas = [f for f in filled if f.cleaned_data.get('correta')]

        if len(corretas) != 1:
            self.add_error(
                None,
                f'Questões objetivas devem ter exatamente 1 alternativa correta (encontrada(s): {len(corretas)}).',
            )
            return False

        return True
