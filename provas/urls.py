from django.urls import path

from . import views

app_name = 'provas'

urlpatterns = [
    # Máscaras de Prova (Blueprints)
    path('', views.prova_list, name='index'),
    path('provas/', views.prova_list, name='prova_list'),
    path('mascaras/', views.mascara_list, name='mascara_list'),
    path('mascaras/nova/', views.mascara_create, name='mascara_create'),
    path('mascaras/nova/enade/', views.mascara_enade_create, name='mascara_enade_create'),
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
    # Aplicações (prova × turma × data): listagem, cadastro, painel e notas
    path('aplicacoes/', views.aplicacao_list, name='aplicacao_list'),
    path('aplicacoes/nova/', views.aplicacao_create, name='aplicacao_create'),
    path('aplicacoes/<int:pk>/', views.aplicacao_detail, name='aplicacao_detail'),
    path(
        'aplicacoes/<int:pk>/atribuir-versoes/',
        views.aplicacao_atribuir_versoes,
        name='aplicacao_atribuir_versoes',
    ),
    path('aplicacoes/<int:pk>/notas/', views.aplicacao_notas, name='aplicacao_notas'),
    path('aplicacoes/<int:pk>/folhas-zip/', views.aplicacao_folhas_zip, name='aplicacao_folhas_zip'),
    path('aplicacoes/<int:pk>/cadernos-zip/', views.aplicacao_cadernos_zip, name='aplicacao_cadernos_zip'),
    path('aplicacoes/<int:pk>/ata/', views.aplicacao_ata_pdf, name='aplicacao_ata_pdf'),
    path('aplicacoes/<int:pk>/upload-folhas/', views.aplicacao_upload_folhas, name='aplicacao_upload_folhas'),
    path('aplicacoes/<int:pk>/corrigir-folhas/', views.aplicacao_corrigir_folhas, name='aplicacao_corrigir_folhas'),
    path('aplicacoes/<int:pk>/revisao/', views.aplicacao_revisao_manual, name='aplicacao_revisao_manual'),
    path('desempenhos/<int:pk>/revisao-manual/', views.desempenho_revisao_manual, name='desempenho_revisao_manual'),
    path(
        'digitalizacoes/<int:pk>/vincular-folha/',
        views.digitalizacao_vincular_folha,
        name='digitalizacao_vincular_folha',
    ),
    path('aplicacoes/<int:pk>/folhas/', views.aplicacao_folhas, name='aplicacao_folhas'),
    path('folhas/<int:pk>/lancamento/', views.folha_lancamento_manual, name='folha_lancamento_manual'),
    path('itens/<int:item_pk>/candidatas/', views.item_candidatas_substituicao, name='item_candidatas_substituicao'),
    path('itens/<int:item_pk>/substituir/', views.item_substituir_questao, name='item_substituir_questao'),
    # AJAX / APIs
    path('api/topicos-estoque/', views.api_topicos_e_estoque, name='api_topicos_estoque'),
    # Demonstração PDF
    path('folha-resposta-teste/', views.folha_resposta_demo, name='folha_resposta_demo'),
]
