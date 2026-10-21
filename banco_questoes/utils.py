"""Utilidades compartilhadas entre os apps (tratamento de parâmetros de requisição, alternativas)."""


def alternativas_repetidas(alternativas) -> str:
    """
    Letras de alternativas com o mesmo texto (ignora maiúsculas e espaços), ex.: ``'A e C'``
    ou ``'A, B e E; C e D'``; vazio se todas são diferentes. ``alternativas``: pares (letra, texto).
    """
    grupos: dict[str, list[str]] = {}
    for letra, texto in alternativas:
        chave = ' '.join(str(texto or '').split()).casefold()
        if chave:
            grupos.setdefault(chave, []).append(str(letra))
    repetidas = [sorted(letras) for letras in grupos.values() if len(letras) > 1]
    return '; '.join(', '.join(letras[:-1]) + ' e ' + letras[-1] for letras in sorted(repetidas))


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
