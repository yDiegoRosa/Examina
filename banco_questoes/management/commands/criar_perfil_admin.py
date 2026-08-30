# banco_questoes/management/commands/criar_perfil_admin.py
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Cria o grupo "Administrador" com todas as permissões do app banco_questoes.'

    def handle(self, *args, **options):
        grupo, criado = Group.objects.get_or_create(name="Administrador")

        permissoes = Permission.objects.filter(content_type__app_label="banco_questoes")
        grupo.permissions.set(permissoes)

        self.stdout.write(
            self.style.SUCCESS(
                f'Grupo "Administrador" {"criado" if criado else "atualizado"} '
                f"com {permissoes.count()} permissão(ões)."
            )
        )
