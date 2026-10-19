"""
Envia um aviso por e-mail aos administradores do sistema (``ADMINS`` no .env).

Usado pela rotina de backup (``scripts/backup_completo.sh``) quando alguma etapa falha::

    echo "detalhes" | python manage.py avisar_administrador --assunto "Falha no backup"

Para conferir a configuração de e-mail: ``python manage.py sendtestemail --admins``.
"""

import sys

from django.conf import settings
from django.core.mail import mail_admins
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Envia um aviso por e-mail aos administradores (ADMINS). O texto vem de --mensagem ou da entrada padrão.'

    def add_arguments(self, parser):
        parser.add_argument('--assunto', required=True, help='Assunto do e-mail (recebe o prefixo [Examina]).')
        parser.add_argument('--mensagem', help='Texto do aviso; sem ela, o texto é lido da entrada padrão.')

    def handle(self, *args, assunto, mensagem, **options):
        if not settings.ADMINS:
            raise CommandError(
                'Nenhum administrador para avisar: defina ADMINS no .env (e-mails separados por vírgula).'
            )
        texto = mensagem if mensagem is not None else sys.stdin.read()
        try:
            mail_admins(assunto, texto.strip() or '(sem detalhes)', fail_silently=False)
        except OSError as exc:  # SMTP fora do ar, credenciais erradas, timeout…
            raise CommandError(f'Falha ao enviar o e-mail: {exc}') from exc
        self.stdout.write(self.style.SUCCESS(f'Aviso enviado para {len(settings.ADMINS)} administrador(es).'))
