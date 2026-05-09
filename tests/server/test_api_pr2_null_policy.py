# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for PR #2 routers: resources, filesystem, relations, pack.

Covers the behavior established in PR #2 of the typed-response rollout:

1. ``ExcludeNoneRoute`` removes ``None`` fields from JSON output on the
   four PR #2 routers.
2. ``extra='allow'`` on high-risk response models forwards unknown fields
   (protects against silent drop when service-side grows a new field).
3. The ``from``/``to`` alias on ``FromTo`` / ``LinkResult`` survives the
   FastAPI serialization path — the JSON key stays ``"from"`` even though
   the Python field is named ``from_``.

The tests build a minimal FastAPI app with dependency overrides; no
fixture from ``tests/server/conftest.py`` is used. pytest still loads
parent ``conftest.py`` modules at collection; a partial environment
that can't satisfy their imports will fail before any test runs here.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openviking.server.auth import get_request_context
from openviking.server.dependencies import set_service
from openviking.server.identity import RequestContext, Role
from openviking.server.routers.filesystem import router as filesystem_router
from openviking.server.routers.pack import router as pack_router
from openviking.server.routers.relations import router as relations_router
from openviking.server.routers.resources import router as resources_router
from openviking_cli.session.user_id import UserIdentifier


def _build_request_context() -> RequestContext:
    return RequestContext(
        user=UserIdentifier(account_id="acct_test", user_id="user_test", agent_id="agent_test"),
        role=Role.USER,
    )


@pytest.fixture
def client_factory():
    previous: Any = None

    def _build(service_mock: Any, router: Any) -> TestClient:
        nonlocal previous
        from openviking.server import dependencies as deps_mod

        previous = deps_mod._service
        set_service(service_mock)
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_request_context] = _build_request_context
        return TestClient(app)

    yield _build

    from openviking.server import dependencies as deps_mod

    deps_mod._service = previous


def test_filesystem_stat_omits_nulls_and_preserves_extras(client_factory) -> None:
    """GET /fs/stat: unset FileStat fields are omitted; future fields preserved."""
    service = MagicMock()
    service.fs = MagicMock()
    service.fs.stat = AsyncMock(
        return_value={
            "name": "foo.md",
            "size": 10,
            "mode": 644,
            "modTime": "2026-04-15T10:00:00Z",
            "isDir": False,
            "meta": None,  # should be omitted
            "tags": None,  # should be omitted
            "future_agfs_field": "keep_me",
        }
    )
    client = client_factory(service, filesystem_router)

    resp = client.get("/api/v1/fs/stat?uri=viking://foo.md").json()

    assert resp["status"] == "ok"
    result = resp["result"]
    assert result["name"] == "foo.md"
    assert result["size"] == 10
    assert result["future_agfs_field"] == "keep_me"
    for none_field in ("meta", "tags", "abstract", "rel_path"):
        assert none_field not in result, f"None field {none_field!r} must be omitted"


def test_filesystem_mv_preserves_from_alias(client_factory) -> None:
    """POST /fs/mv: response JSON key must be ``"from"``, not ``"from_"``."""
    service = MagicMock()
    service.fs = MagicMock()
    service.fs.mv = AsyncMock(return_value=None)
    client = client_factory(service, filesystem_router)

    resp = client.post(
        "/api/v1/fs/mv",
        json={"from_uri": "viking://a", "to_uri": "viking://b"},
    ).json()

    assert resp["status"] == "ok"
    assert resp["result"] == {"from": "viking://a", "to": "viking://b"}
    assert "from_" not in resp["result"], "alias leak: Python field name reached JSON"


def test_relations_link_polymorphic_to_field(client_factory) -> None:
    """POST /relations/link: ``to`` echoes the request which may be str or list."""
    service = MagicMock()
    service.relations = MagicMock()
    service.relations.link = AsyncMock(return_value=None)
    client = client_factory(service, relations_router)

    # List variant
    resp = client.post(
        "/api/v1/relations/link",
        json={"from_uri": "viking://a", "to_uris": ["viking://b", "viking://c"]},
    ).json()
    assert resp["result"] == {"from": "viking://a", "to": ["viking://b", "viking://c"]}

    # Single-string variant
    resp = client.post(
        "/api/v1/relations/link",
        json={"from_uri": "viking://a", "to_uris": "viking://b"},
    ).json()
    assert resp["result"] == {"from": "viking://a", "to": "viking://b"}


def test_relations_list_omits_null_and_preserves_extras(client_factory) -> None:
    """GET /relations: each entry omits nulls; unknown fields forwarded."""
    service = MagicMock()
    service.relations = MagicMock()
    service.relations.relations = AsyncMock(
        return_value=[
            {"uri": "viking://a", "reason": "because", "future_rel_field": 1},
            {"uri": "viking://b", "reason": ""},
        ]
    )
    client = client_factory(service, relations_router)

    resp = client.get("/api/v1/relations?uri=viking://root").json()

    assert resp["status"] == "ok"
    items = resp["result"]
    assert items[0]["uri"] == "viking://a"
    assert items[0]["reason"] == "because"
    assert items[0]["future_rel_field"] == 1, "extra='allow' must forward unknown fields"
    assert items[1]["reason"] == ""


def test_resources_add_resource_omits_nulls_and_preserves_extras(
    client_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /resources: optional None fields omitted; future processor keys forwarded."""
    service = MagicMock()
    service.resources = MagicMock()
    service.resources.add_resource = AsyncMock(
        return_value={
            "status": "success",
            "errors": [],
            "warnings": None,
            "source_path": "viking://foo",
            "meta": {"size": 100},
            "root_uri": "viking://foo",
            "temp_uri": None,
            "queue_status": None,
            "future_resource_key": "preserved",
        }
    )
    # require_remote_resource_source blocks file:// etc — stub it out
    monkeypatch.setattr(
        "openviking.server.routers.resources.require_remote_resource_source",
        lambda p: p,
    )
    from openviking.telemetry.execution import TelemetryExecutionResult
    from openviking.telemetry.request import TelemetrySelection

    async def _fake_run_operation(
        operation: str, telemetry: Any, fn: Any
    ) -> TelemetryExecutionResult[Any]:
        result = await fn()
        return TelemetryExecutionResult(
            result=result,
            telemetry=None,
            selection=TelemetrySelection(include_summary=False),
        )

    monkeypatch.setattr("openviking.server.routers.resources.run_operation", _fake_run_operation)

    client = client_factory(service, resources_router)
    resp = client.post(
        "/api/v1/resources",
        json={"path": "https://example.com/foo"},
    ).json()

    assert resp["status"] == "ok"
    result = resp["result"]
    assert result["status"] == "success"
    assert result["root_uri"] == "viking://foo"
    assert result["future_resource_key"] == "preserved"
    for none_field in ("warnings", "temp_uri", "queue_status"):
        assert none_field not in result, f"None field {none_field!r} must be omitted"


def test_pack_import_returns_uri_ref(client_factory, monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /pack/import: wraps URI string in ``{"uri": str}``."""
    service = MagicMock()
    service.pack = MagicMock()
    service.pack.import_ovpack = AsyncMock(return_value="viking://imported")
    monkeypatch.setattr(
        "openviking.server.routers.pack.resolve_uploaded_temp_file_id",
        lambda tid, d: "/tmp/fake",
    )

    client = client_factory(service, pack_router)
    resp = client.post(
        "/api/v1/pack/import",
        json={"temp_file_id": "upload_x", "parent": "viking://root"},
    ).json()

    assert resp["status"] == "ok"
    assert resp["result"] == {"uri": "viking://imported"}
