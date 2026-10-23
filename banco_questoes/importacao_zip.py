"""
Importação de questões com imagens: um .zip com a planilha (.xlsx ou .csv) e as imagens.

Cada linha da planilha aponta a sua imagem pela coluna ``imagem`` (ex.: ``imagens/q10.png``);
a ordem dos arquivos no ZIP não importa. Ver ``docs/importacao-questoes.md``.

Segurança: o ZIP nunca é extraído para o disco. As imagens são lidas da memória, conferidas
(PNG/JPEG de verdade, tamanho máximo) e gravadas pelo storage do Django com um nome limpo — o
caminho escrito dentro do ZIP não é usado para gravar nada (sem "zip slip"). Há limites de
quantidade de arquivos e de tamanho descompactado contra ZIPs abusivos.
"""

from __future__ import annotations

import io
import posixpath
import zipfile
from dataclasses import dataclass, field

from django.conf import settings
from django.utils.text import get_valid_filename

from .parser import CSVParseError

EXTENSOES_PLANILHA = ('.xlsx', '.csv')
EXTENSOES_IMAGEM = ('.png', '.jpg', '.jpeg')
FORMATOS_IMAGEM = {'PNG', 'JPEG'}
MAX_ARQUIVOS_NO_ZIP = 1000
MAX_TOTAL_DESCOMPACTADO_MB = 500
MAX_TAXA_COMPRESSAO = 200  # descompactado / compactado — acima disso é "zip bomb"


def eh_zip(nome: str) -> bool:
    return nome.lower().endswith('.zip')


def _limite_imagem_bytes() -> int:
    return int(float(getattr(settings, 'IMPORT_IMAGEM_MAX_SIZE_MB', 5)) * 1024 * 1024)


def _normalizar(caminho: str) -> str:
    """Caminho como escrito na planilha → forma canônica dentro do ZIP (barras '/', sem './')."""
    caminho = (caminho or '').strip().replace('\\', '/')
    while caminho.startswith(('./', '/')):
        caminho = caminho[2:] if caminho.startswith('./') else caminho[1:]
    return posixpath.normpath(caminho) if caminho else ''


def _ignorado(nome: str) -> bool:
    """
    Lixo comum de ZIP feito no macOS/Windows (pastas __MACOSX, arquivos ocultos, Thumbs.db) e
    caminhos com ``..`` (que tentariam sair da pasta) — tudo isso é ignorado.
    """
    partes = nome.split('/')
    return any(p.startswith('.') or p == '__MACOSX' for p in partes) or partes[-1].lower() == 'thumbs.db'


@dataclass
class ImagemDoPacote:
    nome_arquivo: str  # nome limpo para gravar (só o nome, sem pastas)
    conteudo: bytes


@dataclass
class PacoteImportacao:
    """Conteúdo de um ZIP de importação: a planilha e o índice das imagens."""

    nome_planilha: str
    planilha: bytes
    _zip: zipfile.ZipFile
    _por_caminho: dict[str, zipfile.ZipInfo] = field(default_factory=dict)

    @classmethod
    def abrir(cls, arquivo) -> PacoteImportacao:
        """Abre e confere o ZIP. Erros do pacote inteiro viram ``CSVParseError``."""
        dados = arquivo.read() if hasattr(arquivo, 'read') else bytes(arquivo)
        if hasattr(arquivo, 'seek'):
            arquivo.seek(0)
        try:
            zf = zipfile.ZipFile(io.BytesIO(dados))
        except zipfile.BadZipFile as exc:
            raise CSVParseError('O arquivo .zip está corrompido ou não é um ZIP.') from exc

        membros = [i for i in zf.infolist() if not i.is_dir() and not _ignorado(i.filename)]
        if len(membros) > MAX_ARQUIVOS_NO_ZIP:
            raise CSVParseError(f'O ZIP tem arquivos demais ({len(membros)}). O limite é {MAX_ARQUIVOS_NO_ZIP}.')
        total = sum(i.file_size for i in membros)
        if total > MAX_TOTAL_DESCOMPACTADO_MB * 1024 * 1024:
            raise CSVParseError(f'O conteúdo do ZIP descompactado passa de {MAX_TOTAL_DESCOMPACTADO_MB} MB.')
        for info in membros:
            if info.compress_size and info.file_size / info.compress_size > MAX_TAXA_COMPRESSAO:
                raise CSVParseError(f'O arquivo "{info.filename}" do ZIP tem compressão suspeita e foi recusado.')

        planilhas = [i for i in membros if i.filename.lower().endswith(EXTENSOES_PLANILHA)]
        if not planilhas:
            raise CSVParseError('O ZIP não tem planilha: inclua um arquivo .xlsx ou .csv com as questões.')
        if len(planilhas) > 1:
            nomes = ', '.join(i.filename for i in planilhas)
            raise CSVParseError(f'O ZIP tem mais de uma planilha ({nomes}). Deixe só uma.')

        planilha = planilhas[0]
        pacote = cls(nome_planilha=planilha.filename, planilha=zf.read(planilha), _zip=zf)
        pacote._por_caminho = {_normalizar(i.filename): i for i in membros if i is not planilha}
        return pacote

    def _localizar(self, caminho: str) -> zipfile.ZipInfo | None:
        """Caminho exato; senão o mesmo ignorando maiúsculas; senão só o nome do arquivo, se for único."""
        alvo = _normalizar(caminho)
        if alvo in self._por_caminho:
            return self._por_caminho[alvo]
        iguais = [i for c, i in self._por_caminho.items() if c.lower() == alvo.lower()]
        if len(iguais) == 1:
            return iguais[0]
        nome = posixpath.basename(alvo).lower()
        mesmo_nome = [i for c, i in self._por_caminho.items() if posixpath.basename(c).lower() == nome]
        return mesmo_nome[0] if len(mesmo_nome) == 1 else None

    def imagem(self, caminho: str) -> ImagemDoPacote:
        """Imagem citada na coluna ``imagem``. ``ValueError`` com a mensagem para o relatório."""
        info = self._localizar(caminho)
        if info is None:
            raise ValueError(f'a imagem "{caminho}" não está no ZIP. Confira o nome do arquivo e a pasta.')
        if not info.filename.lower().endswith(EXTENSOES_IMAGEM):
            raise ValueError(f'"{caminho}" não é uma imagem aceita. Use PNG ou JPG.')
        limite = _limite_imagem_bytes()
        if info.file_size > limite:
            raise ValueError(
                f'a imagem "{caminho}" tem {info.file_size / 1024 / 1024:.1f} MB. O limite é {limite / 1024 / 1024:g} MB.'
            )
        conteudo = self._zip.read(info)
        validar_imagem(conteudo, caminho)
        nome = get_valid_filename(posixpath.basename(info.filename)) or 'imagem.png'
        return ImagemDoPacote(nome_arquivo=nome, conteudo=conteudo)


