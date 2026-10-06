"""Behavioral regressions from the gateway design review."""

import asyncio
import time

import pytest
from conftest import make_due, replay_records

from context_gateway.capture import MAX_ATTEMPTS, CaptureWorker
from context_gateway.client import VikingError
from context_gateway.protocols import plugin_present
from context_gateway.storage import ManagementStore, SQLiteKernelStore
from context_gateway.tool_protocols import ResponseCapture


async def prepare(kernel, credential, policy, messages, **body):
    return await kernel.prepare(
        {"model": "model", "messages": messages, **body},
        "chat",
        {"x-openviking-session": "review"},
        credential,
        {"id": "upstream"},
        policy,
    )


def history(turns):
    return [
        message
        for i in range(turns)
        for message in (
            {"role": "user", "content": f"Question {i}"},
            {"role": "assistant", "content": f"Answer {i}"},
        )
    ]


async def worker_for(store, encryption, credential, viking):
    management = ManagementStore(store.path.parent / "management.sqlite3", encryption)
    await management.initialize()
    await management.save("tenant", "keys", credential["id"], credential)
    return CaptureWorker(store, management, viking)


async def never_summarize(prepared, body):
    pytest.fail("No compaction was due")


@pytest.mark.parametrize("turns", [0, 4])
async def test_screenshot_does_not_trigger_compaction(setup_kernel, credential, policy, turns):
    kernel, _, _, _ = setup_kernel
    policy.update(recall=False, context_window=128000)
    messages = [
        *history(turns),
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Read this screenshot"},
                {"type": "image", "source": {"type": "base64", "data": "A" * (1024 * 1024)}},
            ],
        },
    ]
    body = {"model": "model", "messages": messages}
    result = await asyncio.wait_for(
        kernel.prepare(
            body,
            "anthropic",
            {"x-openviking-session": "image"},
            credential,
            {"id": "upstream"},
            policy,
            summarize=never_summarize,
        ),
        2,
    )
    assert result.body == body
    assert "degradation" not in result.metrics
    # Base64 media counts like an image, not like its text length.
    assert result.context_tokens < 4000 and result.context_window == 128000


@pytest.mark.parametrize(
    "model,window", [("small", 2000), ("alias", 2000), ("large", 1000000), ("unknown", 1000000)]
)
async def test_context_window_follows_the_resolved_model(
    setup_kernel, credential, policy, model, window
):
    kernel, store, _, _ = setup_kernel
    policy.update(recall=False)
    upstream = {
        "id": "upstream",
        "aliases": {"alias": "small"},
        "context_windows": {"small": 2000, "large": 1000000},
    }
    headers = {"x-openviking-session": "model-window"}
    body = {"model": model, "messages": [{"role": "user", "content": "hello"}]}
    first = await kernel.prepare(body, "chat", headers, credential, upstream, policy)
    reply = {"role": "assistant", "content": "hi"}
    await kernel.completed(
        first, credential, ResponseCapture("chat", reply, usage={"input_tokens": 1950})
    )
    body["messages"] += [reply, {"role": "user", "content": "more"}]
    calls = []

    async def summarize(prepared, request):
        calls.append(request)
        return {"choices": [{"message": {"content": "Earlier: hello"}, "finish_reason": "stop"}]}

    second = await kernel.prepare(
        body, "chat", headers, credential, upstream, policy, summarize=summarize
    )
    assert second.context_window == window and second.metrics["context_tokens"] > 1950
    assert len(calls) == (window == 2000)
    assert bool(await replay_records(store, second, "replacement")) == (window == 2000)


async def test_system_tokens_do_not_trigger_commits(setup_kernel, credential, policy):
    kernel, store, viking, encryption = setup_kernel
    policy.update(recall=False, keep_recent_messages=0)

    async def write(*_):
        return {"pending_tokens": 50}

    viking.write = write
    worker = await worker_for(store, encryption, credential, viking)
    for turn in range(1, 5):
        p = await prepare(
            kernel,
            credential,
            policy,
            [*history(turn), {"role": "user", "content": "next"}],
            system="Tool instructions " * 10000,
        )
        await kernel.completed(
            p, credential, ResponseCapture("chat", usage={"input_tokens": 90000})
        )
        assert await worker.once()
    assert viking.commits == []


async def test_pending_archive_blocks_repeated_commit(setup_kernel, credential, policy):
    kernel, store, viking, encryption = setup_kernel
    policy.update(recall=False, commit_tokens=1, keep_recent_messages=0)
    worker = await worker_for(store, encryption, credential, viking)
    for turn in range(1, 4):
        await prepare(
            kernel, credential, policy, [*history(turn), {"role": "user", "content": "next"}]
        )
        assert await worker.once()
    assert len(viking.commits) == 1


