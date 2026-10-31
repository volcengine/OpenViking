# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Compile task results must distinguish final review exclusions from write failures."""

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from vikingbot.compile import entry
from vikingbot.compile.hashing import content_hash
from vikingbot.compile.models import CompileLimits, CompileTask, SanitizedCompileRequest, utc_now
from vikingbot.compile.ops.finalize import FinalReview
from vikingbot.compile.service import BotCompileService
from vikingbot.compile.store import CompileTaskStore
from vikingbot.config.schema import Config, SessionKey
from vikingbot.sandbox.backends.direct import DirectBackend


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario", ["all_excluded", "all_excluded_skill", "partly_excluded", "conflict", "noop"]
)
async def test_compile_task_reports_final_review_outcome(tmp_path, monkeypatch, scenario):
    skill_target = scenario == "all_excluded_skill"
    paths = ("example/SKILL.md", "example/notes.md") if skill_target else ("first.md", "second.md")
    request = SanitizedCompileRequest.model_validate(
        {
            "from": ["viking://resources/source"],
            "to": "viking://agent/skills" if skill_target else "viking://resources/wiki",
            "skill": "viking://agent/skills/wiki",
            "instruction": "Summarize the source.",
        }
    )
    now = utc_now()
    task = CompileTask(
        task_id="cmp_test",
        principal_scope="test",
        sanitized_request=request,
        status="accepted",
        stage="queued",
        created_at=now,
        updated_at=now,
    )
    service: Any = BotCompileService.__new__(BotCompileService)
    service.store = CompileTaskStore(tmp_path / "state")
    service.limits = CompileLimits()
    service._model_slots = asyncio.Semaphore(1)
    service.agent_loop = SimpleNamespace(
        provider=SimpleNamespace(), model="test", temperature=0, tools={}
    )
    await service.store.create(task)

    sandbox = DirectBackend(
        Config.model_validate({}).sandbox,
        SessionKey(type="cli", channel_id="test", chat_id="test"),
        tmp_path / "workspace",
    )
    await sandbox.start()
    target = "viking://resources/wiki/second.md"
    conflicts = [{"uri": target, "code": "CONFLICT", "message": "target changed"}]
    client = SimpleNamespace(
        list_resources=AsyncMock(return_value=[]),
        batch_write=AsyncMock(
            return_value={
                "created": [] if scenario == "conflict" else [target],
                "updated": [],
                "unchanged": [],
                "conflicts": conflicts if scenario == "conflict" else [],
            }
        ),
    )

    async def submit_staged_outputs(runtime, _sources, _context):
        runtime.direct_output_format = "files"
        if scenario == "noop":
            runtime.noop = True
            return []
        references = []
        for name in paths:
            body = (
                "---\nname: example\ndescription: Example skill\n---\n# Example\n"
                if name.endswith("/SKILL.md")
                else f"# {name}\n"
            )
            reference = f"artifacts/{name}"
            await runtime.files.put(
                reference,
                {
                    "path": name,
                    "owner": "test",
                    "content": body,
                    "sha256": content_hash(body.encode()),
                    "base_hash": None,
                    "source_refs": [],
                },
            )
            references.append(reference)
        return references

    async def final_review(*_args, **_kwargs):
        excluded = {paths[0]: "Missing required source coverage"}
        if scenario.startswith("all_excluded"):
            excluded[paths[1]] = "Required companion output is missing"
        return FinalReview(excluded=excluded)

    monkeypatch.setattr(entry, "run", submit_staged_outputs)
    try:
        await service._run_pipeline(
            task_id=task.task_id,
            request=request,
            client=client,
            sandbox=sandbox,
            skill_text="Use the supplied source.",
            source_files=[],
            usage={},
            agent_runner=final_review,
        )
    finally:
        await sandbox.stop()

    stored = await service.store.get(task.task_id)
    assert stored is not None and stored.result is not None
    if scenario.startswith("all_excluded"):
        assert stored.status == "failed"
        assert stored.error is not None
        assert stored.error.code == "COMPILE_INCOMPLETE"
        assert "Final review excluded all 2" in stored.error.message
        assert "Missing required source coverage" in stored.error.message
        assert len(stored.result.warnings) == 2
        client.batch_write.assert_not_awaited()
    elif scenario == "partly_excluded":
        assert stored.status == "completed" and stored.error is None
        assert stored.result.created == [target]
        assert len(stored.result.warnings) == 1
    elif scenario == "conflict":
        assert stored.status == "failed"
        assert stored.error is not None
        assert stored.error.code == "COMPILE_INCOMPLETE"
        assert stored.error.message == "No output was acknowledged."
        assert stored.result.conflicts == conflicts
    else:
        assert stored.status == "completed" and stored.error is None
        client.batch_write.assert_not_awaited()
