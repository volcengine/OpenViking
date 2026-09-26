# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import copy
from urllib.parse import unquote

import pytest

from openviking.storage.vectordb.qdrant_sparse import (
    sparse_owner_point_id,
    stable_sparse_index,
)
from openviking.storage.vectordb.qdrant_utils import to_qdrant_point_id
from scripts.maintenance import qdrant_sparse_upgrade as upgrade
from scripts.maintenance.qdrant_migrate import _META_MARKER_ID


class _FakeQdrant:
    def __init__(
        self,
        *,
        data_collection: str = "data",
        metadata_collection: str = "meta",
        marker: dict[str, object] | None = None,
        points: list[dict[str, object]] | None = None,
    ) -> None:
        self.data_collection = data_collection
        self.metadata_collection = metadata_collection
        marker = marker or _marker(data_collection, metadata_collection)
        self.collections = {
            data_collection: {"points": {}},
            metadata_collection: {
                "points": {
                    _META_MARKER_ID: {
                        "id": _META_MARKER_ID,
                        "payload": copy.deepcopy(marker),
                    },
                    **{
                        str(point["id"]): copy.deepcopy(point)
                        for point in points or []
                    },
                }
            },
        }
        self.requests: list[tuple[str, str, dict[str, object] | None, dict[str, object] | None]] = []
        self.ensure_supported_version_calls = 0
        self.after_put = None
        self.duplicate_owner_readback = False
        self.lost_write = False

    def ensure_supported_version(self) -> None:
        self.ensure_supported_version_calls += 1

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, object] | None = None,
        *,
        params: dict[str, object] | None = None,
    ) -> dict[str, object]:
        self.requests.append((method, path, body, params))
        parts = [unquote(part) for part in path.split("/") if part]
        collection = parts[1]
        state = self.collections[collection]
        points = state["points"]
        assert isinstance(points, dict)

        if method == "GET" and len(parts) == 2:
            return {"result": {"status": "green"}}
        if method == "POST" and parts[-1] == "count":
            assert body == {"exact": True, "filter": {}}
            assert params == {"consistency": "all"}
            return {"result": {"count": len(points)}}
        if method == "POST" and parts[-1] == "scroll":
            assert params == {"consistency": "all"}
            assert body is not None
            offset = body.get("offset")
            start = int(offset) if offset is not None else 0
            limit = int(body["limit"])
            values = list(points.values())
            page = copy.deepcopy(values[start : start + limit])
            next_offset = start + limit if start + limit < len(values) else None
            return {"result": {"points": page, "next_page_offset": next_offset}}
        if method == "POST" and parts[-1] == "points":
            assert params == {"consistency": "all"}
            assert body is not None
            result = [
                copy.deepcopy(points[str(point_id)])
                for point_id in body["ids"]
                if str(point_id) in points
            ]
            if self.duplicate_owner_readback and body["ids"] != [_META_MARKER_ID] and result:
                result.append(copy.deepcopy(result[0]))
            return {"result": result}
        if method == "PUT" and parts[-1] == "points":
            assert body is not None
            assert params == {
                "wait": "true",
                "ordering": "strong",
            }
            points_to_write = body["points"]
            assert isinstance(points_to_write, list)
            owner_ids = [str(point["id"]) for point in points_to_write]
            assert body["update_filter"] == {
                "must_not": [{"has_id": owner_ids}],
            }
            for point in points_to_write:
                point_id = str(point["id"])
                if point_id in owner_ids and point_id in points:
                    continue
                points[point_id] = copy.deepcopy(point)
            if self.lost_write:
                self.lost_write = False
                raise upgrade.QdrantError("simulated lost write response")
            if self.after_put is not None:
                self.after_put()
            return {"result": {"status": "completed"}}
        raise AssertionError(f"unexpected request: {method} {path}")


def _marker(
    data_collection: str = "data",
    metadata_collection: str = "meta",
    *,
    state: str | None = "active",
    setup_complete: object = True,
) -> dict[str, object]:
    marker: dict[str, object] = {
        "_openviking_meta_version": 1,
        "collection_name": data_collection,
        "metadata_collection_name": metadata_collection,
        "sparse_enabled": True,
    }
    if state is not None:
        marker["logical_collection"] = "logical"
        marker["migration_id"] = "migration"
        marker["migration_state"] = state
    if setup_complete is not None:
        marker["setup_complete"] = setup_complete
    return marker


def _sparse_point(term: str, *, owner: bool = False) -> dict[str, object]:
    index = stable_sparse_index(term)
    point_id = (
        sparse_owner_point_id(index)
        if owner
        else to_qdrant_point_id(f"openviking:sparse:{term}")
    )
    return {
        "id": point_id,
        "vector": {"meta": [0.0]},
        "payload": {
            "_openviking_sparse_term": True,
            "term": term,
            "index": index,
        },
    }


def _upgrade(
    client: _FakeQdrant,
    *,
    data_collection: str = "data",
    metadata_collection: str = "meta",
) -> upgrade.SparseDictionaryUpgrade:
    return upgrade.SparseDictionaryUpgrade(
        client=client,
        data_collection=data_collection,
        metadata_collection=metadata_collection,
    )


