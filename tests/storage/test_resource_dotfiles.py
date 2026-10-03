# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""User dot-files in resource trees are listed and indexed like any file.

Resource trees are ``viking://resources``, ``viking://user/{id}/resources`` and
``viking://user/{id}/peers/{peer}/resources``. Only OpenViking metadata
(``.abstract.md``, ``.overview.md``, ...) and storage-layer internal files stay
hidden there. Other namespaces keep hiding every dot-file because they store
their own dot-named metadata.
"""

from __future__ import annotations

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.index_consistency import _file_candidates
from openviking.storage.internal_names import (
    is_hidden_entry_name,
    may_list_user_dotfiles,
)
from openviking.storage.queuefs.semantic_executor import SemanticTreeExecutor
from openviking.storage.viking_fs import VikingFS
from openviking_cli.session.user_id import UserIdentifier

USER_DOTFILES = (".gitlab-ci.yml", ".dockerignore", ".gitignore", ".helmignore")
METADATA_DOTFILES = (
    ".abstract.md",
    ".overview.md",
    ".relations.json",
    ".watch_tasks.json",
    ".path.ovlock",
    ".exact.ovlock.app.py.0123456789abcdef",
    ".redirect.json",
    ".sync_log.json",
)


class _DummyAgfs:
    pass


def _default_ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)


async def _async_true(*_args, **_kwargs):
    return True


@pytest.fixture
def fs(monkeypatch):
    viking_fs = VikingFS(agfs=_DummyAgfs())
    monkeypatch.setattr(viking_fs, "_ctx_or_default", lambda _ctx=None: _default_ctx())
    monkeypatch.setattr(
        viking_fs,
        "_uri_to_path",
        lambda uri, **_kwargs: uri.replace("viking://", "/local/test_account/").rstrip("/"),
    )
    monkeypatch.setattr(
        viking_fs,
        "_read_paths",
        lambda uri, **_kwargs: [uri.replace("viking://", "/local/test_account/").rstrip("/")],
    )
    monkeypatch.setattr(
        viking_fs,
        "_path_to_uri",
        lambda path, **_kwargs: path.replace("/local/test_account/", "viking://"),
    )
    monkeypatch.setattr(viking_fs, "_agfs_path_exists", _async_true)
    monkeypatch.setattr(viking_fs, "_read_path_visible", _async_true)
    monkeypatch.setattr(viking_fs, "_ensure_access", _async_true)
    monkeypatch.setattr(viking_fs, "_is_accessible", lambda _uri, _ctx: True)
    return viking_fs


def _file(name: str) -> dict:
    return {"name": name, "size": 1, "mode": 0o644, "modTime": "", "isDir": False}


def _dir(name: str) -> dict:
    return {"name": name, "size": 0, "mode": 0o755, "modTime": "", "isDir": True}


def _dir_entries() -> list[dict]:
    return [
        _dir(".helm"),
        _file("app.py"),
        *(_file(name) for name in USER_DOTFILES),
        *(_file(name) for name in METADATA_DOTFILES),
    ]


# ── is_hidden_entry_name ──


RESOURCE_TREES = (
    "viking://resources",
    "viking://user/alice/resources",
    "viking://user/alice/peers/web-visitor/resources",
)


@pytest.mark.parametrize("root", RESOURCE_TREES)
@pytest.mark.parametrize("name", USER_DOTFILES + (".helm",))
def test_user_dotfiles_are_visible_in_resource_trees(root, name):
    assert not is_hidden_entry_name(name, f"{root}/repo/{name}")
    assert not is_hidden_entry_name(name, f"{root}/repo")
    assert not is_hidden_entry_name(name, root)


@pytest.mark.parametrize("name", METADATA_DOTFILES + (".watch_tasks.json.tmp",))
def test_metadata_dotfiles_stay_hidden_in_resources(name):
    for root in RESOURCE_TREES:
        assert is_hidden_entry_name(name, f"{root}/repo/{name}")


@pytest.mark.parametrize(
    "uri",
    [
        "viking://user/alice/memories/.gitlab-ci.yml",
        "viking://user/alice/privacy/skill/demo/.gitlab-ci.yml",
        "viking://user/alice/sessions/s1/.gitlab-ci.yml",
        "viking://user/alice/peers/web-visitor/memories/.gitlab-ci.yml",
        "viking://user/alice/.gitlab-ci.yml",
        "viking://user/alice/skills/demo/.gitlab-ci.yml",
        "viking://agent/skills/demo/.gitlab-ci.yml",
        "viking://session/s1/.gitlab-ci.yml",
        "viking://temp/import/.gitlab-ci.yml",
    ],
)
def test_dotfiles_stay_hidden_outside_resources(uri):
    assert is_hidden_entry_name(".gitlab-ci.yml", uri)
    assert is_hidden_entry_name(".meta.json", uri)


def test_regular_names_are_never_hidden():
    assert not is_hidden_entry_name("app.py", "viking://resources/repo")
    assert not is_hidden_entry_name("notes.md", "viking://user/default/memories")


@pytest.mark.parametrize(
    "uri,expected",
    [
        ("viking://", True),
        ("viking://resources", True),
        ("viking://resources/repo", True),
        ("viking://user", True),
        ("viking://user/alice", True),
        ("viking://user/alice/resources/repo", True),
        ("viking://user/alice/peers", True),
        ("viking://user/alice/peers/web-visitor", True),
        ("viking://user/alice/peers/web-visitor/resources", True),
        ("viking://user/alice/memories", False),
        ("viking://user/alice/peers/web-visitor/memories", False),
        ("viking://user/alice/privacy", False),
        ("viking://agent", False),
    ],
)
def test_may_list_user_dotfiles(uri, expected):
    assert may_list_user_dotfiles(uri) is expected


def test_resource_metadata_names_cover_writers():
    from openviking.parse.image_rewrite import IMAGE_MAPPINGS_FILENAME
    from openviking.parse.output import ARTIFACT_MANIFEST_NAME
    from openviking.resource.watch_storage import (
        WATCH_TASK_STORAGE_BAK_URI,
        WATCH_TASK_STORAGE_TMP_URI,
        WATCH_TASK_STORAGE_URI,
    )
    from openviking.server.skill_source_metadata import SOURCE_METADATA_FILENAME
    from openviking.storage.internal_names import WEBDAV_RESERVED_FILENAMES
    from openviking.storage.resource_rnfv import CONTROL_BASENAMES

    watch_names = {
        uri.rsplit("/", 1)[-1]
        for uri in (WATCH_TASK_STORAGE_URI, WATCH_TASK_STORAGE_BAK_URI, WATCH_TASK_STORAGE_TMP_URI)
    }
    written_names = {
        IMAGE_MAPPINGS_FILENAME,
        ARTIFACT_MANIFEST_NAME,
        SOURCE_METADATA_FILENAME,
        *watch_names,
        *CONTROL_BASENAMES,
        *WEBDAV_RESERVED_FILENAMES,
    }
    assert {
        name for name in written_names if not is_hidden_entry_name(name, "viking://resources/x")
    } == set()


# ── ls ──


@pytest.mark.asyncio
async def test_ls_resources_lists_user_dotfiles_only(monkeypatch, fs):
    async def fake_ls_entries(_path, **_kwargs):
        return _dir_entries()

    monkeypatch.setattr(fs, "_ls_entries", fake_ls_entries)

    result = await fs.ls("viking://resources/repo", ctx=_default_ctx())

    assert sorted(entry["name"] for entry in result) == sorted([".helm", "app.py", *USER_DOTFILES])


@pytest.mark.asyncio
async def test_ls_show_all_hidden_still_lists_metadata(monkeypatch, fs):
    async def fake_ls_entries(_path, **_kwargs):
        return [_file("app.py"), _file(".abstract.md"), _file(".gitlab-ci.yml")]

    monkeypatch.setattr(fs, "_ls_entries", fake_ls_entries)

    result = await fs.ls("viking://resources/repo", show_all_hidden=True, ctx=_default_ctx())

    assert {entry["name"] for entry in result} == {"app.py", ".abstract.md", ".gitlab-ci.yml"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "uri", ["viking://user/alice/resources/repo", "viking://user/alice/peers/bot/resources/repo"]
)
async def test_ls_user_and_peer_resources_list_user_dotfiles(monkeypatch, fs, uri):
    async def fake_ls_entries(_path, **_kwargs):
        return _dir_entries()

    monkeypatch.setattr(fs, "_ls_entries", fake_ls_entries)

    result = await fs.ls(uri, ctx=_default_ctx())

    assert sorted(entry["name"] for entry in result) == sorted([".helm", "app.py", *USER_DOTFILES])


@pytest.mark.asyncio
async def test_ls_outside_resources_keeps_hiding_dotfiles(monkeypatch, fs):
    async def fake_ls_entries(_path, **_kwargs):
        return [_file("profile.md"), _file(".meta.json"), _file(".gitlab-ci.yml"), _dir(".cfg")]

    monkeypatch.setattr(fs, "_ls_entries", fake_ls_entries)

    result = await fs.ls("viking://user/default/memories", ctx=_default_ctx())

    assert {entry["name"] for entry in result} == {"profile.md", ".cfg"}


# ── tree ──


def _tree_entry(base_path: str, name: str, *, is_dir: bool = False) -> dict:
    return {
        "path": f"{base_path}/{name}",
        "rel_path": name,
        "info": {
            "name": name,
            "size": 0 if is_dir else 1,
            "mode": 0o755 if is_dir else 0o644,
            "modTime": "",
            "isDir": is_dir,
        },
        "extra": {},
    }


@pytest.mark.asyncio
async def test_tree_resources_requests_hidden_entries_and_filters_metadata(monkeypatch, fs):
    captured = {}

    async def fake_tree_directory(path, **kwargs):
        captured["show_hidden"] = kwargs.get("show_hidden")
        if kwargs.get("offset"):
            return []
        return [_tree_entry(path, entry["name"], is_dir=entry["isDir"]) for entry in _dir_entries()]

    monkeypatch.setattr(fs._async_agfs, "tree_directory", fake_tree_directory)

    result = await fs.tree("viking://resources/repo", ctx=_default_ctx())

    assert captured["show_hidden"] is True
    assert sorted(entry["name"] for entry in result) == sorted([".helm", "app.py", *USER_DOTFILES])


@pytest.mark.asyncio
async def test_tree_outside_resources_keeps_backend_hidden_filter(monkeypatch, fs):
    captured = {}

    async def fake_tree_directory(path, **kwargs):
        captured["show_hidden"] = kwargs.get("show_hidden")
        return [_tree_entry(path, "profile.md")]

    monkeypatch.setattr(fs._async_agfs, "tree_directory", fake_tree_directory)

    result = await fs.tree("viking://user/default/memories", ctx=_default_ctx())

    assert captured["show_hidden"] is False
    assert [entry["name"] for entry in result] == ["profile.md"]


@pytest.mark.asyncio
async def test_tree_from_account_root_hides_dotfiles_outside_resources(monkeypatch, fs):
    async def fake_tree_directory(path, **kwargs):
        assert kwargs.get("show_hidden") is True
        return [
            _tree_entry(path, "resources", is_dir=True),
            _tree_entry(f"{path}/resources", ".gitlab-ci.yml"),
            _tree_entry(f"{path}/resources", ".overview.md"),
            _tree_entry(path, "user", is_dir=True),
            _tree_entry(f"{path}/user", ".meta.json"),
        ]

    monkeypatch.setattr(fs._async_agfs, "tree_directory", fake_tree_directory)

    result = await fs.tree("viking://", ctx=_default_ctx())

    assert [entry["uri"] for entry in result] == [
        "viking://resources",
        "viking://resources/.gitlab-ci.yml",
        "viking://user",
    ]


@pytest.mark.asyncio
async def test_tree_from_user_root_shows_dotfiles_only_in_resource_trees(monkeypatch, fs):
    async def fake_tree_directory(path, **kwargs):
        assert kwargs.get("show_hidden") is True
        return [
            _tree_entry(path, "memories", is_dir=True),
            _tree_entry(f"{path}/memories", ".gitlab-ci.yml"),
            _tree_entry(path, "privacy", is_dir=True),
            _tree_entry(f"{path}/privacy", ".meta.json"),
            _tree_entry(path, "resources", is_dir=True),
            _tree_entry(f"{path}/resources", ".gitlab-ci.yml"),
            _tree_entry(f"{path}/resources", ".overview.md"),
        ]

    monkeypatch.setattr(fs._async_agfs, "tree_directory", fake_tree_directory)

    result = await fs.tree("viking://user/alice", ctx=_default_ctx())

    assert [entry["uri"] for entry in result] == [
        "viking://user/alice/memories",
        "viking://user/alice/privacy",
        "viking://user/alice/resources",
        "viking://user/alice/resources/.gitlab-ci.yml",
    ]


@pytest.mark.asyncio
async def test_recursive_ls_lists_user_dotfiles_in_nested_dirs(monkeypatch, fs):
    from openviking.service.fs_service import FSService

    async def fake_tree_directory(path, **kwargs):
        assert kwargs.get("show_hidden") is True
        return [
            _tree_entry(path, ".gitlab-ci.yml"),
            _tree_entry(path, ".overview.md"),
            _tree_entry(path, "deploy", is_dir=True),
            _tree_entry(f"{path}/deploy", ".helmignore"),
            _tree_entry(f"{path}/deploy", ".abstract.md"),
            _tree_entry(f"{path}/deploy", ".path.ovlock"),
        ]

    monkeypatch.setattr(fs._async_agfs, "tree_directory", fake_tree_directory)

    page = await FSService(viking_fs=fs).ls(
        "viking://resources/repo", ctx=_default_ctx(), recursive=True
    )

    assert [entry["uri"] for entry in page.entries] == [
        "viking://resources/repo/.gitlab-ci.yml",
        "viking://resources/repo/deploy",
        "viking://resources/repo/deploy/.helmignore",
    ]


# ── glob (filesystem engine) ──


@pytest.mark.asyncio
async def test_local_glob_resources_matches_user_dotfiles(monkeypatch, fs):
    captured = {}

    async def fake_glob_directory(path, pattern, **kwargs):
        captured["show_hidden"] = kwargs.get("show_hidden")
        entries = [
            {"path": f"{path}/{name}", "rel_path": name, "name": name, "is_dir": False}
            for name in ("app.yml", ".gitlab-ci.yml", ".overview.md", ".path.ovlock")
        ]
        return {"entries": entries, "next_token": None}

    monkeypatch.setattr(fs._async_agfs, "glob_directory", fake_glob_directory)

    result = await fs.glob("*", uri="viking://resources/repo", ctx=_default_ctx())

    assert captured["show_hidden"] is True
    assert sorted(result["matches"]) == [
        "viking://resources/repo/.gitlab-ci.yml",
        "viking://resources/repo/app.yml",
    ]


@pytest.mark.asyncio
async def test_local_glob_outside_resources_keeps_backend_hidden_filter(monkeypatch, fs):
    captured = {}

    async def fake_glob_directory(path, pattern, **kwargs):
        captured["show_hidden"] = kwargs.get("show_hidden")
        return {
            "entries": [
                {"path": f"{path}/a.md", "rel_path": "a.md", "name": "a.md", "is_dir": False}
            ],
            "next_token": None,
        }

    monkeypatch.setattr(fs._async_agfs, "glob_directory", fake_glob_directory)

    result = await fs.glob("*.md", uri="viking://user/default/memories", ctx=_default_ctx())

    assert captured["show_hidden"] is False
    assert result["matches"] == ["viking://user/default/memories/a.md"]


# ── semantic traversal and index expectations ──


class _LsFS:
    def __init__(self, entries):
        self._entries = entries

    async def ls(self, uri, node_limit=None, ctx=None):
        return self._entries


@pytest.mark.asyncio
async def test_semantic_list_dir_includes_resource_dotfiles(monkeypatch):
    entries = [_dir(".helm"), _file("app.py"), _file(".gitlab-ci.yml"), _file(".overview.md")]
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_executor.get_viking_fs", lambda: _LsFS(entries)
    )
    executor = SemanticTreeExecutor(
        processor=None, context_type="resource", max_concurrent_llm=1, ctx=None
    )

    dirs, files = await executor._list_dir("viking://resources/repo", from_hint="test")

    assert dirs == ["viking://resources/repo/.helm"]
    assert files == ["viking://resources/repo/.gitlab-ci.yml", "viking://resources/repo/app.py"]


@pytest.mark.asyncio
async def test_semantic_list_dir_skips_dot_entries_outside_resources(monkeypatch):
    entries = [_dir(".cfg"), _file("profile.md"), _file(".meta.json")]
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_executor.get_viking_fs", lambda: _LsFS(entries)
    )
    executor = SemanticTreeExecutor(
        processor=None, context_type="memory", max_concurrent_llm=1, ctx=None
    )

    dirs, files = await executor._list_dir("viking://user/default/memories", from_hint="test")

    assert dirs == []
    assert files == ["viking://user/default/memories/profile.md"]


def test_index_expectations_include_resource_dotfiles():
    entries = [
        {"uri": f"viking://resources/repo/{name}", "rel_path": name, "name": name, "size": 1}
        for name in ("app.py", ".gitlab-ci.yml", ".abstract.md", ".overview.md")
    ]

    candidates = _file_candidates("viking://resources/repo", entries)

    assert sorted(name for _, _, name in candidates) == [".gitlab-ci.yml", "app.py"]


# ── vectorization paths ──


class _VectorizeRecorder:
    def __init__(self):
        self.files = []

    async def __call__(self, *, file_path, **_kwargs):
        self.files.append(file_path)
        return True


class _VectorizeFS:
    def __init__(self, entries):
        self._entries = entries

    async def exists(self, _uri, ctx=None):
        return False

    async def ls(self, _uri, node_limit=None, ctx=None):
        return self._entries

    async def tree(self, _uri, node_limit=None, level_limit=None, ctx=None):
        return self._entries


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "root", ["viking://resources/repo", "viking://user/alice/peers/bot/resources/repo"]
)
async def test_index_resource_vectorizes_user_dotfiles(monkeypatch, root):
    from openviking.utils import embedding_utils

    recorder = _VectorizeRecorder()
    entries = [_file("app.py"), _file(".gitlab-ci.yml"), _file(".abstract.md"), _dir(".helm")]
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: _VectorizeFS(entries))
    monkeypatch.setattr(embedding_utils, "vectorize_file", recorder)

    await embedding_utils.index_resource(root, ctx=_default_ctx())

    assert sorted(recorder.files) == [f"{root}/.gitlab-ci.yml", f"{root}/app.py"]


@pytest.mark.asyncio
async def test_resource_processor_vectorizes_user_dotfiles(monkeypatch):
    from types import SimpleNamespace

    from openviking.utils import resource_processor
    from openviking.utils.resource_processor import ResourceProcessor

    root = "viking://user/alice/resources/repo"
    recorder = _VectorizeRecorder()
    entries = [
        {"uri": f"{root}/{name}", "name": name, "isDir": False}
        for name in ("app.py", ".gitlab-ci.yml", ".overview.md", ".path.ovlock")
    ]
    monkeypatch.setattr(resource_processor, "get_viking_fs", lambda: _VectorizeFS(entries))
    monkeypatch.setattr(resource_processor, "vectorize_file", recorder)
    monkeypatch.setattr(
        resource_processor,
        "get_openviking_config",
        lambda: SimpleNamespace(
            queue_workers=SimpleNamespace(
                add_resource=SimpleNamespace(file_vectorization_concurrency=4)
            )
        ),
    )

    await ResourceProcessor.__new__(ResourceProcessor)._vectorize_resource_files(
        root, ctx=_default_ctx()
    )

    assert sorted(recorder.files) == [f"{root}/.gitlab-ci.yml", f"{root}/app.py"]


@pytest.mark.asyncio
async def test_ovpack_import_vectorizes_user_dotfiles(monkeypatch):
    from openviking.storage.ovpack import operations

    root = "viking://resources/pack"
    recorder = _VectorizeRecorder()

    async def no_text(*_args, **_kwargs):
        return ""

    monkeypatch.setattr(operations, "vectorize_file", recorder)
    monkeypatch.setattr(operations, "read_text_if_exists", no_text)
    entries = [
        {"uri": f"{root}/{name}", "name": name, "rel_path": name, "isDir": False}
        for name in ("app.py", ".gitlab-ci.yml", ".abstract.md")
    ]

    await operations._enqueue_direct_vectorization(None, root, _default_ctx(), entries=entries)

    assert sorted(recorder.files) == [f"{root}/.gitlab-ci.yml", f"{root}/app.py"]


def test_semantic_artifact_dir_includes_user_dotfiles(monkeypatch):
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_executor.get_viking_fs", lambda: _LsFS([])
    )
    executor = SemanticTreeExecutor(
        processor=None,
        context_type="resource",
        max_concurrent_llm=1,
        ctx=None,
        artifact_files=[
            "app.py",
            ".gitlab-ci.yml",
            ".artifact_manifest.json",
            ".helm/values.yaml",
            "docs/.image_mappings.json",
        ],
    )
    executor._root_uri = "viking://resources/repo"

    dirs, files = executor._list_artifact_dir("viking://resources/repo")

    assert dirs == ["viking://resources/repo/.helm", "viking://resources/repo/docs"]
    assert files == ["viking://resources/repo/.gitlab-ci.yml", "viking://resources/repo/app.py"]
