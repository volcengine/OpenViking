# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.expr import PathScope
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import InvalidArgumentError, NotFoundError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.glob_config import GlobConfig


class _DummyAgfs:
    pass


class _RemoteGlobVectorStore:
    _backend_type = "vikingdb"

    def __init__(self, records, count=1000, data_count=None):
        self.records = records
        self.count_value = count
        self.data_count = data_count
        self.count_calls = []
        self.random_calls = []

    async def count(self, filter=None, ctx=None):
        self.count_calls.append({"filter": filter, "ctx": ctx})
        return self.count_value

    async def get_collection_meta(self, ctx=None):
        meta = {
            "Fields": [
                {"FieldName": "uri", "FieldType": "path"},
                {"FieldName": "level", "FieldType": "int64"},
                {"FieldName": "name", "FieldType": "string"},
            ]
        }
        if self.data_count is not None:
            meta["CollectionStats"] = {"DataCount": self.data_count}
        return meta

    async def search_by_random(
        self,
        *,
        filter=None,
        limit=10,
        offset=0,
        output_fields=None,
        advance=None,
        ctx=None,
    ):
        self.random_calls.append(
            {
                "filter": filter,
                "limit": limit,
                "offset": offset,
                "output_fields": output_fields,
                "advance": advance,
                "ctx": ctx,
            }
        )
        return list(self.records)


class _FailingRemoteGlobVectorStore(_RemoteGlobVectorStore):
    async def search_by_random(self, **kwargs):
        self.random_calls.append(kwargs)
        raise RuntimeError("remote glob failed")


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


def test_glob_config_default_switch_to_remote_threshold_is_1000():
    config = GlobConfig()

    assert config.engine == "auto"
    assert config.switch_to_remote_threshold == 1000


@pytest.mark.asyncio
async def test_glob_uses_remote_vikingdb_when_count_reaches_threshold(monkeypatch, fs):
    vector_store = _RemoteGlobVectorStore(
        [
            {"uri": "viking://resources/docs/b.md", "level": 2, "name": "b.md"},
            {"uri": "viking://resources/docs", "level": 0, "name": "docs"},
            {"uri": "viking://resources/docs/b.md", "level": 2, "name": "b.md"},
        ],
        count=1000,
    )
    fs.vector_store = vector_store
    fs.glob_config = SimpleNamespace(engine="auto", switch_to_remote_threshold=1000)

    result = await fs.glob(
        "docs/**/*.md",
        uri="viking://resources",
        node_limit=2,
        ctx=_default_ctx(),
    )

    assert result == {
        "matches": [
            "viking://resources/docs/",
            "viking://resources/docs/b.md",
        ],
        "count": 2,
    }
    assert len(vector_store.random_calls) == 1
    call = vector_store.random_calls[0]
    assert call["limit"] == 3
    assert call["offset"] == 0
    assert call["output_fields"] == ["uri", "level", "name"]
    assert call["filter"] == {
        "op": "must",
        "field": "uri",
        "conds": ["viking://resources/docs"],
        "para": "-d=-1",
    }
    assert call["advance"] == {
        "post_process_input_limit": 1000000,
        "post_process_ops": [
            {
                "op": "path_glob",
                "field": "uri",
                "pattern": "/resources/docs/**/*.md",
                "stop_after_matches": 3,
            }
        ],
    }


@pytest.mark.asyncio
async def test_glob_remote_uses_limit_1000_when_node_limit_is_none(monkeypatch, fs):
    vector_store = _RemoteGlobVectorStore([], count=1000)
    fs.vector_store = vector_store
    fs.glob_config = SimpleNamespace(engine="auto", switch_to_remote_threshold=1000)

    await fs.glob("**/*.md", uri="viking://resources", node_limit=None, ctx=_default_ctx())

    call = vector_store.random_calls[0]
    assert call["limit"] == 1000
    assert call["advance"]["post_process_ops"][0]["stop_after_matches"] == 1000