def _writes(client: _FakeQdrant) -> list[tuple[str, str, dict[str, object] | None, dict[str, object] | None]]:
    return [request for request in client.requests if request[0] in {"PUT", "DELETE"}]


def test_preflight_is_read_only_and_reports_missing_owner_points() -> None:
    client = _FakeQdrant(points=[_sparse_point("hello")])

    plan = _upgrade(client).preflight()

    assert plan.term_count == 1
    assert plan.owner_count == 0
    assert plan.missing_owner_count == 1
    assert client.ensure_supported_version_calls == 1
    assert _writes(client) == []


def test_convert_seeds_only_missing_owner_points_and_is_rerunnable() -> None:
    client = _FakeQdrant(points=[_sparse_point("hello")])
    converter = _upgrade(client)

    first = converter.convert(
        confirm=True,
        lock_held=True,
        barrier_held=True,
        old_writers_stopped=True,
    )
    writes_after_first = list(_writes(client))
    second = converter.convert(
        confirm=True,
        lock_held=True,
        barrier_held=True,
        old_writers_stopped=True,
    )

    owner_id = sparse_owner_point_id(stable_sparse_index("hello"))
    points = client.collections["meta"]["points"]
    assert owner_id in points
    assert to_qdrant_point_id("openviking:sparse:hello") in points
    assert len(writes_after_first) == 1
    assert len(_writes(client)) == 1
    assert first["owner_count"] == 1
    assert second["missing_owner_count"] == 0


def test_convert_requires_all_write_acknowledgements_before_any_write() -> None:
    client = _FakeQdrant(points=[_sparse_point("hello")])
    converter = _upgrade(client)

    for missing in ("confirm", "lock_held", "barrier_held", "old_writers_stopped"):
        kwargs = {
            "confirm": True,
            "lock_held": True,
            "barrier_held": True,
            "old_writers_stopped": True,
        }
        kwargs[missing] = False
        with pytest.raises(upgrade.SparseUpgradeError, match=missing.replace("_", "-")):
            converter.convert(**kwargs)

    assert _writes(client) == []


def test_preflight_rejects_colliding_legacy_terms_before_writing() -> None:
    terms = ["69235", "95303"]
    assert stable_sparse_index(terms[0]) == stable_sparse_index(terms[1])
    client = _FakeQdrant(points=[_sparse_point(term) for term in terms])

    with pytest.raises(upgrade.SparseUpgradeError, match="collision"):
        _upgrade(client).convert(
            confirm=True,
            lock_held=True,
            barrier_held=True,
            old_writers_stopped=True,
        )

    assert _writes(client) == []


@pytest.mark.parametrize("state", ["building", "cutting_over", "failed", "rolled_back"])
def test_preflight_rejects_non_serving_migration_states(state: str) -> None:
    client = _FakeQdrant(marker=_marker(state=state))

    with pytest.raises(upgrade.SparseUpgradeError, match="migration state"):
        _upgrade(client).preflight()


@pytest.mark.parametrize("state", [[], {}])
def test_preflight_rejects_malformed_marker_state(state) -> None:
    client = _FakeQdrant(marker={**_marker(), "migration_state": state})

    with pytest.raises(upgrade.SparseUpgradeError, match="migration state"):
        _upgrade(client).preflight()

    assert _writes(client) == []


@pytest.mark.parametrize(
    "fragment",
    [
        pytest.param({"migration_state": "active"}, id="state-only"),
        pytest.param(
            {"target_collection": "data", "target_metadata_collection": "meta"},
            id="target-pair-only",
        ),
        pytest.param({"vector_dimension": 1536}, id="vector-dimension-only"),
        pytest.param({"migrator_version": "qdrant-blue-green-v1"}, id="migrator-version-only"),
    ],
)
def test_preflight_and_convert_reject_incomplete_migration_marker(
    fragment: dict[str, object],
) -> None:
    client = _FakeQdrant(
        marker={**_marker(state=None), **fragment},
        points=[_sparse_point("hello")],
    )
    converter = _upgrade(client)

    with pytest.raises(upgrade.SparseUpgradeError, match="migration marker"):
        converter.preflight()
    assert _writes(client) == []

    with pytest.raises(upgrade.SparseUpgradeError, match="migration marker"):
        converter.convert(
            confirm=True,
            lock_held=True,
            barrier_held=True,
            old_writers_stopped=True,
        )
    assert _writes(client) == []


def test_preflight_rejects_incomplete_marker_and_disabled_sparse() -> None:
    for marker in (
        _marker(setup_complete=False),
        {**_marker(), "sparse_enabled": False},
        {**_marker(), "collection_name": "other"},
    ):
        with pytest.raises(upgrade.SparseUpgradeError):
            _upgrade(_FakeQdrant(marker=marker)).preflight()


@pytest.mark.parametrize("logical_collection", [None, "logical"])
def test_preflight_accepts_plain_current_marker_without_migration_provenance(
    logical_collection: str | None,
) -> None:
    marker = _marker(state=None, setup_complete=None)
    if logical_collection is not None:
        marker["logical_collection"] = logical_collection

    plan = _upgrade(_FakeQdrant(marker=marker)).preflight()

    assert plan.term_count == 0


