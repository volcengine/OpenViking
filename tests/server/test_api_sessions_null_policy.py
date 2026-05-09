# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for sessions router null-handling policy.

These tests cover the behavior established in PR #1 of the typed-response
rollout:

1. ``ExcludeNoneRoute`` removes ``None`` fields from JSON output.
2. ``extra='allow'`` on high-risk response models preserves unknown fields
   (protects against silent drop when service-side adds a new field before
   the schema is updated).
3. Commit, extract, and detail endpoints — the three endpoints whose null
   behavior changed — emit bytes-compatible output with historical callers.

The tests build a minimal FastAPI app using the sessions router with
dependency overrides so the null policy can be verified in isolation from
storage and auth — no fixture from ``tests/server/conftest.py`` is used
and no real ``OpenVikingService`` / AGFS / RAGFS is started.

Note that pytest still loads every ancestor ``conftest.py`` at collection
time, which means the project's root ``tests/conftest.py`` imports are
evaluated. These imports require the full project install profile
(``openviking[dev]`` + bot extras); a partial environment that cannot
satisfy them will fail *before* these tests run, independent of the
tests themselves.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openviking.server.auth import get_request_context
from openviking.server.dependencies import set_service
from openviking.server.identity import RequestContext, Role
from openviking.server.routers.sessions import router as sessions_router
from openviking_cli.session.user_id import UserIdentifier


def _build_request_context() -> RequestContext:
    """Minimal RequestContext that bypasses auth."""
    return RequestContext(
        user=UserIdentifier(account_id="acct_test", user_id="user_test", agent_id="agent_test"),
        role=Role.USER,
    )


@pytest.fixture
def client_factory():
    """Return a factory that builds a TestClient wired to a mock service.

    ``get_service()`` is a module-global accessor (not a FastAPI dependency),
    so we inject through ``set_service`` and reset afterwards.
    """
    previous: Any = None

    def _build(service_mock: Any) -> TestClient:
        nonlocal previous
        from openviking.server import dependencies as deps_mod

        previous = deps_mod._service
        set_service(service_mock)
        app = FastAPI()
        app.include_router(sessions_router)
        app.dependency_overrides[get_request_context] = _build_request_context
        return TestClient(app)

    yield _build

    from openviking.server import dependencies as deps_mod

    deps_mod._service = previous


class _FakeSession:
    """Stand-in for the real Session object used by several endpoints."""

    def __init__(
        self,
        session_id: str = "s_test",
        meta_dict: Dict[str, Any] | None = None,
        messages: List[Any] | None = None,
        context: Dict[str, Any] | None = None,
        archive: Dict[str, Any] | None = None,
        extract_items: List[Any] | None = None,
        contexts_used: int = 0,
        skills_used: int = 0,
    ) -> None:
        self.session_id = session_id
        self.messages = messages or []
        self.meta = SimpleNamespace(to_dict=lambda: dict(meta_dict or {"session_id": session_id}))
        self.user = SimpleNamespace(
            to_dict=lambda: {
                "account_id": "acct_test",
                "user_id": "user_test",
                "agent_id": "agent_test",
            }
        )
        self.stats = SimpleNamespace(contexts_used=contexts_used, skills_used=skills_used)
        self._context_payload = context
        self._archive_payload = archive
        self._extract_items = extract_items or []

    async def load(self) -> None:
        return None

    async def get_session_context(self, token_budget: int = 0) -> Dict[str, Any]:
        return self._context_payload or {}

    async def get_session_archive(self, archive_id: str) -> Dict[str, Any]:
        return self._archive_payload or {}


def test_commit_omits_null_task_id_but_preserves_extra_field(client_factory) -> None:
    """POST /commit: ``task_id=None`` is omitted; unknown future field is preserved."""
    service = MagicMock()
    service.sessions = MagicMock()
    service.sessions.commit_async = AsyncMock(
        return_value={
            "session_id": "s1",
            "status": "accepted",
            "task_id": None,
            "archive_uri": None,
            "archived": False,
            "trace_id": "tr_1",
            "future_unmodeled_field": "keep_me",
        }
    )
    client = client_factory(service)

    resp = client.post("/api/v1/sessions/s1/commit").json()

    assert resp["status"] == "ok"
    result = resp["result"]
    assert result["session_id"] == "s1"
    assert result["status"] == "accepted"
    assert result["trace_id"] == "tr_1"
    assert result["archived"] is False
    assert "task_id" not in result, "None field must be omitted by ExcludeNoneRoute"
    assert "archive_uri" not in result, "None field must be omitted"
    assert result["future_unmodeled_field"] == "keep_me", (
        "extra='allow' must preserve unknown fields (no silent drop)"
    )


