"""Formulários dos cadastros acadêmicos (Aluno e Turma)."""

from django import forms

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

    class Meta:
        model = Turma
        fields = ['periodo', 'nome', 'ativo']
        labels = {
            'periodo': 'Período (Ano.Semestre)',
            'nome': 'Nome da turma',
            'ativo': 'Turma ativa',
        }
        widgets = {
            'periodo': forms.TextInput(
                attrs={'class': 'form-input', 'placeholder': 'Ex.: 2026.2'},
            ),
            'nome': forms.TextInput(
                attrs={'class': 'form-input', 'placeholder': 'Ex.: MAC Noite'},
            ),
            'ativo': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def clean_periodo(self):
        return self.cleaned_data['periodo'].strip()

    def clean_nome(self):
        return self.cleaned_data['nome'].strip()
