# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Crash/recovery tests for the durable fenced commit Phase 2 state machine."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from typing import Any

import pytest

from openviking.message.part import TextPart
from openviking.server.identity import RequestContext, Role
from openviking.session import session as session_module
from openviking_cli.exceptions import FailedPreconditionError
from openviking_cli.session.user_id import UserIdentifier


class _SimulatedProcessLoss(BaseException):
    """Model process loss without Python 3.11 Task's SystemExit re-raise."""


class _AbsoluteActiveCount:
    def __init__(self) -> None:
        self.value = 0
        self.apply_calls = 0
        self.fail_plan = False
        self.fail_apply = False

    async def plan_active_count_targets(
        self,
        _ctx: RequestContext,
        uris: list[str],
    ) -> list[dict[str, Any]]:
        if self.fail_plan:
            raise TimeoutError("active-count plan unavailable")
        if not uris:
            return []
        return [{"id": "context-1", "uri": uris[0], "target": self.value + 1}]

    async def ensure_active_count_targets(
        self,
        _ctx: RequestContext,
        targets: list[dict[str, Any]],
    ) -> int:
        self.apply_calls += 1
        if self.fail_apply:
            raise TimeoutError("active-count apply unavailable")
        for target in targets:
            self.value = max(self.value, int(target["target"]))
        return len(targets)


def _ctx() -> RequestContext:
    return RequestContext(
        user=UserIdentifier.the_default_user("test_user"),
        role=Role.ROOT,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "crash_stage",
    [
        "long_term",
        "execution",
        "relation_link",
        "relations_applied",
        "active_count_applied",
        "active_count",
        "meta_applied",
        "meta",
        "done",
    ],
)
async def test_phase2_durable_receipts_do_not_duplicate_after_crash(
    service,
    monkeypatch: pytest.MonkeyPatch,
    crash_stage: str,
) -> None:
    config = SimpleNamespace(
        memory=SimpleNamespace(
            extraction_enabled=True,
            session_skill_extraction_enabled=True,
        )
    )
    monkeypatch.setattr(session_module, "get_openviking_config", lambda: config)

    async def summary(*_args: Any, **_kwargs: Any) -> str:
        return "# Stable summary\n\nDurable output"

    effects = {"long_term": 0, "execution": 0}
    memory_uris: set[str] = set()

    async def long_term(**_kwargs: Any) -> dict[str, Any]:
        effects["long_term"] += 1
        memory_uris.add("viking://user/memories/profile/fixed")
        return {"contexts": [SimpleNamespace(category="profile")]}

    async def execution(**_kwargs: Any) -> dict[str, Any]:
        effects["execution"] += 1
        memory_uris.add("viking://user/memories/preferences/fixed")
        return {
            "contexts": [SimpleNamespace(category="preferences")],
            "session_skills": [{"uri": "viking://agent/skills/fixed"}],
        }

    compressor = service.sessions._session_compressor
    monkeypatch.setattr(compressor, "extract_long_term_memories", long_term)
    monkeypatch.setattr(compressor, "extract_execution_memories", execution)
    monkeypatch.setattr(
        session_module.Session,
        "_generate_archive_summary_async",
        summary,
    )
    active = _AbsoluteActiveCount()
    monkeypatch.setattr(service.sessions, "_vikingdb", active)

    session_id = f"alice_{hashlib.sha256(crash_stage.encode()).hexdigest()[:48]}"
    session = await service.sessions.create(_ctx(), session_id)
    session.add_message("user", [TextPart("archive exactly once")])
    session.used(
        contexts=["viking://resources/catalog.md"],
        operation_id="used-1",
    )
    operation_id = f"phase2-{crash_stage}"
    phase1 = await session.commit_async(
        keep_recent_count=0,
        operation_id=operation_id,
        operation_sequence_id=1,
    )
    assert phase1["archived"] is True

    crashed = False

    async def crash_after_receipt(stage: str, _operation_id: str) -> None:
        nonlocal crashed
        if stage == crash_stage and not crashed:
            crashed = True
            raise SystemExit(93)

    monkeypatch.setattr(
        session_module,
        "after_fenced_phase2_stage",
        crash_after_receipt,
    )
    with pytest.raises(SystemExit, match="93"):
        await service.sessions.run_fenced_commit_work(
            session_id,
            _ctx(),
            operation_id=operation_id,
            task_id=phase1["task_id"],
            archive_uri=phase1["archive_uri"],
        )

    monkeypatch.setattr(
        session_module,
        "after_fenced_phase2_stage",
        lambda _stage, _operation: None,
    )
    assert (
        await service.sessions.run_fenced_commit_work(
            session_id,
            _ctx(),
            operation_id=operation_id,
            task_id=phase1["task_id"],
            archive_uri=phase1["archive_uri"],
        )
        == "completed"
    )

    assert effects == {"long_term": 1, "execution": 1}
    assert memory_uris == {
        "viking://user/memories/profile/fixed",
        "viking://user/memories/preferences/fixed",
    }
    assert active.value == 1
    assert active.apply_calls == (
        2 if crash_stage == "active_count_applied" else 1
    )

    loaded = await service.sessions.get(session_id, _ctx())
    assert loaded.meta.memories_extracted == {
        "total": 2,
        "profile": 1,
        "preferences": 1,
    }
    manifest_uri = (
        f"{loaded.uri}/.fenced-commits/"
        f"{hashlib.sha256(operation_id.encode()).hexdigest()}.json"
    )
    terminal_manifest = json.loads(
        await service.viking_fs.read_file(manifest_uri, ctx=_ctx())
    )
    for payload_field in (
        "archive_messages",
        "retained_messages",
        "usage_records",
        "memory_policy",
    ):
        assert payload_field not in terminal_manifest
    assert terminal_manifest["phase2"]["state"] == "completed"
    relation_entries = await service.viking_fs.get_relation_table(
        loaded.uri, ctx=_ctx()
    )
    fenced_links = [
        entry
        for entry in relation_entries
        if str(entry.id).startswith("fenced_")
        and entry.uris == ["viking://resources/catalog.md"]
    ]
    assert len(fenced_links) == 1