@pytest.mark.asyncio
async def test_glob_auto_uses_scoped_count_instead_of_collection_stats(monkeypatch, fs):
    vector_store = _RemoteGlobVectorStore(
        [{"uri": "viking://resources/docs/a.md", "level": 2, "name": "a.md"}],
        count=999,
        data_count=1000,
    )
    fs.vector_store = vector_store
    fs.glob_config = SimpleNamespace(engine="auto", switch_to_remote_threshold=1000)

    async def fake_glob_directory(path, pattern, **kwargs):
        return {
            "entries": [
                {
                    "path": "/local/test_account/resources/docs/local.md",
                    "rel_path": "docs/local.md",
                    "name": "local.md",
                    "is_dir": False,
                }
            ],
            "next_token": None,
        }

    monkeypatch.setattr(fs._async_agfs, "glob_directory", fake_glob_directory)

    result = await fs.glob("docs/**/*.md", uri="viking://resources", ctx=_default_ctx())

    assert result == {"matches": ["viking://resources/docs/local.md"], "count": 1}
    assert len(vector_store.count_calls) == 1
    count_filter = vector_store.count_calls[0]["filter"]
    assert isinstance(count_filter, PathScope)
    assert count_filter.path == "viking://resources/docs"
    assert count_filter.depth == -1
    assert vector_store.random_calls == []


@pytest.mark.asyncio
async def test_glob_auto_trusts_empty_remote_result(monkeypatch, fs):
    vector_store = _RemoteGlobVectorStore([], count=1000)
    fs.vector_store = vector_store
    fs.glob_config = SimpleNamespace(engine="auto", switch_to_remote_threshold=1000)

    async def fake_glob_directory(path, pattern, **kwargs):
        raise AssertionError("remote empty glob results should not fall back to fs")

    monkeypatch.setattr(fs._async_agfs, "glob_directory", fake_glob_directory)

    result = await fs.glob("**/*.md", uri="viking://resources", ctx=_default_ctx())

    assert result == {"matches": [], "count": 0}
    assert len(vector_store.random_calls) == 1


@pytest.mark.asyncio
async def test_glob_auto_falls_back_when_remote_search_raises(monkeypatch, fs):
    vector_store = _FailingRemoteGlobVectorStore([], count=1000)
    fs.vector_store = vector_store
    fs.glob_config = SimpleNamespace(engine="auto", switch_to_remote_threshold=1000)

    async def fake_glob_directory(path, pattern, **kwargs):
        return {
            "entries": [
                {
                    "path": "/local/test_account/resources/local.md",
                    "rel_path": "local.md",
                    "name": "local.md",
                    "is_dir": False,
                }
            ],
            "next_token": None,
        }

    monkeypatch.setattr(fs._async_agfs, "glob_directory", fake_glob_directory)

    result = await fs.glob("**/*.md", uri="viking://resources", ctx=_default_ctx())

    assert result == {"matches": ["viking://resources/local.md"], "count": 1}
    assert len(vector_store.random_calls) == 1


@pytest.mark.asyncio
async def test_glob_remote_increases_positive_node_limit_by_twenty_percent(monkeypatch, fs):
    vector_store = _RemoteGlobVectorStore([], count=1000)
    fs.vector_store = vector_store
    fs.glob_config = SimpleNamespace(engine="auto", switch_to_remote_threshold=1000)

    await fs.glob("**/*.md", uri="viking://resources", node_limit=5, ctx=_default_ctx())

    call = vector_store.random_calls[0]
    assert call["limit"] == 6
    assert call["advance"]["post_process_ops"][0]["stop_after_matches"] == 6


@pytest.mark.asyncio
async def test_glob_remote_falls_back_when_node_limit_is_non_positive(monkeypatch, fs):
    vector_store = _RemoteGlobVectorStore(
        [{"uri": "viking://resources/remote.md", "level": 2, "name": "remote.md"}],
        count=1000,
    )
    fs.vector_store = vector_store
    fs.glob_config = SimpleNamespace(engine="auto", switch_to_remote_threshold=1000)

    async def fake_glob_directory(path, pattern, **kwargs):
        return {
            "entries": [
                {
                    "path": "/local/test_account/resources/local.md",
                    "rel_path": "local.md",
                    "name": "local.md",
                    "is_dir": False,
                }
            ],
            "next_token": None,
        }

    monkeypatch.setattr(fs._async_agfs, "glob_directory", fake_glob_directory)

    result = await fs.glob("**/*.md", uri="viking://resources", node_limit=0, ctx=_default_ctx())

    assert result == {"matches": ["viking://resources/local.md"], "count": 1}
    assert vector_store.random_calls == []


