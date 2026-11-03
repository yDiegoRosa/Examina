from django.urls import path

from . import views

app_name = 'academico'

urlpatterns = [
    # Alunos
    path('alunos/', views.aluno_list, name='aluno_list'),
    path('alunos/novo/', views.aluno_create, name='aluno_create'),
    path('alunos/importar/', views.aluno_importar, name='aluno_importar'),
    path('alunos/modelo-planilha/', views.aluno_modelo_planilha, name='aluno_modelo_planilha'),
    path('alunos/<int:pk>/editar/', views.aluno_update, name='aluno_update'),
    path('alunos/<int:pk>/toggle-ativo/', views.aluno_toggle_ativo, name='aluno_toggle_ativo'),
    # Turmas
    path('turmas/', views.turma_list, name='turma_list'),
    path('turmas/nova/', views.turma_create, name='turma_create'),
    path('turmas/<int:pk>/editar/', views.turma_update, name='turma_update'),
    path('turmas/<int:pk>/toggle-ativo/', views.turma_toggle_ativo, name='turma_toggle_ativo'),
    # Matrículas (vínculo aluno × turma)
    path('turmas/<int:pk>/matriculas/', views.turma_matriculas, name='turma_matriculas'),
    path('turmas/<int:pk>/matricular/', views.turma_matricular, name='turma_matricular'),
    path('turmas/<int:pk>/importar-alunos/', views.turma_importar_alunos, name='turma_importar_alunos'),
    path('turmas/<int:pk>/desmatricular/', views.turma_desmatricular, name='turma_desmatricular'),
    # Docências (professor × disciplina na turma) — só o administrador
    path('turmas/<int:pk>/docencias/', views.turma_docencias, name='turma_docencias'),
    path('turmas/<int:pk>/docencias/alternar/', views.turma_docencia_toggle, name='turma_docencia_toggle'),
    # Professores (contas do grupo Docente) — só o administrador
    path('professores/', views.professor_list, name='professor_list'),
    path('professores/novo/', views.professor_create, name='professor_create'),
    path('professores/credenciais/', views.professor_credenciais, name='professor_credenciais'),
    path('professores/<int:pk>/editar/', views.professor_update, name='professor_update'),
    path('professores/<int:pk>/redefinir-senha/', views.professor_redefinir_senha, name='professor_redefinir_senha'),
    path('professores/<int:pk>/toggle-ativo/', views.professor_toggle_ativo, name='professor_toggle_ativo'),
]
