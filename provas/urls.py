from django.urls import path

from . import views

app_name = 'provas'

urlpatterns = [
    # Máscaras de Prova (Blueprints)
    path('', views.prova_list, name='index'),
    path('provas/', views.prova_list, name='prova_list'),
    path('mascaras/', views.mascara_list, name='mascara_list'),
    path('mascaras/nova/', views.mascara_create, name='mascara_create'),
    path('mascaras/<int:pk>/', views.mascara_detail, name='mascara_detail'),
    path('mascaras/<int:pk>/editar/', views.mascara_update, name='mascara_update'),
    path('mascaras/<int:pk>/gerar/', views.mascara_gerar_prova, name='mascara_gerar_prova'),
    path('mascaras/<int:pk>/toggle-ativo/', views.mascara_toggle_ativo, name='mascara_toggle_ativo'),
    # Revisão da Prova Gerada & Troca Individual de Questões
    path('provas/nova-manual/', views.prova_manual_create, name='prova_manual_create'),
    path('provas/<int:pk>/revisao/', views.prova_revisao, name='prova_revisao'),
    path('provas/<int:pk>/capa-pdf/', views.prova_capa_pdf, name='prova_capa_pdf'),
    path('provas/<int:pk>/pdf/', views.prova_pdf, name='prova_pdf'),
    path('provas/<int:pk>/gabarito-pdf/', views.prova_gabarito_pdf, name='prova_gabarito_pdf'),
    path('provas/<int:pk>/gerar-versoes/', views.prova_gerar_versoes_view, name='prova_gerar_versoes'),
    path('provas/<int:pk>/pacote-zip/', views.prova_pacote_zip, name='prova_pacote_zip'),
    path('itens/<int:item_pk>/candidatas/', views.item_candidatas_substituicao, name='item_candidatas_substituicao'),
    path('itens/<int:item_pk>/substituir/', views.item_substituir_questao, name='item_substituir_questao'),
    # AJAX / APIs
    path('api/topicos-estoque/', views.api_topicos_e_estoque, name='api_topicos_estoque'),
    # Demonstração PDF
    path('pdf-teste/', views.pdf_teste_view, name='pdf_teste'),
]
