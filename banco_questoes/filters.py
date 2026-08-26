import django_filters
from django import forms
from django.contrib.auth import get_user_model

from .models import Disciplina, Questao, Topico

User = get_user_model()


class QuestaoFilter(django_filters.FilterSet):
    """
    Filtro avançado de questões com django-filter.

    Facetas:
    - disciplina: componente curricular
    - topico: subdivisão temática
    - dificuldade: Fácil (F), Médio (M), Difícil (D)
    - tipo: Objetiva (OBJ), Discursiva (DIS)
    - origem: exame ou concurso de origem (ex.: ENADE, POSCOMP)
    - ativo: ativas / inativas
    - autor: docente ou usuário criador
    """

    disciplina = django_filters.ModelChoiceFilter(
        queryset=Disciplina.objects.filter(ativo=True),
        empty_label='Todas as disciplinas',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    topico = django_filters.ModelChoiceFilter(
        queryset=Topico.objects.filter(ativo=True),
        empty_label='Todos os tópicos',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    dificuldade = django_filters.ChoiceFilter(
        choices=Questao.Dificuldade.choices,
        empty_label='Todas as dificuldades',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    tipo = django_filters.ChoiceFilter(
        choices=Questao.Tipo.choices,
        empty_label='Todos os tipos',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    origem = django_filters.CharFilter(
        lookup_expr='icontains',
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Ex.: ENADE ADS, POSCOMP'}),
    )
    ativo = django_filters.ChoiceFilter(
        choices=[
            ('', 'Todos os status'),
            ('true', 'Ativa'),
            ('false', 'Inativa'),
        ],
        method='filter_by_ativo',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    autor = django_filters.ModelChoiceFilter(
        queryset=User.objects.all(),
        empty_label='Todos os autores',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )

    class Meta:
        model = Questao
        fields = [
            'disciplina',
            'topico',
            'dificuldade',
            'tipo',
            'origem',
            'ativo',
            'autor',
        ]

    def filter_by_ativo(self, queryset, _name, value):
        """Filtra questões por status ativo (true) ou inativo (false)."""
        if value == 'true':
            return queryset.filter(ativo=True)
        if value == 'false':
            return queryset.filter(ativo=False)
        return queryset
