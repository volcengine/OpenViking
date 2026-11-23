"""Cross-pod session, tenant, delivery and encryption acceptance scenarios."""

import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import pytest

from .harness import (
    ADMIN,
    CHAT,
    MODELS,
    account,
    chat,
    control,
    credential_keys,
    document,
    message_text,
    records,
    scope,
    session_keys,
    wait_for,
    wait_written,
    written_messages,
)

MANUAL_COMMITS = {
    "pending_token_threshold": 0,
    "message_count_threshold": 0,
    "idle_timeout_seconds": 0,
}


def assert_delivery(snapshot, expected, library="library-a", key="key-a"):
    messages = written_messages(snapshot, library)
    assert [(m["role"], message_text(m)) for m in messages] == [
        (m["role"], m["content"]) for m in expected
    ]
    # Inspect HTTP batches too: a mock that deduplicates IDs must not hide duplicate delivery.
    writes = [
        message
        for request in snapshot["requests"]
        if request["key"] == key
        and request["path"].endswith("/messages/batch")
        and request["status"] == 200
        for message in request["body"]["messages"]
    ]
    ids = [source for message in writes for source in message["source_message_ids"]]
    assert len(ids) == len(expected)
    assert set(Counter(ids).values()) == {1}
    assert writes == messages


def test_01_session_continues_across_pods(harness):
    pods = harness.pods()
    assert len(pods) == 2
    models = [harness.request(pod, "GET", MODELS, key="key-a") for pod in pods]
    assert [r.status_code for r in models] == [200, 200]
    assert models[0].json() == models[1].json()
    messages = []
    for turn in range(8):
        messages.append({"role": "user", "content": f"continuity turn {turn}"})
        messages.append(chat(harness, pods[turn % 2], "eight-turns", messages))
    replay, capture = session_keys(harness, "eight-turns")
    snapshot = wait_written(harness, len(messages))
    sessions = snapshot["libraries"]["library-a"]["sessions"]
    assert len(sessions) == 1
    assert_delivery(snapshot, messages)
    # The gateway commits by default, so OpenViking's automatic commits are off; these
    # turns stay below commit_tokens and well within idle_seconds.
    (session,) = sessions.values()
    assert session["auto_commit_policy"] == MANUAL_COMMITS
    assert session["commits"] == [] and session["pending"] == session["messages"]
    assert not any(r["path"].endswith("/commit") for r in snapshot["requests"])
    assert harness.redis_keys(f"*k:{{{scope()}}}:rp:*") == [replay]
    assert harness.redis_keys(f"*k:{{cap}}:{scope()}:*") == [capture]
    value = document(harness, capture)
    assert value["previous"] == [] and value["ov_session"] in sessions
    # The capture document holds no conversation text.
    assert "continuity turn" not in json.dumps(value)


def test_08_same_identity_libraries_are_isolated(harness):
    pods = harness.pods()
    histories = {"key-a": [], "key-b": []}
    for turn in range(2):
        for index, key in enumerate(histories):
            history = histories[key]
            history.append({"role": "user", "content": f"private {key} turn {turn}"})
            history.append(chat(harness, pods[(turn + index) % 2], "same-client-id", history, key))
    captures = {}
    for key in histories:
        _, captures[key] = session_keys(harness, "same-client-id", key)
    assert captures["key-a"] != captures["key-b"]
    wait_written(harness, 4, "library-a")
    snapshot = wait_written(harness, 4, "library-b")
    for key, library in (("key-a", "library-a"), ("key-b", "library-b")):
        assert len(snapshot["libraries"][library]["sessions"]) == 1
        assert_delivery(snapshot, histories[key], library, key)
        recalls = snapshot["libraries"][library]["recalls"]
        assert len(recalls) == 2, recalls
        assert all(key in json.dumps(recall) for recall in recalls)
        sessions = snapshot["libraries"][library]["sessions"]
        assert document(harness, captures[key])["ov_session"] in sessions
    ark = records(harness, "ark")["requests"]
    for request in ark:
        text = json.dumps(request["body"])
        own = "library-a" if "private key-a" in text else "library-b"
        other = "library-b" if own == "library-a" else "library-a"
        assert own in text and other not in text, text
    log = harness.logs()
    assert account("key-a") in log and account("key-b") in log


