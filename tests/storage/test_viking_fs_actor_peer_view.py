# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import NotFoundError, PermissionDeniedError
from openviking_cli.session.user_id import UserIdentifier

_MOD_TIME = "2026-01-01T00:00:00Z"


class _MemoryAGFS:
    def __init__(self):
        self.files = {
            "/local/acct/agent/customer-wang-yue/memories/profile.md": b"legacy-wang",
            "/local/acct/agent/customer-zhang-xiaoxiao/memories/profile.md": b"legacy-zhang",
            "/local/acct/user/support_bot/peers/customer-wang-yue/memories/profile.md": b"wang",
            "/local/acct/user/support_bot/peers/customer-zhang-xiaoxiao/memories/profile.md": (
                b"zhang"
            ),
            "/local/acct/user/support_bot/resources/guide.md": b"guide",
            "/local/acct/user/support_bot/sessions/duplicate/messages.jsonl": (
                b'{"role":"user","content":"new"}\n'
            ),
            "/local/acct/user/support_bot/sessions/new-session/messages.jsonl": (
                b'{"role":"user","content":"new only"}\n'
            ),
            "/local/acct/session/duplicate/messages.jsonl": (
                b'{"role":"user","content":"legacy duplicate"}\n'
            ),
            "/local/acct/session/legacy-session/messages.jsonl": (
                b'{"role":"user","content":"legacy"}\n'
            ),
            "/local/acct/session/other-owned/.meta.json": b'{"created_by_user_id":"other"}',
            "/local/acct/session/other-owned/messages.jsonl": (
                b'{"role":"user","content":"other"}\n'
            ),
            "/local/acct/session/support_bot/nested-session/messages.jsonl": (
                b'{"role":"user","content":"nested"}\n'
            ),
        }
        self.dirs = set()
        for path in self.files:
            parts = path.strip("/").split("/")[:-1]
            current = ""
            for part in parts:
                current += f"/{part}"
                self.dirs.add(current)
        self.writes = []
        self.removed = []

    def ls(self, path, ctx=None):
        if path not in self.dirs:
            raise FileNotFoundError(path)
        prefix = path.rstrip("/") + "/"
        names = set()
        for candidate in [*self.dirs, *self.files]:
            if not candidate.startswith(prefix):
                continue
            rest = candidate[len(prefix) :]
            if rest and "/" not in rest:
                names.add(rest)
        return [self._entry(f"{prefix}{name}") for name in sorted(names)]

    def tree_directory(
        self,
        path,
        show_hidden=False,
        node_limit=None,
        level_limit=None,
        ctx=None,
    ):
        if path not in self.dirs:
            raise FileNotFoundError(path)
        prefix = path.rstrip("/") + "/"
        entries = []
        for candidate in sorted([*self.dirs, *self.files]):
            if not candidate.startswith(prefix):
                continue
            rel_path = candidate[len(prefix) :]
            if not rel_path:
                continue
            if level_limit is not None and len(rel_path.split("/")) > level_limit:
                continue
            entry = {
                "path": candidate,
                "rel_path": rel_path,
                "info": self._entry(candidate),
                "extra": {},
            }
            entries.append(entry)
            if node_limit is not None and len(entries) >= node_limit:
                break
        return entries

    def stat(self, path, ctx=None):
        if path in self.dirs:
            return self._entry(path)
        if path in self.files:
            return self._entry(path)
        raise FileNotFoundError(path)

    def read(self, path, *args, ctx=None):
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def write(self, path, data, ctx=None):
        self.files[path] = data if isinstance(data, bytes) else data.encode("utf-8")
        self.writes.append(path)

    def rm(self, path, recursive=False, ctx=None):
        self.removed.append(path)
        self.files.pop(path, None)
        return {}

    def grep(self, **kwargs):
        return {
            "matches": [
                {
                    "file": "peers/customer-wang-yue/memories/profile.md",
                    "line": 1,
                    "content": "wang",
                },
                {
                    "file": "peers/customer-zhang-xiaoxiao/memories/profile.md",
                    "line": 1,
                    "content": "zhang",
                },
            ],
            "files_scanned": 2,
        }

    def _entry(self, path):
        is_dir = path in self.dirs
        return {
            "name": path.rstrip("/").rsplit("/", 1)[-1],
            "size": 0 if is_dir else len(self.files[path]),
            "mode": 0o755,
            "modTime": _MOD_TIME,
            "isDir": is_dir,
        }


class _CountingVectorStore:
    def __init__(self):
        self.calls = []

    async def count(self, filter=None, ctx=None):
        self.calls.append((filter, ctx))
        return 7


@pytest.fixture
def fs():
    return VikingFS(agfs=_MemoryAGFS())


@pytest.fixture
def actor_ctx():
    return RequestContext(
        user=UserIdentifier("acct", "support_bot"),
        role=Role.USER,
        actor_peer_id="customer-wang-yue",
    )


def _other_peer_uri(suffix="memories/profile.md"):
    return f"viking://user/support_bot/peers/customer-zhang-xiaoxiao/{suffix}"