COLUNAS_COM_IMAGEM = [
    'origem', 'questao', 'tipo', 'disciplina', 'topico', 'dificuldade', 'enunciado',
    'opcao_a', 'opcao_b', 'opcao_c', 'opcao_d', 'opcao_e', 'gabarito', 'tags', 'comentarios', 'imagem',
]  # fmt: skip

LEIA_ME_EXEMPLO = """Exemplo de importação de questões com imagens — Examina

1. A planilha (questoes.xlsx ou um .csv) tem as 15 colunas do modelo de sempre e mais a
   coluna "imagem".
2. Em "imagem", escreva o caminho do arquivo dentro do ZIP (ex.: imagens/exemplo_grafo.png).
   Deixe vazia nas questões sem imagem. A ordem dos arquivos na pasta não importa.
3. Imagens: PNG ou JPG, uma por questão, de preferência só a figura recortada.
4. Compacte a planilha e a pasta de imagens num .zip e envie em Questões > Importar.
"""


def gerar_exemplo_zip() -> bytes:
    """ZIP de exemplo (planilha com a coluna ``imagem`` + uma imagem), coerente com o gabarito."""
    from openpyxl import Workbook
    from PIL import Image, ImageDraw, ImageFont

    # Grafo com 4 vértices e 5 arestas: A e C têm grau 3 → resposta "2" (letra C).
    figura = Image.new('RGB', (480, 320), 'white')
    desenho = ImageDraw.Draw(figura)
    fonte = ImageFont.load_default(size=28)
    vertices = {'A': (100, 70), 'B': (380, 70), 'C': (380, 250), 'D': (100, 250)}
    for a, b in (('A', 'B'), ('B', 'C'), ('C', 'D'), ('D', 'A'), ('A', 'C')):
        desenho.line([vertices[a], vertices[b]], fill='#1f2937', width=4)
    for nome, (x, y) in vertices.items():
        desenho.ellipse([x - 30, y - 30, x + 30, y + 30], fill='#e6f1fb', outline='#185fa5', width=4)
        desenho.text((x, y), nome, fill='#0c447c', font=fonte, anchor='mm')
    imagem = io.BytesIO()
    figura.save(imagem, format='PNG')

    wb = Workbook()
    ws = wb.active
    ws.title = 'Questões'
    ws.append(COLUNAS_COM_IMAGEM)
    disciplina, topico = 'Estrutura de Dados (ESD)', 'Grafos: Representação e Algoritmos de Busca'
    origem = 'Exemplo de importação (pode apagar)'
    ws.append(
        [origem, '1', 'OBJ', disciplina, topico, 'F', 'Observe o grafo da figura. Quantos vértices têm grau 3?',
         '0', '1', '2', '3', '4', 'C', 'exemplo; importacao-zip',
         'A e C ligam-se a três vértices cada.', 'imagens/exemplo_grafo.png']
    )  # fmt: skip
    ws.append(
        [origem, '2', 'DIS', disciplina, topico, 'M',
         'Explique a diferença entre a busca em largura e a busca em profundidade em um grafo.',
         '', '', '', '', '', '', 'exemplo; importacao-zip',
         'Espera-se: largura usa fila e visita por níveis; profundidade usa pilha/recursão.', '']
    )  # fmt: skip
    planilha = io.BytesIO()
    wb.save(planilha)

    saida = io.BytesIO()
    with zipfile.ZipFile(saida, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('questoes.xlsx', planilha.getvalue())
        zf.writestr('imagens/exemplo_grafo.png', imagem.getvalue())
        zf.writestr('LEIA-ME.txt', LEIA_ME_EXEMPLO)
    return saida.getvalue()


def validar_imagem(conteudo: bytes, caminho: str) -> None:
    """Confere se os bytes são mesmo um PNG ou JPEG legível (não só a extensão)."""
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(conteudo)) as img:
            formato = img.format
            img.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        raise ValueError(f'"{caminho}" não é uma imagem válida (arquivo corrompido ou de outro tipo).') from exc
    if formato not in FORMATOS_IMAGEM:
        raise ValueError(f'"{caminho}" é {formato}; use PNG ou JPG.')
