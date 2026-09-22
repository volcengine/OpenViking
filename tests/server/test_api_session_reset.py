# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Reset keeps raw history without reviving context through late or future summaries."""

import json

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.session.session import Session
from openviking.storage.queuefs import SessionCommitMsg, get_queue_manager
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config import OPENVIKING_CONFIG_ENV
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    config = tmp_path / "ov.conf"
    config.write_text(
        json.dumps(
            {
                "storage": {
                    "workspace": str(tmp_path / "data"),
                    "agfs": {"backend": "local"},
                    "vectordb": {"backend": "local"},
                },
                "embedding": {
                    "dense": {
                        "provider": "openai",
                        "model": "test",
                        "api_key": "test-key",
                        "dimension": 2048,
                    }
                },
            }
        )
    )
    monkeypatch.setenv(OPENVIKING_CONFIG_ENV, str(config))
    OpenVikingConfigSingleton.reset_instance()
    yield
    OpenVikingConfigSingleton.reset_instance()


async def test_reset_empty_archive_survives_late_summary_and_next_commit(
    client, service, monkeypatch
):
    jobs = []
    summary_inputs = []

    async def enqueue(queue_name, data):
        jobs.append(SessionCommitMsg(**data))
        return data["task_id"]

    async def summarize(self, messages, latest_archive_overview="", **kwargs):
        summary_inputs.append((messages[0].content, latest_archive_overview))
        return "# Summary\n" + latest_archive_overview + "\n" + messages[0].content

    monkeypatch.setattr(get_queue_manager(), "enqueue", enqueue)
    monkeypatch.setattr(Session, "_generate_archive_summary_async", summarize)
    base = "/api/v1/sessions/reset-same-id"
    created = await client.post(
        "/api/v1/sessions",
        json={
            "session_id": "reset-same-id",
            "memory_policy": {"memory_types": [], "working_memory": {"enabled": True}},
        },
    )
    assert created.status_code == 200, created.text

    async def add(text):
        response = await client.post(f"{base}/messages", json={"role": "user", "content": text})
        assert response.status_code == 200, response.text

    async def commit(**options):
        response = await client.post(f"{base}/commit", json=options)
        assert response.status_code == 200, response.text
        return response.json()["result"]

    async def context():
        response = await client.get(f"{base}/context")
        assert response.status_code == 200, response.text
        return response.json()["result"]

    async def finish(job):
        ctx = RequestContext(user=UserIdentifier.from_dict(job.user), role=Role.USER)
        session = service.sessions.session(
            ctx, session_id=job.session_id, session_uri=job.session_uri
        )
        await session.load()
        assert await session.resume_queued_commit(job)
        assert await session._archive_terminal_state(job.archive_uri) == "completed"
        return session

    await add("old task one")
    await commit()  # Leave Phase 2 pending across reset.
    await add("old task two")
    reset = await commit(reset_context=True)
    assert reset["session_id"] == "reset-same-id"
    assert reset["archive_uri"].endswith("archive_002")
    empty = await context()
    assert empty["messages"] == []
    assert empty["latest_archive_overview"] == ""
    assert empty["stats"]["failedArchives"] == 0
    assert reset["reset_context"] is True

    # Complete both old jobs after the boundary: raw history remains, context stays empty.
    await finish(jobs[0])
    session = await finish(jobs[1])
    assert summary_inputs[1][1].endswith("old task one")
    assert (await context())["latest_archive_overview"] == ""
    fs = session._viking_fs
    boundary = f"{session._session_uri}/history/archive_003"
    assert not await fs.exists(f"{boundary}/.overview.md", ctx=session.ctx)
    assert not await fs.exists(f"{boundary}/messages.jsonl", ctx=session.ctx)
    assert json.loads(await fs.read_file(f"{boundary}/.done", ctx=session.ctx))["context_reset"]
    assert "old task two" in await fs.read_file(
        f"{jobs[1].archive_uri}/messages.jsonl", ctx=session.ctx
    )

    # Reset an already-empty session: confirmed, but no second boundary directory.
    assert (await commit(reset_context=True))["reset_context"] is True
    assert not await fs.exists(f"{session._session_uri}/history/archive_004", ctx=session.ctx)
    # Summarize new work without old overview fallback.
    await add("new task")
    assert (await context())["messages"][0]["parts"][0]["text"] == "new task"
    await commit()
    await finish(jobs[-1])
    assert summary_inputs[-1] == ("new task", "")
    final = await context()
    assert "new task" in final["latest_archive_overview"]
    assert "old task" not in final["latest_archive_overview"]
    assert len(jobs) == 3  # Empty reset archives do not enqueue extraction.


@pytest.mark.parametrize(
    "options",
    [
        {"reset_context": True, "keep_recent_count": 1},
        {"reset_context": True, "retention_mode": "turn_budget"},
        {"reset_context": "true"},
    ],
)
async def test_reset_rejects_retention_and_non_boolean_flag(client, options):
    response = await client.post("/api/v1/sessions/unused/commit", json=options)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"


async def test_reset_write_failure_does_not_block_next_archive(service, monkeypatch):
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.USER)
    session = service.sessions.session(ctx, session_id="reset-write-failure")
    await session.ensure_exists()
    fs = session._viking_fs
    write = fs.write_file
    failed = False

    async def fail_once(uri, content, **kwargs):
        nonlocal failed
        if uri.endswith("archive_001/.done") and not failed:
            failed = True
            raise OSError("transient reset write failure")
        return await write(uri, content, **kwargs)

    monkeypatch.setattr(fs, "write_file", fail_once)
    with pytest.raises(OSError, match="transient reset write failure"):
        await session.commit_async(reset_context=True)

    boundary = f"{session._session_uri}/history/archive_001"
    assert await session._archive_terminal_state(boundary) == "failed"
    assert await session._can_run_archive(2)
    # Retrying reset publishes a new terminal boundary rather than reusing the partial one.
    assert (await session.commit_async(reset_context=True))["reset_context"] is True
    context = await session.get_session_context()
    assert context["messages"] == []
    assert context["latest_archive_overview"] == ""
    assert context["stats"]["failedArchives"] == 0
