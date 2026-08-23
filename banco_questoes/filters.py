import django_filters
from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q

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
    - tags: filtro por tags livres
    - ativo: ativas / inativas
    - autor: docente ou usuário criador
    - q: busca textual livre no enunciado e critérios de correção
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
    tags = django_filters.CharFilter(
        method='filter_by_tags',
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Ex.: enem, matrizes'}),
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
    q = django_filters.CharFilter(
        method='filter_by_q',
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Buscar no enunciado ou critérios…'}),
    )

    class Meta:
        model = Questao
        fields = [
            'disciplina',
            'topico',
            'dificuldade',
            'tipo',
            'tags',
            'ativo',
            'autor',
            'q',
        ]

    def filter_by_tags(self, queryset, _name, value):
        """Filtra questões que possuam a tag informada (case-insensitive)."""
        if not value or not value.strip():
            return queryset
        tag_clean = value.strip()
        return queryset.filter(tags__name__icontains=tag_clean).distinct()

    def filter_by_ativo(self, queryset, _name, value):
        """Filtra questões por status ativo (true) ou inativo (false)."""
        if value == 'true':
            return queryset.filter(ativo=True)
        if value == 'false':
            return queryset.filter(ativo=False)
        return queryset

    def filter_by_q(self, queryset, _name, value):
        """Busca textual ampla no enunciado e nos critérios de correção."""
        if not value or not value.strip():
            return queryset
        term = value.strip()
        return queryset.filter(Q(enunciado__icontains=term) | Q(criterios_correcao__icontains=term)).distinct()
