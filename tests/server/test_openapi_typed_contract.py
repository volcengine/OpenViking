# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""OpenAPI structure assertions for PR #1 typed responses.

These tests lock in the *declaration* contract (not just runtime behavior)
so a future change cannot silently regress a typed endpoint back to
``any`` / empty schema. They also guard two direction-specific invariants:

- sessions / content / search endpoints wrap their payloads in the shared
  ``Response[T]`` envelope.
- bot proxy endpoints return the upstream mirror models directly (no
  envelope), because bot is a passthrough proxy and bolting the envelope
  on would break wire-format compatibility with the upstream service.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest
from fastapi import FastAPI

from openviking.server.routers.bot import router as bot_router
from openviking.server.routers.content import router as content_router
from openviking.server.routers.filesystem import router as filesystem_router
from openviking.server.routers.pack import router as pack_router
from openviking.server.routers.relations import router as relations_router
from openviking.server.routers.resources import router as resources_router
from openviking.server.routers.search import router as search_router
from openviking.server.routers.sessions import router as sessions_router


def _openapi_spec() -> Dict[str, Any]:
    """Build a minimal FastAPI app with the typed routers and return its spec."""
    app = FastAPI()
    app.include_router(sessions_router)
    app.include_router(content_router)
    app.include_router(search_router)
    app.include_router(bot_router, prefix="/bot/v1")
    app.include_router(resources_router)
    app.include_router(filesystem_router)
    app.include_router(relations_router)
    app.include_router(pack_router)
    return app.openapi()


def _response_schema_200(spec: Dict[str, Any], method: str, path: str) -> Dict[str, Any]:
    op = spec["paths"][path][method]
    return op["responses"]["200"]["content"]["application/json"]["schema"]


ENVELOPE_ENDPOINTS = [
    # PR #1 — sessions
    ("post", "/api/v1/sessions"),
    ("get", "/api/v1/sessions"),
    ("get", "/api/v1/sessions/{session_id}"),
    ("delete", "/api/v1/sessions/{session_id}"),
    ("get", "/api/v1/sessions/{session_id}/context"),
    ("get", "/api/v1/sessions/{session_id}/archives/{archive_id}"),
    ("post", "/api/v1/sessions/{session_id}/commit"),
    ("post", "/api/v1/sessions/{session_id}/extract"),
    ("post", "/api/v1/sessions/{session_id}/messages"),
    ("post", "/api/v1/sessions/{session_id}/used"),
    # PR #1 — content
    ("get", "/api/v1/content/read"),
    ("get", "/api/v1/content/abstract"),
    ("get", "/api/v1/content/overview"),
    ("post", "/api/v1/content/write"),
    ("post", "/api/v1/content/reindex"),
    # PR #1 — search
    ("post", "/api/v1/search/find"),
    ("post", "/api/v1/search/search"),
    ("post", "/api/v1/search/grep"),
    ("post", "/api/v1/search/glob"),
    # PR #2 — resources
    ("post", "/api/v1/resources/temp_upload"),
    ("post", "/api/v1/resources"),
    ("post", "/api/v1/skills"),
    # PR #2 — filesystem
    ("get", "/api/v1/fs/ls"),
    ("get", "/api/v1/fs/tree"),
    ("get", "/api/v1/fs/stat"),
    ("post", "/api/v1/fs/mkdir"),
    ("delete", "/api/v1/fs"),
    ("post", "/api/v1/fs/mv"),
    # PR #2 — relations
    ("get", "/api/v1/relations"),
    ("post", "/api/v1/relations/link"),
    ("delete", "/api/v1/relations/link"),
    # PR #2 — pack
    ("post", "/api/v1/pack/import"),
]

BOT_MIRROR_ENDPOINTS = [
    ("get", "/bot/v1/health", "BotHealthResponse"),
    ("post", "/bot/v1/chat", "BotChatResponse"),
]


