"""PDF extraction — Docling behind the registry's extractor seam.

Everything Docling is quarantined here.  The import happens inside the
function, twice over: Docling pulls a torch-backed model stack that takes
seconds to import and downloads model weights on first use, so a process that
never meets a PDF never pays for it — and the offline test suite can fake
`extract_pdf` without Docling being installed at all.

Two choices in here were made by a failing test rather than taste:

  * **The pdfium parsing backend.**  Docling's own parse backend failed
    nondeterministically on multi-page files that every page-by-page probe
    parsed fine — different pages "failed to parse" on different runs of the
    same bytes.  pdfium (Chrome's PDF parser) read the same files without
    complaint, and the layout model does the heavy lifting either way.
  * **Pages are exported one at a time.**  A single whole-document export with
    page-break placeholders silently collapses a page that produced nothing,
    so everything after it would be attributed to the wrong page.  Exporting
    per page number keeps the numbering true even when a page is empty or
    failed to parse.

The contract with the rest of the pipeline is the docstring of
`ExtractionResult`: the returned `text` is the canonical string, its page
spans describe that exact string, and each table also appears in `tables`
verbatim.  OCR is always on, so a scanned page or a picture containing text
comes back as text like any other page.  A page the parser could not read
becomes a warning, never a failed document.
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

# The converter loads model weights, so it is built once per process and only
# when the first PDF actually arrives.
_converter: Optional[Any] = None
_converter_lock = threading.Lock()


def _get_converter() -> Any:
    """Build (once) and return the Docling converter, OCR and tables on."""
    global _converter
    with _converter_lock:
        if _converter is None:
            from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
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
                format_options={
                    InputFormat.PDF: PdfFormatOption(
                        pipeline_options=options, backend=PyPdfiumDocumentBackend
                    )
                }
            )
        return _converter


def _assemble(document: Any) -> tuple[str, list[PageSpan]]:
    """Join per-page markdown exports into one text, measuring spans as we go.

    Args:
        document: The converted DoclingDocument.

    Returns:
        The canonical text with a visible marker before every page after the
        first, and one span per page describing exactly where that page's
        text lies in it — including the empty span of a page that produced
        nothing, so no page's number ever shifts.
    """
    text = ""
    spans: list[PageSpan] = []
    for number in sorted(document.pages.keys()):
        start = len(text)
        # The marker belongs to the page it introduces, so the spans tile the
        # text completely — every character is on exactly one page.
        if spans:
            text += "\n" + PAGE_MARKER_FORMAT.format(page=number) + "\n"
        text += document.export_to_markdown(page_no=number)
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
        The canonical text with page markers, page spans over that text, every
        detected table verbatim, and a warning per page that failed to parse.
    """
    from docling.datamodel.base_models import DocumentStream

    converter = _get_converter()
    converted = converter.convert(
        DocumentStream(name=name.rsplit("/", 1)[-1] or "document.pdf", stream=io.BytesIO(data))
    )
    document = converted.document

    # A page the parser could not read is degraded coverage, not a failed
    # document: the caller reports it and the rest of the file still indexes.
    warnings = [error.error_message for error in converted.errors]

    text, spans = _assemble(document)

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
        "%s: extracted %d page(s), %d table(s), %d character(s), %d warning(s)",
        name,
        len(spans),
        len(tables),
        len(text),
        len(warnings),
    )

    return ExtractionResult(text=text, pages=spans, tables=tables, warnings=warnings)
