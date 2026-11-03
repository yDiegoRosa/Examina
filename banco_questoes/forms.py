from django import forms
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from django.core.exceptions import ValidationError
from django.forms import inlineformset_factory

from .models import Alternativa, Disciplina, Questao, Topico
from .permissions import eh_administrador
from .seguranca_login import minutos_de_bloqueio
from .utils import alternativas_repetidas


# ---------------------------------------------------------------------------
# Autenticação
# ---------------------------------------------------------------------------
class LoginForm(AuthenticationForm):
    """
    Login com o limite de tentativas (``seguranca_login``): usuário bloqueado recebe o aviso
    com o tempo de espera, inclusive na tentativa que atinge o limite.
    """

    login_bloqueado = False

    # Desmarcado: a sessão termina ao fechar o navegador ou após um tempo sem uso
    # (Examina.views.LoginView); marcado: dura 2 semanas.
    lembrar = forms.BooleanField(required=False, label='Manter conectado neste computador')

    def _recusar_se_bloqueado(self, username):
        minutos = minutos_de_bloqueio(username)
        if minutos:
            self.login_bloqueado = True
            raise ValidationError(
                f'Muitas tentativas de login sem sucesso. Tente novamente em {minutos} minuto(s).',
                code='login_bloqueado',
            )

    def clean(self):
        username = self.cleaned_data.get('username')
        self._recusar_se_bloqueado(username)
        try:
            return super().clean()
        except ValidationError:
            # A tentativa errada que acabou de ser registrada pode ter atingido o limite.
            self._recusar_se_bloqueado(username)
            raise


