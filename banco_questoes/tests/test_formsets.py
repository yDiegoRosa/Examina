"""
test_formsets.py — Testes do QuestaoForm + AlternativaFormSet.

Cobre:
- validate_with_alternativas: regras cruzadas questão ↔ alternativas
  - Objetiva válida (≥2 alternativas, exatamente 1 correta) → True
  - Objetiva sem correta → erro
  - Objetiva com 2+ corretas → erro
  - Objetiva com apenas 1 alternativa → erro mínimo
  - Discursiva → bypassa validação de alternativas → True
- clean_topico: FK validação tópico × disciplina
- Configuração do AlternativaFormSet (max_num, can_delete)
"""

import pytest

from banco_questoes.forms import AlternativaFormSet, QuestaoForm
from banco_questoes.models import Topico

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_formset_data(alternativas: list[dict], questao_pk=None, prefix='alternativas') -> dict:
    """
    Constrói o dicionário POST simulando um AlternativaFormSet.

    alternativas: lista de dicts com chaves 'letra', 'texto', 'correta', 'DELETE' (opt)
    """
    data = {
        f'{prefix}-TOTAL_FORMS': str(len(alternativas)),
        f'{prefix}-INITIAL_FORMS': '0',
        f'{prefix}-MIN_NUM_FORMS': '0',
        f'{prefix}-MAX_NUM_FORMS': '5',
    }
    if questao_pk:
        data[f'{prefix}-questao'] = str(questao_pk)

    for i, alt in enumerate(alternativas):
        data[f'{prefix}-{i}-letra'] = alt.get('letra', '')
        data[f'{prefix}-{i}-texto'] = alt.get('texto', '')
        data[f'{prefix}-{i}-correta'] = 'on' if alt.get('correta') else ''
        data[f'{prefix}-{i}-DELETE'] = 'on' if alt.get('DELETE') else ''
        data[f'{prefix}-{i}-id'] = alt.get('id', '')
    return data


def _build_questao_data(tipo='OBJ', disciplina_id=None, **kwargs) -> dict:
    """Constrói dados mínimos válidos para o QuestaoForm."""
    return {
        'tipo': tipo,
        'dificuldade': 'M',
        'disciplina': str(disciplina_id or ''),
        'enunciado': 'Enunciado de teste',
        'criterios_correcao': '' if tipo == 'OBJ' else 'Critério de avaliação.',
        'ativo': 'on',
        **kwargs,
    }


