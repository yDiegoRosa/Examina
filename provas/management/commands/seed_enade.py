"""
seed_enade — abastece o acervo do Componente Específico do ENADE (5 áreas).

Cria/garante estoque suficiente para gerar um Simulado ENADE completo
(27 objetivas + 3 discursivas) distribuído nas 5 grandes áreas. Para cada área,
seleciona os tópicos que o preset priorizaria (os mais abastecidos de objetivas)
e os abastece com objetivas (Fácil/Médio/Difícil, com alternativas A–E) e
discursivas. Idempotente: reexecuções não duplicam questões.

Uso:
    python manage.py seed_enade
    python manage.py seed_enade --limpar   # remove antes o acervo do simulado
"""

import logging

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count, Q

from banco_questoes.models import Alternativa, Disciplina, Questao, Topico
from provas.presets import ENADE_AREAS, ENADE_MAX_TOPICOS_POR_AREA

logger = logging.getLogger(__name__)

ORIGEM = 'ENADE ADS (simulado)'
OBJ_POR_DIFICULDADE = 3
DIS_POR_TOPICO = 3
LETRAS = ('A', 'B', 'C', 'D', 'E')
DIFICULDADES = (
    Questao.Dificuldade.FACIL,
    Questao.Dificuldade.MEDIO,
    Questao.Dificuldade.DIFICIL,
)


