"""Serviço de geração de documentos PDF para o Módulo de Provas via WeasyPrint."""

import logging
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from django.conf import settings
from django.contrib.staticfiles import finders
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


class PDFGenerationError(Exception):
    """Exceção levantada quando ocorre erro durante a compilação do PDF."""


def resolve_static_media_url(url: str) -> str:
    """
    Reescreve uma URL de ``STATIC_URL``/``MEDIA_URL`` para o caminho ``file://`` local.

    Os templates referenciam CSS/imagens por URL raiz-relativa — ``{% static %}``
    gera ``/static/...`` e ``FieldFile.url`` gera ``/media/...``. Como a compilação
    do PDF ocorre sem servidor HTTP (``base_url`` é um caminho de disco), o WeasyPrint
    resolveria essas URLs contra a raiz do sistema de arquivos (``file:///static/...``)
    e falharia ("Failed to load stylesheet"), deixando os PDFs praticamente sem estilo.

    Aqui mapeamos esses prefixos para o arquivo local correspondente:

    * ``STATIC_URL`` → resolvido pelos *finders* do ``staticfiles`` (funciona em
      desenvolvimento, sem depender de ``collectstatic``);
    * ``MEDIA_URL`` → resolvido contra ``MEDIA_ROOT``.

    URLs que não correspondam a um estático/mídia resolvível são devolvidas intactas
    (incluindo ``http(s)://`` e ``data:``), para o *fetcher* padrão tratá-las.
    """
    path = unquote(urlparse(url).path)

    static_prefix = urlparse(settings.STATIC_URL).path
    if static_prefix and static_prefix != '/' and static_prefix in path:
        relative = path[path.index(static_prefix) + len(static_prefix) :]
        absolute = finders.find(relative)
        if absolute:
            return Path(absolute).as_uri()

    media_prefix = urlparse(settings.MEDIA_URL).path
    if media_prefix and media_prefix != '/' and media_prefix in path:
        relative = path[path.index(media_prefix) + len(media_prefix) :]
        absolute = Path(settings.MEDIA_ROOT) / relative
        if absolute.is_file():
            return absolute.as_uri()

    return url


def build_django_url_fetcher() -> Any:
    """
    Constrói um ``url_fetcher`` do WeasyPrint ciente dos estáticos/mídia do Django.

    Retorna uma instância de :class:`weasyprint.urls.URLFetcher` que reescreve as
    URLs raiz-relativas de ``{% static %}``/``FieldFile.url`` (ver
    :func:`resolve_static_media_url`) para o arquivo local antes de buscá-las,
    delegando o restante ao comportamento padrão do WeasyPrint.
    """
    from weasyprint.urls import URLFetcher

    class DjangoURLFetcher(URLFetcher):
        def fetch(self, url, headers=None):
            return super().fetch(resolve_static_media_url(url), headers)

    return DjangoURLFetcher()


class PDFGeneratorService:
    """
    Serviço centralizado para renderização e compilação de PDFs institucionais.

    Utiliza Django Templates para estruturar o HTML e WeasyPrint para compilar
    o documento com suporte avançado a CSS Paged Media (@page, contadores,
    quebras de página e margens de corte).
    """

    @classmethod
    def render_pdf_from_template(
        cls,
        template_name: str,
        context: dict[str, Any],
        base_url: str | None = None,
    ) -> bytes:
        """
        Renderiza um template HTML do Django e compila para bytes de PDF.

        :param template_name: Caminho do template Django (ex.: 'provas/pdf_prova.html')
        :param context: Dicionário de contexto para renderização
        :param base_url: URL/Caminho base para resolução de assets estáticos e mídia
        :return: bytes do documento PDF compilado
        """
        try:
            import weasyprint
        except ImportError as exc:
            logger.error('WeasyPrint não está instalado ou dependências nativas (GTK/Pango) ausentes: %s', exc)
            raise PDFGenerationError(
                'Motor de renderização de PDF (WeasyPrint) não está disponível no servidor.'
            ) from exc

        if base_url is None:
            base_url = str(settings.BASE_DIR)

        try:
            html_content = render_to_string(template_name, context)
            html_doc = weasyprint.HTML(
                string=html_content,
                base_url=base_url,
                url_fetcher=build_django_url_fetcher(),
            )
            pdf_buffer = BytesIO()
            html_doc.write_pdf(target=pdf_buffer)
            pdf_bytes = pdf_buffer.getvalue()
            logger.info(
                'PDF gerado com sucesso a partir de "%s" (%d bytes).',
                template_name,
                len(pdf_bytes),
            )
            return pdf_bytes
        except Exception as exc:
            logger.error('Erro ao compilar PDF a partir de "%s": %s', template_name, exc, exc_info=True)
            raise PDFGenerationError(f'Falha na compilação do PDF: {exc}') from exc
