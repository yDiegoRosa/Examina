"""Serviço de geração de documentos PDF para o Módulo de Provas via WeasyPrint."""

import logging
from io import BytesIO
from typing import Any

from django.conf import settings
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


class PDFGenerationError(Exception):
    """Exceção levantada quando ocorre erro durante a compilação do PDF."""


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
            html_doc = weasyprint.HTML(string=html_content, base_url=base_url)
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
