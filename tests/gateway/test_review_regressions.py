"""Behavioral regressions from the gateway design review."""

import asyncio
import time
from contextlib import contextmanager

import pytest
from conftest import replay_records

from openviking_gateway.protocols import plugin_present
from openviking_gateway.storage import SQLiteKernelStore, SQLiteManagementStore
from openviking_gateway.tool_protocols import ResponseCapture


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
    assert (await store.capture.get(request.scope, request.session)) == old


async def test_reply_lookup_searches_its_index_without_statistics(setup_kernel, monkeypatch):
    # Stores are never ANALYZEd; scanning a user's records cost 20 ms at 1k sessions.
    _, store, _, _ = setup_kernel
    statements, connect = [], store.connect

    @contextmanager
    def traced():
        with connect() as c:
            c.set_trace_callback(statements.append)
            yield c
            c.set_trace_callback(None)

    monkeypatch.setattr(store, "connect", traced)
    await store.replay.replies("scope", ["a", "b"])
    with connect() as c:
        plan = [row[3] for row in c.execute("EXPLAIN QUERY PLAN " + statements[0])]
    assert any("INDEX replay_kinds (scope=? AND kind=? AND anchor=?)" in step for step in plan)


async def test_hot_read_ignores_unrelated_operational_documents(
    setup_kernel, credential, policy, monkeypatch
):
    kernel, store, _, _ = setup_kernel
    policy.update(recall=False)
    p = await prepare(kernel, credential, policy, history(1)[:1])

    def insert():
        # Encoding runs the encryptor on the event loop, so it must happen off the loop thread.
        with store.connect() as c:
            c.executemany(
                "INSERT INTO state(scope,key,value,version,touched) VALUES (?,?,?,1,?)",
                [
                    (p.scope, f"tool:unrelated-{i}", store.encode({"content": "old"}), time.time())
                    for i in range(1500)
                ],
            )

    await store.run(insert, write=True)
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
    from openviking_gateway.kernel import MemoryKernel

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
    from openviking_gateway.protocols import prefix_chain
    from openviking_gateway.storage import digest

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
        {
            "messages": [
                {
                    "role": "user",
                    "content": "Summary: the session began with an `<openviking-context>` block.",
                }
            ]
        },
        {
            "messages": [
                {
                    "role": "user",
                    "content": "```\n<openviking-context>m</openviking-context>\n```",
                }
            ]
        },
        {"messages": [{"role": "user", "content": "The <openviking-context> tag holds recall."}]},
    ],
)
def test_unrelated_content_cannot_disable_memory(body):
    assert not plugin_present(body, {})


async def test_anonymous_greetings_cannot_share_capture(setup_kernel, credential, policy):
    kernel, _, viking, _ = setup_kernel
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
        response = ResponseCapture("chat", {"role": "assistant", "content": "OK"}, complete=True)
        await kernel.completed(request, credential, response)
        assert await kernel.save(request, credential, response) == 4
    assert len(set(sessions)) == 2
    assert len(set(viking.write_sessions)) == 2
    assert "Project A" not in str(viking.writes[1])


async def test_management_get_uses_one_row_and_response_expiry(setup_kernel, monkeypatch):
    _, store, _, encryption = setup_kernel
    management = SQLiteManagementStore(store.path.parent / "management.sqlite3", encryption)
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
