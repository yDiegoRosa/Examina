"""
test_permissions.py — Testes de permissões e acesso às views protegidas.

Cobre:
- is_docente_ou_coordenador(): unauthenticated, superuser, grupos, permissão direta
- can_change_questao(): casos análogos
- Views HTTP:
  - questao_create: login_required + permission_required add_questao
  - questao_update: login_required + permission_required change_questao
  - questao_import: login_required + permission_required add_questao
  - questao_toggle_ativo: somente POST (405 para GET); permissão change_questao
  - questao_list: apenas login_required

Nota sobre URLs:
  banco_questoes.urls está montado na raiz ('') em Examina/urls.py, portanto:
  - questao_list    → /questoes/
  - questao_create  → /questoes/nova/
  - questao_update  → /questoes/<pk>/editar/
  - questao_import  → /questoes/importar/
  - questao_toggle  → /questoes/<pk>/toggle-ativo/
"""

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import Client

from banco_questoes.permissions import can_change_questao, is_docente_ou_coordenador

# ---------------------------------------------------------------------------
# is_docente_ou_coordenador
# ---------------------------------------------------------------------------


class TestIsDocenteOuCoordenador:
    def test_usuario_nao_autenticado_retorna_false(self):
        user = AnonymousUser()
        assert is_docente_ou_coordenador(user) is False

    @pytest.mark.django_db
    def test_superuser_retorna_true(self, user_superuser):
        assert is_docente_ou_coordenador(user_superuser) is True

    @pytest.mark.django_db
    def test_grupo_docente_retorna_true(self, user_docente):
        assert is_docente_ou_coordenador(user_docente) is True

    @pytest.mark.django_db
    def test_grupo_coordenador_retorna_true(self, user_coordenador):
        assert is_docente_ou_coordenador(user_coordenador) is True

    @pytest.mark.django_db
    def test_usuario_sem_grupo_retorna_false(self, user_sem_permissao):
        assert is_docente_ou_coordenador(user_sem_permissao) is False

    @pytest.mark.django_db
    def test_permissao_add_questao_direta_retorna_true(self, user_com_perm_add):
        assert is_docente_ou_coordenador(user_com_perm_add) is True


# ---------------------------------------------------------------------------
# can_change_questao
# ---------------------------------------------------------------------------


class TestCanChangeQuestao:
    def test_usuario_nao_autenticado_retorna_false(self):
        user = AnonymousUser()
        assert can_change_questao(user) is False

    @pytest.mark.django_db
    def test_superuser_retorna_true(self, user_superuser):
        assert can_change_questao(user_superuser) is True

    @pytest.mark.django_db
    def test_grupo_docente_retorna_true(self, user_docente):
        assert can_change_questao(user_docente) is True

    @pytest.mark.django_db
    def test_usuario_sem_grupo_retorna_false(self, user_sem_permissao):
        assert can_change_questao(user_sem_permissao) is False

    @pytest.mark.django_db
    def test_permissao_change_questao_direta_retorna_true(self, user_com_perm_change):
        assert can_change_questao(user_com_perm_change) is True


