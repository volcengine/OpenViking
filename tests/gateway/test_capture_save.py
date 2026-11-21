"""A finished turn is written to OpenViking by the request that ends it."""

import asyncio
import copy
import time

import pytest
from conftest import update_capture
from test_review_regressions import history, prepare

from openviking_gateway.capture import SWEEP_SECONDS, reset_capture
from openviking_gateway.client import VikingError
from openviking_gateway.kernel import MemoryKernel
from openviking_gateway.tool_protocols import ResponseCapture, reply_chain

# Later than any due time.
FOREVER = float("inf")


def reply(text="hello", calls=None):
    """A chat reply; ``calls`` hands the turn to client tools."""
    capture = ResponseCapture("chat")
    message = {"role": "assistant", "content": text}
    if calls:
        message["tool_calls"] = calls
    capture.nonstream(
        {"choices": [{"message": message, "finish_reason": "tool_calls" if calls else "stop"}]}
    )
    return capture


def call(identifier, name="lookup"):
    return {"id": identifier, "type": "function", "function": {"name": name, "arguments": "{}"}}


async def turn(kernel, credential, policy, messages, text="hello", **kwargs):
    """One request whose reply ends the turn; returns the request and the history after it."""
    request = await prepare(kernel, credential, policy, messages, **kwargs)
    response = reply(text)
    await kernel.completed(request, credential, response)
    saved = await kernel.save(request, credential, response)
    return request, [*messages, {"role": "assistant", "content": text}], saved


def texts(viking):
    return [[m["parts"][0]["text"] for m in batch] for batch in viking.writes]


class Keys:
    """The stored key records an idle commit reads its OpenViking key from."""

    def __init__(self, *credentials):
        self.records = {(c["account"], c["id"]): c for c in credentials}

    async def get(self, account, kind, identifier):
        return self.records.get((account, identifier))


@pytest.fixture
def keys(setup_kernel, credential):
    setup_kernel[0].capture.management = keys = Keys(credential)
    return keys


async def test_turn_is_written_when_its_reply_ends_it(setup_kernel, credential, policy):
    kernel, store, viking, _ = setup_kernel
    policy.update(recall=False)
    request, messages, saved = await turn(
        kernel, credential, policy, [{"role": "user", "content": "first"}]
    )
    assert saved == 2
    assert texts(viking) == [["first", "hello"]]
    state = (await store.capture.get(request.scope, request.session)).value
    assert viking.write_sessions == [state["ov_session"]]
    # No conversation text: where the session stands, and when its idle commit is due.
    assert set(state) == {
        "ov_session",
        "delivered",
        "answered",
        "created",
        "reason",
        "previous",
        "peers",
        "idle_at",
        "account",
        "credential_id",
    }
    # The next turn writes only what came after the cursor, into the same session.
    await turn(kernel, credential, policy, [*messages, {"role": "user", "content": "second"}])
    assert texts(viking)[1:] == [["second", "hello"]]
    assert len(set(viking.write_sessions)) == 1
    assert list(viking.sessions) == [state["ov_session"]]


async def settled(kernel):
    """Let the commits a turn started finish."""
    while kernel.capture.tasks:
        await asyncio.gather(*kernel.capture.tasks)


async def test_gateway_commits_at_the_threshold_and_once_idle(
    setup_kernel, credential, policy, keys
):
    kernel, store, viking, _ = setup_kernel
    policy.update(recall=False, commit_tokens=20, keep_recent_messages=1)
    _, messages, _ = await turn(kernel, credential, policy, [{"role": "user", "content": "hi"}])
    await settled(kernel)
    # Below the threshold only the idle commit waits; OpenViking's own commits are off.
    assert not viking.commits and viking.auto_commit == {viking.write_sessions[0]: False}
    long = {"role": "user", "content": "a much longer question " * 8}
    request, _, _ = await turn(kernel, credential, policy, [*messages, long])
    await settled(kernel)
    (session,) = set(viking.write_sessions)
    assert viking.commits == [(session, 1)]
    name = (request.scope, request.session)
    assert await store.capture.waiting(FOREVER) == [name] and list(kernel.capture.timers) == [name]
    # The idle time passes: everything is committed, once, and nothing waits any more.
    kernel.capture.idle(name)
    await settled(kernel)
    assert viking.commits == [(session, 1), (session, 0)]
    assert not await store.capture.waiting(FOREVER) and not kernel.capture.timers


