# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression coverage for canonical options and distributed capability enforcement."""

from unittest.mock import Mock, patch

import pytest
from pydantic import ValidationError

from openviking.storage.vectordb_adapters.opengauss.collection import OpenGaussCollection
from openviking.storage.vectordb_adapters.opengauss.metadata import migrate_persisted_index_meta
from openviking.storage.vectordb_adapters.opengauss_adapter import OpenGaussCollectionAdapter
from openviking_cli.utils.config.vectordb_config import OpenGaussConfig, VectorDBBackendConfig


@pytest.mark.parametrize("flag", ["enable_pq", "enable_rabitq"])
def test_distributed_flags_rejected_before_connection_or_ddl(flag):
    with patch.object(OpenGaussCollectionAdapter, "_connect") as connect:
        with pytest.raises(ValidationError, match="plain HNSW"):
            VectorDBBackendConfig(
                backend="opengauss",
                dimension=512,
                opengauss={
                    "mode": "distributed",
                    "index_type": "hnsw",
                    "build_params": {flag: True},
                },
            )
    connect.assert_not_called()
    coll = OpenGaussCollection.__new__(OpenGaussCollection)
    coll._distributed = True
    coll._distance = "cosine"
    coll._name = "context"
    coll._execute = Mock()
    coll._materialize_index = Mock()
    with pytest.raises(ValueError, match="plain HNSW"):
        coll.create_index(
            "default", {"VectorIndex": {"IndexType": "hnsw"}, "build_params": {flag: True}}
        )
    coll._execute.assert_not_called()
    coll._materialize_index.assert_not_called()


@pytest.mark.parametrize(
    "flag,expected", [("enable_pq", "hnsw-pq"), ("enable_rabitq", "hnsw-rabitq")]
)
def test_canonical_metadata_has_no_duplicate_options(flag, expected):
    cfg = VectorDBBackendConfig(
        backend="opengauss", opengauss={"build_params": {flag: True, "m": 16}}
    )
    with patch.object(OpenGaussCollectionAdapter, "_connect"):
        adapter = OpenGaussCollectionAdapter.from_config(cfg)
    meta = adapter._build_default_index_meta(
        index_name="default",
        distance="cosine",
        use_sparse=False,
        sparse_weight=0,
        scalar_index_fields=[],
    )
    assert meta["VectorIndex"]["IndexType"] == expected
    assert meta["build_params"] == {"m": 16}
    assert "m" not in meta and flag not in meta
    coll = OpenGaussCollection.__new__(OpenGaussCollection)
    coll._name, coll._distance, coll._distributed = "context", "cosine", False
    normalized = coll._normalized_index_meta("default", meta)
    assert flag not in normalized["build_params"]
    assert f"{flag} = on" in coll._create_index_sql(normalized)


def test_alias_conflicts_are_rejected_and_aliases_normalized():
    cfg = OpenGaussConfig(search_params={"hnsw_ef_search": 40})
    assert cfg.search_params == {"ef_search": 40}
    with pytest.raises(ValueError, match="Conflicting"):
        OpenGaussConfig(search_params={"hnsw_ef_search": 40, "ef_search": 80})


def test_persisted_aliases_are_consumed_only_at_read_boundary():
    old = {
        "VectorIndex": {"IndexType": "hnsw-pq"},
        "m": 16,
        "enable_pq": True,
        "build_params": {"m": 32, "enable_pq": True, "parallel_workers": 4},
        "ef_search": 40,
    }
    converted = migrate_persisted_index_meta(old)
    assert converted["build_params"]["m"] == 32
    assert converted["parallel_workers"] == 4
    assert "m" not in converted and "enable_pq" not in converted
    assert "parallel_workers" not in converted["build_params"]
    coll = OpenGaussCollection.__new__(OpenGaussCollection)
    coll._name, coll._distance, coll._distributed = "context", "cosine", False
    with pytest.raises(ValueError, match="must be nested"):
        coll.create_index("default", old)
    normalized = coll._normalized_index_meta("default", converted)
    assert normalized["build_params"] == {"m": 32}
    assert normalized["search_params"] == {"ef_search": 40}
    assert old["m"] == 16


def test_adapter_copies_validated_config_without_reconstructing_it():
    config = VectorDBBackendConfig(backend="opengauss", opengauss={"build_params": {"m": 16}})
    with (
        patch.object(OpenGaussCollectionAdapter, "_connect"),
        patch.object(
            OpenGaussConfig,
            "__init__",
            side_effect=AssertionError("must not revalidate complete config"),
        ),
    ):
        adapter = OpenGaussCollectionAdapter.from_config(config)
    config.opengauss.build_params["m"] = 32
    assert adapter._build_params["m"] == 16


@pytest.mark.parametrize(
    "params", [{"m": "16; DROP TABLE context"}, {"m": True}, {"m": float("nan")}, {"untrusted": 1}]
)
def test_raw_index_options_remain_validated(params):
    with pytest.raises(ValueError):
        OpenGaussConfig(build_params=params)


@pytest.mark.parametrize("ids", [[], ["one", "two"]])
def test_delete_ids_matches_upstream_contract(ids):
    with patch.object(OpenGaussCollectionAdapter, "_connect"):
        adapter = OpenGaussCollectionAdapter("context", OpenGaussConfig())
    collection = Mock()
    adapter.get_collection = Mock(return_value=collection)
    adapter._conn = Mock()
    assert adapter.delete(ids=ids, filter={"op": "must", "field": "id", "conds": ["other"]}) == len(
        ids
    )
    adapter._conn.cursor.assert_not_called()
    if ids:
        collection.delete_data.assert_called_once_with(ids)
    else:
        collection.delete_data.assert_not_called()


def test_filter_delete_has_no_implicit_row_limit():
    with patch.object(OpenGaussCollectionAdapter, "_connect"):
        adapter = OpenGaussCollectionAdapter("context", OpenGaussConfig())
    collection = Mock()
    collection.get_meta_data.return_value = {"Fields": []}
    adapter.get_collection = Mock(return_value=collection)
    adapter._conn = Mock()
    cursor = adapter._conn.cursor.return_value
    cursor.rowcount = 100001
    assert (
        adapter.delete(filter={"op": "must", "field": "account_id", "conds": ["tenant"]}) == 100001
    )
    assert cursor.execute.call_args.args[1] == ["tenant", None]
    adapter._conn.commit.assert_called_once_with()
