"""
Retenção de dados pessoais (LGPD) — prazos aprovados pela coordenação em 2026-11-02.

- Fotos dos cartões-resposta: apagadas ``RETENCAO_FOTOS_DIAS`` (180) depois da data da aplicação —
  servem à correção e à revisão de nota; as notas lançadas continuam.
- Alunos: anonimizados (nome "Aluno anonimizado", matrícula ``ANON-<id>``) quando estão sem matrícula
  ativa há ``RETENCAO_ALUNOS_DIAS`` (730). Como cadernos, cartões, ata e notas sempre leem o nome do
  cadastro, as notas e respostas do aluno ficam anônimas junto e as estatísticas das questões continuam.
  Enquanto o aluno tiver matrícula ativa, os dados dele ficam identificados.
- Tentativas de login (com IP): apagadas depois de ``RETENCAO_TENTATIVAS_LOGIN_DIAS`` (30); sessões
  vencidas também são apagadas.

Prazo 0 desliga a regra. Roda todo dia no ``scripts/backup_completo.sh``, depois de um backup bem-
sucedido (o que for apagado ainda fica nas cópias de segurança por até 35 dias).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

NOME_ANONIMO = 'Aluno anonimizado'
PREFIXO_ANONIMO = 'ANON-'


@dataclass
class ResultadoRetencao:
    fotos: int = 0
    alunos: int = 0
    tentativas: int = 0
    sessoes: int = 0


def texto_prazo(dias: int) -> str:
    """730 → "2 anos", 180 → "6 meses", 30 → "30 dias", 0 → "sem prazo (não é apagado)"."""
    if not dias:
        return 'sem prazo (não é apagado)'
    if dias >= 365 and dias % 365 == 0:
        anos = dias // 365
        return f'{anos} ano' + ('s' if anos > 1 else '')
    if dias >= 60 and dias % 30 == 0:
        return f'{dias // 30} meses'
    return f'{dias} dia' + ('s' if dias > 1 else '')


def prazos() -> dict:
    """Prazos em vigor (dias e texto), para a página de Privacidade e o comando."""
    valores = {
        'fotos': settings.RETENCAO_FOTOS_DIAS,
        'alunos': settings.RETENCAO_ALUNOS_DIAS,
        'tentativas': settings.RETENCAO_TENTATIVAS_LOGIN_DIAS,
    }
    return {chave: {'dias': dias, 'texto': texto_prazo(dias)} for chave, dias in valores.items()}


def apagar_fotos_antigas(hoje: date, dias: int, simular: bool = False) -> int:
    from provas.models import FolhaDigitalizada

    if not dias:
        return 0
    antigas = FolhaDigitalizada.objects.filter(prova_aplicacao__data_aplicacao__lte=hoje - timedelta(days=dias))
    total = antigas.count()
    if not simular:
        for foto in antigas.iterator():
            if foto.imagem:
                foto.imagem.delete(save=False)  # o arquivo no disco
            foto.delete()
    return total


def alunos_para_anonimizar(hoje: date, dias: int) -> list:
    """Alunos sem matrícula ativa cuja última atividade (matrícula, encerramento, prova, edição) passou do prazo."""
    from academico.models import Aluno, Matricula

    if not dias:
        return []
    limite = hoje - timedelta(days=dias)
    com_matricula_ativa = Matricula.objects.filter(situacao=Matricula.Situacao.MATRICULADO).values('aluno_id')
    candidatos = (
        Aluno.objects.exclude(pk__in=com_matricula_ativa)
        .exclude(matricula__startswith=PREFIXO_ANONIMO)
        .annotate(
            ultima_matricula=Max('matriculas__data_matricula'),
            ultimo_encerramento=Max('matriculas__data_encerramento'),
            ultima_prova=Max('folhas_resposta__prova_aplicacao__data_aplicacao'),
        )
    )
    vencidos = []
    for aluno in candidatos:
        datas = [aluno.ultima_matricula, aluno.ultimo_encerramento, aluno.ultima_prova, aluno.atualizado_em.date()]
        if max(d for d in datas if d is not None) <= limite:
            vencidos.append(aluno)
    return vencidos


def anonimizar_alunos(hoje: date, dias: int, simular: bool = False) -> int:
    from academico.models import Matricula

    alunos = alunos_para_anonimizar(hoje, dias)
    if not simular:
        for aluno in alunos:
            with transaction.atomic():
                aluno.nome = NOME_ANONIMO
                aluno.matricula = f'{PREFIXO_ANONIMO}{aluno.pk}'
                aluno.ativo = False
                aluno.save(update_fields=['nome', 'matricula', 'ativo', 'atualizado_em'])
                Matricula.objects.filter(aluno=aluno).exclude(observacao='').update(observacao='')
    return len(alunos)


def apagar_tentativas_de_login(agora, dias: int, simular: bool = False) -> int:
    from banco_questoes.models import TentativaLogin

    if not dias:
        return 0
    antigas = TentativaLogin.objects.filter(criado_em__lt=agora - timedelta(days=dias))
    total = antigas.count()
    if not simular:
        antigas.delete()
    return total


def apagar_sessoes_vencidas(agora, simular: bool = False) -> int:
    from django.contrib.sessions.models import Session

    vencidas = Session.objects.filter(expire_date__lt=agora)
    total = vencidas.count()
    if not simular:
        vencidas.delete()
    return total


def aplicar_retencao(simular: bool = False, agora=None) -> ResultadoRetencao:
    agora = agora or timezone.now()
    hoje = timezone.localdate(agora)
    return ResultadoRetencao(
        fotos=apagar_fotos_antigas(hoje, settings.RETENCAO_FOTOS_DIAS, simular),
        alunos=anonimizar_alunos(hoje, settings.RETENCAO_ALUNOS_DIAS, simular),
        tentativas=apagar_tentativas_de_login(agora, settings.RETENCAO_TENTATIVAS_LOGIN_DIAS, simular),
        sessoes=apagar_sessoes_vencidas(agora, simular),
    )
