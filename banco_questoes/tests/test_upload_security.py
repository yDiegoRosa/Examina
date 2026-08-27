"""
Testes de seguranca - CSVUploadForm
"""

import io

import openpyxl
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from banco_questoes.forms import CSVUploadForm


class CSVUploadFormTest(TestCase):
    """Testes unitarios para CSVUploadForm (extensao, tamanho, magic bytes)."""

    def _make_form(self, filename, content, content_type='text/csv'):
        file_obj = SimpleUploadedFile(filename, content, content_type=content_type)
        return CSVUploadForm(data={}, files={'arquivo': file_obj})

    def test_valid_csv_file(self):
        """Arquivo CSV bem formado deve passar na validacao."""
        csv_bytes = b'origem,questao,enunciado\nEXAMPLE,1,Enunciado de teste\n'
        form = self._make_form('questoes.csv', csv_bytes)
        self.assertTrue(form.is_valid(), msg=form.errors)

    def test_valid_xlsx_file(self):
        """Arquivo XLSX real deve passar na validacao."""
        wb = openpyxl.Workbook()
        buf = io.BytesIO()
        wb.save(buf)
        xlsx_bytes = buf.getvalue()
        form = self._make_form(
            'questoes.xlsx',
            xlsx_bytes,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        self.assertTrue(form.is_valid(), msg=form.errors)

    def test_csv_utf8_bom_is_valid(self):
        """CSV com BOM UTF-8 deve ser aceito."""
        bom_csv = b'\xef\xbb\xbforigem,questao,enunciado\nEXAMPLE,1,Enunciado BOM\n'
        form = self._make_form('bom.csv', bom_csv)
        self.assertTrue(form.is_valid(), msg=form.errors)

    def test_invalid_extension_pdf(self):
        """Arquivo .pdf deve ser rejeitado."""
        form = self._make_form('relatorio.pdf', b'%PDF-1.4 content here', content_type='application/pdf')
        self.assertFalse(form.is_valid())
        self.assertIn('arquivo', form.errors)
        # A mensagem contém "Formato inválido" — verifica que o campo foi rejeitado
        self.assertTrue(any('rmato' in err for err in form.errors['arquivo']))

    def test_invalid_extension_exe(self):
        """Arquivo .exe deve ser rejeitado."""
        form = self._make_form('malware.exe', b'MZ\x90\x00', content_type='application/octet-stream')
        self.assertFalse(form.is_valid())
        self.assertIn('arquivo', form.errors)

    def test_invalid_extension_txt(self):
        """Arquivo .txt deve ser rejeitado mesmo com conteudo CSV valido."""
        csv_bytes = b'origem,questao,enunciado\nEXAMPLE,1,Enunciado\n'
        form = self._make_form('questoes.txt', csv_bytes, content_type='text/plain')
        self.assertFalse(form.is_valid())
        self.assertIn('arquivo', form.errors)

    def test_file_too_large(self):
        """Arquivo acima do limite deve ser rejeitado."""
        large_content = b'A' * (6 * 1024 * 1024)
        with override_settings(CSV_UPLOAD_MAX_SIZE_MB=5):
            form = self._make_form('grande.csv', large_content)
            self.assertFalse(form.is_valid())
            self.assertIn('arquivo', form.errors)
            self.assertIn('muito grande', form.errors['arquivo'][0])

    def test_file_at_exact_limit_is_accepted(self):
        """Arquivo exatamente no limite deve ser aceito."""
        limit_bytes = 5 * 1024 * 1024
        header = b'origem,questao,enunciado,opcao_a,opcao_b,opcao_c,opcao_d,opcao_e,gabarito,comentarios\n'
        padding = b'x' * (limit_bytes - len(header))
        content = header + padding
        with override_settings(CSV_UPLOAD_MAX_SIZE_MB=5):
            form = self._make_form('limite.csv', content)
            self.assertTrue(form.is_valid(), msg=form.errors)

    def test_binary_file_renamed_to_csv(self):
        """Arquivo binario renomeado para .csv deve ser rejeitado."""
        binary_content = b'MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff'
        form = self._make_form('malware.csv', binary_content)
        self.assertFalse(form.is_valid())
        self.assertIn('arquivo', form.errors)

    def test_xlsx_with_wrong_magic_bytes(self):
        """Arquivo .xlsx com magic bytes incorretos deve ser rejeitado."""
        fake_xlsx = b'Este nao e um arquivo xlsx real'
        form = self._make_form('fake.xlsx', fake_xlsx, content_type='application/octet-stream')
        self.assertFalse(form.is_valid())
        self.assertIn('arquivo', form.errors)

    def test_xls_with_wrong_magic_bytes(self):
        """Arquivo .xls com magic bytes incorretos deve ser rejeitado."""
        fake_xls = b'Este nao e um arquivo xls real'
        form = self._make_form('fake.xls', fake_xls, content_type='application/octet-stream')
        self.assertFalse(form.is_valid())
        self.assertIn('arquivo', form.errors)
