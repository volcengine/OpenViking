"""Regression tests for portable Bot session persistence paths."""

import hashlib
import json
import os
import unicodedata
from pathlib import Path
from types import SimpleNamespace

import pytest
from vikingbot.config.schema import SessionKey
from vikingbot.heartbeat.service import HeartbeatService
from vikingbot.openviking_mount.session_state import (
    OPENVIKING_SESSION_ID_FORMAT,
    get_openviking_session_id,
)
from vikingbot.sandbox.manager import SandboxManager
from vikingbot.session.manager import Session, SessionManager
from vikingbot.utils.session_paths import (
    portable_path_component,
    resolve_workspace_path,
    workspace_name,
)


def _write_session(
    path: Path,
    key: SessionKey,
    content: str,
    *,
    metadata: dict | None = None,
) -> None:
    metadata = {
        "_type": "metadata",
        "session_key": key.safe_name(),
        "created_at": "2026-08-20T00:00:00",
        "updated_at": "2026-08-20T00:00:01",
        "metadata": metadata if metadata is not None else {"source": content},
    }
    message = {
        "role": "user",
        "content": content,
        "timestamp": "2026-08-20T00:00:01",
    }
    path.write_text(
        f"{json.dumps(metadata, ensure_ascii=False)}\n{json.dumps(message, ensure_ascii=False)}\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("logical", "portable"),
    [
        ("order-123", "order-123"),
        ("订单😀", "订单😀"),
        ("order:123", "order%3A123"),
        ("folder/name", "folder%2Fname"),
        (r"folder\name", "folder%5Cname"),
        ('bad<>"|?*', "bad%3C%3E%22%7C%3F%2A"),
        ("literal%3A", "literal%253A"),
        ("line\nbreak", "line%0Abreak"),
        ("trailing.", "trailing%2E"),
        ("trailing ", "trailing%20"),
        ("CON", "%43ON"),
    ],
)
def test_portable_path_component_is_readable_and_windows_safe(logical, portable):
    result = portable_path_component(logical)
    digest = hashlib.sha256(logical.encode("utf-8", errors="surrogatepass")).hexdigest()[:16]

    assert result == f"{portable}~{digest}"
    assert not any(character in result for character in '<>:"/\\|?*')
    assert not result.endswith((".", " "))


def test_portable_path_component_bounds_long_names():
    first = portable_path_component("订单" * 200)
    second = portable_path_component("订单" * 199 + "号")

    assert len(first.encode("utf-8")) <= 180
    assert len(second.encode("utf-8")) <= 180
    assert first != second
    assert "订单" in first


def test_portable_path_components_resist_case_and_unicode_normalization_collisions():
    lower = portable_path_component("foo")
    upper = portable_path_component("FOO")
    composed = portable_path_component("é")
    decomposed = portable_path_component("e\u0301")

    assert lower.casefold() != upper.casefold()
    assert unicodedata.normalize("NFD", composed) != unicodedata.normalize("NFD", decomposed)


def test_openviking_storage_id_preserves_successful_legacy_namespace():
    key = SessionKey(type="cli", channel_id="default", chat_id="scope:order:123")
    logical_session_id = key.safe_name()
    session = Session(
        key=key,
        metadata={
            "openviking": {
                "session_id": logical_session_id,
                "last_sync_status": "success",
            }
        },
    )

    assert get_openviking_session_id(session) == logical_session_id
    assert session.metadata["openviking"].get("session_id_format") is None


def test_openviking_storage_id_migrates_known_failed_unsafe_legacy_id():
    key = SessionKey(type="cli", channel_id="default", chat_id="scope:order:123")
    logical_session_id = key.safe_name()
    session = Session(
        key=key,
        metadata={
            "openviking": {
                "session_id": logical_session_id,
                "last_sync_status": "error",
                "last_synced_local_index": -1,
                "last_commit_local_index": -1,
            }
        },
    )

    storage_session_id = get_openviking_session_id(session)

    assert storage_session_id == portable_path_component(logical_session_id)
    assert ":" not in storage_session_id
    assert session.metadata["openviking"]["logical_session_id"] == logical_session_id
    assert session.metadata["openviking"]["session_id_format"] == (OPENVIKING_SESSION_ID_FORMAT)


