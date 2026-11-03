"""Aplica os prazos de guarda de dados pessoais (LGPD) — ver ``Examina.retencao``."""

from django.core.management.base import BaseCommand

from Examina.retencao import aplicar_retencao, prazos


class Command(BaseCommand):
    help = (
        'Apaga fotos de cartões antigas, anonimiza alunos sem matrícula ativa há muito tempo e apaga '
        'tentativas de login e sessões vencidas, conforme os prazos RETENCAO_* do .env.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--simular', action='store_true', help='Só mostra quantos itens seriam afetados.')

    def handle(self, *args, **options):
        simular = options['simular']
        resultado = aplicar_retencao(simular=simular)
        p = prazos()
        prefixo = '[simulação] ' if simular else ''
        verbo = 'seriam' if simular else 'foram'
        linhas = [
            f'Fotos de cartões ({p["fotos"]["texto"]} após a aplicação): {resultado.fotos} {verbo} apagada(s).',
            f'Alunos sem matrícula ativa há {p["alunos"]["texto"]}: {resultado.alunos} {verbo} anonimizado(s).',
            f'Tentativas de login com mais de {p["tentativas"]["texto"]}: {resultado.tentativas} {verbo} apagada(s).',
            f'Sessões vencidas: {resultado.sessoes} {verbo} apagada(s).',
        ]
        for linha in linhas:
            self.stdout.write(prefixo + linha)
