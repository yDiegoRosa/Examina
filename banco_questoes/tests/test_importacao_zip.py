"""Importação de questões com imagens: .zip com a planilha e a pasta de imagens (coluna ``imagem``)."""

import io
import shutil
import tempfile
import zipfile
from pathlib import Path
from unittest import mock

import openpyxl
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from banco_questoes.forms import CSVUploadForm
from banco_questoes.importacao_zip import COLUNAS_COM_IMAGEM, gerar_exemplo_zip
from banco_questoes.importer import QuestaoImportService
from banco_questoes.models import Questao
from banco_questoes.parser import CSVParseError


def png(cor='red', formato='PNG') -> bytes:
    saida = io.BytesIO()
    Image.new('RGB', (40, 30), cor).save(saida, format=formato)
    return saida.getvalue()


def linha(enunciado, imagem='', tipo='OBJ'):
    alternativas = ['Um', 'Dois', '', '', ''] if tipo == 'OBJ' else [''] * 5
    gabarito = 'A' if tipo == 'OBJ' else ''
    return [
        'ENADE teste',
        '1',
        tipo,
        'Estrutura de Dados (ESD)',
        'Tópico ZIP',
        'M',
        enunciado,
        *alternativas,
        gabarito,
        '',
        '',
        imagem,
    ]


def planilha(*linhas) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(COLUNAS_COM_IMAGEM)
    for item in linhas:
        ws.append(item)
    saida = io.BytesIO()
    wb.save(saida)
    return saida.getvalue()


def pacote(arquivos: dict[str, bytes]) -> bytes:
    saida = io.BytesIO()
    with zipfile.ZipFile(saida, 'w', zipfile.ZIP_DEFLATED) as zf:
        for nome, conteudo in arquivos.items():
            zf.writestr(nome, conteudo)
    return saida.getvalue()


class ComMidiaTemporaria(TestCase):
    def setUp(self):
        self.midia = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.midia, True)
        sobrescrita = override_settings(MEDIA_ROOT=self.midia)
        sobrescrita.enable()
        self.addCleanup(sobrescrita.disable)

    def importar(self, conteudo: bytes, nome='questoes.zip'):
        return QuestaoImportService.import_file(conteudo, filename=nome)

    def erros(self, resultado):
        return [e for item in resultado.linhas if item.status != 'SUCESSO' for e in item.erros]