# ---------------------------------------------------------------------------
# Views — questao_list (apenas login_required)
# URLs: banco_questoes montado em '' → /questoes/
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestQuestaoListPermissoes:
    URL = '/questoes/'

    def test_anonimo_redirecionado_para_login(self):
        client = Client()
        response = client.get(self.URL)
        assert response.status_code == 302
        assert 'login' in response['Location']

    def test_usuario_sem_permissao_especial_acessa_200(self, user_sem_permissao):
        """questao_list exige apenas autenticação — qualquer usuário logado acessa."""
        client = Client()
        client.force_login(user_sem_permissao)
        response = client.get(self.URL)
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Views — questao_create (add_questao)
# URL: /questoes/nova/
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestQuestaoCreatePermissoes:
    URL = '/questoes/nova/'

    def test_anonimo_redirecionado_para_login(self):
        client = Client()
        response = client.get(self.URL)
        assert response.status_code == 302
        assert 'login' in response['Location']

    def test_usuario_sem_permissao_recebe_403(self, user_sem_permissao):
        client = Client()
        client.force_login(user_sem_permissao)
        response = client.get(self.URL)
        assert response.status_code == 403

    def test_docente_acessa_200(self, user_docente):
        client = Client()
        client.force_login(user_docente)
        response = client.get(self.URL)
        assert response.status_code == 200

    def test_superuser_acessa_200(self, user_superuser):
        client = Client()
        client.force_login(user_superuser)
        response = client.get(self.URL)
        assert response.status_code == 200

    def test_usuario_com_perm_add_acessa_200(self, user_com_perm_add):
        client = Client()
        client.force_login(user_com_perm_add)
        response = client.get(self.URL)
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Views — questao_update (change_questao)
# URL: /questoes/<pk>/editar/
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestQuestaoUpdatePermissoes:
    def _url(self, pk):
        return f'/questoes/{pk}/editar/'

    def test_anonimo_redirecionado_para_login(self, questao_objetiva):
        client = Client()
        response = client.get(self._url(questao_objetiva.pk))
        assert response.status_code == 302
        assert 'login' in response['Location']

    def test_usuario_sem_permissao_recebe_403(self, user_sem_permissao, questao_objetiva):
        client = Client()
        client.force_login(user_sem_permissao)
        response = client.get(self._url(questao_objetiva.pk))
        assert response.status_code == 403

    def test_docente_acessa_200(self, user_docente, questao_objetiva):
        client = Client()
        client.force_login(user_docente)
        response = client.get(self._url(questao_objetiva.pk))
        assert response.status_code == 200

    def test_usuario_com_perm_change_acessa_200(self, user_com_perm_change, questao_objetiva):
        # Acesso por perfil: só o autor da questão (ou o administrador) a edita.
        questao_objetiva.autor = user_com_perm_change
        questao_objetiva.save(update_fields=['autor'])
        client = Client()
        client.force_login(user_com_perm_change)
        response = client.get(self._url(questao_objetiva.pk))
        assert response.status_code == 200

    def test_usuario_com_perm_change_nao_edita_questao_de_outro_autor(self, user_com_perm_change, questao_objetiva):
        client = Client()
        client.force_login(user_com_perm_change)
        response = client.get(self._url(questao_objetiva.pk))
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Views — questao_import (add_questao)
# URL: /questoes/importar/
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestQuestaoImportPermissoes:
    URL = '/questoes/importar/'

    def test_anonimo_redirecionado_para_login(self):
        client = Client()
        response = client.get(self.URL)
        assert response.status_code == 302
        assert 'login' in response['Location']

    def test_usuario_sem_permissao_recebe_403(self, user_sem_permissao):
        client = Client()
        client.force_login(user_sem_permissao)
        response = client.get(self.URL)
        assert response.status_code == 403

    def test_coordenador_acessa_200(self, user_coordenador):
        client = Client()
        client.force_login(user_coordenador)
        response = client.get(self.URL)
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Views — questao_toggle_ativo (POST-only + change_questao)
# URL: /questoes/<pk>/toggle-ativo/
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestQuestaoToggleAtivo:
    def _url(self, pk):
        return f'/questoes/{pk}/toggle-ativo/'

    def test_get_retorna_405(self, user_com_perm_change, questao_objetiva):
        """GET não deve ser permitido → 405 Method Not Allowed.
        Usa user_com_perm_change para que o check de permissão passe e chegue ao check de método.
        """
        client = Client()
        client.force_login(user_com_perm_change)
        response = client.get(self._url(questao_objetiva.pk))
        assert response.status_code == 405

    def test_anonimo_redirecionado_para_login(self, questao_objetiva):
        client = Client()
        response = client.post(self._url(questao_objetiva.pk))
        assert response.status_code == 302
        assert 'login' in response['Location']

    def test_usuario_sem_permissao_recebe_403(self, user_sem_permissao, questao_objetiva):
        client = Client()
        client.force_login(user_sem_permissao)
        response = client.post(self._url(questao_objetiva.pk))
        assert response.status_code == 403

    def test_docente_pode_inativar(self, user_docente, questao_objetiva):
        """POST com permissão deve inverter ativo e redirecionar."""
        client = Client()
        client.force_login(user_docente)
        ativo_antes = questao_objetiva.ativo

        response = client.post(self._url(questao_objetiva.pk))
        assert response.status_code == 302

        questao_objetiva.refresh_from_db()
        assert questao_objetiva.ativo != ativo_antes

    def test_docente_pode_reativar(self, user_docente, questao_objetiva):
        """Dois POSTs consecutivos devem inativar e depois reativar."""
        client = Client()
        client.force_login(user_docente)

        client.post(self._url(questao_objetiva.pk))  # inativa
        client.post(self._url(questao_objetiva.pk))  # reativa

        questao_objetiva.refresh_from_db()
        assert questao_objetiva.ativo is True  # voltou ao estado original


# ---------------------------------------------------------------------------
# Permissões dos grupos após as migrations (banco novo)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPermissoesDosGruposAposMigrations:
    """
    O banco de teste nasce das migrations — o mesmo caminho de uma instalação limpa.
    A 0002 sozinha deixava os grupos sem permissões (Permission só existe após o
    post_migrate); a 0008 reaplica o mapeamento.
    """

    def test_docente_tem_permissoes_do_banco_de_questoes(self):
        from django.contrib.auth.models import Group

        codenames = set(Group.objects.get(name='Docente').permissions.values_list('codename', flat=True))
        assert {'add_questao', 'change_questao', 'delete_questao', 'view_questao', 'add_topico'} <= codenames

    def test_coordenador_tem_permissoes_sem_delete(self):
        from django.contrib.auth.models import Group

        codenames = set(Group.objects.get(name='Coordenador').permissions.values_list('codename', flat=True))
        assert {'add_questao', 'change_questao', 'view_questao', 'view_disciplina'} <= codenames
        assert 'delete_questao' not in codenames

    def test_membro_do_grupo_herda_permissao_do_menu_importar(self, django_user_model):
        from django.contrib.auth.models import Group

        user = django_user_model.objects.create_user(username='doc_menu', password='testpass123')  # noqa: S106
        user.groups.add(Group.objects.get(name='Docente'))
        assert user.has_perm('banco_questoes.add_questao')
