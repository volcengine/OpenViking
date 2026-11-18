"""Real backend regressions for bounded PostgreSQL multi-row upsert."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.storage.test_pgvector_adapter import adapter as adapter


class CountingCursor:
    def __init__(self, cursor, statements):
        self.cursor, self.statements = cursor, statements

    def __getattr__(self, name):
        return getattr(self.cursor, name)

    def execute(self, query, *args, **kwargs):
        query_text = query.decode() if isinstance(query, bytes) else str(query)
        if (
            query_text.lstrip()
            .upper()
            .startswith(("UPDATE ", "INSERT ", "SAVEPOINT ", "RELEASE SAVEPOINT "))
        ):
            self.statements.append(query_text.split()[0].upper())
        return self.cursor.execute(query, *args, **kwargs)


def test_uniform_batch_uses_bounded_multirow_statements(adapter, monkeypatch):
    collection = adapter.get_collection()._Collection__collection
    statements = []
    cursor = collection._cursor
    monkeypatch.setattr(collection, "_cursor", lambda: CountingCursor(cursor(), statements))
    adapter.upsert([{"id": f"row-{i}", "vector": [1, 0, 0], "content": str(i)} for i in range(251)])
    # Sparse posting maintenance remains in the same transaction; only count
    # the base-table insert rather than its existing metadata/posting writes.
    assert statements.count("SAVEPOINT") == 0
    assert statements.count("RELEASE") == 0
    assert len(adapter.get([f"row-{i}" for i in range(251)])) == 251
    assert statements.count("INSERT") <= 5


def test_repeated_ids_and_mixed_shapes_preserve_input_order_and_patch_semantics(adapter):
    adapter.upsert({"id": "a", "vector": [1, 0, 0], "content": "original", "search_tags": ["old"]})
    adapter.upsert(
        [
            {"id": "a", "content": "first"},
            {"id": "b", "content": "another"},
            {"id": "a", "content": "last"},
            {"id": "a", "search_tags": ["new"]},
            {"id": "b"},
        ]
    )
    rows = {row["id"]: row for row in adapter.get(["a", "b"])}
    assert rows["a"]["content"] == "last"
    assert rows["a"]["search_tags"] == ["new"]
    assert rows["a"]["vector"] == [1.0, 0.0, 0.0]
    assert rows["b"]["content"] == "another"


def test_late_page_failure_rolls_back_all_rows_and_postings(adapter):
    from psycopg2.errors import StringDataRightTruncation

    batch = [
        {"id": f"row-{i}", "vector": [1, 0, 0], "sparse_vector": {"term": 1}} for i in range(120)
    ]
    batch.append({"id": "x" * 257, "vector": [1, 0, 0], "sparse_vector": {"term": 1}})
    with pytest.raises(StringDataRightTruncation):
        adapter.upsert(batch)
    assert adapter.count() == 0
    adapter.upsert({"id": "after", "vector": [1, 0, 0], "sparse_vector": {"term": 1}})
    assert [row["id"] for row in adapter.query(sparse_query_vector={"term": 1}, limit=10)] == [
        "after"
    ]


def test_concurrent_independent_adapters_preserve_rows_and_matching_sparse_postings(adapter):
    from openviking.storage.vectordb_adapters.factory import create_collection_adapter
    from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig

    config = VectorDBBackendConfig(
        backend="pgvector",
        name=adapter.collection_name,
        dimension=3,
        sparse_weight=0.5,
        pgvector=adapter._config,
    )
    writers = [create_collection_adapter(config) for _ in range(4)]

    def write(index):
        writers[index].upsert(
            [
                {
                    "id": f"row-{i}",
                    "vector": [1, 0, 0],
                    "content": str(index),
                    "sparse_vector": {str(index): 1},
                }
                for i in range(20)
            ]
        )

    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(write, range(4)))
        assert adapter.count() == 20
        rows = adapter.get([f"row-{i}" for i in range(20)])
        for row in rows:
            assert row["content"] in {str(i) for i in range(4)}
            assert row["sparse_vector"] == {row["content"]: 1.0}
        # Read through the ranked entrypoint, not just the stored JSONB column.
        found = set()
        for index in range(4):
            results = adapter.query(sparse_query_vector={str(index): 1}, limit=50)
            found.update(row["id"] for row in results)
            assert all(row["content"] == str(index) for row in results)
        assert found == {f"row-{i}" for i in range(20)}
    finally:
        for writer in writers:
            writer.close()


def test_sparse_maintenance_failure_rolls_back_base_batch(adapter, monkeypatch):
    collection = adapter.get_collection()._Collection__collection
    adapter.upsert({"id": "existing", "content": "original", "sparse_vector": {"old": 1}})

    def fail(*args):
        raise RuntimeError("synthetic failure after base writes")

    monkeypatch.setattr(collection, "_sync_sparse_postings_on_cursor", fail)
    with pytest.raises(RuntimeError, match="synthetic failure"):
        adapter.upsert(
            [
                {"id": "existing", "content": "changed", "sparse_vector": {"new": 1}},
                {"id": "new", "content": "not committed", "sparse_vector": {"new": 1}},
            ]
        )
    assert adapter.count() == 1
    assert adapter.get(["existing"])[0]["content"] == "original"
    assert [row["id"] for row in adapter.query(sparse_query_vector={"old": 1}, limit=10)] == [
        "existing"
    ]
    assert adapter.query(sparse_query_vector={"new": 1}, limit=10) == []


def test_unicode_quoted_and_null_values_keep_driver_parameter_binding(adapter):
    text = "Synthetic user's text: '); SELECT 1; --\nUnicode 样本 🧪"
    adapter.upsert(
        [
            {"id": "quoted", "content": text, "search_tags": ["a'b", "样本"]},
            {"id": "ordinary", "content": "other", "search_tags": None},
        ]
    )
    rows = {r["id"]: r for r in adapter.get(["quoted", "ordinary"])}
    assert rows["quoted"]["content"] == text
    assert rows["quoted"]["search_tags"] == ["a'b", "样本"]
    assert rows["ordinary"]["search_tags"] is None
