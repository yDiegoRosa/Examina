from django.apps import AppConfig


class BancoQuestoesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'banco_questoes'

    def ready(self):
        from django.contrib.auth.signals import user_logged_in, user_login_failed

        from .seguranca_login import ao_entrar, ao_falhar_login

        # Limite de tentativas de login (ver seguranca_login).
        user_login_failed.connect(ao_falhar_login, dispatch_uid='banco_questoes_login_falhou')
        user_logged_in.connect(ao_entrar, dispatch_uid='banco_questoes_login_ok')