async def test_idle_commit_is_left_to_a_later_turn(setup_kernel, credential, policy):
    kernel, store, viking, _ = setup_kernel
    policy.update(recall=False)
    request, _, _ = await turn(kernel, credential, policy, [{"role": "user", "content": "hi"}])
    name = (request.scope, request.session)
    # Another process saved a later turn of this conversation and waits on its own timer.
    await update_capture(store, request, idle_at=time.time() + 600)
    kernel.capture.idle(name)
    await settled(kernel)
    assert not viking.commits and await store.capture.waiting(FOREVER) == [name]


async def test_cut_save_leaves_the_waiting_idle_commit_alone(
    setup_kernel, credential, policy, keys
):
    kernel, store, viking, _ = setup_kernel
    policy.update(recall=False)
    request, messages, _ = await turn(
        kernel, credential, policy, [{"role": "user", "content": "hi"}]
    )
    name = (request.scope, request.session)
    messages += [
        {"role": "user", "content": "look it up"},
        {"role": "assistant", "content": "", "tool_calls": [call("c1")]},
        {"role": "tool", "tool_call_id": "c1", "content": "found"},
    ]
    later = await prepare(kernel, credential, policy, messages)
    # A cut inside the next turn moves the cursor; that turn then never ends.
    await kernel.capture.save_cut(later, credential, later.capture_chain[-1])
    assert len(viking.writes) == 2
    kernel.capture.idle(name)
    await settled(kernel)
    assert viking.commits == [(viking.write_sessions[0], 0)]


async def test_overdue_idle_commit_is_run_by_any_process(setup_kernel, credential, policy, keys):
    kernel, store, viking, _ = setup_kernel
    policy.update(recall=False)
    request, _, _ = await turn(kernel, credential, policy, [{"role": "user", "content": "hi"}])
    # The process that saved the turn is gone; another one looks for overdue commits.
    await kernel.capture.close()
    other = MemoryKernel(store, viking, management=keys)
    await other.capture.sweep()
    assert not viking.commits
    await update_capture(store, request, idle_at=time.time() - 2 * SWEEP_SECONDS)

    async def down(key, session, keep=0):
        raise VikingError("openviking_http_503", 503)

    commit, viking.commit = viking.commit, down
    await other.capture.sweep()
    # A failed commit stays due, and the next look runs it.
    assert len(await store.capture.waiting(FOREVER)) == 1
    viking.commit = commit
    await other.capture.sweep()
    assert viking.commits == [(viking.write_sessions[0], 0)]
    assert not await store.capture.waiting(FOREVER)


@pytest.mark.parametrize("stopped", [False, True], ids=["timer", "another process"])
async def test_revoked_key_commits_nothing(setup_kernel, credential, policy, keys, stopped):
    kernel, store, viking, _ = setup_kernel
    policy.update(recall=False)
    request, _, _ = await turn(kernel, credential, policy, [{"role": "user", "content": "hi"}])
    name = (request.scope, request.session)
    keys.records.clear()
    if stopped:
        await kernel.capture.close()
        await update_capture(store, request, idle_at=time.time() - 2 * SWEEP_SECONDS)
        await MemoryKernel(store, viking, management=keys).capture.sweep()
    else:
        kernel.capture.idle(name)
        await settled(kernel)
    # The turn stays saved and uncommitted; the conversation's next turn waits anew.
    assert not viking.commits and not await store.capture.waiting(FOREVER)
    assert "idle_at" not in (await store.capture.get(*name)).value


async def test_turn_saved_while_a_revoked_key_is_looked_up_keeps_its_wait(
    setup_kernel, credential, policy
):
    kernel, store, viking, _ = setup_kernel
    policy.update(recall=False)
    request, messages, _ = await turn(
        kernel, credential, policy, [{"role": "user", "content": "hi"}]
    )
    name = (request.scope, request.session)
    await kernel.capture.close()
    await update_capture(store, request, idle_at=time.time() - 2 * SWEEP_SECONDS)
    renewed = {**credential, "id": "new-key"}
    saver = MemoryKernel(store, viking, management=Keys(renewed))

    class Looking(Keys):
        async def get(self, account, kind, identifier):
            # The conversation goes on under a new key while the revoked one is looked up.
            await turn(saver, renewed, policy, [*messages, {"role": "user", "content": "more"}])
            return None

    await MemoryKernel(store, viking, management=Looking()).capture.sweep()
    state = (await store.capture.get(*name)).value
    assert state["credential_id"] == "new-key" and state["idle_at"] > time.time()
    assert not viking.commits and await store.capture.waiting(FOREVER) == [name]
    await saver.capture.close()


