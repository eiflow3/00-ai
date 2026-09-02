"""PDF ingestion — written before the implementation, as its definition of done.

A PDF is the first format whose bytes are not the text.  The design under test:
extraction normalises the PDF into one canonical markdown string (page markers
in, tables out into their own artifacts), that string is stored durably under
``derived/{document_id}/`` beside the original, and everything downstream —
chunking, preview, golden sets — reads the stored string and never the PDF.

Docling is a vendor, so it is faked at the extractor seam exactly as R2 and
Pinecone are: `fake_extraction` stands in for `pdf_extraction.extract_pdf` and
counts its calls, which is what lets a test assert the expensive step ran once
and only once.  The implementation must therefore resolve the extractor at call
time (the planned lazy import), not bind it at import time.

Everything below the seam is the real code: the queue, the ingestion loop, the
chunkers, provenance and the whole of vector_store.
"""

from types import SimpleNamespace

import httpx
import pytest

from app.main import app
from app.schemas.chunking import ChunkingConfig, ChunkStrategy
from app.schemas.extraction import ExtractedTable, ExtractionResult, PageSpan
from app.schemas.source import IndexState
from app.services import (
    chunker,
    deletion,
    derived_artifacts,
    pdf_extraction,
    provenance,
    sync_status,
    text_extraction,
    uploads,
)

PDF_KEY = "reports/fy2026-review.pdf"

# The canonical text a faked extraction produces: two pages, a visible page
# marker, and one table already lifted out (its markdown lives in `tables`,
# per the design — by the time text is stored, chunked or previewed, the
# document body never contains a raw pipe table).
PAGE_ONE = "# Operations Review\n\n" + ("Revenue grew in Singapore this year. " * 12)
PAGE_TWO = "## Detail\n\n" + ("Engineering headcount rose to 455 staff. " * 12)
CANON_TEXT = PAGE_ONE + "\n<!-- page 2 -->\n" + PAGE_TWO
PAGE_SPANS = [
    PageSpan(page=1, start_offset=0, end_offset=len(PAGE_ONE)),
    PageSpan(page=2, start_offset=len(PAGE_ONE), end_offset=len(CANON_TEXT)),
]
TABLE = ExtractedTable(
    table_id="table-001",
    markdown="| Region | FY26 |\n| --- | --- |\n| APAC | 503.2 |",
    page=2,
    caption="Revenue by region",
)


@pytest.fixture
async def client(lab):
    """The app over ASGI, every vendor faked."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as session:
        yield session


@pytest.fixture
def pdf_key(objects) -> str:
    """A PDF in the bucket.  The bytes are junk on purpose — nothing in the
    offline suite may parse a real PDF; that is the extractor seam's job."""
    objects.put(PDF_KEY, b"%PDF-1.7 these bytes are never parsed offline")
    return PDF_KEY


@pytest.fixture
def fake_extraction(monkeypatch) -> SimpleNamespace:
    """Docling replaced at the seam, with a call counter."""
    state = SimpleNamespace(
        calls=0,
        result=ExtractionResult(
            text=CANON_TEXT, pages=list(PAGE_SPANS), tables=[TABLE]
        ),
    )

    def extract(data: bytes, name: str = "") -> ExtractionResult:
        state.calls += 1
        return state.result

    monkeypatch.setattr(pdf_extraction, "extract_pdf", extract)
    return state


@pytest.fixture
def derived(monkeypatch, objects):
    """derived_artifacts with its storage bindings pointed at the fake bucket.

    The `objects` fixture patches the object_store module attributes; these
    lines cover any function derived_artifacts imported by name, same as
    conftest does for the other services.
    """
    for name in (
        "get_object",
        "put_object",
        "head_object",
        "list_objects",
        "delete_object",
        "delete_prefix",
    ):
        if hasattr(derived_artifacts, name):
            monkeypatch.setattr(derived_artifacts, name, getattr(objects, name, None) or getattr(derived_artifacts, name))
    return derived_artifacts


async def _saved(objects, derived, key: str) -> ExtractionResult:
    """Store the canonical extraction for `key`, as an index run would."""
    source = await objects.head_object(key)
    result = ExtractionResult(text=CANON_TEXT, pages=list(PAGE_SPANS), tables=[TABLE])
    await derived.save(source, result)
    return result


