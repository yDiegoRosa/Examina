"""
Importação de alunos por planilha (.xlsx ou .csv), pedido de 2026-11-02.

A planilha tem as colunas ``matricula`` e ``nome`` na primeira linha. Duas entradas:

- Acadêmico → Alunos → Importar planilha: só cadastra quem ainda não existe;
- Matrículas da turma → Importar planilha: cadastra quem falta e matricula todos na turma
  (do mesmo jeito que o botão "Matricular": reativa uma matrícula antiga do aluno nessa turma).

Decisões do Diego: matrícula que já existe **não** é alterada, mesmo com outro nome — o relatório
mostra os dois nomes para conferência. Linhas com erro são recusadas uma a uma; as demais entram.
Cadastro inativo não é matriculado (igual ao "Matricular" manual, que só lista alunos ativos).
"""

import csv
import io
from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from banco_questoes.disciplinas import normalizar
from banco_questoes.parser import CSVQuestaoParser, clean_cell_value

from .models import Aluno, Matricula, Turma

MAX_LINHAS = 5000
PREFIXO_RESERVADO = 'ANON-'  # matrículas de alunos anonimizados pela retenção (Examina/retencao.py)

# Cabeçalhos aceitos (comparados sem acento, maiúsculas ou espaços extras).
ALIASES = {
    'matricula': {'matricula', 'mat', 'n matricula', 'no matricula', 'numero da matricula', 'registro'},
    'nome': {'nome', 'nome completo', 'aluno', 'nome do aluno', 'estudante'},
}

COLUNAS_MODELO = ['matricula', 'nome']
EXEMPLOS_MODELO = [
    ['2026010123', 'Maria Silva de Souza'],
    ['2026010456', 'João Pedro Oliveira'],
]


class PlanilhaInvalidaError(Exception):
    """Problema no arquivo inteiro (formato, cabeçalho): nada é importado."""


@dataclass
class LinhaImportada:
    numero: int  # linha da planilha (o cabeçalho é a 1)
    matricula: str
    nome: str
    situacao: str = ''  # CRIADO | EXISTENTE | NOME_DIFERENTE | INATIVO | ERRO
    detalhe: str = ''
    turma: str = ''  # MATRICULADO | JA_MATRICULADO | REATIVADO (só na importação pela turma)

    @property
    def erro(self) -> bool:
        return self.situacao == 'ERRO'

    @property
    def aviso(self) -> bool:
        return self.situacao in ('NOME_DIFERENTE', 'INATIVO')


@dataclass
class ResultadoImportacao:
    linhas: list[LinhaImportada] = field(default_factory=list)
    turma: Turma | None = None

    def _contar(self, **filtro) -> int:
        chave, valor = next(iter(filtro.items()))
        return sum(1 for linha in self.linhas if getattr(linha, chave) == valor)

    @property
    def criados(self) -> int:
        return self._contar(situacao='CRIADO')

    @property
    def ja_cadastrados(self) -> int:
        return sum(1 for linha in self.linhas if linha.situacao in ('EXISTENTE', 'NOME_DIFERENTE', 'INATIVO'))

    @property
    def avisos(self) -> int:
        return sum(1 for linha in self.linhas if linha.aviso)

    @property
    def erros(self) -> int:
        return self._contar(situacao='ERRO')

    @property
    def matriculados(self) -> int:
        return sum(1 for linha in self.linhas if linha.turma in ('MATRICULADO', 'REATIVADO'))

    @property
    def ja_na_turma(self) -> int:
        return self._contar(turma='JA_MATRICULADO')


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------
def _coluna(cabecalho: str) -> str | None:
    chave = normalizar(str(cabecalho or '')).replace('º', '').replace('°', '').replace('.', '')
    chave = ' '.join(chave.split())
    for nome, aliases in ALIASES.items():
        if chave in aliases:
            return nome
    return None


def _linhas_xlsx(conteudo: bytes) -> list[list]:
    import openpyxl

    try:
        wb = openpyxl.load_workbook(io.BytesIO(conteudo), read_only=True, data_only=True)
    except Exception as exc:
        raise PlanilhaInvalidaError('Não foi possível abrir a planilha Excel (arquivo corrompido?).') from exc
    try:
        ws = wb.worksheets[0]
        return [list(linha) for linha in ws.iter_rows(values_only=True)]
    finally:
        wb.close()


def _linhas_csv(conteudo: bytes) -> list[list]:
    try:
        texto = CSVQuestaoParser._decode_bytes(conteudo)
    except Exception as exc:
        raise PlanilhaInvalidaError('Não foi possível ler o CSV (salve em UTF-8).') from exc
    texto = texto.strip()
    delimitador = CSVQuestaoParser._detect_delimiter(texto)
    return list(csv.reader(io.StringIO(texto), delimiter=delimitador))