async def test_sweep_leaves_a_wait_that_a_turn_just_renewed(setup_kernel, credential, policy, keys):
    kernel, store, viking, _ = setup_kernel
    policy.update(recall=False)
    request, _, _ = await turn(kernel, credential, policy, [{"role": "user", "content": "hi"}])
    name = (request.scope, request.session)

    async def listed(before):
        # Listed as overdue just before a turn ended and set a later time.
        return [name]

    store.capture.waiting = listed
    await MemoryKernel(store, viking, management=keys).capture.sweep()
    assert not viking.commits and "idle_at" in (await store.capture.get(*name)).value


@pytest.mark.parametrize("status", [401, 404])
async def test_idle_commit_that_openviking_refuses_is_given_up(
    setup_kernel, credential, policy, keys, status
):
    kernel, store, viking, _ = setup_kernel
    policy.update(recall=False)
    request, _, _ = await turn(kernel, credential, policy, [{"role": "user", "content": "hi"}])

    async def refused(key, session, keep=0):
        raise VikingError(f"openviking_http_{status}", status)

    viking.commit = refused
    kernel.capture.idle((request.scope, request.session))
    await settled(kernel)
    # No later try changes that answer: nothing waits, and the next turn waits anew.
    assert not await store.capture.waiting(FOREVER)


async def test_failed_commit_does_not_fail_the_save(setup_kernel, credential, policy):
    kernel, _, viking, _ = setup_kernel
    policy.update(recall=False, commit_tokens=1)

    async def commit(key, session, keep=0):
        raise VikingError("openviking_http_503", 503)

    viking.commit = commit
    _, _, saved = await turn(kernel, credential, policy, [{"role": "user", "content": "hi"}])
    await settled(kernel)
    assert saved == 2 and len(kernel.capture.timers) == 1


async def test_openviking_commits_by_itself_when_asked(setup_kernel, credential, policy):
    _, store, viking, _ = setup_kernel
    kernel = MemoryKernel(store, viking, auto_commit=True)
    policy.update(recall=False, commit_tokens=1, keep_recent_messages=4, idle_seconds=900)
    await turn(kernel, credential, policy, [{"role": "user", "content": "first"}])
    ((session, created),) = viking.sessions.items()
    assert viking.auto_commit == {session: True}
    assert (created.commit_tokens, created.keep_recent_messages, created.idle_seconds) == (
        1,
        4,
        900,
    )
    # The gateway neither commits nor waits to.
    assert not viking.commits and not kernel.capture.timers and not kernel.capture.tasks


async def test_changed_setting_reaches_a_session_saved_before(setup_kernel, credential, policy):
    kernel, store, viking, _ = setup_kernel
    policy.update(recall=False)
    request, messages, _ = await turn(
        kernel, credential, policy, [{"role": "user", "content": "first"}]
    )
    await kernel.capture.close()
    switched = MemoryKernel(store, viking, auto_commit=True)
    await turn(switched, credential, policy, [*messages, {"role": "user", "content": "second"}])
    (session,) = set(viking.write_sessions)
    # The same session is set up again for OpenViking's commits, and nothing waits here.
    assert viking.auto_commit == {session: True}
    stored = (await store.capture.get(request.scope, request.session)).value
    assert stored["created"] == "openviking" and not await store.capture.waiting(FOREVER)


async def test_tool_handoff_saves_nothing_until_the_turn_ends(setup_kernel, credential, policy):
    kernel, _, viking, _ = setup_kernel
    policy.update(recall=False)
    messages = [{"role": "user", "content": "look it up"}]
    request = await prepare(kernel, credential, policy, messages)
    handoff = reply("", [call("c1")])
    assert await kernel.save(request, credential, handoff) == 0
    assert not viking.writes
    messages += [
        {"role": "assistant", "content": "", "tool_calls": [call("c1")]},
        {"role": "tool", "tool_call_id": "c1", "content": "found"},
    ]
    request = await prepare(kernel, credential, policy, messages)
    assert request.kind == "continuation"
    assert await kernel.save(request, credential, reply("done")) == 3
    (batch,) = viking.writes
    assert [m["role"] for m in batch] == ["user", "assistant", "assistant"]
    assert batch[1]["parts"][0]["tool_output"] == "found"


