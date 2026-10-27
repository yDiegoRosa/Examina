"""Aviso por e-mail ao administrador (usado pela rotina de backup quando algo falha)."""

import io
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

from django.core import mail
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, override_settings

ADMINS = [('Administrador', 'admin@exemplo.edu.br')]


@override_settings(ADMINS=ADMINS, SERVER_EMAIL='examina@exemplo.edu.br', EMAIL_SUBJECT_PREFIX='[Examina] ')
class AvisarAdministradorTest(SimpleTestCase):
    def test_envia_o_aviso_aos_administradores(self):
        saida = io.StringIO()
        call_command(
            'avisar_administrador', assunto='Falha no backup: backup do banco', mensagem='detalhes', stdout=saida
        )
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ['admin@exemplo.edu.br'])
        self.assertEqual(email.subject, '[Examina] Falha no backup: backup do banco')
        self.assertEqual(email.body, 'detalhes')
        self.assertEqual(email.from_email, 'examina@exemplo.edu.br')
        self.assertIn('1 administrador', saida.getvalue())

    def test_le_o_texto_da_entrada_padrao(self):
        with mock.patch('sys.stdin', io.StringIO('Últimas linhas do log\n')):
            call_command('avisar_administrador', assunto='Falha', stdout=io.StringIO())
        self.assertEqual(mail.outbox[0].body, 'Últimas linhas do log')

    @override_settings(ADMINS=[])
    def test_sem_administrador_configurado_e_erro(self):
        with self.assertRaisesMessage(CommandError, 'defina ADMINS'):
            call_command('avisar_administrador', assunto='Falha', mensagem='x')

    def test_falha_do_servidor_de_email_vira_erro_do_comando(self):
        alvo = 'banco_questoes.management.commands.avisar_administrador.mail_admins'
        with (
            mock.patch(alvo, side_effect=ConnectionRefusedError('SMTP fora do ar')),
            self.assertRaisesMessage(CommandError, 'SMTP fora do ar'),
        ):
            call_command('avisar_administrador', assunto='Falha', mensagem='x')


class RemetenteDosEmailsTest(SimpleTestCase):
    """O .env de produção é gerado com "EMAIL_REMETENTE=" vazio: o remetente vira a conta do SMTP."""

    def _remetentes(self, **variaveis):
        codigo = (
            'import django; django.setup(); from django.conf import settings; '
            'print(settings.DEFAULT_FROM_EMAIL); print(settings.SERVER_EMAIL)'
        )
        ambiente = {**os.environ, 'DJANGO_SETTINGS_MODULE': 'Examina.settings', **variaveis}
        saida = subprocess.run(  # noqa: S603
            [sys.executable, '-c', codigo],
            cwd=Path(__file__).resolve().parents[2],
            env=ambiente,
            capture_output=True,
            text=True,
            check=True,
        )
        return saida.stdout.split()

    def test_remetente_vazio_usa_a_conta_do_smtp(self):
        remetentes = self._remetentes(EMAIL_REMETENTE='', EMAIL_HOST_USER='examina.teste@gmail.com')
        self.assertEqual(remetentes, ['examina.teste@gmail.com', 'examina.teste@gmail.com'])

    def test_remetente_preenchido_e_mantido(self):
        remetentes = self._remetentes(EMAIL_REMETENTE='avisos@exemplo.edu.br', EMAIL_HOST_USER='conta@gmail.com')
        self.assertEqual(remetentes, ['avisos@exemplo.edu.br', 'avisos@exemplo.edu.br'])
