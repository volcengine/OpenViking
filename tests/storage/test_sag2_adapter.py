# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.storage.collection_schemas import CollectionSchemas
from openviking.storage.errors import CollectionNotFoundError
from openviking.storage.expr import Eq, Or, PathScope
from openviking.storage.vectordb_adapters.base import VIKINGDB_TEXT_FIELD_BYTE_LIMIT
from openviking.storage.vectordb_adapters.sag2_adapter import IndexChanged, Sag2CollectionAdapter
from openviking_cli.utils.config.vectordb_config import VectorDBBackendConfig


def _adapter(tmp_path):
    config = VectorDBBackendConfig(backend="sag2", path=str(tmp_path))
    adapter = Sag2CollectionAdapter.from_config(config)
    assert adapter.create_collection(
        "context",
        CollectionSchemas.context_collection("context", 0, requires_embedding=False),
        distance="cosine",
        sparse_weight=0,
        index_name="default",
    )
    return adapter


def _record(record_id, account="acct", uri=None, text="alpha bridge", **kwargs):
    return {
        "id": record_id,
        "account_id": account,
        "uri": uri or f"viking://resources/{record_id}",
        "context_type": "resource",
        "level": 0,
        "name": record_id,
        "abstract": text,
        "search_tags": ["kind=test"],
        **kwargs,
    }


def test_sag2_crud_lexical_filters_and_epoch(tmp_path):
    adapter = _adapter(tmp_path)
    adapter.upsert(
        [
            _record("a", acl_direct_grants=["1:user:alice"], acl_inherited_grants=["3:group:dev"]),
            _record("b", text="unrelated"),
            _record("secret", account="other", text="alpha bridge"),
        ]
    )
    assert adapter.current_epoch("acct") == 1
    assert adapter.current_epoch("other") == 1
    scope = Eq("account_id", "acct")
    hits = adapter.search_by_keywords(query="alpha", filter=scope, limit=10)
    assert [hit["id"] for hit in hits] == ["a"]
    assert hits[0]["_score"] > 0
    assert hits[0]["acl_direct_grants"] == ["1:user:alice"]
    assert adapter.count(Eq("acl_direct_grants", "1:user:alice")) == 1
    assert adapter.count(Eq("acl_inherited_grants", "3:group:dev")) == 1
    assert "vector" not in hits[0]

    # Every scalar operation works without a vector dimension or random vector.
    assert adapter.count(scope) == 2
    assert adapter.strict_count(scope) == 2
    scalar = adapter.query(filter=PathScope("uri", "viking://resources", -1), limit=10)
    assert {item["id"] for item in scalar} == {"a", "b", "secret"}
    assert adapter.query(filter=Or([]), limit=10) == []
    with pytest.raises(ValueError, match="does not accept"):
        adapter.query(query_vector=[0.1], limit=1)

    adapter.update_data([{"id": "a", "abstract": "changed gamma"}])
    assert adapter.current_epoch("acct") == 2
    assert adapter.current_epoch("other") == 1
    assert adapter.search_by_keywords(query="alpha", filter=scope, limit=10) == []
    assert [x["id"] for x in adapter.search_by_keywords(query="gamma", filter=scope, limit=10)] == [
        "a"
    ]
    with pytest.raises(IndexChanged):
        adapter.check_epoch("acct", 1)

    assert adapter.delete(ids=["a"]) == 1
    assert adapter.current_epoch("acct") == 3
    assert adapter.get(["a"]) == []

    with pytest.raises(CollectionNotFoundError, match="not found"):
        adapter.update_data(
            [{"id": "b", "abstract": "must roll back"}, {"id": "missing", "abstract": "no create"}]
        )
    assert adapter.get(["b"])[0]["abstract"] == "unrelated"
    assert adapter.get(["missing"]) == []
    assert adapter.count(scope) == 1
    assert adapter.current_epoch("acct") == 3
    assert adapter.delete(filter=scope) == 1
    assert adapter.count(scope) == 0
    adapter.close()


@pytest.mark.parametrize("field", ["content", "abstract"])
@pytest.mark.parametrize("as_bytes", [False, True])
def test_sag2_rejects_oversized_evidence_without_partial_write(tmp_path, field, as_bytes):
    adapter = _adapter(tmp_path)
    record = _record("oversized", text="safe")
    value = "x" * (VIKINGDB_TEXT_FIELD_BYTE_LIMIT + 1)
    record[field] = value.encode() if as_bytes else value
    with pytest.raises(ValueError, match="refusing to truncate"):
        adapter.upsert(record)
    assert adapter.count() == 0
    assert adapter.current_epoch("acct") == 0
    adapter.close()


def test_sag2_rejects_invalid_utf8_evidence(tmp_path):
    adapter = _adapter(tmp_path)
    record = _record("invalid-utf8", text="safe")
    record["content"] = b"\xff" * 1024
    with pytest.raises(ValueError, match="valid UTF-8"):
        adapter.upsert(record)
    assert adapter.count() == 0
    assert adapter.current_epoch("acct") == 0
    adapter.close()


def test_sag2_chinese_fts_injection_and_provenance(tmp_path):
    adapter = _adapter(tmp_path)
    adapter.upsert(_record("zh", text="开放维京 支持 中文检索", name="开放维京"))
    for query in ("开放维京", '" OR NEAR/1(', "NUL\0AND"):
        # Malformed user text is tokenized/quoted, never interpreted as FTS syntax.
        adapter.search_by_keywords(query=query, filter=Eq("account_id", "acct"), limit=10)
    assert adapter.search_by_keywords(query="中文检索", limit=10)[0]["id"] == "zh"
    adapter.close()

    reopened = Sag2CollectionAdapter.from_config(
        VectorDBBackendConfig(backend="sag2", path=str(tmp_path))
    )
    assert reopened.collection_exists()
    assert reopened.count() == 1
    reopened.close()


def test_sag2_config_budget_defaults(tmp_path):
    config = VectorDBBackendConfig(
        backend="sag2", path=str(tmp_path), sag2={"candidate_k": 100, "max_hops": 2}
    )
    assert config.sag2.max_judgments == 330
    assert VectorDBBackendConfig(backend="sag2", path=str(tmp_path)).sag2.max_judgments == 115
    with pytest.raises(ValueError, match="requires 'sag2' config"):
        VectorDBBackendConfig(backend="sag2", path=str(tmp_path), sag2=None)
    with pytest.raises(ValueError, match="protected_k"):
        VectorDBBackendConfig(
            backend="sag2", path=str(tmp_path), sag2={"candidate_k": 2, "protected_k": 5}
        )
