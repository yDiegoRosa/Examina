"""Utilidades compartilhadas entre os apps (tratamento de parâmetros de requisição)."""


def parse_pk(valor) -> int | None:
    """
    Converte um identificador vindo de GET/POST em ``int``, ou ``None`` se inválido.

    Evita o ``ValueError`` (HTTP 500) que o ORM levanta ao filtrar uma chave inteira por
    texto arbitrário (ex.: ``?turma=abc``). Aceita só dígitos ASCII, até 18 caracteres
    (cabe em ``BigAutoField``).
    """
    if valor is None:
        return None
    texto = str(valor).strip()
    if texto.isascii() and texto.isdigit() and len(texto) <= 18:
        return int(texto)
    return None