class AlterarSenhaForm(PasswordChangeForm):
    """Troca de senha pelo próprio usuário, com os campos no estilo do sistema."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo in self.fields.values():
            campo.widget.attrs['class'] = 'form-input'

    def clean(self):
        dados = super().clean()
        # Sobretudo no primeiro acesso: a senha temporária não pode continuar sendo a senha.
        if dados.get('old_password') and dados.get('old_password') == dados.get('new_password1'):
            self.add_error('new_password1', 'A nova senha precisa ser diferente da atual.')
        return dados


# ---------------------------------------------------------------------------
# Extensões e magic bytes aceitos para importação
# ---------------------------------------------------------------------------
# CSV: qualquer arquivo de texto não tem magic bytes fixos, mas verificamos
# que os primeiros bytes são ASCII/UTF-8 imprimíveis (sem assinatura binária).
# XLSX / XLS:
#   .xlsx → ZIP  → b'PK\x03\x04'
#   .xls  → OLE2 → b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'
#   .zip  → ZIP  → b'PK\x03\x04' (planilha + imagens — ver importacao_zip.py)
_ALLOWED_EXTENSIONS = ('.csv', '.xlsx', '.xls', '.zip')

_MAGIC_BYTES: dict[str, bytes] = {
    '.xlsx': b'PK\x03\x04',  # OOXML / ZIP container
    '.xls': b'\xd0\xcf\x11\xe0',  # OLE2 Compound Document
    '.zip': b'PK\x03\x04',
}


# Formatos da imagem de apoio (formulário da questão); a importação por ZIP aceita só PNG/JPG.
FORMATOS_IMAGEM_QUESTAO = {'PNG', 'JPEG', 'GIF', 'WEBP'}


def _validate_upload_file(arquivo):
    """
    Valida extensão, tamanho e assinatura binária (magic bytes) de um arquivo
    de upload destinado à importação CSV/XLSX/ZIP. O .zip (planilha + imagens) tem um
    limite de tamanho próprio, ``IMPORT_ZIP_MAX_SIZE_MB``.

    Levanta ``forms.ValidationError`` em caso de falha, sem consumir o stream
    (o ponteiro é reposicionado em 0 após a leitura dos magic bytes).
    """
    # -- 1. Extensão --------------------------------------------------------
    nome = arquivo.name.lower()
    if not any(nome.endswith(ext) for ext in _ALLOWED_EXTENSIONS):
        raise forms.ValidationError(f'Formato inválido. São aceitos apenas: {", ".join(_ALLOWED_EXTENSIONS)}.')

    ext = next(ext for ext in _ALLOWED_EXTENSIONS if nome.endswith(ext))
    limite = 'IMPORT_ZIP_MAX_SIZE_MB' if ext == '.zip' else 'CSV_UPLOAD_MAX_SIZE_MB'
    max_mb = getattr(settings, limite, 50 if ext == '.zip' else 5)
    max_bytes = max_mb * 1024 * 1024

    # -- 2. Tamanho ---------------------------------------------------------
    # InMemoryUploadedFile expõe .size; TemporaryFileUploadedFile também.
    if arquivo.size > max_bytes:
        raise forms.ValidationError(
            f'O arquivo é muito grande ({arquivo.size / 1024 / 1024:.1f} MB). O limite é {max_mb} MB.'
        )

    # -- 3. Magic bytes (assinatura binária real) ---------------------------
    if ext in _MAGIC_BYTES:
        # Lê apenas os bytes necessários para a verificação
        header = arquivo.read(len(_MAGIC_BYTES[ext]))
        arquivo.seek(0)  # Reposiciona o ponteiro para não corromper o stream

        if not header.startswith(_MAGIC_BYTES[ext]):
            raise forms.ValidationError(
                f'O conteúdo do arquivo não corresponde à extensão '
                f'"{ext}". Verifique se o arquivo não está corrompido ou '
                f'renomeado.'
            )
    else:
        # CSV: verifica que os primeiros 512 bytes não contêm bytes nulos
        # (presença de \x00 indica arquivo binário disfarçado de texto).
        sample = arquivo.read(512)
        arquivo.seek(0)
        if b'\x00' in sample:
            raise forms.ValidationError(
                'O arquivo CSV parece ser um arquivo binário. Envie um arquivo de texto puro com extensão .csv.'
            )

    return arquivo


# ---------------------------------------------------------------------------
# Formulário de upload CSV/XLSX para importação em lote
# ---------------------------------------------------------------------------
class CSVUploadForm(forms.Form):
    """
    Formulário para upload de planilhas CSV ou Excel (ou um .zip com a planilha e as
    imagens) destinadas à importação em lote de questões.

    Validações aplicadas no ``clean_arquivo()``:
    - Extensão: apenas .csv, .xlsx, .xls, .zip
    - Tamanho: ≤ ``settings.CSV_UPLOAD_MAX_SIZE_MB`` MB (padrão 5 MB); .zip ≤
      ``settings.IMPORT_ZIP_MAX_SIZE_MB`` MB (padrão 50 MB)
    - Magic bytes: verifica a assinatura binária real do arquivo para
      detectar arquivos renomeados (e.g. .exe disfarçado de .csv)
    """

    arquivo = forms.FileField(
        label='Arquivo CSV, Excel (.xlsx) ou .zip com imagens',
        widget=forms.ClearableFileInput(
            attrs={
                'class': 'form-input form-file-input',
                'accept': '.csv,.xlsx,.xls,.zip',
            }
        ),
        help_text=f'Formatos aceitos: .csv, .xlsx, .xls · Tamanho máximo: '
        f'{getattr(settings, "CSV_UPLOAD_MAX_SIZE_MB", 5)} MB; .zip (planilha + imagens) até '
        f'{getattr(settings, "IMPORT_ZIP_MAX_SIZE_MB", 50)} MB',
    )

    def __init__(self, *args, usuario=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Só o administrador importa "em nome de" outro professor (o professor vê e usa nas provas só
        # as questões de que é autor — ver academico.acesso).
        if usuario is not None and eh_administrador(usuario):
            self.fields['autor'] = _AutorChoiceField(
                label='Autor das questões',
                queryset=possiveis_autores(),
                required=False,
                empty_label='Eu mesmo',
                widget=forms.Select(attrs={'class': 'form-select'}),
                help_text='As questões entram no acervo do professor escolhido, que poderá usá-las nas provas dele.',
            )

    def clean_arquivo(self):
        arquivo = self.cleaned_data.get('arquivo')
        if arquivo:
            _validate_upload_file(arquivo)
        return arquivo


# ---------------------------------------------------------------------------
# Autoria das questões (importar em nome de / transferir em lote)
# ---------------------------------------------------------------------------
def possiveis_autores():
    """Contas ativas que podem ser autoras de questões: professores, coordenação e administração."""
    from django.contrib.auth import get_user_model
    from django.db.models import Q

    from .permissions import GRUPO_ADMINISTRADOR, GRUPO_COORDENADOR

    return (
        get_user_model()
        .objects.filter(is_active=True)
        .filter(Q(is_superuser=True) | Q(groups__name__in=['Docente', GRUPO_COORDENADOR, GRUPO_ADMINISTRADOR]))
        .distinct()
        .order_by('first_name', 'last_name', 'username')
    )


class _AutorChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        nome = obj.get_full_name()
        return f'{nome} ({obj.get_username()})' if nome else obj.get_username()


class TransferirAutoriaForm(forms.Form):
    """Acervo → transferir as questões marcadas (ou todas do filtro atual) para outro professor."""

    novo_autor = _AutorChoiceField(
        label='Transferir para',
        queryset=possiveis_autores(),
        empty_label='Escolha o professor...',
        widget=forms.Select(attrs={'class': 'form-select', 'aria-label': 'Transferir para'}),
    )
    todas_do_filtro = forms.BooleanField(required=False)


# ---------------------------------------------------------------------------
# Alternativa — Form + FormSet
# ---------------------------------------------------------------------------
class AlternativaForm(forms.ModelForm):
    """Form para cada alternativa individual com widgets Vanilla CSS."""

    class Meta:
        model = Alternativa
        fields = ['letra', 'texto', 'correta']
        widgets = {
            'letra': forms.Select(
                choices=[('', '---')] + [(letra, letra) for letra in 'ABCDE'],
                attrs={'class': 'form-select select-letter'},
            ),
            'texto': forms.Textarea(
                attrs={
                    'class': 'form-textarea textarea-sm',
                    'rows': 2,
                    'placeholder': 'Digite o conteúdo desta alternativa…',
                },
            ),
            'correta': forms.CheckboxInput(
                attrs={'class': 'form-checkbox'},
            ),
        }


AlternativaFormSet = inlineformset_factory(
    Questao,
    Alternativa,
    form=AlternativaForm,
    min_num=0,
    max_num=5,
    extra=2,
    validate_min=False,
    can_delete=True,
)


# ---------------------------------------------------------------------------
# Questão — ModelForm com validação condicional
# ---------------------------------------------------------------------------
class QuestaoForm(forms.ModelForm):
    """
    ModelForm para cadastro de questões.

    Validação condicional:
    - Objetiva → exige pelo menos 2 alternativas e exatamente 1 correta
      (verificado via ``validate_with_alternativas``).
    - Discursiva → exige ``criterios_correcao`` preenchido.
    """

    class Meta:
        model = Questao
        fields = [
            'origem',
            'numero_questao',
            'tipo',
            'dificuldade',
            'disciplina',
            'topico',
            'texto_auxiliar',
            'imagem',
            'enunciado',
            'criterios_correcao',
            'ativo',
        ]
        labels = {
            'origem': 'Origem / Exame (Opcional)',
            'numero_questao': 'Nº da Questão no Exame (Opcional)',
            'tipo': 'Tipo de questão',
            'dificuldade': 'Nível de dificuldade',
            'disciplina': 'Disciplina',
            'topico': 'Tópico curricular',
            'texto_auxiliar': 'Texto auxiliar / Contextualização (Opcional)',
            'imagem': 'Imagem / Diagrama de apoio (Opcional)',
            'enunciado': 'Enunciado da questão',
            'criterios_correcao': 'Comentários / Justificativa da Resposta (Opcional)',
            'ativo': 'Questão ativa no acervo',
        }
        widgets = {
            'origem': forms.TextInput(
                attrs={
                    'class': 'form-input',
                    'placeholder': 'Ex.: ENADE ADS 2021, POSCOMP 2025',
                },
            ),
            'numero_questao': forms.TextInput(
                attrs={
                    'class': 'form-input',
                    'placeholder': 'Ex.: 26, 15',
                },
            ),
            'tipo': forms.Select(attrs={'class': 'form-select'}),
            'dificuldade': forms.Select(attrs={'class': 'form-select'}),
            'disciplina': forms.Select(attrs={'class': 'form-select'}),
            'topico': forms.Select(attrs={'class': 'form-select'}),
            'texto_auxiliar': forms.Textarea(
                attrs={
                    'class': 'form-textarea',
                    'rows': 3,
                    'placeholder': 'Texto motivador, trecho de código, contextualização ou artigo base…',
                },
            ),
            'imagem': forms.ClearableFileInput(
                attrs={
                    'class': 'form-input form-file-input',
                    'accept': '.png,.jpg,.jpeg,.gif,.webp,image/png,image/jpeg,image/gif,image/webp',
                },
            ),
            'enunciado': forms.Textarea(
                attrs={
                    'class': 'form-textarea',
                    'rows': 4,
                    'placeholder': 'Redija aqui o enunciado completo da questão…',
                },
            ),
            'criterios_correcao': forms.Textarea(
                attrs={
                    'class': 'form-textarea',
                    'rows': 3,
                    'placeholder': 'Justificativa analítica fundamentando o gabarito e detalhando a resposta da questão…',
                },
            ),
            'ativo': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.limite_imagem_mb = f'{float(getattr(settings, "IMPORT_IMAGEM_MAX_SIZE_MB", 5)):g}'
        self.fields['disciplina'].queryset = Disciplina.objects.filter(ativo=True).order_by('nome')

        disciplina_id = None
        if self.is_bound:
            disciplina_id = self.data.get('disciplina')
        elif self.instance and self.instance.pk and self.instance.disciplina_id:
            disciplina_id = self.instance.disciplina_id

        if disciplina_id:
            self.fields['topico'].queryset = Topico.objects.filter(disciplina_id=disciplina_id, ativo=True).order_by(
                'nome'
            )
        else:
            self.fields['topico'].queryset = Topico.objects.filter(ativo=True).order_by('nome')

    # ------------------------------------------------------------------
    # Field-level validation
    # ------------------------------------------------------------------
    def clean_imagem(self):
        """Imagem nova: PNG, JPG, GIF ou WebP de verdade, até IMPORT_IMAGEM_MAX_SIZE_MB (o mesmo limite do ZIP)."""
        from django.core.files.uploadedfile import UploadedFile
        from PIL import Image, UnidentifiedImageError

        imagem = self.cleaned_data.get('imagem')
        if not isinstance(imagem, UploadedFile):
            return imagem  # sem envio novo (mantém a atual) ou "remover" marcado
        limite_mb = float(getattr(settings, 'IMPORT_IMAGEM_MAX_SIZE_MB', 5))
        if imagem.size > limite_mb * 1024 * 1024:
            raise forms.ValidationError(
                f'A imagem tem {imagem.size / (1024 * 1024):.1f} MB; o limite é {limite_mb:g} MB. '
                'Recorte só a figura ou reduza a resolução.'
            )
        try:
            imagem.seek(0)
            with Image.open(imagem) as img:
                formato = img.format
                img.verify()
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
            raise forms.ValidationError('O arquivo não é uma imagem válida (corrompido ou de outro tipo).') from exc
        finally:
            imagem.seek(0)
        if formato not in FORMATOS_IMAGEM_QUESTAO:
            raise forms.ValidationError(f'Imagem em {formato}: use PNG, JPG, GIF ou WebP.')
        return imagem

    def clean_topico(self):
        """Garante que o tópico pertence à disciplina selecionada."""
        topico = self.cleaned_data.get('topico')
        disciplina = self.cleaned_data.get('disciplina')

        if topico and disciplina and topico.disciplina_id != disciplina.pk:
            raise forms.ValidationError('O tópico selecionado não pertence à disciplina informada.')
        return topico

    # ------------------------------------------------------------------
    # Validação cruzada com o formset de alternativas
    # ------------------------------------------------------------------
    def validate_with_alternativas(self, alternativa_formset):
        """
        Deve ser chamado pela view após ``form.is_valid()`` e
        ``formset.is_valid()`` para aplicar regras cruzadas.

        Retorna True se tudo válido, False caso contrário (erros são
        adicionados ao form).
        """
        tipo = self.cleaned_data.get('tipo')

        if tipo != Questao.Tipo.OBJETIVA:
            return True

        # Conta apenas forms preenchidos e não marcados para deleção
        filled = [f for f in alternativa_formset.forms if f.cleaned_data and not f.cleaned_data.get('DELETE')]

        if len(filled) < 2:
            self.add_error(
                None,
                'Questões objetivas precisam de no mínimo 2 alternativas.',
            )
            return False

        repetidas = alternativas_repetidas((f.cleaned_data.get('letra'), f.cleaned_data.get('texto')) for f in filled)
        if repetidas:
            self.add_error(
                None, f'Alternativas repetidas ({repetidas}): cada alternativa precisa de um texto diferente.'
            )
            return False

        corretas = [f for f in filled if f.cleaned_data.get('correta')]

        if len(corretas) != 1:
            self.add_error(
                None,
                f'Questões objetivas devem ter exatamente 1 alternativa correta (encontrada(s): {len(corretas)}).',
            )
            return False

        return True
