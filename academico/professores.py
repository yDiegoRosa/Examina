"""
Contas de professor criadas em Acadêmico → Professores.

- Login no formato ``nome.sobrenome`` (primeiro nome + último sobrenome, sem acentos, em
  minúsculas); se já existir, ``nome.sobrenome2``, ``nome.sobrenome3``...
- Senha temporária aleatória, diferente para cada professor, mostrada uma única vez ao
  administrador (não fica gravada em lugar nenhum além do hash do Django). No primeiro login o
  professor é obrigado a trocá-la (``PerfilProfessor.troca_senha_pendente``).
"""

import secrets
import unicodedata

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import transaction

from .models import PerfilProfessor

GRUPO_DOCENTE = 'Docente'

# Partículas que não viram sobrenome no login ("Ana Maria de Souza" → ana.souza).
_PARTICULAS = {'da', 'das', 'de', 'do', 'dos', 'e', 'd'}
# Sem caracteres que se confundem ao ditar ou copiar à mão (0/O, 1/l/I).
_ALFABETO_SENHA = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789'


def _sem_acentos(texto: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFKD', texto) if not unicodedata.combining(c))


def partes_do_nome(nome_completo: str) -> tuple[str, str]:
    """("Ana", "Maria de Souza"): o primeiro nome e o resto, como o Django guarda (first/last_name)."""
    palavras = nome_completo.split()
    return (palavras[0], ' '.join(palavras[1:])) if palavras else ('', '')


def login_base(nome_completo: str) -> str:
    """Login sem o número de desempate. Ex.: "Ana Maria de Souza" → "ana.souza"; "João" → "joao"."""
    palavras = [''.join(c for c in p if c.isalnum()) for p in _sem_acentos(nome_completo).lower().split()]
    palavras = [p for p in palavras if p]
    if not palavras:
        return ''
    sobrenomes = [p for p in palavras[1:] if p not in _PARTICULAS]
    return f'{palavras[0]}.{sobrenomes[-1]}' if sobrenomes else palavras[0]


def gerar_login(nome_completo: str) -> str:
    """Login livre a partir do nome: ``ana.souza``, ou ``ana.souza2``... se já existir."""
    base = login_base(nome_completo)
    if not base:
        raise ValueError('Informe o nome completo do professor.')
    usuarios = get_user_model().objects
    login, numero = base, 1
    while usuarios.filter(username__iexact=login).exists():
        numero += 1
        login = f'{base}{numero}'
    return login


def gerar_senha_temporaria(tamanho: int = 10) -> str:
    return ''.join(secrets.choice(_ALFABETO_SENHA) for _ in range(tamanho))


@transaction.atomic
def criar_professor(nome_completo: str, email: str = '') -> tuple:
    """Cria a conta (grupo Docente, troca de senha pendente). Devolve (usuário, senha temporária)."""
    primeiro, resto = partes_do_nome(nome_completo)
    senha = gerar_senha_temporaria()
    usuario = get_user_model().objects.create_user(
        username=gerar_login(nome_completo), password=senha, first_name=primeiro, last_name=resto, email=email
    )
    usuario.groups.add(Group.objects.get_or_create(name=GRUPO_DOCENTE)[0])
    PerfilProfessor.objects.create(usuario=usuario, troca_senha_pendente=True)
    return usuario, senha


@transaction.atomic
def redefinir_senha(usuario) -> str:
    """Nova senha temporária; o professor volta a ter de trocá-la no próximo login."""
    senha = gerar_senha_temporaria()
    usuario.set_password(senha)
    usuario.save(update_fields=['password'])
    PerfilProfessor.objects.update_or_create(usuario=usuario, defaults={'troca_senha_pendente': True})
    return senha


def troca_de_senha_pendente(usuario) -> bool:
    perfil = getattr(usuario, 'perfil_professor', None) if usuario.is_authenticated else None
    return bool(perfil and perfil.troca_senha_pendente)
