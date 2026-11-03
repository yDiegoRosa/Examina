"""
Correções de 2026-11-02:

- imagem de apoio no formulário da questão: PNG/JPG/GIF/WebP de verdade, até IMPORT_IMAGEM_MAX_SIZE_MB
  (antes aceitava qualquer tamanho);
- modelo de planilha: o exemplo de C usava ``++x + x++`` (comportamento indefinido em C — o
  gabarito não era garantido); agora usa incrementos em comandos separados.
"""

import io

import openpyxl
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from banco_questoes.forms import QuestaoForm
from banco_questoes.models import Disciplina


def imagem(formato='PNG', nome='figura.png', tamanho=(40, 30)) -> SimpleUploadedFile:
    saida = io.BytesIO()
    Image.new('RGB', tamanho, 'blue').save(saida, format=formato)
    return SimpleUploadedFile(nome, saida.getvalue(), content_type=f'image/{formato.lower()}')


class ImagemDaQuestaoTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.disciplina = Disciplina.objects.create(nome='Disciplina da imagem (DIM)')

    def _form(self, arquivo):
        dados = {
            'tipo': 'DIS',
            'dificuldade': 'M',
            'disciplina': str(self.disciplina.pk),
            'enunciado': 'Enunciado com figura',
            'criterios_correcao': 'Critério.',
            'ativo': 'on',
        }
        return QuestaoForm(data=dados, files={'imagem': arquivo} if arquivo else {})

    def test_png_jpg_gif_e_webp_sao_aceitos(self):
        for formato, nome in (('PNG', 'a.png'), ('JPEG', 'b.jpg'), ('GIF', 'c.gif'), ('WEBP', 'd.webp')):
            with self.subTest(formato=formato):
                form = self._form(imagem(formato, nome))
                self.assertTrue(form.is_valid(), form.errors)

    def test_sem_imagem_continua_valido(self):
        self.assertTrue(self._form(None).is_valid())

    @override_settings(IMPORT_IMAGEM_MAX_SIZE_MB=0.001)
    def test_imagem_acima_do_limite_e_recusada(self):
        form = self._form(imagem(tamanho=(400, 400)))
        self.assertFalse(form.is_valid())
        self.assertIn('o limite é 0.001 MB', form.errors['imagem'][0])

    def test_arquivo_que_nao_e_imagem_e_recusado(self):
        falso = SimpleUploadedFile('virus.png', b'isto nao e uma imagem', content_type='image/png')
        form = self._form(falso)
        self.assertFalse(form.is_valid())
        self.assertIn('imagem', form.errors)

    def test_formato_fora_da_web_e_recusado(self):
        form = self._form(imagem('BMP', 'figura.bmp'))
        self.assertFalse(form.is_valid())
        self.assertIn('use PNG, JPG, GIF ou WebP', form.errors['imagem'][0])

    def test_tela_mostra_o_limite(self):
        admin = get_user_model().objects.create_superuser('admin_img', 'a@exemplo.com', 'x')
        self.client.force_login(admin)
        tela = self.client.get(reverse('banco_questoes:questao_create'))
        self.assertContains(tela, 'PNG, JPG, GIF ou WebP, até 5 MB')


class ExemploDeCNoModeloTest(TestCase):
    """Acervo vazio → o modelo baixado traz as questões de exemplo (inclusive a de C)."""

    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser('admin_modelo', 'm@exemplo.com', 'x'))

    def test_csv(self):
        csv = self.client.get(reverse('banco_questoes:download_modelo_csv')).content.decode('utf-8-sig')
        self.assertIn('int y = x++;', csv)
        self.assertIn('7 5 7', csv)
        self.assertNotIn('++x + x++', csv)

    def test_excel(self):
        resposta = self.client.get(reverse('banco_questoes:download_modelo_xlsx'))
        ws = openpyxl.load_workbook(io.BytesIO(resposta.content)).active
        textos = [str(c.value) for linha in ws.iter_rows() for c in linha if c.value]
        self.assertTrue(any('int z = ++x;' in t for t in textos))
        self.assertFalse(any('++x + x++' in t for t in textos))
