"""Streaming, rollout, cache failure and startup acceptance scenarios."""

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from .harness import (
    CHAT,
    chat,
    control,
    credential_keys,
    document,
    records,
    session_keys,
    wait_for,
    written_messages,
)
from .sdk_probe import probe


def stream_body(model="mock-long", messages=None):
    return {
        "model": model,
        "stream": True,
        "messages": messages or [{"role": "user", "content": "stream continuity"}],
    }


def read_stream(
    harness, pod, model="mock-silent", session="stream", after_first=None, messages=None
):
    lines, timestamps = [], []
    try:
        with harness.stream(
            pod, stream_body(model, messages), headers={"X-OpenViking-Session": session}
        ) as response:
            encoding = response.headers.get("content-encoding")
            for line in response.iter_lines():
                if line:
                    lines.append(line)
                    timestamps.append(time.monotonic())
                    if len(lines) == 1 and after_first:
                        after_first()
    except httpx.RemoteProtocolError:
        return {"lines": lines, "times": timestamps, "truncated": True}
    return {"lines": lines, "times": timestamps, "encoding": encoding, "truncated": False}


def test_02_default_grace_finishes_stream_and_other_pod_delivers(harness):
    harness.redeploy({"default_profile": {"idle_seconds": 45}})
    victim, _ = harness.pods()
    victim_ip = next(
        p["status"]["podIP"] for p in harness.pod_objects() if p["metadata"]["name"] == victim
    )
    history = [{"role": "user", "content": "first pending turn"}]
    history.append(chat(harness, victim, "graceful", history))
    history.append({"role": "user", "content": "finish during termination"})
    result = read_stream(
        harness,
        victim,
        session="graceful",
        messages=history,
        after_first=lambda: harness.kubectl("delete", "pod", victim, "--wait=false"),
    )
    assert "data: [DONE]" in result["lines"] and not result["truncated"]
    snapshot = wait_for(
        lambda: value if len(written_messages(value := records(harness))) == 4 else None,
        timeout=110,
    )
    assert len(snapshot["libraries"]["library-a"]["sessions"]) == 1
    writes = [r for r in snapshot["requests"] if r["path"].endswith("/messages/batch")]
    ids = [
        source for r in writes for m in r["body"]["messages"] for source in m["source_message_ids"]
    ]
    assert len(ids) == len(set(ids)) == 4
    # The next request confirms the first turn, so the victim may deliver it
    # immediately. Only the final, idle-delayed tail must survive on another Pod.
    tail_ids = {
        source
        for message in written_messages(snapshot)[-2:]
        for source in message["source_message_ids"]
    }
    tail_writes = [
        r
        for r in writes
        if any(tail_ids.intersection(m["source_message_ids"]) for m in r["body"]["messages"])
    ]
    assert tail_writes and all(r["source_pod"] != victim_ip for r in tail_writes), tail_writes


@pytest.mark.standalone
def test_03_short_grace_truncates_stream_without_splitting_next_turn(harness):
    victim, _ = harness.pods()
    result = read_stream(
        harness,
        victim,
        model="mock-long",
        session="short-grace",
        after_first=lambda: harness.kubectl(
            "delete", "pod", victim, "--grace-period=25", "--wait=false"
        ),
    )
    assert "data: [DONE]" not in result["lines"], "the negative control must outlive the 25s grace"
    assert result["times"][-1] - result["times"][0] < 40
    pod = next(p for p in harness.pods() if p != victim)
    chat(harness, pod, "short-grace", [{"role": "user", "content": "continue after truncation"}])
    _, capture = session_keys(harness, "short-grace")
    wait_for(lambda: not document(harness, capture).get("pending"))
    sessions = records(harness)["libraries"]["library-a"]["sessions"]
    assert len(sessions) == 1
    assert len(next(iter(sessions.values()))["messages"]) == 2