class ImportacaoZipTest(ComMidiaTemporaria):
    def test_importa_questoes_com_e_sem_imagem(self):
        figura = png()
        resultado = self.importar(
            pacote(
                {
                    'questoes.xlsx': planilha(
                        linha('Questão com figura', 'imagens/q1.png'),
                        linha('Discursiva sem figura', tipo='DIS'),
                    ),
                    'imagens/q1.png': figura,
                }
            )
        )
        self.assertEqual((resultado.total_sucesso, resultado.total_rejeitadas, resultado.total_com_imagem), (2, 0, 1))
        com_figura = Questao.objects.get(enunciado='Questão com figura')
        self.assertTrue(com_figura.imagem.name.startswith('questoes/imagens/q1'))
        self.assertEqual(Path(self.midia, com_figura.imagem.name).read_bytes(), figura)
        self.assertFalse(Questao.objects.get(enunciado='Discursiva sem figura').imagem)
        self.assertIn('q1', next(i.imagem for i in resultado.linhas if i.questao_id == com_figura.pk))

    def test_imagem_que_falta_recusa_so_aquela_linha(self):
        resultado = self.importar(
            pacote(
                {
                    'questoes.xlsx': planilha(linha('Sem o arquivo', 'imagens/nao_existe.png'), linha('Normal')),
                }
            )
        )
        self.assertEqual((resultado.total_sucesso, resultado.total_rejeitadas), (1, 1))
        self.assertIn('Linha 2: a imagem "imagens/nao_existe.png" não está no ZIP', self.erros(resultado)[0])
        self.assertFalse(Questao.objects.filter(enunciado='Sem o arquivo').exists())

    def test_encontra_a_imagem_pelo_nome_sem_diferenciar_maiusculas(self):
        resultado = self.importar(
            pacote(
                {
                    'questoes.xlsx': planilha(
                        linha('Só o nome do arquivo', 'Q1.PNG'),
                        linha('Barra invertida e ./', '.\\imagens\\q2.png'),
                    ),
                    'imagens/q1.png': png('blue'),
                    'imagens/q2.png': png('green'),
                }
            )
        )
        self.assertEqual((resultado.total_sucesso, resultado.total_com_imagem), (2, 2), self.erros(resultado))

    def test_nome_repetido_em_pastas_diferentes_exige_o_caminho(self):
        resultado = self.importar(
            pacote(
                {
                    'questoes.xlsx': planilha(linha('Ambígua', 'q1.png')),
                    'a/q1.png': png(),
                    'b/q1.png': png(),
                }
            )
        )
        self.assertEqual(resultado.total_rejeitadas, 1)

    def test_arquivo_que_nao_e_imagem_e_recusado(self):
        resultado = self.importar(
            pacote(
                {
                    'questoes.xlsx': planilha(
                        linha('Falsa', 'imagens/falsa.png'),
                        linha('GIF', 'imagens/animada.png'),
                        linha('Texto', 'imagens/nota.txt'),
                    ),
                    'imagens/falsa.png': b'isto nao e uma imagem',
                    'imagens/animada.png': png(formato='GIF'),
                    'imagens/nota.txt': b'texto',
                }
            )
        )
        erros = self.erros(resultado)
        self.assertEqual(resultado.total_sucesso, 0)
        self.assertIn('não é uma imagem válida', erros[0])
        self.assertIn('é GIF; use PNG ou JPG', erros[1])
        self.assertIn('não é uma imagem aceita', erros[2])

    @override_settings(IMPORT_IMAGEM_MAX_SIZE_MB=0.0001)
    def test_imagem_acima_do_limite_e_recusada(self):
        resultado = self.importar(
            pacote({'questoes.xlsx': planilha(linha('Grande', 'g.png')), 'g.png': png() + b'\0' * 500})
        )
        self.assertIn('O limite é', self.erros(resultado)[0])

    def test_planilha_csv_dentro_do_zip(self):
        csv = 'disciplina,enunciado,opcao_a,opcao_b,gabarito,imagem\nESD,CSV com figura,Sim,Não,A,figuras/x.jpg\n'.encode()
        resultado = self.importar(pacote({'dados.csv': csv, 'figuras/x.jpg': png(formato='JPEG')}))
        self.assertEqual((resultado.total_sucesso, resultado.total_com_imagem), (1, 1), self.erros(resultado))

    def test_arquivo_com_ponto_ponto_no_caminho_e_ignorado(self):
        # "Zip slip": um caminho que tenta sair da pasta nunca é usado — nem para gravar.
        resultado = self.importar(
            pacote({'questoes.xlsx': planilha(linha('Zip slip', '../../evil.png')), '../../evil.png': png()})
        )
        self.assertEqual((resultado.total_sucesso, resultado.total_rejeitadas), (0, 1))
        self.assertIn('não está no ZIP', self.erros(resultado)[0])
        self.assertFalse(Path(self.midia).parent.joinpath('evil.png').exists())
        self.assertFalse(any(Path(self.midia).rglob('*.png')))

    def test_imagem_e_gravada_com_nome_limpo(self):
        resultado = self.importar(
            pacote(
                {
                    'questoes.xlsx': planilha(linha('Nome sujo', 'img/Minha figura (1).png')),
                    'img/Minha figura (1).png': png(),
                }
            )
        )
        self.assertEqual(resultado.total_com_imagem, 1, self.erros(resultado))
        nome = Questao.objects.get(enunciado='Nome sujo').imagem.name
        self.assertTrue(nome.startswith('questoes/imagens/Minha_figura_1'), nome)

    def test_imagem_em_planilha_solta_e_recusada_com_explicacao(self):
        resultado = self.importar(planilha(linha('Sem zip', 'imagens/q1.png'), linha('Sem imagem')), 'q.xlsx')
        self.assertEqual((resultado.total_sucesso, resultado.total_rejeitadas), (1, 1))
        self.assertIn('só funciona na importação por .zip', self.erros(resultado)[0])

    def test_problemas_do_pacote_inteiro(self):
        casos = {
            'sem planilha': (pacote({'imagens/q1.png': png()}), 'não tem planilha'),
            'duas planilhas': (pacote({'a.xlsx': planilha(), 'b.csv': b'enunciado\nx\n'}), 'mais de uma planilha'),
            'corrompido': (b'PK\x03\x04 lixo', 'corrompido'),
        }
        for caso, (conteudo, mensagem) in casos.items():
            with self.subTest(caso), self.assertRaisesMessage(CSVParseError, mensagem):
                self.importar(conteudo)

    def test_lixo_do_macos_e_ignorado(self):
        conteudo = pacote(
            {'questoes.xlsx': planilha(linha('Mac')), '__MACOSX/._questoes.xlsx': b'x', '.DS_Store': b'x'}
        )
        self.assertEqual(self.importar(conteudo).total_sucesso, 1)

    def test_zip_com_arquivos_demais_e_recusado(self):
        conteudo = pacote({'questoes.xlsx': planilha(linha('X')), 'a.png': png(), 'b.png': png()})
        with (
            mock.patch('banco_questoes.importacao_zip.MAX_ARQUIVOS_NO_ZIP', 2),
            self.assertRaisesMessage(CSVParseError, 'arquivos demais'),
        ):
            self.importar(conteudo)

    def test_exemplo_para_download_importa_sem_erros(self):
        resultado = self.importar(gerar_exemplo_zip())
        self.assertEqual((resultado.total_sucesso, resultado.total_com_imagem), (2, 1), self.erros(resultado))
        objetiva = Questao.objects.get(tipo='OBJ', origem__startswith='Exemplo de importação')
        self.assertEqual(objetiva.alternativas.get(correta=True).letra, 'C')
        with Image.open(Path(self.midia, objetiva.imagem.name)) as figura:
            self.assertEqual(figura.format, 'PNG')


