"""
conftest.py — Fixtures compartilhadas para a suite pytest-django do Examina.

Fornece:
- Usuários com diferentes perfis (sem permissão, docente, coordenador, superusuário)
- Objetos de banco prontos (Disciplina, Topico, Questao objetiva e discursiva)
- Strings CSV válidas para os parsers
"""

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType

from banco_questoes.models import Alternativa, Disciplina, Questao, Topico

User = get_user_model()


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------


def _get_or_create_group(name: str) -> Group:
    group, _ = Group.objects.get_or_create(name=name)
    return group


def _setup_group_permissions(group: Group) -> None:
    """Adiciona add_questao e change_questao ao grupo para que @permission_required passe."""
    ct = ContentType.objects.get_for_model(Questao)
    for codename in ('add_questao', 'change_questao'):
        perm = Permission.objects.get(content_type=ct, codename=codename)
        group.permissions.add(perm)


def _add_perm(user, codename: str) -> None:
    ct = ContentType.objects.get_for_model(Questao)
    perm = Permission.objects.get(content_type=ct, codename=codename)
    user.user_permissions.add(perm)


# ---------------------------------------------------------------------------
# Fixtures de Usuários
# ---------------------------------------------------------------------------


@pytest.fixture
def user_sem_permissao(db):
    """Usuário autenticado sem nenhuma permissão especial."""
    return User.objects.create_user(
        username='sem_permissao',
        password='testpass123',
    )


@pytest.fixture
def user_docente(db):
    """Usuário no grupo Docente (com permissões add_questao e change_questao)."""
    grupo = _get_or_create_group('Docente')
    _setup_group_permissions(grupo)
    user = User.objects.create_user(username='docente', password='testpass123')
    user.groups.add(grupo)
    return user


@pytest.fixture
def user_coordenador(db):
    """Usuário no grupo Coordenador (com permissões add_questao e change_questao)."""
    grupo = _get_or_create_group('Coordenador')
    _setup_group_permissions(grupo)
    user = User.objects.create_user(username='coordenador', password='testpass123')
    user.groups.add(grupo)
    return user


@pytest.fixture
def user_superuser(db):
    """Superusuário (acesso total)."""
    return User.objects.create_superuser(
        username='admin',
        password='testpass123',
        email='admin@example.com',
    )


@pytest.fixture
def user_com_perm_add(db):
    """Usuário com permissão add_questao concedida diretamente (sem grupo)."""
    user = User.objects.create_user(username='add_perm', password='testpass123')
    _add_perm(user, 'add_questao')
    return user


@pytest.fixture
def user_com_perm_change(db):
    """Usuário com permissão change_questao concedida diretamente (sem grupo)."""
    user = User.objects.create_user(username='change_perm', password='testpass123')
    _add_perm(user, 'change_questao')
    return user


# ---------------------------------------------------------------------------
# Fixtures de Modelos
# ---------------------------------------------------------------------------


@pytest.fixture
def disciplina(db):
    return Disciplina.objects.create(nome='Computação')


@pytest.fixture
def disciplina_outra(db):
    return Disciplina.objects.create(nome='Matemática')


@pytest.fixture
def topico(db, disciplina):
    return Topico.objects.create(disciplina=disciplina, nome='Algoritmos')


@pytest.fixture
def questao_objetiva(db, disciplina, user_docente):
    """Questão objetiva válida com 4 alternativas (A–D), gabarito B."""
    q = Questao.objects.create(
        tipo=Questao.Tipo.OBJETIVA,
        dificuldade=Questao.Dificuldade.MEDIO,
        disciplina=disciplina,
        enunciado='Qual é a capital do Brasil?',
        ativo=True,
        autor=user_docente,
    )
    Alternativa.objects.create(questao=q, letra='A', texto='São Paulo', correta=False)
    Alternativa.objects.create(questao=q, letra='B', texto='Brasília', correta=True)
    Alternativa.objects.create(questao=q, letra='C', texto='Rio de Janeiro', correta=False)
    Alternativa.objects.create(questao=q, letra='D', texto='Salvador', correta=False)
    return q


@pytest.fixture
def questao_discursiva(db, disciplina, user_docente):
    """Questão discursiva válida com critérios de correção."""
    return Questao.objects.create(
        tipo=Questao.Tipo.DISCURSIVA,
        dificuldade=Questao.Dificuldade.FACIL,
        disciplina=disciplina,
        enunciado='Explique o conceito de polimorfismo.',
        criterios_correcao='O aluno deve mencionar sobrescrita e sobrecarga.',
        ativo=True,
        autor=user_docente,
    )


# ---------------------------------------------------------------------------
# Fixtures de CSV
# ---------------------------------------------------------------------------


@pytest.fixture
def csv_objetivo_valido():
    """CSV 10 colunas com questão objetiva válida (vírgula, gabarito C)."""
    return (
        'origem,questao,enunciado,opcao_a,opcao_b,opcao_c,opcao_d,opcao_e,gabarito,comentarios\n'
        'ENADE ADS 2021,26,Qual protocolo opera na camada de transporte?,'
        'HTTP,FTP,TCP,DNS,,C,TCP é o protocolo da camada de transporte.\n'
    )


@pytest.fixture
def csv_discursivo_valido():
    """CSV 10 colunas com questão discursiva válida (sem alternativas)."""
    return (
        'origem,questao,enunciado,opcao_a,opcao_b,opcao_c,opcao_d,opcao_e,gabarito,comentarios\n'
        'POSCOMP 2025,5,Descreva o funcionamento do algoritmo QuickSort.,,,,,,,'
        'O aluno deve descrever a divisão por pivô e a recursão.\n'
    )


@pytest.fixture
def csv_sem_enunciado():
    """CSV com coluna enunciado ausente (header inválido)."""
    return 'origem,questao,opcao_a,opcao_b,gabarito\nENADE,1,Alt A,Alt B,A\n'
