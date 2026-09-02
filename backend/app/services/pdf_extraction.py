"""PDF extraction — Docling behind the registry's extractor seam.

Everything Docling is quarantined here.  The import happens inside the
function, twice over: Docling pulls a torch-backed model stack that takes
seconds to import and downloads model weights on first use, so a process that
never meets a PDF never pays for it — and the offline test suite can fake
`extract_pdf` without Docling being installed at all.

The contract with the rest of the pipeline is the docstring of
`ExtractionResult`: the returned `text` is the canonical string, its page
spans describe that exact string, and each table also appears in `tables`
verbatim.  OCR is always on, so a scanned page or a picture containing text
comes back as text like any other page.
"""

import io
import logging
import threading
from typing import Any, Optional

from app.schemas.extraction import ExtractedTable, ExtractionResult, PageSpan
from app.services.provenance import table_id_for

logger = logging.getLogger(__name__)

# Marker injected between pages of the stored markdown. Human-readable in the
# artifact; the authoritative page data is `ExtractionResult.pages`, never a
# re-parse of these.
PAGE_MARKER_FORMAT = "<!-- page {page} -->"

# Sentinel Docling is asked to place at page breaks, replaced (and measured)
# before anything downstream sees the text. A form feed cannot appear in
# markdown Docling emits, so splitting on it is exact.
_PAGE_SENTINEL = "\f"

# The converter loads model weights, so it is built once per process and only
# when the first PDF actually arrives.
_converter: Optional[Any] = None
_converter_lock = threading.Lock()


def _get_converter() -> Any:
    """Build (once) and return the Docling converter, OCR and tables on."""
    global _converter
    with _converter_lock:
        if _converter is None:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import (
                PdfPipelineOptions,
                TableFormerMode,
            )
            from docling.document_converter import DocumentConverter, PdfFormatOption

            options = PdfPipelineOptions(do_ocr=True, do_table_structure=True)
            options.table_structure_options.mode = TableFormerMode.ACCURATE

            logger.info("Loading Docling models (first PDF of this process)")
            _converter = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
            )
        return _converter


def _spans_from(marked: str) -> tuple[str, list[PageSpan]]:
    """Replace page sentinels with visible markers, measuring spans as we go.

    Args:
        marked: The markdown export with a sentinel at each page break.

    Returns:
        The final text with human-readable markers, and one span per page
        describing exactly where that page's text lies in it.
    """
    pieces = marked.split(_PAGE_SENTINEL)

    text = ""
    spans: list[PageSpan] = []
    for number, piece in enumerate(pieces, start=1):
        if number > 1:
            text += "\n" + PAGE_MARKER_FORMAT.format(page=number) + "\n"
        start = len(text)
        text += piece
        spans.append(PageSpan(page=number, start_offset=start, end_offset=len(text)))

    return text, spans


def extract_pdf(data: bytes, name: str = "document.pdf") -> ExtractionResult:
    """Convert one PDF's bytes into the canonical extraction result.

    Runs Docling's full pipeline — layout analysis, reading order, OCR on
    image-only content, table structure — and is therefore slow: seconds for a
    born-digital file, minutes for a large scan.  Callers on the event loop
    must hand it to a thread.

    Args:
        data: The PDF's raw bytes.
        name: The filename, used only for logging and Docling's bookkeeping.

    Returns:
        The canonical text with page markers, page spans over that text, and
        every detected table verbatim.
    """
    from docling.datamodel.base_models import DocumentStream

    converter = _get_converter()
    converted = converter.convert(
        DocumentStream(name=name.rsplit("/", 1)[-1] or "document.pdf", stream=io.BytesIO(data))
    )
    document = converted.document

    marked = document.export_to_markdown(page_break_placeholder=_PAGE_SENTINEL)
    text, spans = _spans_from(marked)

    tables: list[ExtractedTable] = []
    for index, table in enumerate(document.tables):
        provenance = table.prov[0] if getattr(table, "prov", None) else None
        caption = table.caption_text(document) if hasattr(table, "caption_text") else None
        tables.append(
            ExtractedTable(
                table_id=table_id_for(index),
                markdown=table.export_to_markdown(document),
                page=getattr(provenance, "page_no", None),
                caption=caption or None,
            )
        )

    logger.info(
        "%s: extracted %d page(s), %d table(s), %d character(s)",
        name,
        len(spans),
        len(tables),
        len(text),
    )

    return ExtractionResult(text=text, pages=spans, tables=tables)