class FormularioZipTest(TestCase):
    def form(self, nome, conteudo):
        return CSVUploadForm(data={}, files={'arquivo': SimpleUploadedFile(nome, conteudo, 'application/zip')})

    def test_zip_valido_e_aceito_com_limite_proprio(self):
        conteudo = pacote({'questoes.xlsx': planilha()}) + b'\0' * (6 * 1024 * 1024)  # > limite do CSV (5 MB)
        self.assertTrue(self.form('q.zip', conteudo).is_valid())

    def test_zip_com_assinatura_errada_e_recusado(self):
        form = self.form('q.zip', b'MZ\x90\x00 executavel')
        self.assertFalse(form.is_valid())
        self.assertIn('não corresponde à extensão', form.errors['arquivo'][0])

    @override_settings(IMPORT_ZIP_MAX_SIZE_MB=1)
    def test_zip_acima_do_limite_e_recusado(self):
        form = self.form('q.zip', b'PK\x03\x04' + b'\0' * (2 * 1024 * 1024))
        self.assertFalse(form.is_valid())
        self.assertIn('O limite é 1 MB', form.errors['arquivo'][0])


class TelaImportacaoZipTest(ComMidiaTemporaria):
    def setUp(self):
        super().setUp()
        self.client.force_login(get_user_model().objects.create_superuser('admin_zip', 'a@exemplo.com', 'x'))

    def test_campo_de_arquivo_tem_o_nome_que_o_formulario_espera(self):
        # Regressão: a tela enviava "csv_file" e o formulário espera "arquivo" (toda importação falhava).
        tela = self.client.get(reverse('banco_questoes:questao_import'))
        self.assertContains(tela, 'name="arquivo"')
        self.assertContains(tela, reverse('banco_questoes:download_exemplo_zip'))

    def test_envio_de_zip_pela_tela(self):
        conteudo = pacote({'questoes.xlsx': planilha(linha('Pela tela', 'imagens/q.png')), 'imagens/q.png': png()})
        resposta = self.client.post(
            reverse('banco_questoes:questao_import'),
            {'arquivo': SimpleUploadedFile('questoes.zip', conteudo, 'application/zip')},
        )
        self.assertContains(resposta, '1 com imagem')
        self.assertContains(resposta, 'Com imagem: q')
        self.assertTrue(Questao.objects.get(enunciado='Pela tela').imagem)

    def test_download_do_exemplo(self):
        resposta = self.client.get(reverse('banco_questoes:download_exemplo_zip'))
        self.assertEqual(resposta['Content-Type'], 'application/zip')
        nomes = zipfile.ZipFile(io.BytesIO(resposta.content)).namelist()
        self.assertEqual(sorted(nomes), ['LEIA-ME.txt', 'imagens/exemplo_grafo.png', 'questoes.xlsx'])