# ---------------------------------------------------------------------------
# Testes: validate_with_alternativas
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestValidateWithAlternativas:
    def test_objetiva_valida_duas_alternativas_uma_correta(self, disciplina):
        """Objetiva com ≥2 alternativas e exatamente 1 correta → True."""
        form_data = _build_questao_data(tipo='OBJ', disciplina_id=disciplina.pk)
        form = QuestaoForm(data=form_data)
        assert form.is_valid(), f'Form inválido: {form.errors}'

        alts = [
            {'letra': 'A', 'texto': 'Opção A', 'correta': False},
            {'letra': 'B', 'texto': 'Opção B', 'correta': True},
        ]
        formset_data = _build_formset_data(alts)
        formset = AlternativaFormSet(data=formset_data)
        assert formset.is_valid(), f'Formset inválido: {formset.errors}'

        result = form.validate_with_alternativas(formset)
        assert result is True
        assert form.non_field_errors() == []

    def test_objetiva_sem_nenhuma_correta_gera_erro(self, disciplina):
        """Objetiva sem alternativa correta → adiciona erro não-campo."""
        form_data = _build_questao_data(tipo='OBJ', disciplina_id=disciplina.pk)
        form = QuestaoForm(data=form_data)
        assert form.is_valid()

        alts = [
            {'letra': 'A', 'texto': 'Opção A', 'correta': False},
            {'letra': 'B', 'texto': 'Opção B', 'correta': False},
        ]
        formset_data = _build_formset_data(alts)
        formset = AlternativaFormSet(data=formset_data)
        assert formset.is_valid()

        result = form.validate_with_alternativas(formset)
        assert result is False
        erros = str(form.non_field_errors())
        assert 'exatamente 1' in erros

    def test_objetiva_com_duas_corretas_gera_erro(self, disciplina):
        """Objetiva com 2 alternativas corretas → erro 'exatamente 1'."""
        form_data = _build_questao_data(tipo='OBJ', disciplina_id=disciplina.pk)
        form = QuestaoForm(data=form_data)
        assert form.is_valid()

        alts = [
            {'letra': 'A', 'texto': 'Opção A', 'correta': True},
            {'letra': 'B', 'texto': 'Opção B', 'correta': True},
            {'letra': 'C', 'texto': 'Opção C', 'correta': False},
        ]
        formset_data = _build_formset_data(alts)
        formset = AlternativaFormSet(data=formset_data)
        assert formset.is_valid()

        result = form.validate_with_alternativas(formset)
        assert result is False
        erros = str(form.non_field_errors())
        assert 'exatamente 1' in erros

    def test_objetiva_com_apenas_uma_alternativa_gera_erro(self, disciplina):
        """Objetiva com só 1 alternativa → erro 'mínimo 2'."""
        form_data = _build_questao_data(tipo='OBJ', disciplina_id=disciplina.pk)
        form = QuestaoForm(data=form_data)
        assert form.is_valid()

        alts = [
            {'letra': 'A', 'texto': 'Única opção', 'correta': True},
        ]
        formset_data = _build_formset_data(alts)
        formset = AlternativaFormSet(data=formset_data)
        assert formset.is_valid()

        result = form.validate_with_alternativas(formset)
        assert result is False
        erros = str(form.non_field_errors())
        assert 'mínimo 2' in erros

    def test_objetiva_alternativa_marcada_delete_nao_conta(self, disciplina):
        """Alternativa com DELETE=True deve ser ignorada na contagem."""
        form_data = _build_questao_data(tipo='OBJ', disciplina_id=disciplina.pk)
        form = QuestaoForm(data=form_data)
        assert form.is_valid()

        # 3 alts, mas 2 marcadas para DELETE → efetivamente 1 alternativa
        alts = [
            {'letra': 'A', 'texto': 'Opção A', 'correta': True},
            {'letra': 'B', 'texto': 'Opção B', 'correta': False, 'DELETE': True},
            {'letra': 'C', 'texto': 'Opção C', 'correta': False, 'DELETE': True},
        ]
        formset_data = _build_formset_data(alts)
        formset = AlternativaFormSet(data=formset_data)
        assert formset.is_valid()

        result = form.validate_with_alternativas(formset)
        assert result is False  # Só 1 alternativa efetiva → mínimo não atingido

    def test_discursiva_bypassa_validacao_de_alternativas(self, disciplina):
        """Discursiva deve retornar True sem validar alternativas."""
        form_data = _build_questao_data(
            tipo='DIS',
            disciplina_id=disciplina.pk,
            criterios_correcao='Critério de avaliação.',
        )
        form = QuestaoForm(data=form_data)
        assert form.is_valid(), f'Form inválido: {form.errors}'

        # Formset vazio — não deve importar para discursivas
        formset = AlternativaFormSet(data=_build_formset_data([]))

        result = form.validate_with_alternativas(formset)
        assert result is True


# ---------------------------------------------------------------------------
# Testes: clean_topico (FK validação)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCleanTopico:
    def test_topico_da_disciplina_correta_passa(self, disciplina, topico):
        """Tópico pertencente à disciplina selecionada deve ser aceito."""
        form_data = _build_questao_data(
            tipo='OBJ',
            disciplina_id=disciplina.pk,
            topico=topico.pk,
        )
        form = QuestaoForm(data=form_data)
        # O campo topico pode ter erro de outras validações;
        # verificamos especificamente que clean_topico não adiciona erros
        form.is_valid()
        assert 'O tópico selecionado não pertence à disciplina informada.' not in str(form.errors)

    def test_topico_de_outra_disciplina_gera_erro(self, disciplina, disciplina_outra, db):
        """Tópico de disciplina diferente deve levantar ValidationError."""
        topico_outra = Topico.objects.create(disciplina=disciplina_outra, nome='Álgebra')
        form_data = _build_questao_data(
            tipo='OBJ',
            disciplina_id=disciplina.pk,
            topico=topico_outra.pk,
        )
        form = QuestaoForm(data=form_data)
        # O queryset de `topico` é filtrado pela disciplina submetida (ver QuestaoForm.__init__),
        # então um tópico de outra disciplina é rejeitado no nível do campo, antes de clean_topico.
        # O essencial é que a combinação inválida não seja aceita.
        assert form.is_valid() is False
        assert 'topico' in form.errors


# ---------------------------------------------------------------------------
# Testes: Configuração do AlternativaFormSet
# ---------------------------------------------------------------------------


class TestAlternativaFormSetConfig:
    def test_max_num_e_cinco(self):
        assert AlternativaFormSet.max_num == 5

    def test_can_delete_habilitado(self):
        assert AlternativaFormSet.can_delete is True

    def test_min_num_zero(self):
        assert AlternativaFormSet.min_num == 0
