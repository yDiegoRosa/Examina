from django.urls import path

from . import views

app_name = 'desempenho'

urlpatterns = [
    path('', views.dashboard_fragilidades, name='dashboard'),
    path('psicometria/', views.psicometria, name='psicometria'),
]
