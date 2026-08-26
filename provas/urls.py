from django.urls import path

from . import views

app_name = 'provas'

urlpatterns = [
    path('', views.prova_list, name='index'),
    path('pdf-teste/', views.pdf_teste_view, name='pdf_teste'),
]