async def test_failed_write_is_caught_up_by_the_next_turn(setup_kernel, credential, policy):
    kernel, store, viking, _ = setup_kernel
    policy.update(recall=False)
    messages = [{"role": "user", "content": "first"}]
    request = await prepare(kernel, credential, policy, messages)
    viking.write_failure = VikingError("openviking_http_503", 503)
    with pytest.raises(VikingError):
        await kernel.save(request, credential, reply())
    assert (await store.capture.get(request.scope, request.session)).value["delivered"] == ""
    viking.write_failure = None
    messages += [{"role": "assistant", "content": "hello"}, {"role": "user", "content": "second"}]
    await turn(kernel, credential, policy, messages)
    assert texts(viking) == [["first", "hello", "second", "hello"]]


@pytest.mark.parametrize("change", ["regenerate", "edit", "compact"])
async def test_changed_history_goes_on_in_the_same_session(
    setup_kernel, credential, policy, change
):
    kernel, store, viking, _ = setup_kernel
    policy.update(recall=False)
    _, messages, _ = await turn(
        kernel, credential, policy, history(1) + [{"role": "user", "content": "next"}]
    )
    before = len(viking.writes)
    if change == "regenerate":
        changed = messages[:-1]
    elif change == "edit":
        changed = [*copy.deepcopy(messages), {"role": "user", "content": "more"}]
        changed[1]["content"] = "a corrected answer"
    else:
        changed = [
            {"role": "user", "content": "compact summary"},
            {"role": "assistant", "content": "continue"},
            {"role": "user", "content": "more"},
        ]
    request, _, _ = await turn(kernel, credential, policy, changed, text="again")
    # Only the turn itself is written again, never the history before it.
    assert texts(viking)[before:] == [[changed[-1]["content"], "again"]]
    assert len(set(viking.write_sessions)) == 1
    state = (await store.capture.get(request.scope, request.session)).value
    assert state["previous"] == [] and state["reason"] == ""


async def test_first_request_with_history_writes_all_of_it(setup_kernel, credential, policy):
    kernel, _, viking, _ = setup_kernel
    policy.update(recall=False)
    await turn(kernel, credential, policy, [*history(2), {"role": "user", "content": "next"}])
    assert texts(viking) == [["Question 0", "Answer 0", "Question 1", "Answer 1", "next", "hello"]]


async def test_long_turn_moves_the_cursor_after_each_batch(setup_kernel, credential, policy):
    kernel, store, viking, _ = setup_kernel
    policy.update(recall=False)
    messages = [*history(60), {"role": "user", "content": "next"}]
    request = await prepare(kernel, credential, policy, messages)
    writes = []

    async def write(key, session, batch):
        if writes:
            raise VikingError("openviking_timeout")
        writes.append(batch)

    viking.write = write
    with pytest.raises(VikingError):
        await kernel.save(request, credential, reply())
    assert len(writes[0]) == 100
    state = (await store.capture.get(request.scope, request.session)).value
    assert state["delivered"] == request.capture_chain[99]

    async def rest(key, session, batch):
        writes.append(batch)

    viking.write = rest
    assert await kernel.save(request, credential, reply()) == 22
    assert writes[1][0]["parts"][0]["text"] == "Question 50"


async def test_overlapping_saves_never_move_the_cursor_back(setup_kernel, credential, policy):
    kernel, store, viking, _ = setup_kernel
    policy.update(recall=False)
    first = [{"role": "user", "content": "first"}]
    early = await prepare(kernel, credential, policy, first)
    later = await prepare(
        kernel,
        credential,
        policy,
        [*first, {"role": "assistant", "content": "hello"}, {"role": "user", "content": "second"}],
    )
    write, pending = viking.write, [later]

    async def slow(key, session, batch):
        # The next turn ends, and saves, while this write is still in flight.
        if pending:
            await kernel.save(pending.pop(), credential, reply())
        await write(key, session, batch)

    viking.write = slow
    await kernel.save(early, credential, reply())
    state = (await store.capture.get(early.scope, early.session)).value
    assert state["delivered"] == reply_chain(later, reply().output)[-1]


@pytest.mark.parametrize("turns", [49, 75], ids=["one batch then two", "two batches each"])
async def test_overlapping_save_of_a_longer_history_writes_what_is_missing(
    setup_kernel, credential, policy, turns
):
    kernel, store, viking, _ = setup_kernel
    policy.update(recall=False)
    older = [*history(turns), {"role": "user", "content": "older"}]
    newer = [
        *older,
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "newer"},
    ]
    early = await prepare(kernel, credential, policy, older)
    later = await prepare(kernel, credential, policy, newer)
    write, pending = viking.write, [early]

    async def slow(key, session, batch):
        # The earlier turn's save runs to its end while this first write is in flight.
        if pending:
            await kernel.save(pending.pop(), credential, reply())
        return await write(key, session, batch)

    viking.write = slow
    # The batch in flight, then only what the earlier save did not write.
    assert await kernel.save(later, credential, reply("done")) == 102
    written = [m["parts"][0]["text"] for batch in viking.writes for m in batch]
    assert written.count("newer") == written.count("done") == 1
    assert len(written) == len(newer) + 1 + 100
    state = (await store.capture.get(later.scope, later.session)).value
    assert state["delivered"] == reply_chain(later, reply("done").output)[-1]
    # The wait of the turn that ended last is the one that stands.
    assert kernel.capture.timers[later.scope, later.session][1] == state["idle_at"]


