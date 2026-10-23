"""Aviso por e-mail ao administrador (usado pela rotina de backup quando algo falha)."""

import io
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
