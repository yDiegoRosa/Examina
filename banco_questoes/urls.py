from django.urls import path

from . import views

app_name = 'banco_questoes'

urlpatterns = [
    path('', views.questao_list, name='index'),
    path('questoes/', views.questao_list, name='questao_list'),
    path('questoes/nova/', views.questao_create, name='questao_create'),
    path('questoes/<int:pk>/editar/', views.questao_update, name='questao_update'),
    path('questoes/<int:pk>/toggle-ativo/', views.questao_toggle_ativo, name='questao_toggle_ativo'),
    path('questoes/importar/', views.questao_import, name='questao_import'),
    path('questoes/modelo-csv/', views.download_modelo_csv, name='download_modelo_csv'),
    path('questoes/modelo-excel/', views.download_modelo_xlsx, name='download_modelo_xlsx'),
]