def ler_planilha(arquivo) -> list[LinhaImportada]:
    """Linhas (matrícula, nome) da planilha, sem validar; as totalmente vazias são ignoradas."""
    nome = (getattr(arquivo, 'name', '') or '').lower()
    arquivo.seek(0)
    conteudo = arquivo.read()
    linhas = _linhas_xlsx(conteudo) if nome.endswith('.xlsx') else _linhas_csv(conteudo)
    if not linhas:
        raise PlanilhaInvalidaError('A planilha está vazia.')

    indices = {}
    for posicao, cabecalho in enumerate(linhas[0]):
        coluna = _coluna(cabecalho)
        if coluna and coluna not in indices:
            indices[coluna] = posicao
    faltando = [c for c in COLUNAS_MODELO if c not in indices]
    if faltando:
        raise PlanilhaInvalidaError(
            'A primeira linha precisa ter as colunas "matricula" e "nome" '
            f'(faltou: {", ".join(faltando)}). Baixe o modelo para conferir.'
        )

    resultado = []
    for numero, valores in enumerate(linhas[1:], start=2):

        def celula(coluna, valores=valores):
            posicao = indices[coluna]
            return clean_cell_value(valores[posicao]) if posicao < len(valores) else ''

        matricula, nome_aluno = celula('matricula'), ' '.join(celula('nome').split())
        if not matricula and not nome_aluno:
            continue
        resultado.append(LinhaImportada(numero=numero, matricula=matricula.strip(), nome=nome_aluno))
        if len(resultado) > MAX_LINHAS:
            raise PlanilhaInvalidaError(f'A planilha passa de {MAX_LINHAS} alunos; divida em arquivos menores.')
    if not resultado:
        raise PlanilhaInvalidaError('A planilha não tem nenhum aluno abaixo do cabeçalho.')
    return resultado


# ---------------------------------------------------------------------------
# Importação
# ---------------------------------------------------------------------------
def _validar(linhas: list[LinhaImportada]) -> None:
    vistas: dict[str, int] = {}
    for linha in linhas:
        problema = ''
        if not linha.matricula:
            problema = 'Matrícula vazia.'
        elif not linha.nome:
            problema = 'Nome vazio.'
        elif len(linha.matricula) > Aluno._meta.get_field('matricula').max_length:
            problema = 'Matrícula com mais de 30 caracteres.'
        elif len(linha.nome) > Aluno._meta.get_field('nome').max_length:
            problema = 'Nome com mais de 200 caracteres.'
        elif linha.matricula.upper().startswith(PREFIXO_RESERVADO):
            problema = f'Matrícula começando com "{PREFIXO_RESERVADO}" é reservada a alunos anonimizados.'
        elif linha.matricula in vistas:
            problema = f'Matrícula repetida na planilha (já aparece na linha {vistas[linha.matricula]}).'
        if problema:
            linha.situacao, linha.detalhe = 'ERRO', problema
        else:
            vistas[linha.matricula] = linha.numero


def _matricular(aluno: Aluno, turma: Turma) -> str:
    existente = Matricula.objects.filter(aluno=aluno, turma=turma).first()
    if existente and existente.situacao == Matricula.Situacao.MATRICULADO:
        return 'JA_MATRICULADO'
    Matricula.objects.update_or_create(
        aluno=aluno,
        turma=turma,
        defaults={
            'situacao': Matricula.Situacao.MATRICULADO,
            'data_matricula': timezone.localdate(),
            'data_encerramento': None,
        },
    )
    return 'REATIVADO' if existente else 'MATRICULADO'


def importar_alunos(arquivo, turma: Turma | None = None) -> ResultadoImportacao:
    """Cadastra os alunos novos da planilha (e, com ``turma``, matricula os ativos nela)."""
    linhas = ler_planilha(arquivo)
    _validar(linhas)
    validas = [linha for linha in linhas if not linha.erro]
    cadastrados = {a.matricula: a for a in Aluno.objects.filter(matricula__in=[li.matricula for li in validas])}

    with transaction.atomic():
        for linha in validas:
            aluno = cadastrados.get(linha.matricula)
            if aluno is None:
                aluno = Aluno.objects.create(matricula=linha.matricula, nome=linha.nome)
                linha.situacao = 'CRIADO'
            elif not aluno.ativo:
                linha.situacao = 'INATIVO'
                linha.detalhe = f'Cadastro inativo ("{aluno.nome}"): reative em Acadêmico → Alunos.'
                if turma is not None:
                    linha.detalhe += ' Não foi matriculado.'
                continue
            elif normalizar(aluno.nome) != normalizar(linha.nome):
                linha.situacao = 'NOME_DIFERENTE'
                linha.detalhe = f'Já cadastrado como "{aluno.nome}" — o cadastro foi mantido.'
            else:
                linha.situacao = 'EXISTENTE'
            if turma is not None:
                linha.turma = _matricular(aluno, turma)

    return ResultadoImportacao(linhas=linhas, turma=turma)


def gerar_modelo_xlsx() -> bytes:
    """
    Planilha modelo. A 1ª aba (a única lida na importação) traz só o cabeçalho, com a coluna da
    matrícula em formato texto (preserva zeros à esquerda); os exemplos ficam na aba "Como preencher",
    para não virarem alunos de mentira se o modelo for enviado sem apagar nada.
    """
    import openpyxl
    from openpyxl.styles import Font

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Alunos'
    ws.append(COLUNAS_MODELO)
    for celula in ws[1]:
        celula.font = Font(bold=True)
    for (celula,) in ws.iter_rows(min_row=2, max_row=500, min_col=1, max_col=1):
        celula.number_format = '@'
    ws.column_dimensions['A'].width = 18
    ws.column_dimensions['B'].width = 42

    ajuda = wb.create_sheet('Como preencher')
    for linha in (
        ['Preencha a aba "Alunos": uma linha por aluno, a partir da linha 2.'],
        ['matricula = número de matrícula institucional; nome = nome completo.'],
        ['Matrícula que já existe no sistema não é alterada (o relatório avisa se o nome for diferente).'],
        [],
        COLUNAS_MODELO,
        *EXEMPLOS_MODELO,
    ):
        ajuda.append(linha)
    ajuda.column_dimensions['A'].width = 18
    ajuda.column_dimensions['B'].width = 42
    saida = io.BytesIO()
    wb.save(saida)
    return saida.getvalue()
