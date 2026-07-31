# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier


class _DummyAgfs:
    pass


def _default_ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)


@pytest.fixture
def fs(monkeypatch):
    viking_fs = VikingFS(agfs=_DummyAgfs())
    monkeypatch.setattr(viking_fs, "_ctx_or_default", lambda _ctx=None: _default_ctx())
    monkeypatch.setattr(
        viking_fs, "_uri_to_path", lambda _uri, **_kwargs: "/local/test_account/resources"
    )
    monkeypatch.setattr(
        viking_fs, "_read_paths", lambda _uri, **_kwargs: ["/local/test_account/resources"]
    )
    monkeypatch.setattr(viking_fs, "_agfs_path_exists", lambda _path: _async_true())
    monkeypatch.setattr(viking_fs, "_read_path_visible", lambda *_args, **_kwargs: _async_true())
    monkeypatch.setattr(
        viking_fs,
        "_path_to_uri",
        lambda path, **_kwargs: path.replace("/local/test_account/", "viking://"),
    )
    monkeypatch.setattr(viking_fs, "_is_accessible", lambda _uri, _ctx: True)
    return viking_fs


async def _async_true():
    return True


@pytest.mark.asyncio
async def test_glob_delegates_to_agfs_with_paging_and_visibility(monkeypatch, fs):
    calls = []

    pages = [
        {
            "entries": [
                {
                    "path": "/local/test_account/resources/group/a.md",
                    "rel_path": "group/a.md",
                    "name": "a.md",
                    "is_dir": False,
                },
                {
                    "path": "/local/test_account/resources/_system/secret.md",
                    "rel_path": "_system/secret.md",
                    "name": "secret.md",
                    "is_dir": False,
                },
            ],
            "next_token": "tok-1",
        },
        {
            "entries": [
                {
                    "path": "/local/test_account/resources/group/b.md",
                    "rel_path": "group/b.md",
                    "name": "b.md",
                    "is_dir": False,
                }
            ],
            "next_token": None,
        },
    ]

    async def fake_glob_directory(path, pattern, **kwargs):
        calls.append({"path": path, "pattern": pattern, **kwargs})
        return pages[len(calls) - 1]

    monkeypatch.setattr(fs._async_agfs, "glob_directory", fake_glob_directory)

    result = await fs.glob("**/*.md", uri="viking://resources", node_limit=2, ctx=_default_ctx())

    assert result == {
        "matches": [
            "viking://resources/group/a.md",
            "viking://resources/group/b.md",
        ],
        "count": 2,
    }
    assert [call["continuation_token"] for call in calls] == [None, "tok-1"]
    assert all(call["page_size"] == 2 for call in calls)


@pytest.mark.asyncio
async def test_glob_stops_after_reaching_limit_at_page_end(monkeypatch, fs):
    calls = []

    async def fake_glob_directory(path, pattern, **kwargs):
        calls.append({"path": path, "pattern": pattern, **kwargs})
        return {
            "entries": [
                {
                    "path": "/local/test_account/resources/group/a.md",
                    "rel_path": "group/a.md",
                    "name": "a.md",
                    "is_dir": False,
                },
                {
                    "path": "/local/test_account/resources/group/b.md",
                    "rel_path": "group/b.md",
                    "name": "b.md",
                    "is_dir": False,
                },
            ],
            "next_token": "tok-should-not-be-used",
        }

    monkeypatch.setattr(fs._async_agfs, "glob_directory", fake_glob_directory)

    result = await fs.glob("**/*.md", uri="viking://resources", node_limit=2, ctx=_default_ctx())

    assert result == {
        "matches": [
            "viking://resources/group/a.md",
            "viking://resources/group/b.md",
        ],
        "count": 2,
    }
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_glob_trusts_backend_glob_matches(monkeypatch, fs):
    async def fake_glob_directory(path, pattern, **kwargs):
        return {
            "entries": [
                {
                    "path": "/local/test_account/resources/a.md",
                    "rel_path": "a.md",
                    "name": "a.md",
                    "is_dir": False,
                }
            ],
            "next_token": None,
        }

    monkeypatch.setattr(fs._async_agfs, "glob_directory", fake_glob_directory)

    result = await fs.glob("**/*.md", uri="viking://resources", ctx=_default_ctx())

    assert result == {"matches": ["viking://resources/a.md"], "count": 1}


@pytest.mark.asyncio
async def test_glob_rejects_empty_pattern(fs):
    with pytest.raises(InvalidArgumentError):
        await fs.glob("", uri="viking://resources", ctx=_default_ctx())


@pytest.mark.asyncio
async def test_glob_checks_access_before_listing(monkeypatch, fs):
    called = False

    def fake_ensure_access(uri, ctx):
        nonlocal called
        called = True
        raise PermissionError(f"denied: {uri}")

    monkeypatch.setattr(fs, "_ensure_access", fake_ensure_access)

    with pytest.raises(PermissionError):
        await fs.glob("**/*.md", uri="viking://resources", ctx=_default_ctx())

    assert called is True


