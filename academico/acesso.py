"""
Acesso por perfil: o que cada usuário enxerga (pedido do Diego em 2026-10-17).

- **Administrador** (superusuário ou grupo "Administrador"): vê e altera tudo; é o único que
  gerencia os cadastros (acadêmico, docências) e gera o Simulado ENADE.
- **Coordenador** (grupo "Coordenador"): vê tudo; altera só o que é dele, como um professor.
- **Professor** (grupo "Docente"): vê e altera só o que é dele — as questões, máscaras e
  provas de que é autor e as aplicações dessas provas —, monta provas só com as próprias
  questões e só das disciplinas que leciona, aplica provas só nas turmas das suas docências
  e, no desempenho, vê só os pares turma + disciplina das suas docências (inclusive o ENADE).

As funções recebem um queryset (ou partem de ``objects.all()``) e devolvem o recorte do
usuário. Nas telas abertas por número na URL, use-as com ``get_object_or_404``: objeto de
outro professor responde 404, sem revelar que existe.
"""

from __future__ import annotations

from django.db.models import Q

from banco_questoes.permissions import eh_administrador, ve_tudo

__all__ = [
    'aplicacoes_visiveis',
    'autor_do_acervo',
    'disciplinas_permitidas',
    'docencias_do_usuario',
    'eh_administrador',
    'filtro_turma_disciplina',
    'mascaras_visiveis',
    'no_acervo_de',
    'pode_aplicar',
    'provas_visiveis',
    'questoes_alteraveis',
    'questoes_visiveis',
    'turmas_permitidas',
    've_tudo',
]


def docencias_do_usuario(user):
    """Docências ativas do usuário (vazio para quem não é professor de nenhuma turma)."""
    from .models import Docencia

    return Docencia.objects.filter(professor=user, ativo=True)


def disciplinas_permitidas(user):
    """Disciplinas em que o usuário pode criar provas: todas (visão total) ou as que leciona."""
    from banco_questoes.models import Disciplina

    disciplinas = Disciplina.objects.filter(ativo=True)
    if ve_tudo(user):
        return disciplinas
    return disciplinas.filter(docencias__professor=user, docencias__ativo=True).distinct()


def turmas_permitidas(user, queryset=None):
    """Turmas que o usuário enxerga: todas (visão total) ou as das suas docências."""
    from .models import Turma

    turmas = Turma.objects.all() if queryset is None else queryset
    if ve_tudo(user):
        return turmas
    return turmas.filter(docencias__professor=user, docencias__ativo=True).distinct()


def _do_autor(user, queryset, campo='autor'):
    return queryset if ve_tudo(user) else queryset.filter(**{campo: user})


def questoes_visiveis(user, queryset=None):
    """Questões que o usuário vê (no acervo e para montar provas): todas ou as dele."""
    from banco_questoes.models import Questao

    return _do_autor(user, Questao.objects.all() if queryset is None else queryset)


def autor_do_acervo(user):
    """
    De quem são as questões que o usuário usa para montar provas: ``None`` (acervo todo) para
    quem vê tudo; o próprio usuário para o professor (decisão do Diego: provas só com as
    próprias questões). Passado como ``autor_questoes`` ao motor de geração e à montagem manual.
    """
    return None if ve_tudo(user) else user


def no_acervo_de(queryset, autor_questoes):
    """Recorta um queryset de questões ao acervo de ``autor_questoes`` (``None`` = acervo todo)."""
    return queryset if autor_questoes is None else queryset.filter(autor=autor_questoes)


def questoes_alteraveis(user, queryset=None):
    """Questões que o usuário pode editar e inativar: todas (administrador) ou as dele."""
    from banco_questoes.models import Questao

    questoes = Questao.objects.all() if queryset is None else queryset
    return questoes if eh_administrador(user) else questoes.filter(autor=user)


def mascaras_visiveis(user, queryset=None):
    """Máscaras que o usuário vê: todas ou as dele."""
    from provas.models import Mascara

    return _do_autor(user, Mascara.objects.all() if queryset is None else queryset)


def provas_visiveis(user, queryset=None):
    """Provas que o usuário vê: todas ou as dele (o Simulado ENADE é do administrador)."""
    from provas.models import Prova

    return _do_autor(user, Prova.objects.all() if queryset is None else queryset)


def aplicacoes_visiveis(user, queryset=None):
    """Aplicações que o usuário vê: todas ou as das provas dele."""
    from provas.models import ProvaAplicacao

    return _do_autor(user, ProvaAplicacao.objects.all() if queryset is None else queryset, 'prova__autor')


def pode_aplicar(user, prova, turma) -> bool:
    """O professor só aplica a própria prova numa turma em que leciona a disciplina dela."""
    if ve_tudo(user):
        return True
    disciplina = prova.disciplina_efetiva
    return (
        prova.autor_id == user.pk
        and disciplina is not None
        and docencias_do_usuario(user).filter(turma=turma, disciplina=disciplina).exists()
    )


def filtro_turma_disciplina(user, campo_turma: str, campo_disciplina: str) -> Q | None:
    """
    Filtro dos dados de desempenho: os pares turma + disciplina das docências do usuário.

    ``None`` para quem vê tudo; ``Q`` que não casa nada para quem não tem docência. Ex.:
    ``filtro_turma_disciplina(user, 'folha_resposta__prova_aplicacao__turma', 'questao__disciplina')``.
    """
    if ve_tudo(user):
        return None
    filtro = Q(pk__in=[])
    for turma_id, disciplina_id in docencias_do_usuario(user).values_list('turma_id', 'disciplina_id'):
        filtro |= Q(**{f'{campo_turma}_id': turma_id, f'{campo_disciplina}_id': disciplina_id})
    return filtro