@pytest.mark.asyncio
async def test_glob_remote_uses_remote_fields_for_extra_fields(monkeypatch, fs):
    vector_store = _RemoteGlobVectorStore(
        [
            {
                "uri": "viking://resources/remote.md",
                "level": 2,
                "name": "remote.md",
                "size": 10,
                "mode": 0o644,
                "modTime": "2026-08-30T00:00:00Z",
                "updated_at": "2026-08-31T00:00:00Z",
            }
        ],
        count=1000,
    )
    fs.vector_store = vector_store
    fs.glob_config = SimpleNamespace(engine="auto", switch_to_remote_threshold=1000)

    async def fake_glob_directory(path, pattern, **kwargs):
        return {
            "entries": [
                {
                    "path": "/local/test_account/resources/local.md",
                    "rel_path": "local.md",
                    "name": "local.md",
                    "is_dir": False,
                }
            ],
            "next_token": None,
        }

    monkeypatch.setattr(fs._async_agfs, "glob_directory", fake_glob_directory)

    result = await fs.glob(
        "**/*.md",
        uri="viking://resources",
        node_limit=2,
        ctx=_default_ctx(),
        extra_fields=["updated_at"],
    )

    assert result == {
        "matches": [
            {
                "uri": "viking://resources/remote.md",
                "name": "remote.md",
                "isDir": False,
                "size": 10,
                "mode": 0o644,
                "modTime": "2026-08-30T00:00:00Z",
                "updated_at": "2026-08-31T00:00:00Z",
            }
        ],
        "count": 1,
    }
    assert len(vector_store.random_calls) == 1
    assert vector_store.random_calls[0]["output_fields"] == [
        "uri",
        "level",
        "name",
        "size",
        "mode",
        "modTime",
        "updated_at",
    ]


@pytest.mark.asyncio
async def test_glob_remote_entry_mode_fills_missing_stat_fields(monkeypatch, fs):
    vector_store = _RemoteGlobVectorStore(
        [
            {
                "uri": "viking://resources/remote.md",
                "level": 2,
                "id": "remote-vector-id",
                "_score": 0.42,
                "name": "remote.md",
            }
        ],
        count=1000,
    )
    fs.vector_store = vector_store
    fs.glob_config = SimpleNamespace(engine="auto", switch_to_remote_threshold=1000)

    stat_calls = []

    async def fake_stat(uri, ctx=None, skip_count=False):
        stat_calls.append({"uri": uri, "skip_count": skip_count})
        return {
            "uri": uri,
            "name": "from-stat.md",
            "isDir": False,
            "size": 7,
            "mode": 0o644,
            "modTime": "2026-08-29T00:00:00Z",
        }

    monkeypatch.setattr(fs, "stat", fake_stat)

    result = await fs.glob(
        "**/*.md",
        uri="viking://resources",
        node_limit=2,
        ctx=_default_ctx(),
        extra_fields=[],
    )

    assert result == {
        "matches": [
            {
                "uri": "viking://resources/remote.md",
                "id": "remote-vector-id",
                "name": "remote.md",
                "isDir": False,
                "size": 7,
                "mode": 0o644,
                "modTime": "2026-08-29T00:00:00Z",
            }
        ],
        "count": 1,
    }
    assert stat_calls == [{"uri": "viking://resources/remote.md", "skip_count": True}]


@pytest.mark.asyncio
async def test_glob_remote_filters_hidden_files_but_keeps_hidden_dir_children(monkeypatch, fs):
    vector_store = _RemoteGlobVectorStore(
        [
            {"uri": "viking://resources/.hidden.md", "level": 2, "name": ".hidden.md"},
            {
                "uri": "viking://resources/.hidden_dir/nested.md",
                "level": 2,
                "name": "nested.md",
            },
        ],
        count=1000,
    )
    fs.vector_store = vector_store
    fs.glob_config = SimpleNamespace(engine="auto", switch_to_remote_threshold=1000)

    result = await fs.glob("**/*.md", uri="viking://resources", node_limit=10, ctx=_default_ctx())

    assert result == {
        "matches": ["viking://resources/.hidden_dir/nested.md"],
        "count": 1,
    }