async def test_overlapping_save_after_a_changed_history_writes_all_of_its_turn(
    setup_kernel, credential, policy
):
    kernel, store, viking, _ = setup_kernel
    policy.update(recall=False)
    steps = [
        message
        for i in range(120)
        for message in (
            {"role": "assistant", "content": f"step {i}", "tool_calls": [call(f"c{i}")]},
            {"role": "tool", "tool_call_id": f"c{i}", "content": "ok"},
        )
    ]
    older = [{"role": "user", "content": "task"}, *steps]
    newer = [
        *older,
        {"role": "assistant", "content": "finished"},
        {"role": "user", "content": "next"},
    ]
    early = await prepare(kernel, credential, policy, older)
    later = await prepare(kernel, credential, policy, newer)
    # The client rewrote its history: each save starts at its own turn's user message.
    await update_capture(store, early, delivered="gone")
    write, pending = viking.write, [later]

    async def slow(key, session, batch):
        # The next turn ends, and saves, while this first write is in flight.
        if pending:
            saved = await kernel.save(pending.pop(), credential, reply("done"))
            assert saved == 2
        return await write(key, session, batch)

    viking.write = slow
    assert await kernel.save(early, credential, reply("finished")) == 122
    written = [m["parts"][0]["text"] for batch in viking.writes for m in batch]
    assert written.count("step 119") == written.count("finished") == 1
    state = (await store.capture.get(early.scope, early.session)).value
    assert state["delivered"] == reply_chain(later, reply("done").output)[-1]


async def test_manual_reset_writes_the_whole_history_to_a_new_session(
    setup_kernel, credential, policy
):
    kernel, store, viking, _ = setup_kernel
    policy.update(recall=False)
    request, messages, _ = await turn(
        kernel, credential, policy, [{"role": "user", "content": "first"}]
    )
    old = (await store.capture.get(request.scope, request.session)).value["ov_session"]
    await reset_capture(store.capture, request.scope, request.session, "manual_reset")
    request, _, _ = await turn(
        kernel, credential, policy, [*messages, {"role": "user", "content": "second"}]
    )
    state = (await store.capture.get(request.scope, request.session)).value
    assert state["ov_session"] != old and state["previous"] == [old]
    assert viking.write_sessions == [old, state["ov_session"]]
    assert texts(viking)[1] == ["first", "hello", "second", "hello"]
    assert request.metrics["capture_reason"] == "manual_reset"


async def test_document_of_an_earlier_version_keeps_its_cursor_and_drops_its_queue(
    setup_kernel, credential, policy
):
    kernel, store, viking, _ = setup_kernel
    policy.update(recall=False)
    request, messages, _ = await turn(
        kernel, credential, policy, [{"role": "user", "content": "first"}]
    )
    state = (await store.capture.get(request.scope, request.session)).value
    await update_capture(
        store,
        request,
        created=False,
        pending=[{"anchor": "a", "messages": [{"role": "user", "parts": []}], "ready": 0}],
        retained=[],
        archive=None,
        error={"reason": "outage", "attempts": 5, "retry_at": 0},
        policy={},
    )
    request, _, _ = await turn(
        kernel, credential, policy, [*messages, {"role": "user", "content": "second"}]
    )
    stored = (await store.capture.get(request.scope, request.session)).value
    assert "pending" not in stored and "error" not in stored
    assert stored["ov_session"] == state["ov_session"]
    assert texts(viking)[1:] == [["second", "hello"]]
    # Its session, created with automatic commits off, gets the profile's settings.
    assert len(viking.sessions) == 1 and stored["created"] == "gateway"


async def test_capture_off_or_auxiliary_request_saves_nothing(setup_kernel, credential, policy):
    kernel, _, viking, _ = setup_kernel
    policy.update(recall=False, capture=False)
    _, _, saved = await turn(kernel, credential, policy, [{"role": "user", "content": "first"}])
    assert saved == 0 and not viking.writes and not viking.sessions