@pytest.mark.standalone
def test_09_revocation_refuses_key_and_replacement_uses_new_partition(harness):
    pods = harness.pods()
    chat(harness, pods[0], "revoked-tail", [{"role": "user", "content": "saved secret"}])
    _, capture = session_keys(harness, "revoked-tail")
    wait_written(harness, 2)
    control(harness, "/_revoke", key="key-a")
    response = harness.request(
        pods[1],
        "POST",
        CHAT,
        key="key-a",
        json={
            "model": "mock",
            "messages": [{"role": "user", "content": "refused"}],
        },
    )
    assert response.status_code == 401, response.text
    assert not credential_keys(harness, "key-a")
    snapshot = records(harness)
    refused = [r for r in snapshot["requests"] if r["key"] == "key-a" and r["status"] == 401]
    assert refused and all(r["path"] == "/health" for r in refused), refused
    count = sum(r["key"] == "key-a" for r in snapshot["requests"])
    # Nothing uses a stored key in the background.
    time.sleep(5)
    assert sum(r["key"] == "key-a" for r in records(harness)["requests"]) == count
    chat(
        harness, pods[1], "revoked-tail", [{"role": "user", "content": "replacement"}], "key-a-new"
    )
    _, replacement = session_keys(harness, "revoked-tail", "key-a-new")
    assert replacement != capture
    sessions = wait_written(harness, 4)["libraries"]["library-a"]["sessions"]
    assert len(sessions) == 2
    assert document(harness, replacement)["ov_session"] != document(harness, capture)["ov_session"]


def test_12_finished_turns_write_one_hundred_sessions_once(harness):
    pods = harness.pods()
    for pod in pods:
        assert harness.request(pod, "GET", "/health").status_code == 200
    started = time.monotonic()

    def send(number):
        message = {"role": "user", "content": f"capture race {number:03d}"}
        answer = chat(harness, pods[number % 2], f"capture-race-{number}", [message])
        return message, answer

    with ThreadPoolExecutor(max_workers=32) as executor:
        expected = list(executor.map(send, range(100)))
    snapshot = wait_for(
        lambda: s if len(written_messages(s := records(harness))) == 200 else None,
        timeout=120,
    )
    assert time.monotonic() - started < 180
    sessions = snapshot["libraries"]["library-a"]["sessions"]
    assert len(sessions) == 100
    delivered = {
        message_text(value["messages"][0]): value["messages"] for value in sessions.values()
    }
    assert len(delivered) == 100
    for question, answer in expected:
        assert [(m["role"], message_text(m)) for m in delivered[question["content"]]] == [
            ("user", question["content"]),
            ("assistant", answer["content"]),
        ]
    writes = [r for r in snapshot["requests"] if r["path"].endswith("/messages/batch")]
    ids = [
        source for r in writes for m in r["body"]["messages"] for source in m["source_message_ids"]
    ]
    assert len(ids) == 200 and set(Counter(ids).values()) == {1}
    assert len({r["source_pod"] for r in writes}) == 2
    captures = harness.redis_keys(f"*k:{{cap}}:{scope()}:*")
    assert len(captures) == 100
    for capture in captures:
        # The cursor moves right after the write it follows.
        value = wait_for(
            lambda capture=capture: (
                (value := document(harness, capture)).get("delivered") and value
            ),
            timeout=10,
        )
        assert value["ov_session"] in sessions
    assert len(harness.redis_keys(f"*k:{{{scope()}}}:rp:*")) == 100


@pytest.mark.standalone
def test_13_unordered_existing_key_never_reaches_upstream(harness):
    pods = harness.pods()
    chat(harness, pods[0], "ordered", [{"role": "user", "content": "initial request"}])
    assert credential_keys(harness, "key-a")
    control(harness, "/_unorder", key="key-a")
    before = len(records(harness, "ark")["requests"])
    response = harness.request(
        pods[1],
        "POST",
        CHAT,
        key="key-a",
        json={
            "model": "mock",
            "messages": [{"role": "user", "content": "unpaid request"}],
        },
    )
    assert response.status_code == 402, response.text
    assert len(records(harness, "ark")["requests"]) == before
    chat(harness, pods[1], "other-key", [{"role": "user", "content": "still ordered"}], "key-b")
    assert len(records(harness, "ark")["requests"]) == before + 1