ROLLOUT_CLIENT = """
import asyncio, http.client, json, time
import aiohttp
base = "http://gateway:1935"
headers = {"Authorization": "Bearer key-a"}
results = {"short": 0, "chat": 0, "keepalive": 0, "keepalive_reuses": 0, "keepalive_connections": 0, "raw_keepalive_reused": False, "errors": [], "done": False}
async def lane(name):
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(force_close=True)) as client:
        for i in range(120):
            try:
                if name == "short":
                    response = await client.get(base + "/health")
                else:
                    response = await client.post(base + "/gateway/v1/chat/completions", headers=headers,
                        json={"model":"mock", "messages":[{"role":"user","content":str(i)}]})
                await response.read()
                assert response.status == 200, response.status
                results[name] += 1
            except Exception as error:
                results["errors"].append([name, type(error).__name__, str(error)])
            await asyncio.sleep(.5)
async def keepalive():
    trace = aiohttp.TraceConfig()
    async def reused(session, context, params):
        results["keepalive_reuses"] += 1
    async def created(session, context, params):
        results["keepalive_connections"] += 1
    trace.on_connection_reuseconn.append(reused)
    trace.on_connection_create_end.append(created)
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(keepalive_timeout=75),
            timeout=aiohttp.ClientTimeout(total=15), trace_configs=[trace]) as client:
        for _ in range(10):
            try:
                async with client.get(base + "/health") as response:
                    await response.read()
                    assert response.status == 200
                    results["keepalive"] += 1
            except Exception as error:
                results["errors"].append(["keepalive", type(error).__name__, str(error)])
            await asyncio.sleep(6)
async def long_stream():
    async with aiohttp.ClientSession() as client:
        async with client.post(base + "/gateway/v1/chat/completions", headers=headers,
            json={"model":"mock-long","stream":True,"messages":[{"role":"user","content":"long rollout"}]}) as r:
            assert r.status == 200
            body = await r.text()
            results["done"] = "data: [DONE]" in body
def raw_idle_reuse():
    connection = http.client.HTTPConnection("RAW_GATEWAY_HOST", 1935, timeout=15)
    try:
        connection.request("GET", "/health")
        response = connection.getresponse()
        response.read()
        assert response.status == 200
        original = connection.sock
        assert original is not None
        time.sleep(6)
        connection.request("GET", "/health")
        response = connection.getresponse()
        response.read()
        assert response.status == 200 and connection.sock is original
        results["raw_keepalive_reused"] = True
    finally:
        connection.close()
async def main():
    await asyncio.to_thread(raw_idle_reuse)
    await asyncio.gather(lane("short"), lane("chat"), keepalive(), long_stream())
    print(json.dumps(results))
asyncio.run(main())
"""


def test_04_rollout_preserves_three_traffic_lanes_and_long_stream(harness):
    runner = json.loads(harness.kubectl("get", "pods", "-l", "app=mock-ov", "-o", "json"))["items"][
        0
    ]["metadata"]["name"]
    with ThreadPoolExecutor() as executor:
        running = executor.submit(
            harness.kubectl,
            "exec",
            "-i",
            runner,
            "--",
            "python",
            "-",
            input=ROLLOUT_CLIENT.replace(
                "RAW_GATEWAY_HOST", harness.pod_objects()[0]["status"]["podIP"]
            ),
            timeout=110,
        )
        wait_for(
            lambda: any(
                r["body"].get("model") == "mock-long" for r in records(harness, "ark")["requests"]
            )
        )
        harness.kubectl("rollout", "restart", "deployment/gateway")
        result = json.loads(running.result(timeout=115))
        (harness.directory / "rollout-traffic.json").write_text(json.dumps(result, indent=2))
    assert result["errors"] == [], result
    assert (result["short"], result["chat"], result["keepalive"]) == (120, 120, 10), result
    # A real pool observes the FIN when a draining Pod closes an idle connection.
    assert result["raw_keepalive_reused"], result
    assert result["keepalive_reuses"] >= 7, result
    assert 1 <= result["keepalive_connections"] <= 3, result
    assert result["done"], result
    harness.kubectl("rollout", "status", "deployment/gateway", "--timeout=120s")


