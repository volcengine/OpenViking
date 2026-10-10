# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

import copy

import pytest

from openviking.storage.vectordb import qdrant_sparse
from openviking.storage.vectordb.collection import qdrant_rest
from openviking.storage.vectordb.qdrant_utils import to_qdrant_point_id


def test_colliding_terms_address_the_same_owner_point() -> None:
    assert qdrant_sparse.stable_sparse_index("69235") == 1092531686
    assert qdrant_sparse.stable_sparse_index("95303") == 1092531686
    assert qdrant_sparse.sparse_owner_point_id(1092531686) == to_qdrant_point_id(
        "openviking:sparse-index:1092531686"
    )


@pytest.mark.parametrize(
    "prefix", ["openviking:sparse:69235", "openviking:sparse-index:1092531686"]
)
def test_sparse_binding_accepts_legacy_and_owner_ids(prefix: str) -> None:
    point = {
        "id": to_qdrant_point_id(prefix),
        "payload": {"_openviking_sparse_term": True, "term": "69235", "index": 1092531686},
    }
    assert qdrant_sparse.parse_sparse_point(point) == ("69235", 1092531686)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("_openviking_sparse_term", False),
        ("term", ""),
        ("term", 69235),
        ("index", True),
        ("index", "1092531686"),
        ("index", 1),
        ("index", 0x80000000),
    ],
)
def test_sparse_binding_rejects_corrupt_payload(field, value) -> None:
    point = {
        "id": to_qdrant_point_id("openviking:sparse:69235"),
        "payload": {"_openviking_sparse_term": True, "term": "69235", "index": 1092531686},
    }
    point["payload"][field] = value
    with pytest.raises(ValueError):
        qdrant_sparse.parse_sparse_point(point)


def test_sparse_binding_rejects_noncanonical_point_id() -> None:
    with pytest.raises(ValueError, match="point"):
        qdrant_sparse.parse_sparse_point(
            {
                "id": to_qdrant_point_id("unrelated"),
                "payload": {
                    "_openviking_sparse_term": True,
                    "term": "69235",
                    "index": 1092531686,
                },
            }
        )


@pytest.mark.parametrize("version", ["1.16.0", "v1.16.1+build.2", "1.19.0", "2.0.0"])
def test_qdrant_version_accepts_stable_conditional_owner_servers(version: str) -> None:
    qdrant_rest.validate_qdrant_version(version)


@pytest.mark.parametrize("version", [None, 1.16, "1.10.0", "1.15.9", "1.16.0-rc.1", "dev"])
def test_qdrant_version_rejects_missing_unsupported_or_unstable_servers(version) -> None:
    with pytest.raises(qdrant_rest.QdrantError, match="version"):
        qdrant_rest.validate_qdrant_version(version)


def test_qdrant_version_gate_caches_only_success() -> None:
    client = qdrant_rest.QdrantRestClient("http://qdrant.test")
    replies = [{"version": "1.15.9"}, {"version": "1.16.0"}]
    requests = []

    def request(method, path):
        requests.append((method, path))
        return copy.deepcopy(replies.pop(0))

    client.request = request
    with pytest.raises(qdrant_rest.QdrantError, match="1.16.0"):
        client.ensure_supported_version()
    client.ensure_supported_version()
    client.ensure_supported_version()
    assert requests == [("GET", "/"), ("GET", "/")]


def test_registration_does_not_succeed_without_a_visible_owner() -> None:
    dictionary = qdrant_sparse.SparseTermDictionary(
        resolve_term=lambda term: None,
        resolve_index=lambda index: None,
        persist=lambda term, index: None,
    )
    with pytest.raises(ValueError, match="owner"):
        dictionary.index_for("69235")