def test_14_tools_and_responses_cross_pods_with_evicted_receipt(harness):
    harness.redeploy(config={"default_profile": {"gateway_tools": True}})
    pods = harness.pods()
    question = {"role": "user", "content": "look up blue"}
    answer = chat(harness, pods[0], "hidden-tools", [question], model="mock-tools")
    chat(harness, pods[1], "hidden-tools", [question], model="mock-tools")
    calls = records(harness)["libraries"]["library-a"]["tool_calls"]
    assert len(calls) == 1, calls
    chat(
        harness,
        pods[1],
        "hidden-tools",
        [
            question,
            answer,
            {
                "role": "user",
                "content": "look up blue again",
            },
        ],
        model="mock-tools",
    )
    assert len(records(harness)["libraries"]["library-a"]["tool_calls"]) == 2
    _, capture = session_keys(harness, "hidden-tools")
    assert document(harness, capture)["previous"] == []
    previous = None
    for turn in range(4):
        body = {"model": "mock", "input": [{"role": "user", "content": f"response chain {turn}"}]}
        if previous:
            body["previous_response_id"] = previous
        response = harness.request(
            pods[turn % 2],
            "POST",
            "/gateway/v1/responses",
            key="key-a",
            json=body,
            headers={"X-OpenViking-Session": "responses-chain"},
        )
        assert response.status_code == 200, response.text
        previous = response.json()["id"]
        assert previous
    chain = [r for r in records(harness, "ark")["requests"] if r["path"] == "/v1/responses"]
    assert len(chain) == 4
    for turn, request in enumerate(chain):
        effective = json.dumps(request["effective_input"])
        assert all(f"response chain {earlier}" in effective for earlier in range(turn + 1))
    control(harness, "/_tool_delay", seconds=8)
    retry_history = [{"role": "user", "content": "evict this in-flight tool receipt"}]
    baseline = len(records(harness)["libraries"]["library-a"]["tool_calls"])
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            chat, harness, pods[0], "evicted-tool", retry_history, model="mock-tools"
        )
        wait_for(
            lambda: len(records(harness)["libraries"]["library-a"]["tool_calls"]) == baseline + 1,
            interval=0.2,
        )
        receipts = [
            key
            for key in harness.redis_keys(f"*k:{{{scope()}}}:st:*")
            if "owner" in document(harness, key) and "content" not in document(harness, key)
        ]
        assert len(receipts) == 1, receipts
        before = len(records(harness, "ark")["requests"])
        second = executor.submit(
            chat, harness, pods[1], "evicted-tool", retry_history, model="mock-tools"
        )
        wait_for(lambda: len(records(harness, "ark")["requests"]) > before, interval=0.2)
        # The second provider response is immediate; allow its receipt waiter to start.
        time.sleep(0.5)
        assert not first.done() and not second.done()
        assert harness.redis("DEL", receipts[0]).strip() == "1"
        second.result(timeout=10)
        first.result(timeout=15)
    assert len(records(harness)["libraries"]["library-a"]["tool_calls"]) == baseline + 1
    retry_requests = records(harness, "ark")["requests"][before:]
    assert any("unknown outcome" in json.dumps(r["body"]) for r in retry_requests)
    assert "KeyError" not in harness.logs()


@pytest.mark.standalone
def test_15_management_updates_and_revocations_propagate(harness):
    harness.redeploy(config={"management_api": True, "tenancy": "account", "management_key": ""})
    pods = harness.pods()
    studio = harness.request(pods[0], "GET", "/studio/", key="key-a")
    assert studio.status_code == 200 and "text/html" in studio.headers["content-type"]
    upstream = {
        "name": "managed",
        "protocol": "chat",
        "base_url": "http://mock-ark:8080/v1",
        "api_key": "managed-test-key",
        "models": ["mock"],
    }
    response = harness.request(
        pods[0], "PUT", ADMIN + "/upstreams/managed", key="key-a", json=upstream
    )
    assert response.status_code == 200, response.text
    response = harness.request(
        pods[0],
        "PUT",
        ADMIN + "/policies/default",
        key="key-a",
        json={"name": "Default"},
    )
    assert response.status_code == 200, response.text
    issued = harness.request(
        pods[0],
        "POST",
        ADMIN + "/keys",
        key="key-a",
        json={
            "name": "issued",
            "openviking_key": "key-a",
            "policy_id": "default",
            "upstream_ids": ["managed"],
        },
    )
    assert issued.status_code == 200, issued.text
    secret, identifier = issued.json()["key"], issued.json()["id"]
    chat(harness, pods[1], "managed-before", [{"role": "user", "content": "before update"}], secret)
    upstream["models"] = ["mock", "mock-tools"]
    response = harness.request(
        pods[0], "PUT", ADMIN + "/upstreams/managed", key="key-a", json=upstream
    )
    assert response.status_code == 200, response.text
    time.sleep(2.2)
    response = harness.request(pods[1], "GET", MODELS, key=secret)
    assert response.status_code == 200, response.text
    assert {row["id"] for row in response.json()["data"]} == {"mock", "mock-tools"}
    response = harness.request(pods[0], "DELETE", ADMIN + f"/keys/{identifier}", key="key-a")
    assert response.status_code == 200, response.text
    before = len(records(harness, "ark")["requests"])
    time.sleep(2.2)
    response = harness.request(
        pods[1],
        "POST",
        CHAT,
        key=secret,
        json={
            "model": "mock",
            "messages": [{"role": "user", "content": "after revoke"}],
        },
    )
    assert response.status_code == 401, response.text
    assert len(records(harness, "ark")["requests"]) == before