@pytest.mark.standalone
def test_05_eviction_keeps_permanent_state_and_recovers_credentials(harness):
    pods = harness.pods()
    permanent = "ovgw:v1:m:{a-e2e}:o:policies:permanent"
    tombstone = "ovgw:v1:kd:{deleted-e2e}"
    ready = "ovgw:v1:k:{cap}:ready"
    harness.redis("HSET", permanent, "v", '{"name":"permanent"}', "rev", 1)
    harness.redis("SET", tombstone, 1)
    harness.redis("CONFIG", "SET", "maxmemory", "30mb")
    big = "retained context " * 8192

    def send(number):
        return chat(
            harness,
            pods[number % 2],
            f"eviction-{number}",
            [{"role": "user", "content": f"{number}: {big}"}],
            idle_seconds=120,
        )

    with ThreadPoolExecutor(max_workers=12) as executor:
        list(executor.map(send, range(200)))
    stats = dict(
        line.split(":", 1) for line in harness.redis("INFO", "stats").splitlines() if ":" in line
    )
    assert int(stats["evicted_keys"]) > 0, stats
    assert harness.redis("EXISTS", permanent) == "1"
    assert harness.redis("EXISTS", tombstone) == "1"
    assert harness.redis("TTL", permanent) == harness.redis("TTL", tombstone) == "-1"
    assert harness.redis("TYPE", ready) == "zset" and harness.redis("TTL", ready) == "-1"
    chat(harness, pods[1], "eviction-0", [{"role": "user", "content": "continue after eviction"}])
    harness.redis("CONFIG", "SET", "maxmemory", "96mb")
    chat(
        harness,
        pods[0],
        "credential-rebuild",
        [{"role": "user", "content": "pending before key deletion"}],
    )
    _, capture = session_keys(harness, "credential-rebuild")
    for key in credential_keys(harness, "key-a"):
        harness.redis("DEL", key)
    wait_for(
        lambda: (
            (document(harness, capture).get("error") or {}).get("reason") == "gateway_key_revoked"
        ),
        timeout=40,
    )
    chat(
        harness,
        pods[1],
        "credential-rebuild",
        [{"role": "user", "content": "new request restores key"}],
    )
    assert credential_keys(harness, "key-a")
    wait_for(
        lambda: (
            not document(harness, capture).get("error")
            and not document(harness, capture).get("pending")
        ),
        timeout=90,
    )
    assert "KeyError" not in harness.logs()


def stop_redis(harness, aof):
    harness.kubectl("scale", "statefulset/redis", "--replicas=0")
    harness.kubectl("wait", "--for=delete", "pod/redis-0", "--timeout=40s")
    if not aof:
        value = json.loads(harness.kubectl("get", "statefulset/redis", "-o", "json"))
        args = value["spec"]["template"]["spec"]["containers"][0]["args"]
        args[args.index("--appendonly") + 1] = "no"
        harness.kubectl(
            "patch",
            "statefulset/redis",
            "--type=json",
            "-p",
            json.dumps(
                [{"op": "replace", "path": "/spec/template/spec/containers/0/args", "value": args}]
            ),
        )


def outage_requests(harness, pod):
    counts = {"degraded": 0, "authentication_500": 0}
    for number in range(5):
        response = harness.request(
            pod,
            "POST",
            CHAT,
            json={
                "model": "mock",
                "messages": [{"role": "user", "content": f"redis outage {number}"}],
            },
        )
        if response.status_code == 200:
            counts["degraded"] += 1
        else:
            assert response.status_code == 500 and response.headers["content-type"].startswith(
                "text/plain"
            ), response.text
            counts["authentication_500"] += 1
    (harness.directory / "redis-outage.json").write_text(json.dumps(counts))
    if counts["degraded"]:
        assert "memory_store_failure" in harness.logs()
    return counts


@pytest.mark.standalone
def test_06_aof_restart_preserves_management_tombstones_and_pending_capture(harness):
    harness.redeploy({"default_profile": {"idle_seconds": 45}})
    pod = harness.pods()[0]
    chat(harness, pod, "aof-tail", [{"role": "user", "content": "persist this pending tail"}])
    _, capture = session_keys(harness, "aof-tail")
    keys = credential_keys(harness, "key-a")
    tombstone = "ovgw:v1:kd:{aof-deleted-scope}"
    harness.redis("SET", tombstone, 1)
    stop_redis(harness, aof=True)
    outage_requests(harness, pod)
    harness.kubectl("scale", "statefulset/redis", "--replicas=1")
    harness.kubectl("rollout", "status", "statefulset/redis", "--timeout=90s")
    assert harness.redis("EXISTS", tombstone) == "1"
    assert all(harness.redis("EXISTS", key) == "1" for key in keys)
    assert document(harness, capture)
    wait_for(lambda: len(written_messages(records(harness))) >= 2, timeout=90)
    assert not document(harness, capture).get("pending")