def test_preflight_rejects_inconsistent_optional_provenance() -> None:
    marker = {
        **_marker(),
        "target_collection": "data",
        "target_metadata_collection": "other-meta",
    }

    with pytest.raises(upgrade.SparseUpgradeError, match="provenance"):
        _upgrade(_FakeQdrant(marker=marker)).preflight()


def test_convert_detects_marker_change_before_next_batch(monkeypatch) -> None:
    monkeypatch.setattr(upgrade, "_BATCH_SIZE", 1)
    client = _FakeQdrant(points=[_sparse_point("hello"), _sparse_point("world")])
    calls = 0

    def change_marker() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            client.collections["meta"]["points"][_META_MARKER_ID]["payload"]["migration_state"] = (
                "retained"
            )

    client.after_put = change_marker

    with pytest.raises(upgrade.SparseUpgradeError, match="marker changed"):
        _upgrade(client).convert(
            confirm=True,
            lock_held=True,
            barrier_held=True,
            old_writers_stopped=True,
        )

    assert len(_writes(client)) == 1


def test_convert_rejects_unknown_sidecar_points_without_writing() -> None:
    client = _FakeQdrant(
        points=[
            {
                "id": to_qdrant_point_id("unexpected"),
                "payload": {"term": "hello", "index": stable_sparse_index("hello")},
            }
        ]
    )

    with pytest.raises(upgrade.SparseUpgradeError, match="sparse term marker"):
        _upgrade(client).preflight()

    assert _writes(client) == []


def test_preflight_rejects_marker_with_null_migration_id() -> None:
    marker = {
        **_marker(),
        "logical_collection": "data",
        "migration_id": None,
    }

    with pytest.raises(upgrade.SparseUpgradeError, match="provenance"):
        _upgrade(_FakeQdrant(marker=marker)).preflight()


def test_preflight_rejects_null_sparse_row_provenance() -> None:
    point = _sparse_point("hello")
    payload = point["payload"]
    assert isinstance(payload, dict)
    payload.update({"logical_collection": None, "migration_id": None})
    client = _FakeQdrant(points=[point])

    with pytest.raises(upgrade.SparseUpgradeError, match="provenance"):
        _upgrade(client).preflight()

    assert _writes(client) == []


def test_preflight_rejects_marker_point_with_wrong_id(monkeypatch) -> None:
    client = _FakeQdrant()
    converter = _upgrade(client)
    retrieve = converter._retrieve

    def wrong_marker_id(ids: list[str]) -> list[dict[str, object]]:
        points = retrieve(ids)
        if ids == [_META_MARKER_ID]:
            points[0]["id"] = "wrong-marker-id"
        return points

    monkeypatch.setattr(converter, "_retrieve", wrong_marker_id)

    with pytest.raises(upgrade.SparseUpgradeError, match="marker point ID"):
        converter.preflight()


def test_convert_rejects_duplicate_owner_readback_ids() -> None:
    client = _FakeQdrant(points=[_sparse_point("hello")])
    client.duplicate_owner_readback = True

    with pytest.raises(upgrade.SparseUpgradeError, match="duplicate"):
        _upgrade(client).convert(
            confirm=True,
            lock_held=True,
            barrier_held=True,
            old_writers_stopped=True,
        )


@pytest.mark.parametrize("next_offset", [-1, "", "not-a-uuid"])
def test_preflight_rejects_invalid_scroll_offsets(monkeypatch, next_offset) -> None:
    client = _FakeQdrant(points=[_sparse_point("hello")])
    request = client.request
    scroll_calls = 0

    def invalid_scroll_offset(
        method: str,
        path: str,
        body: dict[str, object] | None = None,
        *,
        params: dict[str, object] | None = None,
    ) -> dict[str, object]:
        nonlocal scroll_calls
        if method == "POST" and path.endswith("/points/scroll"):
            scroll_calls += 1
            if scroll_calls > 1:
                return {"result": {"points": [], "next_page_offset": None}}
        response = request(method, path, body, params=params)
        if method == "POST" and path.endswith("/points/scroll"):
            result = response["result"]
            assert isinstance(result, dict)
            result["next_page_offset"] = next_offset
        return response

    monkeypatch.setattr(client, "request", invalid_scroll_offset)

    with pytest.raises(upgrade.SparseUpgradeError, match="offset"):
        _upgrade(client).preflight()

    assert _writes(client) == []


def test_convert_reruns_after_lost_owner_write_response() -> None:
    client = _FakeQdrant(points=[_sparse_point("hello")])
    client.lost_write = True
    converter = _upgrade(client)

    with pytest.raises(upgrade.SparseUpgradeError, match="owner write failed"):
        converter.convert(
            confirm=True,
            lock_held=True,
            barrier_held=True,
            old_writers_stopped=True,
        )

    result = converter.convert(
        confirm=True,
        lock_held=True,
        barrier_held=True,
        old_writers_stopped=True,
    )

    assert result["missing_owner_count"] == 0
    assert len(_writes(client)) == 1
