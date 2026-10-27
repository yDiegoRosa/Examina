"""
Disciplinas oficiais e o reconhecimento da disciplina escrita nas planilhas de importação.

Oficial é a disciplina ativa no formato "Nome (SIGLA)" — as 34 da matriz da FAETERJ-Rio
(migration ``0005_seed_disciplinas``), com a sigla sem o número do período ("Segurança da
Informação (SEG)", não "4SEG"). A importação não cria disciplinas: o texto da planilha precisa
corresponder a uma oficial, escrito de qualquer um destes jeitos (sem diferenciar acentos e
maiúsculas): "Segurança da Informação (SEG)", "Segurança da Informação", "SEG" ou "4SEG".
"""

import re
import unicodedata

from .models import Disciplina

_COM_SIGLA = re.compile(r'^(?P<nome>.*?)\s*\((?P<sigla>[^()]+)\)\s*$')
_SIGLA_COM_PERIODO = re.compile(r'^\d+(?P<sigla>[a-z][a-z0-9]*)$')

EXEMPLO = '"Segurança da Informação" ou "SEG"'


def normalizar(texto: str) -> str:
    """Sem acentos, minúsculas e espaços simples: "  Segurança  da INFORMAÇÃO" → "seguranca da informacao"."""
    sem_acento = ''.join(c for c in unicodedata.normalize('NFKD', texto or '') if not unicodedata.combining(c))
    return ' '.join(sem_acento.casefold().split())


class CatalogoDisciplinas:
    """Índice das disciplinas oficiais por nome completo, nome sem a sigla e sigla."""

    def __init__(self, disciplinas=None):
        if disciplinas is None:
            disciplinas = Disciplina.objects.filter(ativo=True)
        self._por_chave: dict[str, Disciplina] = {}
        for disciplina in disciplinas:
            partes = _COM_SIGLA.match(disciplina.nome)
            if not partes:  # fora do formato oficial (ex.: "Não Classificada")
                continue
            for chave in (disciplina.nome, partes['nome'], partes['sigla']):
                self._por_chave.setdefault(normalizar(chave), disciplina)

    def resolver(self, texto: str) -> Disciplina | None:
        """A disciplina oficial correspondente ao texto da planilha, ou None."""
        alvo = normalizar(texto)
        if not alvo:
            return None
        candidatos = [alvo]
        partes = _COM_SIGLA.match(alvo)  # "Segurança da Informação (4SEG)"
        if partes:
            candidatos += [partes['nome'], partes['sigla']]
        for candidato in list(candidatos):
            com_periodo = _SIGLA_COM_PERIODO.match(candidato)  # "4seg" → "seg"
            if com_periodo:
                candidatos.append(com_periodo['sigla'])
        for candidato in candidatos:
            if candidato in self._por_chave:
                return self._por_chave[candidato]
        return None
