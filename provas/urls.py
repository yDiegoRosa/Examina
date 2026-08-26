from django.urls import path

from . import views

app_name = 'provas'

urlpatterns = [
    # Máscaras de Prova (Blueprints)
    path('', views.mascara_list, name='index'),
    path('mascaras/', views.mascara_list, name='mascara_list'),
    path('mascaras/nova/', views.mascara_create, name='mascara_create'),
    path('mascaras/<int:pk>/', views.mascara_detail, name='mascara_detail'),
    path('mascaras/<int:pk>/editar/', views.mascara_update, name='mascara_update'),
    path('mascaras/<int:pk>/toggle-ativo/', views.mascara_toggle_ativo, name='mascara_toggle_ativo'),
    # AJAX / APIs
    path('api/topicos-estoque/', views.api_topicos_e_estoque, name='api_topicos_estoque'),
    # Demonstração PDF
    path('pdf-teste/', views.pdf_teste_view, name='pdf_teste'),
]
