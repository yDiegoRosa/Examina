"""
diagnosticar_omr — diagnóstico da leitura óptica em fotos/digitalizações reais.

Ferramenta do teste de campo (calibração): para cada imagem, informa se o QR e os 4
marcadores foram lidos, endireita a folha e mostra o preenchimento medido em cada bolha
(A–E) com o resultado segundo os limites atuais de ``provas.services.omr``
(``MIN_FILL`` e ``AMBIG_RATIO``). **Somente leitura** — não grava nada no banco.

Uso:
    python manage.py diagnosticar_omr foto1.jpg foto2.jpg
    python manage.py diagnosticar_omr pasta/*.jpg --questoes 30          # sem QR legível
    python manage.py diagnosticar_omr foto.jpg --salvar-canonica saida/  # imagem endireitada

O número de questões vem da folha identificada pelo QR; sem QR, use ``--questoes``.
"""

from pathlib import Path

import cv2
from django.core.management.base import BaseCommand

from provas.services import omr
from provas.services.answer_sheet_layout import build_layout
from provas.services.folha_service import (
    FOLHA_ALTERNATIVAS,
    FOLHA_COLUMNS,
    FOLHA_ID_DIGITS,
    contar_questoes_objetivas,
)


class Command(BaseCommand):
    help = 'Diagnostica a leitura óptica (QR, marcadores e bolhas) de imagens reais, sem gravar no banco.'

    def add_arguments(self, parser):
        parser.add_argument('imagens', nargs='+', help='Arquivos de imagem (JPG/PNG) das folhas.')
        parser.add_argument(
            '--questoes',
            type=int,
            default=None,
            help='Nº de questões objetivas da folha, quando o QR não for lido.',
        )
        parser.add_argument(
            '--salvar-canonica',
            default=None,
            help='Pasta onde salvar a imagem endireitada (deskew) de cada folha, para inspeção visual.',
        )

    def handle(self, *args, **options):
        self.stdout.write(f'Limites atuais: MIN_FILL={omr.MIN_FILL} • AMBIG_RATIO={omr.AMBIG_RATIO}\n')
        pasta_canonica = Path(options['salvar_canonica']) if options['salvar_canonica'] else None
        if pasta_canonica:
            pasta_canonica.mkdir(parents=True, exist_ok=True)

        resumo = {'imagens': 0, 'qr': 0, 'marcadores': 0, 'ok': 0, 'branco': 0, 'ambigua': 0}
        marcadas: list[float] = []  # preenchimento da bolha escolhida (status ok)
        vazias: list[float] = []  # maior preenchimento entre as demais bolhas da questão

        for caminho in options['imagens']:
            resumo['imagens'] += 1
            self.stdout.write(self.style.MIGRATE_HEADING(f'== {caminho}'))
            try:
                imagem = omr.carregar_imagem(caminho)
            except omr.OmrError as exc:
                self.stdout.write(self.style.ERROR(f'   Imagem ilegível: {exc}'))
                continue

            payload, origem_qr = omr.ler_qr_da_folha(imagem)
            token = omr.extrair_qr_token(payload)
            folha = omr.localizar_folha(token) if token else None
            onde = ' no recorte da imagem endireitada' if origem_qr == 'recorte' else ''
            if folha is not None:
                resumo['qr'] += 1
                self.stdout.write(f'   QR: ok{onde} — {folha.aluno.nome} (versão {folha.prova_versao.identificador})')
            else:
                motivo = 'não lido' if not payload else f'lido{onde} ({payload!r}), mas não corresponde a uma folha'
                self.stdout.write(self.style.WARNING(f'   QR: {motivo}'))

            cinza = cv2.cvtColor(imagem, cv2.COLOR_BGR2GRAY) if imagem.ndim == 3 else imagem
            _, largura_marcadores = omr.detectar_marcadores_com_largura(cinza)

            try:
                canonica = omr.corrigir_perspectiva(imagem)
            except omr.OmrError as exc:
                self.stdout.write(self.style.ERROR(f'   Marcadores: {exc} (sem deskew, bolhas não lidas)'))
                continue
            resumo['marcadores'] += 1
            escala = f' (imagem reduzida a {largura_marcadores} px)' if largura_marcadores else ''
            self.stdout.write(f'   Marcadores: 4 detectados{escala} — folha endireitada')
            if pasta_canonica:
                destino = pasta_canonica / f'{Path(caminho).stem}_canonica.png'
                cv2.imwrite(str(destino), canonica)
                self.stdout.write(f'   Imagem endireitada: {destino}')

            num_questoes = contar_questoes_objetivas(folha.prova_versao) if folha else options['questoes']
            if not num_questoes:
                self.stdout.write(self.style.WARNING('   Bolhas: informe --questoes (QR não identificou a folha).'))
                continue

            geometry = build_layout(
                num_questoes=num_questoes,
                num_alternativas=FOLHA_ALTERNATIVAS,
                id_digits=FOLHA_ID_DIGITS,
                columns=FOLHA_COLUMNS,
            )
            for numero, leitura in sorted(omr.ler_respostas(canonica, geometry.answer_bubbles).items()):
                resumo[leitura.status] += 1
                ordenadas = sorted(leitura.preenchimentos.values(), reverse=True)
                if leitura.status == 'ok':
                    marcadas.append(ordenadas[0])
                    vazias.append(ordenadas[1] if len(ordenadas) > 1 else 0.0)
                bolhas = '  '.join(f'{letra} {fracao:.2f}' for letra, fracao in sorted(leitura.preenchimentos.items()))
                resultado = f'ok ({leitura.marcada})' if leitura.status == 'ok' else leitura.status
                dx, dy = leitura.deslocamento_mm
                ajuste = f'  [ajuste {dx:+.1f}, {dy:+.1f} mm]' if (dx or dy) else ''
                self.stdout.write(f'   Q{numero:02d}  {bolhas}  → {resultado}{ajuste}')

        self._resumo(resumo, marcadas, vazias)

    def _resumo(self, resumo, marcadas, vazias):
        self.stdout.write(self.style.MIGRATE_HEADING('\n== Resumo'))
        self.stdout.write(
            f'   {resumo["imagens"]} imagem(ns) • folha identificada pelo QR em {resumo["qr"]} • '
            f'marcadores ok em {resumo["marcadores"]}'
        )
        self.stdout.write(
            f'   Questões: {resumo["ok"]} lidas • {resumo["branco"]} em branco • {resumo["ambigua"]} ambígua(s)'
        )
        if marcadas:
            self.stdout.write(
                f'   Bolha marcada: menor preenchimento {min(marcadas):.2f} (limite MIN_FILL {omr.MIN_FILL}) • '
                f'maior preenchimento entre não marcadas {max(vazias):.2f}'
            )
            self.stdout.write(
                '   Calibração: MIN_FILL deve ficar entre esses dois valores — confira os casos em branco/ambíguos acima.'
            )
