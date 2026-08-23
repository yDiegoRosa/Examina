from django import forms
from django.forms import inlineformset_factory

from .models import Alternativa, Questao


# ---------------------------------------------------------------------------
# Alternativa — Form + FormSet
# ---------------------------------------------------------------------------
class AlternativaForm(forms.ModelForm):
    """Form para cada alternativa individual."""

    class Meta:
        model = Alternativa
        fields = ['letra', 'texto', 'correta']
        widgets = {
            'letra': forms.Select(
                choices=[('', '---')] + [(letra, letra) for letra in 'ABCDE'],
                attrs={'class': 'form-select form-select-sm'},
            ),
            'texto': forms.Textarea(
                attrs={
                    'class': 'form-control form-control-sm',
                    'rows': 2,
                    'placeholder': 'Texto da alternativa…',
                },
            ),
            'correta': forms.CheckboxInput(
                attrs={'class': 'form-check-input'},
            ),
        }


AlternativaFormSet = inlineformset_factory(
    Questao,
    Alternativa,
    form=AlternativaForm,
    min_num=2,
    max_num=5,
    extra=2,
    validate_min=True,
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
            'tipo',
            'dificuldade',
            'disciplina',
            'topico',
            'enunciado',
            'criterios_correcao',
            'tags',
            'ativo',
        ]
        labels = {
            'tipo': 'Tipo de questão',
            'dificuldade': 'Nível de dificuldade',
            'disciplina': 'Disciplina',
            'topico': 'Tópico',
            'enunciado': 'Enunciado',
            'criterios_correcao': 'Critérios de correção',
            'tags': 'Tags',
            'ativo': 'Questão ativa',
        }
        widgets = {
            'tipo': forms.Select(attrs={'class': 'form-select'}),
            'dificuldade': forms.Select(attrs={'class': 'form-select'}),
            'disciplina': forms.Select(attrs={'class': 'form-select'}),
            'topico': forms.Select(attrs={'class': 'form-select'}),
            'enunciado': forms.Textarea(
                attrs={
                    'class': 'form-control',
                    'rows': 5,
                    'placeholder': 'Digite o enunciado da questão…',
                },
            ),
            'criterios_correcao': forms.Textarea(
                attrs={
                    'class': 'form-control',
                    'rows': 4,
                    'placeholder': 'Descreva os critérios de correção…',
                },
            ),
            'ativo': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Tags widget vem do taggit — aplicar classe via init
        if 'tags' in self.fields:
            self.fields['tags'].widget.attrs.update(
                {
                    'class': 'form-control',
                    'placeholder': 'ex.: enem, geometria, 2024',
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