async def test_capture_retry_blocks_later_turns_and_recovers(setup_kernel, credential, policy):
    kernel, store, viking, encryption = setup_kernel
    policy.update(recall=False)
    request = await prepare(
        kernel, credential, policy, [*history(2), {"role": "user", "content": "next"}]
    )
    worker = await worker_for(store, encryption, credential, viking)
    attempted = []

    async def fail(key, session, messages):
        attempted.append(messages[0]["parts"][0]["text"])
        raise RuntimeError("temporary outage")

    viking.write = fail
    assert await worker.once()
    assert not await worker.once()  # second turn may not overtake the delayed retry
    for _ in range(MAX_ATTEMPTS - 1):
        await make_due(store)
        assert await worker.once()
    assert attempted == ["Question 0"] * MAX_ATTEMPTS
    assert not await worker.once()
    next_request = await prepare(
        kernel, credential, policy, [*history(3), {"role": "user", "content": "more"}]
    )
    assert next_request.metrics["capture_status"] == "paused"
    assert (await store.capture.get(request.scope, request.session)).value["error"]

    async def recovered(key, session, messages):
        attempted.append(messages[0]["parts"][0]["text"])
        return {"pending_tokens": 10}

    viking.write = recovered
    await make_due(store)
    assert await worker.once()
    assert attempted[-3:] == ["Question 0", "Question 1", "Question 2"]
    assert not (await store.capture.get(request.scope, request.session)).value["error"]


async def test_capture_log_records_only_status_changes(setup_kernel, credential, policy, caplog):
    kernel, store, viking, encryption = setup_kernel
    policy.update(recall=False)
    await prepare(kernel, credential, policy, [*history(1), {"role": "user", "content": "next"}])
    worker = await worker_for(store, encryption, credential, viking)
    failure = [RuntimeError("temporary outage")]

    async def fail(key, session, messages):
        raise failure[0]

    async def changes():
        logs = await worker.management.logs("tenant")
        return [
            (log["capture_status"], log["capture_reason"])
            for log in reversed(logs)
            if log["kind"] == "capture"
        ]

    viking.write = fail
    for _ in range(MAX_ATTEMPTS + 3):  # Paused retries every RECOVERY_SECONDS.
        assert await worker.once()
        await make_due(store)
    assert await changes() == [("retrying", "delivery_failed"), ("paused", "delivery_failed")]
    warnings = [r for r in caplog.records if r.levelname == "WARNING" and "Capture" in r.message]
    assert len(warnings) == 2
    failure[0] = VikingError("connection_lost")
    for _ in range(2):
        assert await worker.once()
        await make_due(store)
    assert (await changes())[2:] == [("paused", "connection_lost")]

    async def recovered(key, session, messages):
        return {"pending_tokens": 10}

    viking.write = recovered
    assert await worker.once()
    assert (await changes())[2:] == [
        ("paused", "connection_lost"),
        ("active", "delivery_recovered"),
    ]


async def test_capture_retry_recovers_in_order(setup_kernel, credential, policy):
    kernel, store, viking, encryption = setup_kernel
    policy.update(recall=False)
    await prepare(kernel, credential, policy, [*history(2), {"role": "user", "content": "next"}])
    worker = await worker_for(store, encryption, credential, viking)
    attempted, delivered = [], []

    async def write(key, session, messages):
        text = messages[0]["parts"][0]["text"]
        attempted.append(text)
        if len(attempted) == 1:
            raise RuntimeError("temporary outage")
        delivered.append(text)
        return {"pending_tokens": 20}

    viking.write = write
    assert await worker.once()
    await make_due(store)
    assert await worker.once()
    assert delivered == ["Question 0", "Question 1"]


async def test_idle_capture_does_not_mix_a_regenerated_branch(setup_kernel, credential, policy):
    kernel, store, viking, encryption = setup_kernel
    policy.update(recall=False)
    p = await prepare(kernel, credential, policy, history(1)[:1])
    await kernel.completed(p, credential, ResponseCapture("chat", history(1)[1], complete=True))
    await make_due(store)
    worker = await worker_for(store, encryption, credential, viking)
    await worker.once()
    messages = [
        history(1)[0],
        {"role": "assistant", "content": "Edited answer"},
        {"role": "user", "content": "next"},
    ]
    await prepare(kernel, credential, policy, messages)
    await worker.once()
    assert len(viking.writes) == 2
    assert viking.write_sessions[0] != viking.write_sessions[1]
    assert "Edited answer" in str(viking.writes[1])
    assert not (await store.capture.get(p.scope, p.session)).value["error"]