@pytest.mark.standalone
def test_07_restart_without_aof_loses_tail_but_recreates_key(harness):
    harness.redeploy({"default_profile": {"idle_seconds": 120}})
    pod = harness.pods()[0]
    chat(harness, pod, "lost-tail", [{"role": "user", "content": "nonpersistent pending tail"}])
    _, capture = session_keys(harness, "lost-tail")
    old_keys = harness.redis_keys("ovgw:*")
    assert old_keys and document(harness, capture)["pending"]
    stop_redis(harness, aof=False)
    harness.kubectl("scale", "statefulset/redis", "--replicas=1")
    harness.kubectl("rollout", "status", "statefulset/redis", "--timeout=90s")
    assert harness.redis("DBSIZE") == "0"
    chat(harness, pod, "fresh-after-restart", [{"role": "user", "content": "recreated credential"}])
    assert credential_keys(harness, "key-a") and not document(harness, capture)
    assert all(
        all(c["restartCount"] == 0 for c in p["status"]["containerStatuses"])
        for p in harness.pod_objects()
    )


def rollout_candidates(harness, marker):
    pods = json.loads(
        harness.kubectl("get", "pods", "-l", "app.kubernetes.io/instance=gateway", "-o", "json")
    )["items"]
    return [p for p in pods if p["metadata"].get("annotations", {}).get("e2e-startup") == marker]


@pytest.mark.standalone
def test_10_startup_waits_for_503_but_rejects_dev_and_404(harness):
    control(harness, "/_fail", status=503)
    harness.redeploy({}, {"podAnnotations": {"e2e-startup": "unavailable"}}, wait=False)
    pods = wait_for(lambda: rollout_candidates(harness, "unavailable"))
    pod = pods[0]["metadata"]["name"]
    wait_for(lambda: "waiting" in harness.kubectl("logs", pod, check=False).lower(), timeout=45)
    time.sleep(5)
    status = rollout_candidates(harness, "unavailable")[0]["status"]
    assert all(c["restartCount"] == 0 for c in status.get("containerStatuses", []))
    assert not any(c.get("ready") for c in status.get("containerStatuses", []))
    restored = time.monotonic()
    control(harness, "/_fail", on=False)
    # Time only the waiting pod's recovery; the rest of the rollout starts pods from scratch.
    wait_for(lambda: pod in harness.pods(), timeout=40)
    assert time.monotonic() - restored < 30
    harness.kubectl("rollout", "status", "deployment/gateway", "--timeout=120s", timeout=130)
    for marker, config in (
        ("dev", {}),
        ("notfound", {"openviking_url": "http://mock-ov:1933/notfound"}),
    ):
        control(harness, "/_auth_mode", mode="dev" if marker == "dev" else "api_key")
        harness.redeploy(config, {"podAnnotations": {"e2e-startup": marker}}, wait=False)

        def refused(marker=marker):
            for candidate in rollout_candidates(harness, marker):
                for container in candidate["status"].get("containerStatuses", []):
                    terminated = container.get("state", {}).get("terminated") or container.get(
                        "lastState", {}
                    ).get("terminated")
                    if terminated and terminated["exitCode"] != 0:
                        return candidate

        wait_for(refused, timeout=35)


@pytest.mark.standalone
def test_11_sse_heartbeats_preserve_actual_client_parsers(harness):
    pod = harness.pods()[0]
    with ThreadPoolExecutor(max_workers=2) as executor:
        plain = executor.submit(read_stream, harness, pod, "mock-silent", "heartbeat-raw")
        encoded = executor.submit(read_stream, harness, pod, "mock-silent-gzip", "heartbeat-gzip")
        result = asyncio.run(probe(harness.url(pod) + "/gateway", "key-a"))
        (harness.directory / "sdk-probe.json").write_text(json.dumps(result, indent=2))
        assert result["passed"], result
        plain, encoded = plain.result(), encoded.result()
    comments = [
        stamp
        for line, stamp in zip(plain["lines"], plain["times"], strict=True)
        if line.startswith(":")
    ]
    assert len(comments) >= 2, plain
    assert 10 <= comments[0] - plain["times"][0] <= 20
    assert 10 <= comments[1] - comments[0] <= 20
    assert "data: [DONE]" in plain["lines"]
    assert encoded["encoding"] == "gzip"
    assert not any(line.startswith(":") for line in encoded["lines"])
    assert "data: [DONE]" in encoded["lines"]