@pytest.mark.parametrize("method,path", ENVELOPE_ENDPOINTS)
def test_envelope_endpoints_reference_response_model(method: str, path: str) -> None:
    """sessions/content/search endpoints must wrap payloads in ``Response[T]``."""
    spec = _openapi_spec()
    schema = _response_schema_200(spec, method, path)
    # Either a direct $ref or an allOf/oneOf wrapping one — flatten to str
    as_text = str(schema)
    assert "$ref" in as_text, (
        f"{method.upper()} {path}: response schema has no $ref "
        f"(would be untyped any in generated SDKs). Got: {schema}"
    )
    assert "Response" in as_text, (
        f"{method.upper()} {path}: response schema does not reference a "
        f"Response[T] model. Got: {schema}"
    )


@pytest.mark.parametrize("method,path,expected_model", BOT_MIRROR_ENDPOINTS)
def test_bot_endpoints_use_mirror_models_not_envelope(
    method: str, path: str, expected_model: str
) -> None:
    """bot proxy endpoints must return mirror models directly, not Response[T]."""
    spec = _openapi_spec()
    schema = _response_schema_200(spec, method, path)
    as_text = str(schema)
    assert expected_model in as_text, (
        f"{method.upper()} {path}: expected schema reference to {expected_model!r}. Got: {schema}"
    )
    assert "Response_" not in as_text, (
        f"{method.upper()} {path}: bot proxy endpoint must not wrap upstream "
        f"responses in Response[T] envelope. Got: {schema}"
    )


def test_whitelisted_non_json_endpoints_remain_unmodeled() -> None:
    """Binary/stream endpoints are intentionally exempt from response_model.

    Locks in the whitelist from ``docs/api_schema_guidelines.md`` §4. If
    this test fails because one of these endpoints gained a typed schema
    reference, the whitelist doc must be updated in the same PR.

    FastAPI synthesizes a default ``application/json`` response with an
    empty ``schema: {}`` when no ``response_model`` is declared; that is
    acceptable here because codegen tools treat empty schemas as untyped
    and fall back to raw ``Response`` types. What must *not* appear is a
    ``$ref`` to one of the typed models.
    """
    spec = _openapi_spec()

    non_json_paths = [
        ("/api/v1/content/download", "get"),
        ("/bot/v1/chat/stream", "post"),
        ("/api/v1/pack/export", "post"),
    ]
    for path, method in non_json_paths:
        resp_200 = spec["paths"][path][method]["responses"]["200"]
        json_schema = resp_200.get("content", {}).get("application/json", {}).get("schema", {})
        assert "$ref" not in str(json_schema), (
            f"{method.upper()} {path} must not reference a typed model; it is "
            f"whitelisted as a non-JSON response (bytes/SSE). Got: {json_schema}"
        )


def test_openapi_component_schemas_include_typed_models() -> None:
    """Typed models must be emitted as reusable components so codegen can name them."""
    spec = _openapi_spec()
    components = spec.get("components", {}).get("schemas", {})
    required = {
        # PR #1 — sessions
        "SessionCreatedResult",
        "SessionListItem",
        "SessionDetail",
        "SessionArchiveDetail",
        "SessionContextResult",
        "SessionDeletedResult",
        "CommitResult",
        "ContextItem",
        "MessageAddedResult",
        "UsageRecordedResult",
        # PR #1 — content
        "ContentWriteResult",
        "ReindexResult",
        # PR #1 — search
        "SearchResult",
        "SearchHit",
        "GrepResult",
        "GlobResult",
        # PR #1 — bot mirror
        "BotHealthResponse",
        "BotChatResponse",
        # PR #2 — resources
        "TempUploadResult",
        "AddResourceResult",
        "AddSkillResult",
        # PR #2 — filesystem
        "FileStat",
        "FromTo",
        # PR #2 — relations
        "RelationEntry",
        "LinkResult",
        # PR #2 — common
        "URIRef",
    }
    missing = required - set(components.keys())
    assert not missing, f"OpenAPI components missing typed models: {sorted(missing)}"
