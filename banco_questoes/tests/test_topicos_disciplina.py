from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from banco_questoes.filters import QuestaoFilter
from banco_questoes.forms import QuestaoForm
from banco_questoes.models import Disciplina, Topico


class TopicosPorDisciplinaTest(TestCase):
    """Testes unitários e de integração para filtragem de tópicos por disciplina."""

    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username='docente_topicos', password='secret_password')  # noqa: S106
        self.client.login(username='docente_topicos', password='secret_password')  # noqa: S106

        self.disc_calc = Disciplina.objects.create(nome='Cálculo I')
        self.disc_prog = Disciplina.objects.create(nome='Algoritmos e Programação')

        self.top_lim = Topico.objects.create(disciplina=self.disc_calc, nome='Limites e Continuidade')
        self.top_der = Topico.objects.create(disciplina=self.disc_calc, nome='Derivadas')
        self.top_vet = Topico.objects.create(disciplina=self.disc_prog, nome='Vetores e Matrizes')

    def test_api_topicos_por_disciplina_returns_only_matching_topics(self):
        """Verifica que o endpoint api_topicos_por_disciplina retorna apenas tópicos da disciplina."""
        url = f'{reverse("banco_questoes:api_topicos_por_disciplina")}?disciplina_id={self.disc_calc.pk}'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['disciplina_id'], self.disc_calc.pk)
        self.assertEqual(len(data['topicos']), 2)
        top_nomes = [t['nome'] for t in data['topicos']]
        self.assertIn('Limites e Continuidade', top_nomes)
        self.assertIn('Derivadas', top_nomes)
        self.assertNotIn('Vetores e Matrizes', top_nomes)

    def test_questao_form_filters_topics_by_disciplina_in_data(self):
        """Verifica que QuestaoForm restringe o queryset de tópicos à disciplina selecionada."""
        form = QuestaoForm(data={'disciplina': self.disc_calc.pk})
        qs = form.fields['topico'].queryset
        self.assertIn(self.top_lim, qs)
        self.assertIn(self.top_der, qs)
        self.assertNotIn(self.top_vet, qs)

    def test_questao_filter_filters_topics_by_disciplina(self):
        """Verifica que QuestaoFilter restringe o queryset de tópicos à disciplina filtrada."""
        filtro = QuestaoFilter(data={'disciplina': self.disc_calc.pk})
        qs = filtro.filters['topico'].extra['queryset']
        self.assertIn(self.top_lim, qs)
        self.assertIn(self.top_der, qs)
        self.assertNotIn(self.top_vet, qs)