class Command(BaseCommand):
    help = 'Popula o acervo do Componente Específico do ENADE (5 áreas) para testes. Idempotente.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--limpar',
            action='store_true',
            help='Remove o acervo do simulado (origem "ENADE ADS (simulado)") antes de recriar.',
        )

    def handle(self, *args, **options):
        autor = get_user_model().objects.filter(is_superuser=True).first()

        self.stdout.write(self.style.NOTICE('Abastecendo acervo do Simulado ENADE (Componente Específico)...'))

        with transaction.atomic():
            if options.get('limpar'):
                removidas = Questao.objects.filter(origem=ORIGEM).count()
                Questao.objects.filter(origem=ORIGEM).delete()
                self.stdout.write(self.style.WARNING(f'Removidas {removidas} questões do simulado anterior.'))

            criados_obj = 0
            criados_dis = 0
            criados_top = 0
            usados_disc: set[int] = set()
            resumo = []

            for area, padroes in ENADE_AREAS.items():
                filtro = Q()
                for padrao in padroes:
                    filtro |= Q(nome__icontains=padrao)

                discs = [d for d in Disciplina.objects.filter(filtro, ativo=True) if d.pk not in usados_disc]
                usados_disc.update(d.pk for d in discs)
                if not discs:
                    self.stdout.write(self.style.WARNING(f'  [{area}] nenhuma disciplina encontrada — pulada.'))
                    continue

                topicos, novos = self._selecionar_topicos(area, discs)
                criados_top += novos

                for topico in topicos:
                    co, cd = self._abastecer_topico(topico, autor)
                    criados_obj += co
                    criados_dis += cd

                resumo.append((area, len(topicos)))

        for area, n in resumo:
            self.stdout.write(f'  [{area}] {n} tópicos abastecidos.')

        self.stdout.write(
            self.style.SUCCESS(
                f'Concluído! {criados_obj} objetivas + {criados_dis} discursivas criadas '
                f'({criados_top} tópicos novos). Acervo total: '
                f'{Questao.objects.filter(ativo=True).count()} questões ativas.'
            )
        )

    def _selecionar_topicos(self, area, discs):
        """
        Seleciona os tópicos a abastecer, na MESMA ordem usada pelo preset
        (mais objetivas primeiro), completando com tópicos novos se necessário.

        Returns:
            (lista_de_topicos, quantidade_de_topicos_criados)
        """
        obj_having = list(
            Topico.objects.filter(disciplina__in=discs, ativo=True)
            .annotate(
                n_obj=Count('questoes', filter=Q(questoes__ativo=True, questoes__tipo=Questao.Tipo.OBJETIVA)),
            )
            .filter(n_obj__gt=0)
            .order_by('-n_obj', 'nome')
        )[:ENADE_MAX_TOPICOS_POR_AREA]

        selecionados = list(obj_having)
        ja = {t.pk for t in selecionados}
        novos = 0

        faltam = ENADE_MAX_TOPICOS_POR_AREA - len(selecionados)
        if faltam > 0:
            # Aproveita tópicos existentes sem objetivas antes de criar novos.
            sem_obj = list(
                Topico.objects.filter(disciplina__in=discs, ativo=True)
                .exclude(pk__in=ja)
                .order_by('disciplina__nome', 'nome')[:faltam]
            )
            for topico in sem_obj:
                selecionados.append(topico)
                ja.add(topico.pk)
                faltam -= 1

        base = discs[0]
        indice = 1
        while faltam > 0:
            topico, criado = Topico.objects.get_or_create(
                disciplina=base,
                nome=f'{area} — Tópico ENADE {indice}',
                defaults={'descricao': f'Tópico gerado para o Simulado ENADE ({area}).', 'ativo': True},
            )
            if topico.pk not in ja:
                selecionados.append(topico)
                ja.add(topico.pk)
                faltam -= 1
                if criado:
                    novos += 1
            indice += 1

        return selecionados, novos

    def _abastecer_topico(self, topico, autor):
        criados_obj = 0
        criados_dis = 0

        for dificuldade in DIFICULDADES:
            existentes = Questao.objects.filter(
                topico=topico,
                tipo=Questao.Tipo.OBJETIVA,
                dificuldade=dificuldade,
                ativo=True,
            ).count()
            for indice in range(existentes, OBJ_POR_DIFICULDADE):
                if self._criar_objetiva(topico, dificuldade, indice, autor):
                    criados_obj += 1

        existentes_dis = Questao.objects.filter(
            topico=topico,
            tipo=Questao.Tipo.DISCURSIVA,
            ativo=True,
        ).count()
        for indice in range(existentes_dis, DIS_POR_TOPICO):
            if self._criar_discursiva(topico, indice, autor):
                criados_dis += 1

        return criados_obj, criados_dis

    def _criar_objetiva(self, topico, dificuldade, indice, autor):
        disc = topico.disciplina
        dif_label = Questao.Dificuldade(dificuldade).label
        enunciado = (
            f'[SIMULADO ENADE] {disc.nome} — {topico.nome}: questão objetiva '
            f'({dif_label}) nº {indice + 1}. Assinale a alternativa correta.'
        )
        questao, criada = Questao.objects.get_or_create(
            enunciado=enunciado,
            defaults={
                'disciplina': disc,
                'topico': topico,
                'tipo': Questao.Tipo.OBJETIVA,
                'dificuldade': dificuldade,
                'origem': ORIGEM,
                'autor': autor,
                'ativo': True,
            },
        )
        if criada:
            correta_idx = indice % len(LETRAS)
            Alternativa.objects.bulk_create(
                [
                    Alternativa(
                        questao=questao,
                        letra=LETRAS[k],
                        texto=f'Alternativa {LETRAS[k]} sobre {topico.nome}.',
                        correta=(k == correta_idx),
                    )
                    for k in range(len(LETRAS))
                ]
            )
        return criada

    def _criar_discursiva(self, topico, indice, autor):
        disc = topico.disciplina
        enunciado = (
            f'[SIMULADO ENADE] {disc.nome} — {topico.nome}: questão discursiva '
            f'nº {indice + 1}. Disserte sobre o tema, apresentando os conceitos '
            f'fundamentais e um exemplo aplicado.'
        )
        _questao, criada = Questao.objects.get_or_create(
            enunciado=enunciado,
            defaults={
                'disciplina': disc,
                'topico': topico,
                'tipo': Questao.Tipo.DISCURSIVA,
                'dificuldade': Questao.Dificuldade.MEDIO,
                'origem': ORIGEM,
                'criterios_correcao': (
                    'Espera-se domínio dos conceitos centrais do tópico, coerência '
                    'argumentativa e um exemplo aplicado correto.'
                ),
                'autor': autor,
                'ativo': True,
            },
        )
        return criada
