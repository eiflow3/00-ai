"""The table describer — prose in, grid out, page spans never lied about.

The LLM is a vendor, so it is faked at the adapter seam (`get_adapter`), the
same way R2, Pinecone and the embeddings are faked in conftest.  What runs for
real is everything this module owns: locating a table in the document text,
splicing the description over it, keeping the page spans true to the final
string, and degrading per table instead of failing the document.
"""

import pytest

from app.schemas.extraction import ExtractedTable, ExtractionResult, PageSpan
from app.schemas.prompt import PromptId
from app.services import provenance, table_describer

SOURCE_KEY = "reports/tables.pdf"
DOCUMENT_ID = provenance.document_id_for(SOURCE_KEY)

TABLE_MD = "| Region | FY26 |\n| --- | --- |\n| APAC | 503.2 |"
PAGE_ONE = "# Report\n\nRevenue grew in every region this year.\n\n"
PAGE_TWO_HEAD = "## Financials\n\n"
PAGE_TWO_TAIL = "\n\nHeadcount grew alongside revenue.\n"

TEXT = PAGE_ONE + PAGE_TWO_HEAD + TABLE_MD + PAGE_TWO_TAIL
SPANS = [
    PageSpan(page=1, start_offset=0, end_offset=len(PAGE_ONE)),
    PageSpan(page=2, start_offset=len(PAGE_ONE), end_offset=len(TEXT)),
]
TABLE = ExtractedTable(
    table_id="table-001", markdown=TABLE_MD, page=2, caption="Revenue by region"
)

DESCRIPTION = "The table lists FY26 revenue by region; APAC leads at 503.2."


class _FakeAdapter:
    """Stands in for an LLM adapter: streams a canned reply, or refuses."""

    def __init__(self, reply: str = DESCRIPTION, fail: bool = False) -> None:
        self.reply = reply
        self.fail = fail
        self.calls = 0

    async def stream(self, messages, model, temperature: float = 1.0):
        self.calls += 1
        if self.fail:
            raise RuntimeError("the provider is down")
        yield self.reply


@pytest.fixture
def adapter(monkeypatch) -> _FakeAdapter:
    fake = _FakeAdapter()
    monkeypatch.setattr(table_describer, "get_adapter", lambda provider: fake)
    return fake


def _extraction() -> ExtractionResult:
    return ExtractionResult(text=TEXT, pages=list(SPANS), tables=[TABLE])


def _assert_spans_describe(text: str, pages: list[PageSpan]) -> None:
    """The module's stated invariant: spans always describe the final text."""
    assert pages[0].start_offset == 0
    assert pages[-1].end_offset == len(text)
    for before, after in zip(pages, pages[1:]):
        assert before.end_offset == after.start_offset
    for span in pages:
        assert span.start_offset <= span.end_offset


async def test_a_described_table_becomes_prose_plus_link(adapter):
    result, warnings = await table_describer.describe_tables(_extraction(), SOURCE_KEY)

    assert warnings == []
    assert adapter.calls == 1
    # The grid is gone from what will be chunked and embedded…
    assert TABLE_MD not in result.text
    # …replaced by the prose and the link a reader can follow back.
    assert DESCRIPTION in result.text
    assert (
        provenance.table_link_for(DOCUMENT_ID, "table-001", "Revenue by region")
        in result.text
    )
    _assert_spans_describe(result.text, result.pages)
    # The splice happened on page 2, so page 1 is untouched.
    assert result.pages[0] == SPANS[0]


async def test_a_failed_description_leaves_the_table_inline(monkeypatch):
    """Degrade, don't fail: the raw grid still embeds, and the warning says why."""
    fake = _FakeAdapter(fail=True)
    monkeypatch.setattr(table_describer, "get_adapter", lambda provider: fake)

    result, warnings = await table_describer.describe_tables(_extraction(), SOURCE_KEY)

    assert TABLE_MD in result.text
    assert result.text == TEXT
    assert len(warnings) == 1
    assert "table-001" in warnings[0]
    _assert_spans_describe(result.text, result.pages)


async def test_an_unlocatable_table_is_skipped_before_the_model_is_paid(adapter):
    """Locate first, describe second — a description with nowhere to go is
    money spent on an orphan."""
    unplaceable = ExtractedTable(table_id="table-001", markdown="| ghost |", page=1)
    extraction = ExtractionResult(
        text=TEXT, pages=list(SPANS), tables=[unplaceable]
    )

    result, warnings = await table_describer.describe_tables(extraction, SOURCE_KEY)

    assert adapter.calls == 0
    assert result.text == TEXT
    assert len(warnings) == 1


async def test_a_document_without_tables_costs_nothing(adapter):
    plain = ExtractionResult(text="just prose", pages=[], tables=[])

    result, warnings = await table_describer.describe_tables(plain, SOURCE_KEY)

    assert result is plain
    assert warnings == []
    assert adapter.calls == 0


async def test_the_description_prompt_renders_from_the_catalog():
    """The prompt is a registry entry like every other — editable, resettable."""
    from app.services import prompt_catalog

    template = prompt_catalog.defaults()[PromptId.TABLE_DESCRIPTION]
    rendered = prompt_catalog.render(
        template, {"table_markdown": TABLE_MD, "caption": "Revenue", "page": "2"}
    )
    assert TABLE_MD in rendered


# --- The artifacts endpoints ----------------------------------------------------


@pytest.fixture
async def client(lab):
    import httpx

    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as session:
        yield session


async def test_a_table_link_resolves_to_the_stored_table(client, objects):
    from app.services import derived_artifacts

    objects.put(SOURCE_KEY, b"%PDF-1.7 x")
    source = await objects.head_object(SOURCE_KEY)
    await derived_artifacts.save(source, _extraction())

    listing = await client.get(f"/artifacts/{DOCUMENT_ID}/tables")
    assert listing.status_code == 200
    assert [t["table_id"] for t in listing.json()["tables"]] == ["table-001"]

    response = await client.get(f"/artifacts/{DOCUMENT_ID}/tables/table-001")
    assert response.status_code == 200
    body = response.json()
    assert body["markdown"] == TABLE_MD
    assert body["page"] == 2
    assert body["caption"] == "Revenue by region"


async def test_a_missing_table_is_a_404_not_an_error(client, objects):
    response = await client.get(f"/artifacts/{DOCUMENT_ID}/tables/table-999")
    assert response.status_code == 404