@pytest.mark.asyncio
async def test_glob_preserves_request_uri_alias(monkeypatch, fs):
    monkeypatch.setattr(fs, "_uri_to_path", lambda _uri, **_kwargs: "/local/test_account/user")
    monkeypatch.setattr(fs, "_read_paths", lambda _uri, **_kwargs: ["/local/test_account/user"])
    monkeypatch.setattr(
        fs, "_path_to_uri", lambda _path, **_kwargs: "viking://user/test_account/demo.md"
    )

    async def fake_glob_directory(path, pattern, **kwargs):
        return {
            "entries": [
                {
                    "path": "/local/test_account/user/demo.md",
                    "rel_path": "demo.md",
                    "name": "demo.md",
                    "is_dir": False,
                }
            ],
            "next_token": None,
        }

    monkeypatch.setattr(fs._async_agfs, "glob_directory", fake_glob_directory)

    result = await fs.glob("**/*.md", uri="viking://user", ctx=_default_ctx())

    assert result == {"matches": ["viking://user/demo.md"], "count": 1}


@pytest.mark.asyncio
async def test_glob_preserves_root_uri(monkeypatch, fs):
    monkeypatch.setattr(fs, "_uri_to_path", lambda _uri, **_kwargs: "/local/test_account")
    monkeypatch.setattr(fs, "_read_paths", lambda _uri, **_kwargs: ["/local/test_account"])
    monkeypatch.setattr(
        fs, "_path_to_uri", lambda _path, **_kwargs: "viking://resources/should-not-leak.md"
    )

    async def fake_glob_directory(path, pattern, **kwargs):
        return {
            "entries": [
                {
                    "path": "/local/test_account/resources/demo.md",
                    "rel_path": "resources/demo.md",
                    "name": "demo.md",
                    "is_dir": False,
                }
            ],
            "next_token": None,
        }

    monkeypatch.setattr(fs._async_agfs, "glob_directory", fake_glob_directory)

    result = await fs.glob("**/*.md", uri="viking://", ctx=_default_ctx())

    assert result == {"matches": ["viking://resources/demo.md"], "count": 1}


@pytest.mark.asyncio
async def test_glob_keeps_directory_matches(monkeypatch, fs):
    async def fake_glob_directory(path, pattern, **kwargs):
        return {
            "entries": [
                {
                    "path": "/local/test_account/resources/folder",
                    "rel_path": "folder",
                    "name": "folder",
                    "is_dir": True,
                }
            ],
            "next_token": None,
        }

    monkeypatch.setattr(fs._async_agfs, "glob_directory", fake_glob_directory)

    result = await fs.glob("**/*", uri="viking://resources", ctx=_default_ctx())

    assert result == {"matches": ["viking://resources/folder"], "count": 1}


@pytest.mark.asyncio
async def test_glob_preserves_legacy_session_alias(monkeypatch, fs):
    """中文注释：legacy session URI 仍应保持旧别名返回。"""
    monkeypatch.setattr(
        fs, "_uri_to_path", lambda _uri, **_kwargs: "/local/test_account/user/alice/sessions/sess_1"
    )
    monkeypatch.setattr(
        fs,
        "_read_paths",
        lambda _uri, **_kwargs: ["/local/test_account/user/alice/sessions/sess_1"],
    )

    async def fake_glob_directory(path, pattern, **kwargs):
        return {
            "entries": [
                {
                    "path": "/local/test_account/user/alice/sessions/sess_1/messages.jsonl",
                    "rel_path": "messages.jsonl",
                    "name": "messages.jsonl",
                    "is_dir": False,
                }
            ],
            "next_token": None,
        }

    monkeypatch.setattr(fs._async_agfs, "glob_directory", fake_glob_directory)

    result = await fs.glob("**/*.jsonl", uri="viking://session/alice/sess_1", ctx=_default_ctx())

    assert result == {"matches": ["viking://session/alice/sess_1/messages.jsonl"], "count": 1}


@pytest.mark.asyncio
async def test_glob_uses_path_to_uri_for_non_legacy_namespace(monkeypatch, fs):
    """中文注释：非 legacy 命名空间必须回落到 _path_to_uri，避免错误沿用请求别名。"""
    monkeypatch.setattr(
        fs,
        "_uri_to_path",
        lambda _uri, **_kwargs: "/local/test_account/resources/actual-root",
    )
    monkeypatch.setattr(
        fs,
        "_read_paths",
        lambda _uri, **_kwargs: ["/local/test_account/resources/actual-root"],
    )
    monkeypatch.setattr(
        fs,
        "_path_to_uri",
        lambda path, **_kwargs: path.replace(
            "/local/test_account/resources/", "viking://resources/"
        ),
    )

    async def fake_glob_directory(path, pattern, **kwargs):
        return {
            "entries": [
                {
                    "path": "/local/test_account/resources/actual-root/demo.md",
                    "rel_path": "demo.md",
                    "name": "demo.md",
                    "is_dir": False,
                }
            ],
            "next_token": None,
        }

    monkeypatch.setattr(fs._async_agfs, "glob_directory", fake_glob_directory)

    result = await fs.glob(
        "**/*.md",
        uri="viking://resources/alias-root",
        ctx=_default_ctx(),
    )

    assert result == {"matches": ["viking://resources/actual-root/demo.md"], "count": 1}
