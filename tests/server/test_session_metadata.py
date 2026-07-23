# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for session metadata: persistence, API, and extractor prompt injection."""

import json
from unittest.mock import patch

import httpx
import pytest

from openviking.session.session_metadata import (
    METADATA_MAX_BYTES,
    METADATA_MAX_KEYS,
    MetadataValidationError,
    merge_metadata,
    render_metadata_prompt_block,
    validate_metadata,
)
from openviking_cli.utils.config import OPENVIKING_CONFIG_ENV
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton
from tests.utils.mock_agfs import MockLocalAGFS


@pytest.fixture(autouse=True)
def _configure_test_env(monkeypatch, tmp_path):
    """Per-file env setup mirroring tests/server/test_api_sessions.py."""
    config_path = tmp_path / "ov.conf"
    config_path.write_text(
        json.dumps(
            {
                "storage": {
                    "workspace": str(tmp_path / "workspace"),
                    "agfs": {"backend": "local"},
                    "vectordb": {"backend": "local"},
                },
                "embedding": {
                    "dense": {
                        "provider": "openai",
                        "model": "test-embedder",
                        "api_base": "http://127.0.0.1:11434/v1",
                        "dimension": 1024,
                    }
                },
                "encryption": {"enabled": False},
            }
        ),
        encoding="utf-8",
    )

    mock_agfs = MockLocalAGFS(root_path=tmp_path / "mock_agfs_root")

    monkeypatch.setenv(OPENVIKING_CONFIG_ENV, str(config_path))
    OpenVikingConfigSingleton.reset_instance()

    with patch("openviking.utils.agfs_utils.create_agfs_client", return_value=mock_agfs):
        yield

    OpenVikingConfigSingleton.reset_instance()


# ---------------------------------------------------------------------------
# Pure helper unit tests
# ---------------------------------------------------------------------------


def test_validate_metadata_accepts_none():
    assert validate_metadata(None) is None


def test_validate_metadata_accepts_simple_dict():
    payload = {"project": "alpha", "stack": ["go", "rust"]}
    assert validate_metadata(payload) == payload


def test_validate_metadata_rejects_oversized_payload():
    big_value = "x" * (METADATA_MAX_BYTES + 1)
    with pytest.raises(MetadataValidationError):
        validate_metadata({"blob": big_value})


def test_validate_metadata_rejects_too_many_keys():
    payload = {f"k{i}": i for i in range(METADATA_MAX_KEYS + 1)}
    with pytest.raises(MetadataValidationError):
        validate_metadata(payload)


def test_merge_metadata_merges_by_default():
    existing = {"project": "alpha", "lang": "go"}
    incoming = {"lang": "rust", "owner": "yeyitech"}
    assert merge_metadata(existing, incoming) == {
        "project": "alpha",
        "lang": "rust",
        "owner": "yeyitech",
    }


def test_merge_metadata_replace_overwrites():
    existing = {"project": "alpha", "lang": "go"}
    incoming = {"owner": "yeyitech"}
    assert merge_metadata(existing, incoming, replace=True) == {"owner": "yeyitech"}


def test_render_metadata_prompt_block_empty_returns_empty_string():
    assert render_metadata_prompt_block(None) == ""
    assert render_metadata_prompt_block({}) == ""


def test_render_metadata_prompt_block_includes_delimiters_and_keys():
    block = render_metadata_prompt_block({"project": "alpha", "stack": ["go", "rust"]})
    assert block.startswith("[Session metadata]")
    assert block.endswith("[/Session metadata]")
    assert "project: alpha" in block
    assert '"go"' in block  # list values rendered as JSON


# ---------------------------------------------------------------------------
# HTTP API tests
# ---------------------------------------------------------------------------


