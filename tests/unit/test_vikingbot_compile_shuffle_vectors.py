from collections import Counter
from types import SimpleNamespace

from vikingbot.compile.models import CompileLimits
from vikingbot.compile.ops.shuffle import Shuffle
from vikingbot.compile.pipeline_io import MAX_JOB_ATTEMPTS
from vikingbot.compile.plan import Routing
from vikingbot.compile.results import Record


class Files:
    def __init__(self):
        self.items = {}

    async def get(self, key):
        return self.items.get(key)

    async def put(self, key, value):
        self.items[key] = value


class Embeddings:
    """Fail chosen texts a given number of times, as a rate-limited provider would."""

    def __init__(self, failures):
        self.failures = dict(failures)
        self.batches = []

    async def compile_embeddings(self, batch, target_uri, expected_model=None):
        if not batch:
            return {"model": "embed-test", "vectors": []}
        self.batches.append(list(batch))
        for text in batch:
            if self.failures.get(text, 0):
                self.failures[text] -= 1
                raise RuntimeError("HTTP 429: requests per second limit exceeded")
        return {"model": "embed-test", "vectors": [[float(len(text))] for text in batch]}


def _runtime(client):
    return SimpleNamespace(
        client=client,
        target="viking://resources/out",
        files=Files(),
        limits=CompileLimits(),
        metrics=Counter(),
        failures=[],
        status={},
        contract=SimpleNamespace(route=Routing(instructions="group related records")),
    )


def _record(index, *, text=None):
    return Record(
        record_id=f"r{index}",
        payload_ref=f"p{index}",
        source_refs=[],
        routing_text=text or f"record text {index}",
        scope={},
        parents=[],
    )


def _text(record):
    return f"{record.routing_text} {{}}"


async def test_vectors_keep_successful_batches_and_retry_failed_ones():
    records = [_record(i) for i in range(40)]
    flaky, broken = _text(records[0]), _text(records[39])
    client = Embeddings({flaky: 1, broken: MAX_JOB_ATTEMPTS})
    runtime = _runtime(client)

    vectors = await Shuffle(runtime).vectors(records)

    # The first batch recovers on retry; the second keeps failing and only its records lose vectors.
    assert all(vector is not None for vector in vectors[:32])
    assert vectors[32:] == [None] * 8
    # Both batches retry once, then only the failing batch retries until the budget ends.
    assert runtime.metrics["embedding_batch_retries"] == 2 + (MAX_JOB_ATTEMPTS - 2)
    assert len(runtime.failures) == 1
    assert runtime.failures[0].startswith("8 records have no routing vector after 1 embedding")
    assert "HTTP 429" in runtime.failures[0]


async def test_shuffle_excludes_records_without_vectors_instead_of_failing_the_task():
    records = [_record(i) for i in range(3)]
    client = Embeddings({_text(record): MAX_JOB_ATTEMPTS for record in records})
    runtime = _runtime(client)
    node = SimpleNamespace(name="shuffle", task="route", against_target=False)

    groups = await Shuffle(runtime).run(node, records)

    assert groups == []
    assert runtime.status == {record.record_id: "failed" for record in records}
    assert runtime.metrics["route_blocked_records"] == 3
    assert len(runtime.failures) == 1