@pytest.mark.asyncio
async def test_glob_remote_absolute_pattern_uses_pattern_prefix_as_filter(monkeypatch, fs):
    vector_store = _RemoteGlobVectorStore(
        [{"uri": "viking://resources/docs/a.md", "level": 2, "name": "a.md"}],
        count=1000,
    )
    fs.vector_store = vector_store
    fs.glob_config = SimpleNamespace(engine="auto", switch_to_remote_threshold=1000)

    result = await fs.glob(
        "/resources/docs/**/*.md",
        uri="viking://resources",
        node_limit=2,
        ctx=_default_ctx(),
    )

    assert result == {"matches": ["viking://resources/docs/a.md"], "count": 1}
    call = vector_store.random_calls[0]
    assert call["filter"] == {
        "op": "must",
        "field": "uri",
        "conds": ["viking://resources/docs"],
        "para": "-d=-1",
    }
    assert call["advance"]["post_process_ops"][0]["pattern"] == "/resources/docs/**/*.md"


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
@pytest.mark.parametrize("pattern", ["[", "foo[bar", "[]", "{", "foo}"])
async def test_glob_rejects_invalid_pattern_before_remote_search(fs, pattern):
    vector_store = _RemoteGlobVectorStore([], count=1000)
    fs.vector_store = vector_store
    fs.glob_config = SimpleNamespace(engine="auto", switch_to_remote_threshold=1000)

    with pytest.raises(InvalidArgumentError):
        await fs.glob(pattern, uri="viking://resources", ctx=_default_ctx())

    assert vector_store.count_calls == []
    assert vector_store.random_calls == []


@pytest.mark.asyncio
async def test_glob_checks_access_before_listing(monkeypatch, fs):
    called = False

    async def fake_ensure_access(uri, ctx):
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

    # Trailing slash is the only type signal the flat `matches` list can carry.
    assert result == {"matches": ["viking://resources/folder/"], "count": 1}


@pytest.mark.asyncio
async def test_glob_marks_directories_but_not_files(monkeypatch, fs):
    async def fake_glob_directory(path, pattern, **kwargs):
        return {
            "entries": [
                {
                    "path": "/local/test_account/resources/folder",
                    "rel_path": "folder",
                    "name": "folder",
                    "is_dir": True,
                },
                {
                    "path": "/local/test_account/resources/a.md",
                    "rel_path": "a.md",
                    "name": "a.md",
                    "is_dir": False,
                },
                {
                    "path": "/local/test_account/resources/b.md",
                    "rel_path": "b.md",
                    "name": "b.md",
                },
            ],
            "next_token": None,
        }

    monkeypatch.setattr(fs._async_agfs, "glob_directory", fake_glob_directory)

    result = await fs.glob("**/*", uri="viking://resources", ctx=_default_ctx())

    assert result["matches"] == [
        "viking://resources/folder/",
        "viking://resources/a.md",
        "viking://resources/b.md",
    ]


@pytest.mark.asyncio
async def test_glob_directory_metadata_uses_is_dir_instead_of_trailing_slash(monkeypatch, fs):
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

    async def fake_stat(*_args, **_kwargs):
        raise NotFoundError("viking://resources/folder", "file")

    monkeypatch.setattr(fs, "stat", fake_stat)

    result = await fs.glob(
        "**/*",
        uri="viking://resources",
        ctx=_default_ctx(),
        extra_fields=[],
    )

    assert result == {
        "matches": [
            {
                "uri": "viking://resources/folder",
                "name": "folder",
                "isDir": True,
            }
        ],
        "count": 1,
    }


@pytest.mark.asyncio
async def test_glob_preserves_canonical_session_uri(monkeypatch, fs):
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

    result = await fs.glob(
        "**/*.jsonl",
        uri="viking://user/alice/sessions/sess_1",
        ctx=_default_ctx(),
    )

    assert result == {
        "matches": ["viking://user/alice/sessions/sess_1/messages.jsonl"],
        "count": 1,
    }


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