def _actor_peer_uri(suffix="memories/profile.md"):
    return f"viking://user/support_bot/peers/customer-wang-yue/{suffix}"


@pytest.mark.asyncio
async def test_actor_peer_view_filters_ls_peer_collection(fs, actor_ctx):
    entries = await fs.ls("viking://user/support_bot/peers", ctx=actor_ctx)

    assert [entry["uri"] for entry in entries] == [
        "viking://user/support_bot/peers/customer-wang-yue"
    ]


@pytest.mark.asyncio
async def test_actor_peer_view_filters_legacy_agent_collection(fs, actor_ctx):
    entries = await fs.ls("viking://agent", ctx=actor_ctx)

    assert [entry["uri"] for entry in entries] == ["viking://agent/customer-wang-yue"]
    assert (
        await fs.read_file(
            "viking://agent/customer-wang-yue/memories/profile.md",
            ctx=actor_ctx,
        )
        == "legacy-wang"
    )
    with pytest.raises(PermissionDeniedError):
        await fs.read_file(
            "viking://agent/customer-zhang-xiaoxiao/memories/profile.md",
            ctx=actor_ctx,
        )


@pytest.mark.asyncio
async def test_legacy_session_scope_merges_new_and_unmigrated_sessions(fs, actor_ctx):
    entries = await fs.ls("viking://session", ctx=actor_ctx)

    assert [entry["uri"] for entry in entries] == [
        "viking://session/duplicate",
        "viking://session/new-session",
        "viking://session/legacy-session",
        "viking://session/nested-session",
    ]

    assert (
        await fs.read_file("viking://session/duplicate/messages.jsonl", ctx=actor_ctx)
        == '{"role":"user","content":"new"}\n'
    )
    assert (
        await fs.read_file("viking://session/legacy-session/messages.jsonl", ctx=actor_ctx)
        == '{"role":"user","content":"legacy"}\n'
    )
    assert (
        await fs.read_file("viking://session/nested-session/messages.jsonl", ctx=actor_ctx)
        == '{"role":"user","content":"nested"}\n'
    )
    with pytest.raises(NotFoundError):
        await fs.read_file("viking://session/other-owned/messages.jsonl", ctx=actor_ctx)


@pytest.mark.asyncio
async def test_actor_peer_view_filters_tree_from_user_root(fs, actor_ctx):
    entries = await fs.tree(
        "viking://user/support_bot",
        ctx=actor_ctx,
        level_limit=None,
    )
    uris = {entry["uri"] for entry in entries}

    assert _actor_peer_uri() in uris
    assert _other_peer_uri() not in uris
    assert "viking://user/support_bot/resources/guide.md" in uris


@pytest.mark.asyncio
async def test_actor_peer_view_blocks_read_stat_and_write_to_other_peer(fs, actor_ctx):
    with pytest.raises(PermissionDeniedError):
        await fs.stat(_other_peer_uri(), ctx=actor_ctx)
    with pytest.raises(PermissionDeniedError):
        await fs.read_file(_other_peer_uri(), ctx=actor_ctx)
    with pytest.raises(PermissionDeniedError):
        await fs.write_file(_other_peer_uri(), "blocked", ctx=actor_ctx)

    await fs.write_file("viking://user/support_bot/resources/new.md", "allowed", ctx=actor_ctx)
    assert "/local/acct/user/support_bot/resources/new.md" in fs._async_agfs._client.writes


@pytest.mark.asyncio
async def test_actor_peer_view_stat_does_not_count_hidden_peer_roots(actor_ctx):
    vector_store = _CountingVectorStore()
    fs = VikingFS(agfs=_MemoryAGFS(), vector_store=vector_store)

    user_root = await fs.stat("viking://user/support_bot", ctx=actor_ctx)
    peer_collection = await fs.stat("viking://user/support_bot/peers", ctx=actor_ctx)
    user_resources = await fs.stat("viking://user/support_bot/resources", ctx=actor_ctx)

    assert "count" not in user_root
    assert "count" not in peer_collection
    assert user_resources["count"] == 7


@pytest.mark.asyncio
async def test_actor_peer_view_blocks_mutating_other_peer_and_peer_collection(fs, actor_ctx):
    with pytest.raises(PermissionDeniedError):
        await fs.rm(_other_peer_uri(), ctx=actor_ctx)
    with pytest.raises(PermissionDeniedError):
        await fs.mv(_actor_peer_uri(), _other_peer_uri(), ctx=actor_ctx)
    with pytest.raises(PermissionDeniedError):
        await fs.rm("viking://user/support_bot/peers", recursive=True, ctx=actor_ctx)
    with pytest.raises(PermissionDeniedError):
        await fs.rm("viking://user/support_bot", recursive=True, ctx=actor_ctx)


@pytest.mark.asyncio
async def test_actor_peer_view_filters_grep_matches(fs, actor_ctx):
    result = await fs.grep("viking://user/support_bot", pattern="profile", ctx=actor_ctx)

    assert [match["uri"] for match in result["matches"]] == [_actor_peer_uri()]
    assert result["files_scanned"] == 1
