from django.urls import path

from . import views

app_name = 'banco_questoes'

urlpatterns = [
    path('questoes/', views.questao_list, name='questao_list'),
    path('questoes/nova/', views.questao_create, name='questao_create'),
    path('questoes/<int:pk>/editar/', views.questao_update, name='questao_update'),
    path('questoes/importar/', views.questao_import, name='questao_import'),
    path('questoes/modelo-csv/', views.download_modelo_csv, name='download_modelo_csv'),
]