def test_session_detail_omits_null_optional_fields(client_factory) -> None:
    """GET /{id}: unset optional meta fields (``memories_extracted``) are omitted."""
    service = MagicMock()
    session = _FakeSession(
        session_id="s1",
        meta_dict={
            "session_id": "s1",
            "created_at": "2026-04-15T10:00:00",
            "updated_at": "2026-04-15T10:30:00",
            "message_count": 2,
            "commit_count": 0,
            # memories_extracted intentionally omitted -> None after model_validate
            # llm_token_usage intentionally omitted
        },
        messages=[SimpleNamespace(content="hi"), SimpleNamespace(content="there")],
    )
    service.sessions = MagicMock()
    service.sessions.get = AsyncMock(return_value=session)
    client = client_factory(service)

    resp = client.get("/api/v1/sessions/s1").json()

    assert resp["status"] == "ok"
    result = resp["result"]
    assert result["session_id"] == "s1"
    assert result["message_count"] == 2
    assert result["pending_tokens"] >= 0
    assert "memories_extracted" not in result, "None optional field must be omitted"
    assert "llm_token_usage" not in result, "None optional field must be omitted"
    assert "last_commit_at" not in result, "None optional field must be omitted"
    assert result["user"] == {
        "account_id": "acct_test",
        "user_id": "user_test",
        "agent_id": "agent_test",
    }


def test_extract_context_items_omit_null_typeunion_fields(client_factory) -> None:
    """POST /extract: memory-context items omit fields that only skills populate."""
    # Simulate Context.to_dict() for a memory context (no name/description/tags)
    memory_context_dict = {
        "id": "ctx_1",
        "uri": "viking://memories/foo",
        "abstract": "a fact",
        "context_type": "memory",
        "category": "entities",
        "created_at": "2026-04-15T10:00:00",
        "related_uri": [],
        "account_id": "acct_test",
        "owner_space": "user",
        # memory contexts have no name/description/tags/skill-only fields
        # vector intentionally absent, meta absent
    }

    class _CtxObj:
        def to_dict(self) -> Dict[str, Any]:
            return memory_context_dict

    service = MagicMock()
    service.sessions = MagicMock()
    service.sessions.extract = AsyncMock(return_value=[_CtxObj()])
    client = client_factory(service)

    resp = client.post("/api/v1/sessions/s1/extract").json()

    assert resp["status"] == "ok"
    items = resp["result"]
    assert len(items) == 1
    ctx = items[0]
    assert ctx["id"] == "ctx_1"
    assert ctx["context_type"] == "memory"
    # skill-only fields must not appear
    for skill_only in ("name", "description", "tags"):
        assert skill_only not in ctx, f"unset optional field {skill_only!r} must be omitted"
    # unset optional fields must be omitted
    for optional_absent in ("parent_uri", "temp_uri", "vector", "meta", "level", "user"):
        assert optional_absent not in ctx, (
            f"None optional field {optional_absent!r} must be omitted"
        )


def test_delete_session_always_has_session_id(client_factory) -> None:
    """DELETE /{id}: sanity check non-Optional field is always present."""
    service = MagicMock()
    service.sessions = MagicMock()
    service.sessions.delete = AsyncMock(return_value=None)
    client = client_factory(service)

    resp = client.delete("/api/v1/sessions/s1").json()

    assert resp["status"] == "ok"
    assert resp["result"] == {"session_id": "s1"}


@pytest.mark.parametrize(
    "meta_extra_field,expected_passthrough",
    [
        ("some_future_counter", 42),
        ("rolled_out_feature_flag", True),
    ],
)
def test_session_meta_preserves_unknown_fields(
    client_factory, meta_extra_field: str, expected_passthrough: Any
) -> None:
    """Silent field drop protection: unmodeled meta keys reach the response."""
    service = MagicMock()
    session = _FakeSession(
        meta_dict={
            "session_id": "s1",
            "created_at": "2026-04-15T10:00:00",
            "updated_at": "2026-04-15T10:30:00",
            "message_count": 0,
            "commit_count": 0,
            meta_extra_field: expected_passthrough,
        },
    )
    service.sessions = MagicMock()
    service.sessions.get = AsyncMock(return_value=session)
    client = client_factory(service)

    resp = client.get("/api/v1/sessions/s1").json()

    assert resp["status"] == "ok"
    assert resp["result"][meta_extra_field] == expected_passthrough, (
        "extra='allow' must pass through unknown meta fields"
    )
