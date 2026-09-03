"""What the app does when an index it names is not there.

Two situations, and they used to be the same crash. A fresh account has no
index until something writes one. And once production became a pointer, the
original index could be retired outright — which is exactly what happened here,
and it took `GET /sources` down with a 401 from a handle whose host no longer
resolved.

Both are ordinary states, not faults: an index that does not exist holds no
vectors, and that is a complete answer. The rule these tests hold in place is
that reading is allowed to come back empty, and is never allowed to bring an
index into existence as a side effect of being asked — a read that provisioned
would quietly resurrect an index somebody had just deleted.
"""

import httpx
import pytest
from pinecone.exceptions import NotFoundException

from app.config import settings
from app.main import app
from app.services import answer_space, retrieval, vector_store


@pytest.fixture
def retired(monkeypatch):
    """A world where the original production index no longer exists.

    Only the lab index resolves, and only through the probe. The provisioning
    handle fails outright, which is what turns "a read created an index" from a
    silent surprise into a failed test.
    """
    lab: dict = {}

    def provision(index_name: str = ""):
        pytest.fail(f"a read provisioned the index {index_name!r}")

    def probe(index_name: str = ""):
        if index_name != settings.pinecone_lab_index_name:
            raise NotFoundException(f"no index named {index_name!r}")
        return lab.setdefault(index_name, _FakeLab())

    monkeypatch.setattr(
        vector_store.PineconeManager, "get_index", staticmethod(provision)
    )
    monkeypatch.setattr(
        vector_store.PineconeManager, "get_probe_index", staticmethod(probe)
    )

    return lab


class _FakeLab:
    """The lab index, holding nothing. Enough surface for a read to run."""

    def list(self, prefix: str = "", namespace: str = ""):
        return iter(())

    def fetch(self, ids: list[str], namespace: str = ""):
        return type("Fetched", (), {"vectors": {}})()

    def query(self, **kwargs):
        return type("Queried", (), {"matches": []})()

    def describe_index_stats(self) -> dict:
        return {"namespaces": {}, "total_vector_count": 0}


def test_listing_a_missing_index_reads_as_empty(retired):
    """Not an exception, and not a newly created index either."""
    assert vector_store.list_vector_ids("") == []


def test_fetching_from_a_missing_index_reads_as_empty(retired):
    """The ids cannot exist if the index does not."""
    assert vector_store.fetch_vectors(["anything#00000"]) == {}


def test_querying_a_missing_index_returns_no_matches(retired):
    """Reported as no context rather than as a failed request.

    The chat stream has already sent its status code by the time retrieval
    runs, so an exception here could only surface as an event the client treats
    as best-effort.
    """
    assert vector_store.query_similar([0.0] * 8, top_k=3) == []


def test_deleting_from_a_missing_index_removes_nothing(retired):
    """And does not create an index in order to have something to delete."""
    assert vector_store.delete_vectors(["anything#00000"]) == 0


async def test_retrieval_answers_nothing_rather_than_failing(retired, embeddings):
    """The whole point of the guards: a retired index degrades, not crashes."""
    result = await retrieval.retrieve("anything at all", top_k=3)

    assert result.chunks == []


async def test_production_pointed_at_a_retired_index_reports_missing(retired):
    """The default pointer, once the index it names has been deleted."""
    space = await answer_space.describe()

    assert space.variant_id == ""
    assert space.state.value == "missing"
    assert space.vector_count == 0


async def test_pointing_back_at_a_retired_index_is_refused(retired):
    """It cannot answer anything, so adopting it would be adopting silence."""
    with pytest.raises(answer_space.UnusableSpace):
        await answer_space.point_at("")


async def test_the_sources_listing_still_renders(retired, objects, report_key):
    """The regression this file is named for: a 500 on the main screen."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.get("/sources?refresh=true")

    assert response.status_code == 200
    listed = response.json()

    assert [row["source_key"] for row in listed] == [report_key]
    assert listed[0]["state"] == "not_indexed"
    assert listed[0]["variants"] == []