# --- The format is accepted ---------------------------------------------------


def test_pdf_is_a_supported_extension():
    assert text_extraction.is_supported("any/folder/report.pdf")
    assert ".pdf" in text_extraction.SUPPORTED_EXTENSIONS


def test_a_pdf_upload_passes_validation():
    uploads.validate("report.pdf", b"%PDF-1.7 x")


def test_content_type_follows_the_extension():
    """A stored PDF must not be tagged text/plain, or a browser will
    download garbage and the bucket lies about what it holds."""
    assert uploads.content_type_for("a.pdf") == "application/pdf"
    assert uploads.content_type_for("a.md").startswith("text/markdown")
    assert uploads.content_type_for("a.txt").startswith("text/plain")


# --- Derived artifacts are private plumbing -----------------------------------


def test_nothing_can_be_uploaded_into_the_derived_area():
    with pytest.raises(uploads.UploadRejected):
        uploads.normalise_key("document.md", prefix="derived/abc123")


async def test_derived_artifacts_never_appear_as_sources(client, lab, objects, pdf_key, derived):
    await _saved(objects, derived, pdf_key)

    response = await client.get("/sources")

    assert response.status_code == 200
    listed = {status["source_key"] for status in response.json()}
    assert pdf_key in listed
    assert not any(key.startswith(provenance.DERIVED_PREFIX) for key in listed)


# --- The derived artifact lifecycle -------------------------------------------


async def test_a_saved_extraction_reads_back_whole(objects, pdf_key, derived):
    saved = await _saved(objects, derived, pdf_key)

    loaded = await derived.load_extraction(pdf_key)

    assert loaded is not None
    assert loaded.text == saved.text
    assert loaded.pages == saved.pages
    assert [table.table_id for table in loaded.tables] == ["table-001"]
    # The markdown artifact is the same canonical string, page markers and all.
    assert await derived.load_markdown(pdf_key) == CANON_TEXT
    assert "<!-- page 2 -->" in CANON_TEXT


async def test_a_stale_extraction_is_treated_as_absent(objects, pdf_key, derived):
    """The artifact is a snapshot of one version of the file.  When the file
    changes underneath it, pretending the snapshot is current would serve
    stale text to preview and golden generation — absent is honest."""
    await _saved(objects, derived, pdf_key)
    objects.put(pdf_key, b"%PDF-1.7 replaced bytes, new etag")

    assert await derived.load_extraction(pdf_key) is None


async def test_table_artifacts_save_and_read_back(objects, pdf_key, derived):
    await _saved(objects, derived, pdf_key)
    document_id = provenance.document_id_for(pdf_key)

    tables = await derived.list_tables(document_id)
    assert [table.table_id for table in tables] == ["table-001"]

    table = await derived.get_table(document_id, "table-001")
    assert table.markdown == TABLE.markdown
    assert table.page == 2
    assert table.caption == "Revenue by region"


async def test_deleting_a_source_takes_its_derived_artifacts_with_it(
    lab, objects, pdf_key, derived
):
    await _saved(objects, derived, pdf_key)
    document_id = provenance.document_id_for(pdf_key)

    await deletion.delete_source(pdf_key)

    leftover = [
        key
        for key in objects.objects
        if key.startswith(f"{provenance.DERIVED_PREFIX}{document_id}/")
    ]
    assert leftover == []


def test_table_links_are_spelled_only_by_provenance():
    """The link format is provenance's to own; the frontend regex and the
    describer both build on this exact shape."""
    document_id = provenance.document_id_for(PDF_KEY)
    link = provenance.table_link_for(document_id, "table-001", "Revenue by region")
    assert link == f"[Revenue by region](table://{document_id}/table-001)"


# --- Pages ride along with chunks ----------------------------------------------


async def _cut(pages):
    config = ChunkingConfig(
        strategy=ChunkStrategy.FIXED, chunk_size=64, chunk_overlap=0
    )
    return await chunker.chunk_document(PDF_KEY, CANON_TEXT, config, pages=pages)