@pytest.mark.asyncio
async def test_phase2_started_without_receipt_is_ambiguous_and_not_replayed(
    service,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = SimpleNamespace(
        memory=SimpleNamespace(
            extraction_enabled=True,
            session_skill_extraction_enabled=False,
        )
    )
    monkeypatch.setattr(session_module, "get_openviking_config", lambda: config)
    monkeypatch.setattr(
        session_module.Session,
        "_generate_archive_summary_async",
        lambda *_args, **_kwargs: _async_value("# Summary"),
    )
    calls = 0

    async def lost_after_effect(**_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        raise _SimulatedProcessLoss("after-effect receipt loss")

    compressor = service.sessions._session_compressor
    monkeypatch.setattr(
        compressor,
        "extract_long_term_memories",
        lost_after_effect,
    )
    active = _AbsoluteActiveCount()
    monkeypatch.setattr(service.sessions, "_vikingdb", active)

    session_id = "alice_" + "a" * 48
    session = await service.sessions.create(_ctx(), session_id)
    session.add_message("user", [TextPart("ambiguous effect")])
    phase1 = await session.commit_async(
        operation_id="ambiguous-operation",
        operation_sequence_id=2,
    )
    with pytest.raises(_SimulatedProcessLoss, match="after-effect receipt loss"):
        await service.sessions.run_fenced_commit_work(
            session_id,
            _ctx(),
            operation_id="ambiguous-operation",
            task_id=phase1["task_id"],
            archive_uri=phase1["archive_uri"],
        )
    manifest_uri = (
        f"{session.uri}/.fenced-commits/"
        f"{hashlib.sha256(b'ambiguous-operation').hexdigest()}.json"
    )
    started_manifest = json.loads(
        await service.viking_fs.read_file(manifest_uri, ctx=_ctx())
    )
    assert started_manifest["phase2"]["steps"]["long_term"]["state"] == "started"
    with pytest.raises(FailedPreconditionError) as ambiguous:
        await service.sessions.run_fenced_commit_work(
            session_id,
            _ctx(),
            operation_id="ambiguous-operation",
            task_id=phase1["task_id"],
            archive_uri=phase1["archive_uri"],
        )
    assert ambiguous.value.details["reason"] == "commit_phase2_effect_ambiguous"
    assert calls == 1


async def _async_value(value: Any) -> Any:
    return value


@pytest.mark.asyncio
async def test_deterministic_relation_read_timeout_never_overwrites_table(
    service,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = "alice_" + "b" * 48
    session = await service.sessions.create(_ctx(), session_id)
    await service.viking_fs.link_deterministic(
        session.uri,
        "viking://resources/original.md",
        link_id="fenced_original",
        reason="fenced-session-commit",
        ctx=_ctx(),
    )
    original_read = service.viking_fs._async_agfs.read
    writes = 0
    original_write = service.viking_fs._write_relation_table

    async def timeout_read(path: str, *args: Any, **kwargs: Any) -> Any:
        if path.endswith("/.relations.json"):
            raise TimeoutError("simulated relation read timeout")
        return await original_read(path, *args, **kwargs)

    async def counted_write(*args: Any, **kwargs: Any) -> None:
        nonlocal writes
        writes += 1
        await original_write(*args, **kwargs)

    monkeypatch.setattr(service.viking_fs._async_agfs, "read", timeout_read)
    monkeypatch.setattr(service.viking_fs, "_write_relation_table", counted_write)
    with pytest.raises(TimeoutError, match="simulated relation read timeout"):
        await service.viking_fs.link_deterministic(
            session.uri,
            "viking://resources/new.md",
            link_id="fenced_new",
            reason="fenced-session-commit",
            ctx=_ctx(),
        )
    assert writes == 0

    monkeypatch.setattr(service.viking_fs._async_agfs, "read", original_read)
    entries = await service.viking_fs.get_relation_table(session.uri, ctx=_ctx())
    assert [(entry.id, entry.uris) for entry in entries] == [
        ("fenced_original", ["viking://resources/original.md"])
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["plan", "apply"])
async def test_active_count_backend_failure_never_completes_receipt(
    service,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    config = SimpleNamespace(
        memory=SimpleNamespace(
            extraction_enabled=False,
            session_skill_extraction_enabled=False,
        )
    )
    monkeypatch.setattr(session_module, "get_openviking_config", lambda: config)
    monkeypatch.setattr(
        session_module.Session,
        "_generate_archive_summary_async",
        lambda *_args, **_kwargs: _async_value("# Summary"),
    )
    active = _AbsoluteActiveCount()
    active.fail_plan = failure == "plan"
    active.fail_apply = failure == "apply"
    monkeypatch.setattr(service.sessions, "_vikingdb", active)

    session_id = "alice_" + ("c" if failure == "plan" else "d") * 48
    session = await service.sessions.create(_ctx(), session_id)
    session.add_message("user", [TextPart("strict active count")])
    session.used(
        contexts=["viking://resources/catalog.md"],
        operation_id="used-active",
    )
    operation_id = f"active-failure-{failure}"
    phase1 = await session.commit_async(
        operation_id=operation_id,
        operation_sequence_id=3,
    )
    with pytest.raises(TimeoutError, match="active-count"):
        await service.sessions.run_fenced_commit_work(
            session_id,
            _ctx(),
            operation_id=operation_id,
            task_id=phase1["task_id"],
            archive_uri=phase1["archive_uri"],
        )

    manifest_uri = (
        f"{session.uri}/.fenced-commits/"
        f"{hashlib.sha256(operation_id.encode()).hexdigest()}.json"
    )
    manifest = json.loads(
        await service.viking_fs.read_file(manifest_uri, ctx=_ctx())
    )
    assert manifest["phase2"]["steps"].get(
        "active_count", {"state": "pending"}
    )["state"] in {
        "pending",
        "planned",
    }
    assert active.value == 0

    active.fail_plan = False
    active.fail_apply = False
    assert (
        await service.sessions.run_fenced_commit_work(
            session_id,
            _ctx(),
            operation_id=operation_id,
            task_id=phase1["task_id"],
            archive_uri=phase1["archive_uri"],
        )
        == "completed"
    )
    assert active.value == 1


@pytest.mark.asyncio
async def test_fenced_phase1_history_transport_failure_never_reuses_archive(
    service,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = "alice_" + "e" * 48
    session = await service.sessions.create(_ctx(), session_id)
    session.add_message("user", [TextPart("first archive")])
    first = await session.commit_async(
        operation_id="strict-history-first",
        operation_sequence_id=10,
    )
    assert first["archive_uri"].endswith("/archive_001")

    current = await service.sessions.get(session_id, _ctx())
    current.add_message("user", [TextPart("must not overwrite")])
    original_ls = service.viking_fs.ls
    history_calls = 0

    async def fail_second_history_ls(uri: str, *args: Any, **kwargs: Any):
        nonlocal history_calls
        if uri == f"{current.uri}/history":
            history_calls += 1
            if history_calls == 2:
                raise TimeoutError("history listing unavailable")
        return await original_ls(uri, *args, **kwargs)

    monkeypatch.setattr(service.viking_fs, "ls", fail_second_history_ls)
    with pytest.raises(TimeoutError, match="history listing unavailable"):
        await service.sessions.commit_async(
            session_id,
            _ctx(),
            operation_id="strict-history-second",
            operation_sequence_id=11,
        )
    assert history_calls == 2
    assert not await service.viking_fs.exists(
        f"{current.uri}/history/archive_002/messages.jsonl",
        ctx=_ctx(),
    )


@pytest.mark.asyncio
async def test_previous_overview_transport_failure_retries_before_receipts(
    service,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = SimpleNamespace(
        memory=SimpleNamespace(
            extraction_enabled=False,
            session_skill_extraction_enabled=False,
        )
    )
    monkeypatch.setattr(session_module, "get_openviking_config", lambda: config)
    monkeypatch.setattr(
        session_module.Session,
        "_generate_archive_summary_async",
        lambda *_args, **_kwargs: _async_value("# Stable overview"),
    )
    session_id = "alice_" + "f" * 48
    session = await service.sessions.create(_ctx(), session_id)
    session.add_message("user", [TextPart("previous")])
    first = await session.commit_async(
        operation_id="overview-first",
        operation_sequence_id=20,
    )
    assert (
        await service.sessions.run_fenced_commit_work(
            session_id,
            _ctx(),
            operation_id="overview-first",
            task_id=first["task_id"],
            archive_uri=first["archive_uri"],
        )
        == "completed"
    )

    current = await service.sessions.get(session_id, _ctx())
    current.add_message("user", [TextPart("next")])
    second = await current.commit_async(
        operation_id="overview-second",
        operation_sequence_id=21,
    )
    original_read = service.viking_fs.read_file

    async def fail_previous_overview(uri: str, *args: Any, **kwargs: Any):
        if uri == f"{first['archive_uri']}/.overview.md":
            raise TimeoutError("overview transport unavailable")
        return await original_read(uri, *args, **kwargs)

    monkeypatch.setattr(service.viking_fs, "read_file", fail_previous_overview)
    with pytest.raises(TimeoutError, match="overview transport unavailable"):
        await service.sessions.run_fenced_commit_work(
            session_id,
            _ctx(),
            operation_id="overview-second",
            task_id=second["task_id"],
            archive_uri=second["archive_uri"],
        )
    manifest_uri = (
        f"{current.uri}/.fenced-commits/"
        f"{hashlib.sha256(b'overview-second').hexdigest()}.json"
    )
    manifest = json.loads(await original_read(manifest_uri, ctx=_ctx()))
    assert manifest.get("phase2", {}).get("steps", {}) == {}

    monkeypatch.setattr(service.viking_fs, "read_file", original_read)
    assert (
        await service.sessions.run_fenced_commit_work(
            session_id,
            _ctx(),
            operation_id="overview-second",
            task_id=second["task_id"],
            archive_uri=second["archive_uri"],
        )
        == "completed"
    )


@pytest.mark.asyncio
async def test_legacy_and_fenced_commits_share_padded_contiguous_archive_names(
    service,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_phase2(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(
        session_module.Session,
        "_run_memory_extraction",
        no_phase2,
    )
    session_id = "alice_" + "1" * 48
    session = await service.sessions.create(_ctx(), session_id)
    session.add_message("user", [TextPart("legacy one")])
    legacy_one = await session.commit_async()

    session = await service.sessions.get(session_id, _ctx())
    session.add_message("user", [TextPart("fenced two")])
    fenced_two = await session.commit_async(
        operation_id="mixed-fenced-two",
        operation_sequence_id=999,
    )

    session = await service.sessions.get(session_id, _ctx())
    session.add_message("user", [TextPart("legacy three")])
    legacy_three = await session.commit_async()

    assert [
        legacy_one["archive_uri"].rsplit("/", 1)[-1],
        fenced_two["archive_uri"].rsplit("/", 1)[-1],
        legacy_three["archive_uri"].rsplit("/", 1)[-1],
    ] == ["archive_001", "archive_002", "archive_003"]