def test_new_session_uses_portable_filename_without_changing_logical_key(tmp_path):
    manager = SessionManager(tmp_path / "bot")
    key = SessionKey(
        type="cli",
        channel_id="default",
        chat_id="account:order__123/branch?",
    )
    session = Session(key=key, metadata={"openviking": {"session_id": key.safe_name()}})
    session.add_message("user", "hello")

    manager._save_unlocked(session)

    path = manager._get_session_path(key)
    assert path.name == f"{portable_path_component(key.safe_name())}.jsonl"
    assert path.name.startswith("cli__default__account%3Aorder__123%2Fbranch%3F~")
    assert path.exists()
    first_line = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert first_line["session_key"] == key.safe_name()
    assert first_line["session_key_fields"] == key.model_dump(mode="json")
    assert first_line["metadata"]["openviking"]["session_id"] == key.safe_name()

    restarted = SessionManager(tmp_path / "bot")
    loaded = restarted.get_or_create(key)
    assert loaded.key == key
    assert loaded.messages[0]["content"] == "hello"
    assert restarted.list_sessions()[0]["key"] == key


def test_legacy_metadata_preserves_double_underscore_in_chat_id(tmp_path):
    manager = SessionManager(tmp_path / "bot")
    key = SessionKey(type="cli", channel_id="default", chat_id="legacy__session")
    path = manager.sessions_dir / f"{key.safe_name()}.jsonl"
    _write_session(path, key, "legacy history")

    assert manager.get_or_create(key).key == key
    assert manager.list_sessions()[0]["key"] == key


def test_legacy_session_is_loaded_then_lazily_migrated_on_save(tmp_path):
    manager = SessionManager(tmp_path / "bot")
    key = SessionKey(type="cli", channel_id="default", chat_id="legacy%session")
    legacy_path = manager.sessions_dir / f"{key.safe_name()}.jsonl"
    canonical_path = manager._get_session_path(key)
    _write_session(legacy_path, key, "legacy history")

    loaded = manager.get_or_create(key)

    assert loaded.messages[0]["content"] == "legacy history"
    assert manager.has_persisted(key)
    assert legacy_path.exists()
    assert not canonical_path.exists()

    manager._save_unlocked(loaded)

    assert canonical_path.exists()
    assert not legacy_path.exists()
    assert SessionManager(tmp_path / "bot").get_or_create(key).messages[0]["content"] == (
        "legacy history"
    )


@pytest.mark.skipif(os.name == "nt", reason="Windows cannot create the legacy colon filename")
def test_legacy_openapi_session_migrates_without_changing_openviking_id(tmp_path):
    manager = SessionManager(tmp_path / "bot")
    key = SessionKey(
        type="cli",
        channel_id="default",
        chat_id="4ab668637bdb513f9384c8a8:order-123:7425fd71",
    )
    legacy_path = manager.sessions_dir / f"{key.safe_name()}.jsonl"
    canonical_path = manager._get_session_path(key)
    openviking_id = key.safe_name()
    _write_session(
        legacy_path,
        key,
        "existing OpenAPI history",
        metadata={"openviking": {"session_id": openviking_id}},
    )

    loaded = manager.get_or_create(key)
    manager._save_unlocked(loaded)

    assert canonical_path.name == f"{portable_path_component(key.safe_name())}.jsonl"
    assert canonical_path.exists()
    assert not legacy_path.exists()
    assert loaded.key == key
    assert loaded.metadata["openviking"]["session_id"] == openviking_id
    assert loaded.messages[0]["content"] == "existing OpenAPI history"


def test_delete_removes_canonical_and_legacy_files(tmp_path):
    manager = SessionManager(tmp_path / "bot")
    key = SessionKey(type="cli", channel_id="default", chat_id="both%paths")
    canonical_path = manager._get_session_path(key)
    legacy_path = manager.sessions_dir / f"{key.safe_name()}.jsonl"
    _write_session(canonical_path, key, "canonical")
    _write_session(legacy_path, key, "legacy")

    assert manager.delete(key)
    assert not canonical_path.exists()
    assert not legacy_path.exists()