@pytest.mark.standalone
def test_16_shared_root_key_decrypts_across_pods_and_missing_key_blocks_start(harness):
    harness.redeploy(config={"encryption": {"enabled": True, "provider": "local"}})
    pods = harness.pods()
    messages = [{"role": "user", "content": "encrypted session private marker"}]
    messages.append(chat(harness, pods[0], "encrypted-session", messages))
    replay, capture = session_keys(harness, "encrypted-session")
    assert (
        harness.redis(
            "EVAL",
            "return string.sub(redis.call('HGET',KEYS[1],ARGV[1]),1,4)",
            "1",
            replay,
            "root:",
        ).strip()
        == "OVE1"
    )
    root_hash = harness.redis(
        "EVAL", "return redis.sha1hex(redis.call('HGET',KEYS[1],ARGV[1]))", "1", replay, "root:"
    ).strip()
    messages.append({"role": "user", "content": "continue encrypted session"})
    messages.append(chat(harness, pods[1], "encrypted-session", messages))
    snapshot = wait_written(harness, 4)
    assert len(snapshot["libraries"]["library-a"]["sessions"]) == 1
    assert_delivery(snapshot, messages)
    assert session_keys(harness, "encrypted-session") == (replay, capture)
    assert (
        harness.redis(
            "EVAL", "return redis.sha1hex(redis.call('HGET',KEYS[1],ARGV[1]))", "1", replay, "root:"
        ).strip()
        == root_hash
    )
    harness.kubectl(
        "patch",
        "secret",
        "gateway-secrets",
        "--type=json",
        "-p",
        '[{"op":"remove","path":"/data/root-key"}]',
    )
    harness.redeploy(
        config={}, values={"podAnnotations": {"e2e/missing-root-key": "true"}}, wait=False
    )
    old = set(pods)

    # A new ReplicaSet cannot mount the root-key Secret item once the key is removed.
    def rejected_pod():
        result = json.loads(
            harness.kubectl("get", "pods", "-l", "app.kubernetes.io/instance=gateway", "-o", "json")
        )
        for pod in result["items"]:
            if pod["metadata"]["name"] in old:
                continue
            events = harness.kubectl(
                "get",
                "events",
                "--field-selector",
                "involvedObject.name=" + pod["metadata"]["name"],
                "-o",
                "json",
            )
            statuses = pod["status"].get("containerStatuses", [])
            if (
                "FailedMount" in events
                and "root-key" in events
                and not any(s.get("ready") for s in statuses)
            ):
                return pod
        return None

    rejected = wait_for(rejected_pod, timeout=90)
    assert not any(
        c.get("type") == "Ready" and c.get("status") == "True"
        for c in rejected["status"].get("conditions", [])
    )


@pytest.mark.standalone
def test_17_turn_ended_during_openviking_outage_is_written_by_next_turn(harness):
    pod = harness.pods()[0]
    messages = [{"role": "user", "content": "saved before the outage"}]
    messages.append(chat(harness, pod, "outage", messages))
    wait_written(harness, 2)

    def batches():
        return [r for r in records(harness)["requests"] if r["path"].endswith("/messages/batch")]

    control(harness, "/_fail", status=503)
    try:
        messages.append({"role": "user", "content": "ends during the outage"})
        messages.append(chat(harness, pod, "outage", messages))
        wait_for(lambda: [r for r in batches() if r["status"] == 503])
        # The request's record says the turn was not saved.
        wait_for(lambda: '"capture_reason":"openviking_http_503"' in harness.logs(), timeout=30)
        # One attempt per finished turn: nothing retries it in the background.
        time.sleep(10)
        assert [r["status"] for r in batches()] == [200, 503]
    finally:
        control(harness, "/_fail", on=False)
    messages.append({"role": "user", "content": "after the outage"})
    messages.append(chat(harness, pod, "outage", messages))
    snapshot = wait_written(harness, len(messages))
    assert len(snapshot["libraries"]["library-a"]["sessions"]) == 1
    assert_delivery(snapshot, messages)


