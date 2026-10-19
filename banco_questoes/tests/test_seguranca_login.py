"""
Limite de tentativas de login (banco_questoes.seguranca_login) e troca de senha pelo próprio
usuário (/senha/alterar/).
"""

from datetime import timedelta

import pytest
from django.contrib.auth import authenticate
from django.urls import reverse
from django.utils import timezone

from banco_questoes.models import TentativaLogin

SENHA = 'Senha-forte-123'


@pytest.fixture
def professor(django_user_model):
    return django_user_model.objects.create_user(username='prof_login', password=SENHA)


def _entrar(client, senha, username='prof_login'):
    return client.post(reverse('login'), {'username': username, 'password': senha})


@pytest.mark.django_db
class TestLimiteDeTentativas:
    def test_bloqueia_ao_atingir_o_limite_mesmo_com_a_senha_certa(self, client, professor, settings):
        settings.LOGIN_MAX_TENTATIVAS = 3
        for _ in range(2):
            resposta = _entrar(client, 'errada')
            assert 'Credenciais Inválidas' in resposta.content.decode()

        # A 3ª tentativa errada atinge o limite e já mostra o aviso de bloqueio.
        resposta = _entrar(client, 'errada')
        assert 'Acesso temporariamente bloqueado' in resposta.content.decode()
        assert TentativaLogin.objects.filter(username='prof_login').count() == 3

        # Bloqueado: nem a senha certa entra (e a espera não aumenta).
        resposta = _entrar(client, SENHA)
        assert resposta.status_code == 200
        assert 'Tente novamente em 15 minuto(s)' in resposta.content.decode()
        assert '_auth_user_id' not in client.session
        assert TentativaLogin.objects.filter(username='prof_login').count() == 3

    def test_bloqueio_vale_para_o_backend_admin_e_ignora_maiusculas(self, client, professor, settings):
        settings.LOGIN_MAX_TENTATIVAS = 2
        for _ in range(2):
            _entrar(client, 'errada', username='PROF_LOGIN')

        assert authenticate(username='prof_login', password=SENHA) is None
        assert client.login(username='prof_login', password=SENHA) is False

    def test_outro_usuario_nao_e_afetado(self, client, professor, django_user_model, settings):
        settings.LOGIN_MAX_TENTATIVAS = 2
        django_user_model.objects.create_user(username='outra_prof', password=SENHA)
        for _ in range(2):
            _entrar(client, 'errada')

        resposta = _entrar(client, SENHA, username='outra_prof')
        assert resposta.status_code == 302

    def test_bloqueio_expira_depois_da_janela(self, client, professor, settings):
        settings.LOGIN_MAX_TENTATIVAS = 2
        for _ in range(2):
            _entrar(client, 'errada')
        TentativaLogin.objects.update(criado_em=timezone.now() - timedelta(minutes=16))

        resposta = _entrar(client, SENHA)
        assert resposta.status_code == 302

    def test_login_certo_zera_as_tentativas(self, client, professor):
        _entrar(client, 'errada')
        _entrar(client, 'errada')
        resposta = _entrar(client, SENHA)

        assert resposta.status_code == 302
        assert not TentativaLogin.objects.filter(username='prof_login').exists()


@pytest.mark.django_db
class TestAlterarSenha:
    def test_exige_login(self, client):
        resposta = client.get(reverse('password_change'))
        assert resposta.status_code == 302
        assert reverse('login') in resposta['Location']

    def test_professor_troca_a_propria_senha_e_continua_conectado(self, client, professor):
        client.force_login(professor)
        pagina = client.get(reverse('password_change'))
        assert pagina.status_code == 200
        assert 'Alterar senha' in pagina.content.decode()

        resposta = client.post(
            reverse('password_change'),
            {'old_password': SENHA, 'new_password1': 'Nova-senha-456', 'new_password2': 'Nova-senha-456'},
        )
        assert resposta.status_code == 302
        assert resposta['Location'] == reverse('password_change_done')
        assert client.get(reverse('password_change_done')).status_code == 200  # sessão mantida

        professor.refresh_from_db()
        assert professor.check_password('Nova-senha-456')

    def test_senha_atual_errada_nao_troca(self, client, professor):
        client.force_login(professor)
        resposta = client.post(
            reverse('password_change'),
            {'old_password': 'errada', 'new_password1': 'Nova-senha-456', 'new_password2': 'Nova-senha-456'},
        )
        assert resposta.status_code == 200
        professor.refresh_from_db()
        assert professor.check_password(SENHA)

    def test_menu_tem_o_link_para_alterar_senha(self, client, professor):
        client.force_login(professor)
        html = client.get(reverse('banco_questoes:questao_list')).content.decode()
        # Botão redondo da conta na barra (nome no title) e item do menu no celular.
        assert f'href="{reverse("password_change")}" class="user-capsule"' in html
        assert 'title="prof_login — Minha conta (alterar senha)"' in html
        assert 'class="nav-item-conta"' in html
