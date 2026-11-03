"""
URL configuration for Examina project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.conf import settings
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from .views import AlterarSenhaView, LoginView, media_protegida, privacidade

urlpatterns = [
    path('admin/', admin.site.urls),
    path('login/', LoginView.as_view(), name='login'),
    path('privacidade/', privacidade, name='privacidade'),
    path(
        'logout/',
        auth_views.LogoutView.as_view(),
        name='logout',
    ),
    # Troca de senha pelo próprio usuário (templates com nome próprio: os padrões
    # "registration/password_change_*" do admin teriam precedência).
    # Obrigatória no primeiro acesso do professor (senha temporária) — ver AlterarSenhaView.
    path('senha/alterar/', AlterarSenhaView.as_view(), name='password_change'),
    path(
        'senha/alterada/',
        auth_views.PasswordChangeDoneView.as_view(template_name='registration/alterar_senha_ok.html'),
        name='password_change_done',
    ),
    path('academico/', include('academico.urls')),
    path('provas/', include('provas.urls')),
    path('desempenho/', include('desempenho.urls')),
    # Mídia (uploads) sempre autenticada — ver Examina.views.media_protegida.
    path(f'{settings.MEDIA_URL.strip("/")}/<path:caminho>', media_protegida, name='media_protegida'),
    path('', include('banco_questoes.urls')),
]
