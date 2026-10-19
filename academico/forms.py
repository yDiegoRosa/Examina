"""Formulários dos cadastros acadêmicos (Aluno, Turma e Docência)."""

from django import forms
from django.contrib.auth import get_user_model

from banco_questoes.models import Disciplina
from banco_questoes.permissions import GRUPO_COORDENADOR

from .models import Aluno, Turma


class AlunoForm(forms.ModelForm):
    """Formulário de criação/edição de Aluno."""

    class Meta:
        model = Aluno
        fields = ['nome', 'matricula', 'ativo']
        labels = {
            'nome': 'Nome completo',
            'matricula': 'Matrícula',
            'ativo': 'Aluno ativo',
        }
        widgets = {
            'nome': forms.TextInput(
                attrs={'class': 'form-input', 'placeholder': 'Ex.: Maria Silva de Souza'},
            ),
            'matricula': forms.TextInput(
                attrs={'class': 'form-input', 'placeholder': 'Ex.: 2026010123'},
            ),
            'ativo': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def clean_matricula(self):
        return self.cleaned_data['matricula'].strip()

    def clean_nome(self):
        return self.cleaned_data['nome'].strip()


class TurmaForm(forms.ModelForm):
    """Formulário de criação/edição de Turma."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # No banco o turno pode ser vazio (turmas antigas); na tela ele é obrigatório.
        self.fields['turno'].required = True
        self.fields['turno'].choices = [('', 'Selecione o turno'), *Turma.Turno.choices]
        if self.instance.pk and self.instance.matriculas.exists():
            self.fields['periodo'].disabled = True
            self.fields['periodo'].help_text = (
                'O período não pode ser alterado porque esta turma já possui '
                'matrículas. Para outro período, cadastre uma nova turma.'
            )

    class Meta:
        model = Turma
        fields = ['periodo', 'nome', 'turno', 'ativo']
        labels = {
            'periodo': 'Período (Ano.Semestre)',
            'nome': 'Nome da turma',
            'turno': 'Turno',
            'ativo': 'Turma ativa',
        }
        widgets = {
            'periodo': forms.TextInput(
                attrs={'class': 'form-input', 'placeholder': 'Ex.: 2026.2'},
            ),
            'nome': forms.TextInput(
                attrs={'class': 'form-input', 'placeholder': 'Ex.: RSD'},
            ),
            'turno': forms.Select(attrs={'class': 'form-select'}),
            'ativo': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def clean_periodo(self):
        return self.cleaned_data['periodo'].strip()

    def clean_nome(self):
        return self.cleaned_data['nome'].strip()


class _ProfessorChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        nome = obj.get_full_name()
        return f'{nome} ({obj.get_username()})' if nome else obj.get_username()


class DocenciaForm(forms.Form):
    """Docência na turma: professor (grupos Docente/Coordenador) e disciplina que ele leciona."""

    professor = _ProfessorChoiceField(
        label='Professor',
        queryset=get_user_model()
        .objects.filter(is_active=True, groups__name__in=['Docente', GRUPO_COORDENADOR])
        .distinct()
        .order_by('first_name', 'username'),
        empty_label='Selecione o professor...',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    disciplina = forms.ModelChoiceField(
        label='Disciplina',
        queryset=Disciplina.objects.filter(ativo=True).order_by('nome'),
        empty_label='Selecione a disciplina...',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