async def test_create_session_with_metadata(client: httpx.AsyncClient):
    metadata = {"project": "alpha", "stack": "go"}
    resp = await client.post("/api/v1/sessions", json={"metadata": metadata})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["metadata"] == metadata
    session_id = body["result"]["session_id"]

    get_resp = await client.get(f"/api/v1/sessions/{session_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["result"]["metadata"] == metadata


async def test_update_metadata_merge(client: httpx.AsyncClient):
    create_resp = await client.post(
        "/api/v1/sessions", json={"metadata": {"project": "alpha", "lang": "go"}}
    )
    session_id = create_resp.json()["result"]["session_id"]

    patch_resp = await client.patch(
        f"/api/v1/sessions/{session_id}/metadata",
        json={"metadata": {"lang": "rust", "owner": "yeyitech"}},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["result"]["metadata"] == {
        "project": "alpha",
        "lang": "rust",
        "owner": "yeyitech",
    }

    get_resp = await client.get(f"/api/v1/sessions/{session_id}")
    assert get_resp.json()["result"]["metadata"] == {
        "project": "alpha",
        "lang": "rust",
        "owner": "yeyitech",
    }


async def test_update_metadata_replace(client: httpx.AsyncClient):
    create_resp = await client.post(
        "/api/v1/sessions", json={"metadata": {"project": "alpha", "lang": "go"}}
    )
    session_id = create_resp.json()["result"]["session_id"]

    patch_resp = await client.patch(
        f"/api/v1/sessions/{session_id}/metadata?replace=true",
        json={"metadata": {"owner": "yeyitech"}},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["result"]["metadata"] == {"owner": "yeyitech"}


async def test_metadata_size_limit(client: httpx.AsyncClient):
    # ~17 KB blob
    payload = {"blob": "x" * (METADATA_MAX_BYTES + 1024)}
    resp = await client.post("/api/v1/sessions", json={"metadata": payload})
    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "INVALID_ARGUMENT"


async def test_metadata_keycount_limit(client: httpx.AsyncClient):
    payload = {f"k{i}": i for i in range(METADATA_MAX_KEYS + 1)}
    resp = await client.post("/api/v1/sessions", json={"metadata": payload})
    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "INVALID_ARGUMENT"


async def test_metadata_persists_through_load(client: httpx.AsyncClient, service):
    """The metadata field must round-trip through the on-disk .meta.json store."""
    metadata = {"project": "alpha", "stack": ["go", "rust"]}
    create_resp = await client.post("/api/v1/sessions", json={"metadata": metadata})
    session_id = create_resp.json()["result"]["session_id"]

    # Reload session via service to bypass any in-memory cache (each call creates
    # a fresh Session object, so .load() reads the persisted .meta.json).
    from openviking.server.identity import RequestContext, Role
    from openviking_cli.session.user_id import UserIdentifier

    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    fresh = service.sessions.session(ctx, session_id)
    await fresh.load()
    assert fresh.meta.metadata == metadata


# ---------------------------------------------------------------------------
# Extractor prompt-injection tests
# ---------------------------------------------------------------------------


def test_extractor_includes_metadata_in_prompt():
    """The extractor's system instruction must include a [Session metadata] block."""
    from openviking.session.memory.session_extract_context_provider import (
        SessionExtractContextProvider,
    )

    metadata = {"project": "alpha", "tech_stack": "go,rust"}
    provider = SessionExtractContextProvider(
        messages=[],
        session_metadata=metadata,
    )
    instruction = provider.instruction()
    assert "[Session metadata]" in instruction
    assert "[/Session metadata]" in instruction
    assert "project: alpha" in instruction
    assert "tech_stack: go,rust" in instruction


def test_extractor_no_metadata_block_when_empty():
    from openviking.session.memory.session_extract_context_provider import (
        SessionExtractContextProvider,
    )

    provider_none = SessionExtractContextProvider(messages=[], session_metadata=None)
    assert "[Session metadata]" not in provider_none.instruction()

    provider_empty = SessionExtractContextProvider(messages=[], session_metadata={})
    assert "[Session metadata]" not in provider_empty.instruction()


def test_extractor_prompt_block_threaded_through_session_meta():
    """SessionMeta.metadata round-trips through to_dict/from_dict."""
    # SessionMeta should default metadata to None and accept a dict.
    from openviking.session.session import SessionMeta

    meta = SessionMeta()
    assert meta.metadata is None
    meta.metadata = {"project": "alpha"}

    serialized = json.dumps(meta.to_dict())
    parsed = SessionMeta.from_dict(json.loads(serialized))
    assert parsed.metadata == {"project": "alpha"}
