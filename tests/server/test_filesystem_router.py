# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Filesystem router tests."""

from types import SimpleNamespace

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.server.routers import filesystem
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.asyncio
async def test_rm_preserves_memory_cleanup(monkeypatch):
    cleanup = {"status": "success", "memory_uris": ["viking://user/alice/memories/entities/a.md"]}

    async def fake_rm(uri, ctx=None, recursive=False, wait=False, timeout=None):
        return {"estimated_deleted_count": 1, "memory_cleanup": cleanup}

    monkeypatch.setattr(
        filesystem,
        "get_service",
        lambda: SimpleNamespace(fs=SimpleNamespace(rm=fake_rm)),
    )

    response = await filesystem.rm(
        uri="viking://resources/id_card.pdf",
        recursive=True,
        _ctx=RequestContext(user=UserIdentifier("acct", "alice"), role=Role.USER),
    )

    assert response.result["uri"] == "viking://resources/id_card.pdf"
    assert response.result["estimated_deleted_count"] == 1
    assert response.result["memory_cleanup"] == cleanup


@pytest.mark.asyncio
async def test_cp_resolves_paths_and_preserves_service_result(monkeypatch):
    calls = []

    async def fake_cp(from_uri, to_uri, recursive=False, ctx=None):
        calls.append((from_uri, to_uri, recursive, ctx))
        return {
            "operation_id": "copy-1",
            "from": from_uri,
            "to": to_uri,
            "recursive": recursive,
            "semantic_root_uri": "viking://resources/archive",
            "semantic_status": "queued",
        }

    monkeypatch.setattr(
        filesystem,
        "get_service",
        lambda: SimpleNamespace(fs=SimpleNamespace(cp=fake_cp)),
    )
    monkeypatch.setattr(
        filesystem,
        "resolve_path_variables",
        lambda uri: uri.replace("{test:resources}", "viking://resources"),
    )
    ctx = RequestContext(user=UserIdentifier("acct", "alice"), role=Role.USER)

    response = await filesystem.cp(
        filesystem.CpRequest(
            from_uri="{test:resources}/a.md",
            to_uri="{test:resources}/archive/a.md",
            recursive=True,
        ),
        _ctx=ctx,
    )

    assert calls == [
        (
            "viking://resources/a.md",
            "viking://resources/archive/a.md",
            True,
            ctx,
        )
    ]
    assert response.result == {
        "operation_id": "copy-1",
        "from": "viking://resources/a.md",
        "to": "viking://resources/archive/a.md",
        "recursive": True,
        "semantic_root_uri": "viking://resources/archive",
        "semantic_status": "queued",
    }


def test_cp_request_defaults_to_non_recursive():
    request = filesystem.CpRequest(
        from_uri="viking://resources/a.md",
        to_uri="viking://resources/b.md",
    )

    assert request.recursive is False


@pytest.mark.asyncio
async def test_attrs_returns_memory_fields_and_tags(monkeypatch):
    raw_memory = (
        "Original preference\n\n"
        "<!-- MEMORY_FIELDS\n"
        '{"memory_type": "preferences", "tags": ["ui"], "fields": {"topic": "theme"}, '
        '"resource_refs": ["viking://resources/docs/api.md"]}\n'
        "-->"
    )

    async def fake_stat(uri, ctx=None):
        return {"isDir": False}

    async def fake_read(uri, ctx=None):
        return raw_memory

    class FakeVectorManager:
        async def filter(self, **kwargs):
            return [
                {
                    "uri": kwargs["filter"]["conds"][0],
                    "level": 2,
                    "search_tags": ["team=search"],
                }
            ]

    monkeypatch.setattr(
        filesystem,
        "get_service",
        lambda: SimpleNamespace(
            fs=SimpleNamespace(stat=fake_stat, read=fake_read),
            vikingdb_manager=FakeVectorManager(),
        ),
    )

    response = await filesystem.attrs(
        uri="viking://user/alice/memories/preferences/theme.md",
        _ctx=RequestContext(user=UserIdentifier("acct", "alice"), role=Role.USER),
    )

    attrs = response.result["attrs"]
    assert attrs["memory"] == {
        "tags": ["ui"],
        "fields": {"topic": "theme"},
        "resource_refs": ["viking://resources/docs/api.md"],
        "memory_type": "preferences",
    }
    assert attrs["tags"] == ["team=search"]


@pytest.mark.asyncio
async def test_mkdir_echoes_canonical_home_alias(monkeypatch):
    seen = {}

    async def fake_mkdir(uri, ctx=None, description=None):
        seen["uri"] = uri

    monkeypatch.setattr(
        filesystem,
        "get_service",
        lambda: SimpleNamespace(fs=SimpleNamespace(mkdir=fake_mkdir)),
    )

    response = await filesystem.mkdir(
        filesystem.MkdirRequest(uri="viking://~/resources/notes"),
        _ctx=RequestContext(user=UserIdentifier("acct", "alice"), role=Role.USER),
    )

    assert seen["uri"] == "viking://user/alice/resources/notes"
    assert response.result["uri"] == "viking://user/alice/resources/notes"