def commit_requests(snapshot):
    return [r for r in snapshot["requests"] if r["path"].endswith("/commit")]


@pytest.mark.standalone
def test_18_gateway_commits_past_commit_tokens_and_after_idle(harness):
    pods = harness.pods()
    ips = {p["metadata"]["name"]: p["status"]["podIP"] for p in harness.pod_objects()}
    profile = {"commit_tokens": 1000, "keep_recent_messages": 2, "idle_seconds": 10}
    messages = [{"role": "user", "content": "a turn below commit_tokens"}]
    messages.append(chat(harness, pods[0], "commits", messages, **profile))
    snapshot = wait_written(harness, 2)
    ((sid, session),) = snapshot["libraries"]["library-a"]["sessions"].items()
    assert session["auto_commit_policy"] == MANUAL_COMMITS
    assert session["commits"] == []
    # The other Pod ends a turn that passes commit_tokens. Its write reports the pending
    # tokens, so it commits at once and keeps the latest keep_recent_messages.
    messages.append({"role": "user", "content": " ".join(["large"] * 1500)})
    messages.append(chat(harness, pods[1], "commits", messages, **profile))

    def commits(count):
        def check():
            snapshot = records(harness)
            found = snapshot["libraries"]["library-a"]["sessions"][sid]["commits"]
            return len(found) >= count and snapshot

        return check

    snapshot = wait_for(commits(1), timeout=8)
    writes = [r for r in snapshot["requests"] if r["path"].endswith("/messages/batch")]
    assert len(writes) == 2 and writes[1]["source_pod"] == ips[pods[1]]
    threshold = snapshot["libraries"]["library-a"]["sessions"][sid]["commits"][0]
    assert threshold["keep_recent_count"] == 2
    assert [message_text(m) for m in threshold["messages"]] == [m["content"] for m in messages[:2]]
    assert threshold["source_pod"] == ips[pods[1]]
    # idle_seconds after the last turn its Pod commits the rest. The first Pod's timer
    # fired earlier and found a later turn saved, so it committed nothing.
    snapshot = wait_for(commits(2), timeout=40)
    session = snapshot["libraries"]["library-a"]["sessions"][sid]
    idle = session["commits"][1]
    assert idle["keep_recent_count"] == 0 and idle["source_pod"] == ips[pods[1]]
    assert [message_text(m) for m in idle["messages"]] == [m["content"] for m in messages[2:]]
    assert session["pending"] == []
    assert 9.5 <= idle["time"] - writes[1]["time"] <= 30
    time.sleep(3)
    snapshot = records(harness)
    assert [(r["source_pod"], r["body"]) for r in commit_requests(snapshot)] == [
        (ips[pods[1]], {"keep_recent_count": 2, "enable_working_memory": False}),
        (ips[pods[1]], {"keep_recent_count": 0, "enable_working_memory": False}),
    ]
    assert all(r["status"] == 200 for r in commit_requests(snapshot))


@pytest.mark.standalone
def test_19_openviking_auto_commit_leaves_commits_to_openviking(harness):
    harness.redeploy({"openviking_auto_commit": True})
    pod = harness.pods()[0]
    profile = {"commit_tokens": 1000, "keep_recent_messages": 2, "idle_seconds": 5}
    messages = [{"role": "user", "content": " ".join(["large"] * 1500)}]
    messages.append(chat(harness, pod, "auto-commit", messages, **profile))
    snapshot = wait_written(harness, 2)
    (session,) = snapshot["libraries"]["library-a"]["sessions"].values()
    # OpenViking's idle time is at least 300 seconds.
    assert session["auto_commit_policy"] == {
        "pending_token_threshold": 1000,
        "message_count_threshold": 0,
        "idle_timeout_seconds": 300,
        "keep_recent_count": 2,
    }
    # Neither the threshold nor idle_seconds makes the gateway commit.
    time.sleep(8)
    assert not commit_requests(records(harness))