def test_list_sessions_deduplicates_interrupted_migration(tmp_path):
    manager = SessionManager(tmp_path / "bot")
    key = SessionKey(type="cli", channel_id="default", chat_id="both%paths")
    canonical_path = manager._get_session_path(key)
    legacy_path = manager.sessions_dir / f"{key.safe_name()}.jsonl"
    _write_session(legacy_path, key, "legacy")
    _write_session(canonical_path, key, "canonical")

    sessions = manager.list_sessions()

    assert len(sessions) == 1
    assert sessions[0]["key"] == key
    assert sessions[0]["path"] == str(canonical_path)


def test_session_files_do_not_collide_after_casefold_or_unicode_normalization(tmp_path):
    manager = SessionManager(tmp_path / "bot")
    keys = [
        SessionKey(type="cli", channel_id="default", chat_id="foo"),
        SessionKey(type="cli", channel_id="default", chat_id="FOO"),
        SessionKey(type="cli", channel_id="default", chat_id="é"),
        SessionKey(type="cli", channel_id="default", chat_id="e\u0301"),
    ]

    paths = []
    for key in keys:
        session = Session(key=key)
        session.add_message("user", key.chat_id)
        manager._save_unlocked(session)
        paths.append(manager._get_session_path(key))

    assert len({path.name.casefold() for path in paths}) == len(paths)
    assert len({unicodedata.normalize("NFD", path.name) for path in paths}) == len(paths)
    assert all(path.exists() for path in paths)


def test_legacy_lookup_does_not_reuse_a_case_folded_different_session(tmp_path):
    manager = SessionManager(tmp_path / "bot")
    lower = SessionKey(type="cli", channel_id="default", chat_id="foo")
    upper = SessionKey(type="cli", channel_id="default", chat_id="FOO")
    _write_session(manager.sessions_dir / f"{lower.safe_name()}.jsonl", lower, "lower")

    assert manager._find_session_path(upper) is None


@pytest.mark.parametrize(
    ("mode", "logical_name"),
    [
        ("per-session", "cli__alerts:primary__order:123"),
        ("per-channel", "cli__alerts:primary"),
    ],
)
def test_workspace_names_are_portable_for_every_isolation_mode(mode, logical_name):
    key = SessionKey(type="cli", channel_id="alerts:primary", chat_id="order:123")

    result = workspace_name(key, mode, portable=True)

    assert result == portable_path_component(logical_name)
    assert result.startswith(logical_name.replace(":", "%3A"))
    assert ":" not in result


def test_sandbox_and_heartbeat_reuse_existing_legacy_workspace(tmp_path):
    key = SessionKey(type="cli", channel_id="default", chat_id="legacy%workspace")
    legacy_name = workspace_name(key, "per-session", portable=False)
    legacy_path = tmp_path / legacy_name
    legacy_path.mkdir()

    sandbox_manager = object.__new__(SandboxManager)
    sandbox_manager.workspace = tmp_path
    sandbox_manager.config = SimpleNamespace(sandbox=SimpleNamespace(mode="per-session"))
    assert sandbox_manager.to_workspace_id(key) == legacy_name
    assert sandbox_manager.get_workspace_path(key) == legacy_path

    session_manager = SimpleNamespace(
        list_sessions=lambda: [
            {
                "key": key,
                "created_at": "2026-08-20T00:00:00",
                "updated_at": "2026-08-20T00:00:01",
                "metadata": {},
            }
        ]
    )
    heartbeat = HeartbeatService(
        workspace=tmp_path,
        sandbox_mode="per-session",
        session_manager=session_manager,
    )
    assert heartbeat._get_all_workspaces() == {legacy_path: [key]}


def test_legacy_workspace_with_path_separators_is_not_reused(tmp_path):
    key = SessionKey(type="cli", channel_id="default", chat_id="nested/workspace")
    canonical = tmp_path / workspace_name(key, "per-session", portable=True)

    assert resolve_workspace_path(tmp_path, key, "per-session") == canonical
    assert canonical.parent == tmp_path


def test_legacy_workspace_lookup_requires_exact_logical_name(tmp_path):
    lower = SessionKey(type="cli", channel_id="default", chat_id="foo")
    upper = SessionKey(type="cli", channel_id="default", chat_id="FOO")
    (tmp_path / lower.safe_name()).mkdir()

    expected = tmp_path / workspace_name(upper, "per-session", portable=True)
    assert resolve_workspace_path(tmp_path, upper, "per-session") == expected
