"""Formulários dos cadastros acadêmicos (Aluno, Turma e Docência)."""

from django import forms
from django.contrib.auth import get_user_model

from banco_questoes.models import Disciplina
from banco_questoes.permissions import GRUPO_COORDENADOR

from .models import Aluno, Docencia, Turma


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


class ImportarAlunosForm(forms.Form):
    """Planilha de alunos (.xlsx ou .csv) com as colunas matricula e nome."""

    arquivo = forms.FileField(
        label='Planilha de alunos',
        widget=forms.ClearableFileInput(
            attrs={
                'class': 'form-input',
                'accept': '.xlsx,.csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,text/csv',
            }
        ),
    )

    def clean_arquivo(self):
        from banco_questoes.forms import _validate_upload_file

        arquivo = self.cleaned_data['arquivo']
        if not arquivo.name.lower().endswith(('.xlsx', '.csv')):
            raise forms.ValidationError('Envie a planilha em .xlsx (Excel) ou .csv.')
        return _validate_upload_file(arquivo)  # tamanho (CSV_UPLOAD_MAX_SIZE_MB) e conteúdo real


class _ProfessorChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        nome = obj.get_full_name()
        return f'{nome} ({obj.get_username()})' if nome else obj.get_username()


def _professores_ativos():
    return (
        get_user_model()
        .objects.filter(is_active=True, groups__name__in=['Docente', GRUPO_COORDENADOR])
        .distinct()
        .order_by('first_name', 'username')
    )


class TurmaForm(forms.ModelForm):
    """
    Criação/edição de Turma pela disciplina: o nome é a sigla dela ("SEG"), ou sigla + complemento
    ("SEG-A") quando há mais de uma turma da mesma disciplina no mesmo período e turno.

    No cadastro, o professor (opcional) vira a docência da turma na disciplina dela.
    """

    professor = _ProfessorChoiceField(
        label='Professor (opcional)',
        queryset=get_user_model().objects.none(),
        required=False,
        empty_label='Vincular depois em "Docentes da turma"',
        widget=forms.Select(attrs={'class': 'form-select'}),
        help_text='Quem leciona a disciplina nesta turma. Outros professores podem ser incluídos depois.',
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # No banco o turno pode ser vazio (turmas antigas); na tela ele é obrigatório.
        self.fields['turno'].required = True
        self.fields['turno'].choices = [('', 'Selecione o turno'), *Turma.Turno.choices]
        self.fields['disciplina'].required = True
        self.fields['disciplina'].queryset = Disciplina.objects.filter(ativo=True).order_by('nome')
        self.fields['disciplina'].empty_label = 'Selecione a disciplina...'
        if self.instance.pk:
            # Na edição, os professores ficam na tela "Docentes da turma".
            del self.fields['professor']
        else:
            self.fields['professor'].queryset = _professores_ativos()
        if self.instance.pk and self.instance.matriculas.exists():
            self.fields['periodo'].disabled = True
            self.fields['periodo'].help_text = (
                'O período não pode ser alterado porque esta turma já possui '
                'matrículas. Para outro período, cadastre uma nova turma.'
            )

    class Meta:
        model = Turma
        fields = ['periodo', 'turno', 'disciplina', 'complemento', 'ativo']
        labels = {
            'periodo': 'Período (Ano.Semestre)',
            'turno': 'Turno',
            'disciplina': 'Disciplina',
            'complemento': 'Complemento (opcional)',
            'ativo': 'Turma ativa',
        }
        help_texts = {
            'disciplina': 'O nome da turma é a sigla da disciplina (ex.: Segurança da Informação → SEG).',
            'complemento': (
                'Só se houver mais de uma turma da mesma disciplina no mesmo período e turno: '
                'com "A", o nome fica SEG-A.'
            ),
        }
        widgets = {
            'periodo': forms.TextInput(
                attrs={'class': 'form-input', 'placeholder': 'Ex.: 2026.2'},
            ),
            'turno': forms.Select(attrs={'class': 'form-select'}),
            'disciplina': forms.Select(attrs={'class': 'form-select'}),
            'complemento': forms.TextInput(
                attrs={'class': 'form-input', 'placeholder': 'Ex.: A', 'maxlength': 10},
            ),
            'ativo': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def clean_periodo(self):
        return self.cleaned_data['periodo'].strip()

    def clean_complemento(self):
        return self.cleaned_data['complemento'].strip().upper()

    def clean(self):
        dados = super().clean()
        disciplina = dados.get('disciplina')
        if disciplina is None or self.has_error('complemento'):
            return dados
        nome = Turma.montar_nome(disciplina, dados.get('complemento', ''))
        periodo = dados.get('periodo', self.instance.periodo)
        repetida = Turma.objects.filter(periodo=periodo, nome__iexact=nome, turno=dados.get('turno'))
        if self.instance.pk:
            repetida = repetida.exclude(pk=self.instance.pk)
        if repetida.exists():
            raise forms.ValidationError(
                f'Já existe a turma {nome} neste período e turno. Se for outra turma da mesma disciplina, '
                'preencha o complemento (ex.: A, B).'
            )
        self.instance.nome = nome
        return dados

    def save(self, commit=True):
        turma = super().save(commit=commit)
        professor = self.cleaned_data.get('professor')
        if commit and professor:
            Docencia.objects.get_or_create(professor=professor, disciplina=turma.disciplina, turma=turma)
        return turma


class ProfessorForm(forms.Form):
    """Cadastro/edição do professor: o login (nome.sobrenome) é gerado a partir do nome no cadastro."""

    nome_completo = forms.CharField(
        label='Nome completo',
        max_length=150,
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Ex.: Ana Maria de Souza'}),
        help_text='O login é gerado a partir do nome: primeiro nome + último sobrenome (ex.: ana.souza).',
    )
    email = forms.EmailField(
        label='E-mail (opcional)',
        required=False,
        widget=forms.EmailInput(attrs={'class': 'form-input', 'placeholder': 'Ex.: ana.souza@faeterj-rio.edu.br'}),
    )

    def clean_nome_completo(self):
        from .professores import login_base

        nome = ' '.join(self.cleaned_data['nome_completo'].split())
        if not login_base(nome):
            raise forms.ValidationError('Informe o nome completo com letras.')
        return nome

    def clean_email(self):
        return self.cleaned_data['email'].strip().lower()


class DocenciaForm(forms.Form):
    """Docência na turma: professor (grupos Docente/Coordenador) e disciplina que ele leciona."""

    professor = _ProfessorChoiceField(
        label='Professor',
        queryset=_professores_ativos(),
        empty_label='Selecione o professor...',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    disciplina = forms.ModelChoiceField(
        label='Disciplina',
        queryset=Disciplina.objects.filter(ativo=True).order_by('nome'),
        empty_label='Selecione a disciplina...',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