async def test_repeated_prepare_has_one_read_and_no_capture_write(
    setup_kernel, credential, policy, monkeypatch
):
    kernel, store, _, _ = setup_kernel
    policy.update(recall=False)
    messages = [*history(5), {"role": "user", "content": "next"}]
    request = await prepare(kernel, credential, policy, messages)
    old = await store.capture.get(request.scope, request.session)
    calls = []
    run = store.run

    async def tracked(fn, *args, **kwargs):
        calls.append((fn.__name__, kwargs.get("write", False)))
        return await run(fn, *args, **kwargs)

    monkeypatch.setattr(store, "run", tracked)
    await prepare(kernel, credential, policy, messages)
    assert calls == [("replies", False), ("read_batch", False)]
    assert len(old.value["pending"]) == 5


async def test_hot_read_ignores_unrelated_operational_documents(
    setup_kernel, credential, policy, monkeypatch
):
    kernel, store, _, _ = setup_kernel
    policy.update(recall=False)
    p = await prepare(kernel, credential, policy, history(1)[:1])
    with store.connect() as c:
        c.executemany(
            "INSERT INTO state(scope,key,value,version,touched) VALUES (?,?,?,1,?)",
            [
                (p.scope, f"tool:unrelated-{i}", store.encode({"content": "old"}), time.time())
                for i in range(1500)
            ],
        )
    decoded = []
    original = store.decode

    def decode(value):
        decoded.append(1)
        return original(value)

    monkeypatch.setattr(store, "decode", decode)
    await prepare(kernel, credential, policy, history(1)[:1])
    assert len(decoded) <= 3


async def test_conditional_budget_is_atomic_across_stores(setup_kernel, credential, policy):
    kernel, store, viking, encryption = setup_kernel
    from context_gateway.kernel import MemoryKernel

    other = MemoryKernel(SQLiteKernelStore(store.path, encryption), viking)
    policy.update(session_max_tokens=160, capture=False)

    async def recall(key, query, policy, exclude, budget):
        return {
            "entries": [{"uri": "viking://test/" + query, "text": "memory " * 8}],
            "rendered": "memory " * 8,
        }

    viking.recall = recall
    # The budget is per context window, so the branches share their first message.
    branches = [[*history(1), {"role": "user", "content": f"Question {i}"}] for i in range(12)]
    await asyncio.gather(
        *(
            prepare(kernel if i % 2 else other, credential, policy, messages)
            for i, messages in enumerate(branches)
        )
    )
    from context_gateway.protocols import prefix_chain
    from context_gateway.storage import digest

    anchors = [prefix_chain(messages)[-1] for messages in branches]
    records = await store.replay.read(digest("tenant\0alice\0chat"), digest("review"), anchors)
    assert 0 < sum(value["tokens"] for value in records.values()) <= 160
    other.store.close()


@pytest.mark.parametrize(
    "body",
    [
        {"messages": [{"role": "assistant", "content": "See <openviking-context> in the docs"}]},
        {
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "file", "file": {"name": "memory_notes.txt"}}],
                }
            ]
        },
        {"tools": [{"function": {"name": "ov_other_tool"}}]},
        {
            "messages": [
                {"role": "tool", "content": "<openviking-context>quoted</openviking-context>"}
            ]
        },
    ],
)
def test_unrelated_content_cannot_disable_memory(body):
    assert not plugin_present(body, {})


async def test_anonymous_greetings_cannot_share_capture_or_archive(
    setup_kernel, credential, policy
):
    kernel, store, _, _ = setup_kernel
    policy.update(gateway_tools=True)
    sessions = []
    for answer in ("Project A", "Project B"):
        body = {
            "messages": [
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": answer},
                {"role": "user", "content": "继续"},
            ]
        }
        request = await kernel.prepare(body, "chat", {}, credential, {"id": "upstream"}, policy)
        sessions.append(request.session)
        assert request.tools_active
        await kernel.completed(
            request,
            credential,
            ResponseCapture("chat", {"role": "assistant", "content": "OK"}, complete=True),
        )
    assert len(set(sessions)) == 2
    assert await store.capture.claim() is not None


async def test_management_get_uses_one_row_and_response_expiry(setup_kernel, monkeypatch):
    _, store, _, encryption = setup_kernel
    management = ManagementStore(store.path.parent / "management.sqlite3", encryption)
    await management.initialize()
    for i in range(30):
        await management.save("tenant", "responses", str(i), {"upstream_id": "test"}, ttl=100)
    calls = []
    decode = management.decode
    monkeypatch.setattr(management, "decode", lambda value: (calls.append(1), decode(value))[1])
    assert (await management.get("tenant", "responses", "5"))["upstream_id"] == "test"
    assert len(calls) == 1
    await management.save("tenant", "responses", "expired", {"upstream_id": "test"}, ttl=-1)
    assert await management.get("tenant", "responses", "expired") is None
    await management.expire_logs(time.time())
    with management.connect() as c:
        assert not c.execute("SELECT 1 FROM objects WHERE id='expired'").fetchone()