async def test_chunks_carry_the_pages_they_came_from():
    chunks = await _cut(PAGE_SPANS)
    boundary = PAGE_SPANS[0].end_offset

    assert len(chunks) > 1
    for chunk in chunks:
        if chunk.start_offset < boundary:
            assert chunk.page_start == 1
        if chunk.end_offset > boundary:
            assert chunk.page_end == 2
        assert chunk.page_start <= chunk.page_end


async def test_text_files_carry_no_pages():
    """Absence, not zero: a page number on a .txt chunk would be an invention."""
    chunks = await _cut(None)
    assert all(chunk.page_start is None and chunk.page_end is None for chunk in chunks)


def test_page_metadata_is_written_only_when_known(objects, pdf_key):
    """Pinecone rejects nulls, so the pageless case omits the keys outright."""
    source = SimpleNamespace(
        key=PDF_KEY, etag="abc", last_modified=__import__("datetime").datetime.now()
    )

    with_pages = provenance.build_metadata(
        source, 0, "text", chunk_total=1, page_start=1, page_end=2
    )
    assert with_pages[provenance.METADATA_PAGE_START] == 1
    assert with_pages[provenance.METADATA_PAGE_END] == 2

    without = provenance.build_metadata(source, 0, "text", chunk_total=1)
    assert provenance.METADATA_PAGE_START not in without
    assert provenance.METADATA_PAGE_END not in without


# --- The pipeline end to end ----------------------------------------------------


async def test_indexing_a_pdf_stores_the_markdown_and_pages(
    lab, objects, pdf_key, derived, fake_extraction, index_variant
):
    run = await index_variant(pdf_key)

    assert run.state.value == "completed"
    assert fake_extraction.calls == 1
    # The normalised markdown is now durable, beside the original.
    assert await derived.load_markdown(pdf_key) == CANON_TEXT

    # And the vectors carry their pages out the other side.
    from app.services import index_catalog

    chunks = await index_catalog.get_chunks(pdf_key)
    assert chunks
    assert chunks[0].page_start == 1
    assert any(chunk.page_end == 2 for chunk in chunks)


async def test_an_unchanged_pdf_is_never_extracted_twice(
    lab, objects, pdf_key, derived, fake_extraction, index_variant
):
    """Extraction is the minutes-long step, and the stored artifact is the
    record that it already happened — even a forced re-embed reuses it."""
    await index_variant(pdf_key)
    await index_variant(pdf_key, force=True)

    assert fake_extraction.calls == 1


async def test_a_changed_pdf_is_extracted_again(
    lab, objects, pdf_key, derived, fake_extraction, index_variant
):
    await index_variant(pdf_key)
    objects.put(pdf_key, b"%PDF-1.7 v2, different etag")

    await index_variant(pdf_key, force=True)

    assert fake_extraction.calls == 2


async def test_an_unindexed_pdf_still_counts_as_indexable(lab, objects, pdf_key):
    """A .pdf must not fall into UNSUPPORTED — that state is for formats the
    pipeline cannot read at all."""
    status = await sync_status.get_status(pdf_key)
    assert status.state != IndexState.UNSUPPORTED


# --- Read-back goes through the artifact, never the PDF -------------------------


async def test_previewing_an_unindexed_pdf_says_index_it_first(
    client, lab, objects, pdf_key, fake_extraction
):
    """Preview is a synchronous endpoint; a minutes-long OCR run has no place
    inside it.  409 tells the client the file exists but its text does not yet."""
    response = await client.post(
        "/chunking/preview",
        json={
            "source_key": pdf_key,
            "config": {"strategy": "fixed", "chunk_size": 512, "chunk_overlap": 64},
        },
    )

    assert response.status_code == 409
    assert fake_extraction.calls == 0


async def test_preview_of_an_indexed_pdf_reads_the_stored_markdown(
    client, lab, objects, pdf_key, derived, fake_extraction, index_variant
):
    await index_variant(pdf_key)

    response = await client.post(
        "/chunking/preview",
        json={
            "source_key": pdf_key,
            "config": {"strategy": "fixed", "chunk_size": 512, "chunk_overlap": 64},
        },
    )

    assert response.status_code == 200
    assert response.json()["stats"]["chunk_count"] > 0
    # The whole point of the artifact: the expensive step ran once, at index
    # time, and preview never triggers it again.
    assert fake_extraction.calls == 1
