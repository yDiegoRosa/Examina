from django.urls import path

from . import views

app_name = 'academico'

urlpatterns = [
    # Alunos
    path('alunos/', views.aluno_list, name='aluno_list'),
    path('alunos/novo/', views.aluno_create, name='aluno_create'),
    path('alunos/<int:pk>/editar/', views.aluno_update, name='aluno_update'),
    path('alunos/<int:pk>/toggle-ativo/', views.aluno_toggle_ativo, name='aluno_toggle_ativo'),
    # Turmas
    path('turmas/', views.turma_list, name='turma_list'),
    path('turmas/nova/', views.turma_create, name='turma_create'),
    path('turmas/<int:pk>/editar/', views.turma_update, name='turma_update'),
    path('turmas/<int:pk>/toggle-ativo/', views.turma_toggle_ativo, name='turma_toggle_ativo'),
]
