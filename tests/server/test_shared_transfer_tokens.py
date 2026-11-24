"""Opt-in transfer tokens retain purpose and identity across real Redis owners."""

import asyncio
import os

import pytest

from openviking.server.config import ServerConfig
from openviking.server.identity import Role
from openviking.server.transfer_token_backend import RedisTransferTokens
from openviking.server.upload_token_store import UploadTokenError, UploadTokenStore
from openviking_cli.exceptions import InvalidArgumentError, UnavailableError

# Reuse the existing synthetic HTTP/auth fixtures, with separate Redis owners at
# MCP mint and HTTP redemption. No service backend or model is started here.
from tests.server.test_content_download_token import api_key_manager as api_key_manager
from tests.server.test_content_download_token import client as client
from tests.server.test_content_download_token import fake_fs as fake_fs
from tests.server.test_resources_temp_upload_token import (
    upload_api_key_manager as upload_api_key_manager,
)


@pytest.fixture
async def pair():
    endpoint = os.environ.get("OV_TEST_TRANSFER_REDIS")
    if not endpoint:
        pytest.skip("OV_TEST_TRANSFER_REDIS is required for a real disposable Redis")
    import uuid

    prefix = "test-" + uuid.uuid4().hex
    backends = [
        RedisTransferTokens(
            {"endpoints": [endpoint], "command_timeout_ms": 1000},
            service_name="synthetic-transfer",
            key_prefix=prefix,
        )
        for _ in range(2)
    ]
    await asyncio.gather(*(value.initialize() for value in backends))
    stores = [UploadTokenStore("download") for _ in backends]
    for store, backend in zip(stores, backends, strict=True):
        store._backend = backend
    try:
        yield stores, backends
    finally:
        await asyncio.gather(*(value.close() for value in backends))


def test_default_is_memory_and_topology_is_explicit():
    assert ServerConfig().transfer_tokens.backend == "memory"
    with pytest.raises(InvalidArgumentError):
        RedisTransferTokens(
            {"endpoints": ["redis://127.0.0.1:1"], "command_timeout_ms": 0},
            service_name="synthetic",
        )
    with pytest.raises(InvalidArgumentError):
        RedisTransferTokens(
            {"mode": "cluster", "endpoints": ["redis://127.0.0.1:1"]}, service_name="synthetic"
        )


@pytest.mark.asyncio
async def test_mint_on_one_owner_consumes_once_on_another(pair):
    stores, _ = pair
    token, expires = await stores[0].aissue(
        "acct",
        "alice",
        30,
        role=Role.ADMIN,
        actor_peer_id="actor",
        from_oauth=True,
        uri="viking://resources/demo.bin",
        tags=[],
    )
    consumed = await stores[1].aconsume(token)
    assert consumed.account_id == "acct" and consumed.user_id == "alice"
    assert consumed.role == Role.ADMIN and consumed.actor_peer_id == "actor"
    assert consumed.from_oauth and consumed.tags == []
    assert consumed.uri == "viking://resources/demo.bin" and expires > 0
    with pytest.raises(UploadTokenError):
        await stores[0].aconsume(token)


@pytest.mark.asyncio
async def test_concurrent_redeemers_have_one_winner(pair):
    stores, _ = pair
    token, _ = await stores[0].aissue("acct", "alice", 30, role=Role.USER)
    results = await asyncio.gather(
        *(stores[i % 2].aconsume(token) for i in range(20)), return_exceptions=True
    )
    assert sum(not isinstance(result, BaseException) for result in results) == 1
    assert all(
        not isinstance(result, BaseException) or isinstance(result, UploadTokenError)
        for result in results
    )


@pytest.mark.asyncio
async def test_upload_and_download_cannot_consume_each_others_tokens(pair):
    stores, backends = pair
    upload = UploadTokenStore("upload")
    upload._backend = backends[1]
    token, _ = await stores[0].aissue(
        "acct", "alice", 30, role=Role.USER, uri="viking://resources/demo.bin"
    )
    with pytest.raises(UploadTokenError):
        await upload.aconsume(token)
    assert (await stores[1].aconsume(token)).uri.endswith("demo.bin")


@pytest.mark.asyncio
async def test_redis_expiry_is_authoritative_despite_pod_clock_skew(pair, monkeypatch):
    stores, _ = pair
    import openviking.server.upload_token_store as owner

    monkeypatch.setattr(owner.time, "time", lambda: 1.0)
    token, expires = await stores[0].aissue("acct", "alice", 30, role=Role.USER)
    assert expires > 1_000_000
    monkeypatch.setattr(owner.time, "time", lambda: 10**15)
    assert (await stores[1].aconsume(token)).user_id == "alice"


@pytest.mark.asyncio
async def test_malformed_records_burn_and_do_not_downgrade_to_memory(pair):
    stores, backends = pair
    token, _ = await stores[0].aissue("acct", "alice", 30, role=Role.USER)
    await backends[0]._run(
        lambda redis: redis.set(backends[0].key("download", token), '{"version":999}', ex=30)
    )
    with pytest.raises(UploadTokenError):
        await stores[1].aconsume(token)
    with pytest.raises(UploadTokenError):
        await stores[0].aconsume(token)
    assert not stores[0]._store


@pytest.mark.asyncio
async def test_cancel_after_physical_consume_keeps_token_burned(pair, monkeypatch):
    stores, backends = pair
    token, _ = await stores[0].aissue("acct", "alice", 30, role=Role.USER)
    committed = asyncio.Event()
    release = asyncio.Event()
    consume = backends[0].consume

    async def pause(*args):
        result = await consume(*args)
        committed.set()
        await release.wait()
        return result

    monkeypatch.setattr(backends[0], "consume", pause)
    task = asyncio.create_task(stores[0].aconsume(token))
    await asyncio.wait_for(committed.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(UploadTokenError):
        await stores[1].aconsume(token)


@pytest.mark.asyncio
async def test_no_synchronous_fallback_when_shared_mode_is_active(pair):
    stores, _ = pair
    with pytest.raises(RuntimeError, match="aissue"):
        stores[0].issue("acct", "alice", 30, role=Role.USER)
    with pytest.raises(RuntimeError, match="aconsume"):
        stores[0].consume("synthetic")


@pytest.mark.asyncio
async def test_expiry_uses_redis_ttl_and_preserves_other_purpose(pair):
    stores, backends = pair
    token, _ = await stores[0].aissue("acct", "alice", 30, role=Role.USER)
    await backends[0]._run(lambda redis: redis.pexpire(backends[0].key("download", token), 1))
    await asyncio.sleep(0.02)
    with pytest.raises(UploadTokenError):
        await stores[1].aconsume(token)


@pytest.mark.asyncio
async def test_collision_does_not_overwrite_existing_identity(pair, monkeypatch):
    stores, _ = pair
    import openviking.server.upload_token_store as owner

    values = iter(["A" * 10, "A" * 10, "B" * 10])
    monkeypatch.setattr(owner, "_new_token", lambda: next(values))
    first, _ = await stores[0].aissue("acct", "alice", 30, role=Role.USER)
    second, _ = await stores[1].aissue("acct", "bob", 30, role=Role.USER)
    assert first != second
    assert (await stores[1].aconsume(first)).user_id == "alice"
    assert (await stores[0].aconsume(second)).user_id == "bob"


@pytest.mark.asyncio
async def test_lost_consume_reply_is_unavailable_and_never_replayed(pair, monkeypatch):
    from redis.exceptions import ConnectionError

    stores, backends = pair
    token, _ = await stores[0].aissue("acct", "alice", 30, role=Role.USER)
    client = backends[0]._handle.client
    physical_eval = client.eval
    calls = []

    async def lose_reply(*args):
        calls.append(args)
        await physical_eval(*args)
        raise ConnectionError("synthetic reply loss after Redis commit")

    monkeypatch.setattr(client, "eval", lose_reply)
    with pytest.raises(UnavailableError, match="outcome may be unknown"):
        await stores[0].aconsume(token)
    assert len(calls) == 1
    with pytest.raises(UploadTokenError):
        await stores[1].aconsume(token)
    assert not stores[0]._store


@pytest.mark.asyncio
async def test_backend_close_waits_for_admitted_physical_operation(pair):
    import threading

    _, backends = pair
    backend = backends[0]
    entered, release = threading.Event(), threading.Event()

    async def operation(redis):
        assert await redis.ping()
        entered.set()
        while not release.is_set():
            await asyncio.sleep(0.005)
        return "done"

    task = asyncio.create_task(backend._run(operation))
    while not entered.is_set():
        await asyncio.sleep(0.005)
    close = asyncio.create_task(backend.close())
    await asyncio.sleep(0.02)
    assert not close.done()
    release.set()
    assert await task == "done"
    await asyncio.wait_for(close, 2)
    with pytest.raises(UnavailableError):
        await backend._run(lambda redis: redis.ping())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("role", "not-registered"),
        ("from_oauth", "false"),
        ("tags", {}),
        ("list_only", 1),
        ("processing_mode", "unknown"),
    ],
)
async def test_invalid_identity_payload_is_burned(pair, field, value):
    import json

    stores, backends = pair
    token, _ = await stores[0].aissue("acct", "alice", 30, role=Role.USER)
    key = backends[0].key("download", token)
    raw = await backends[0]._run(lambda redis: redis.get(key))
    record = json.loads(raw)
    payload = json.loads(record["payload"])
    payload[field] = value
    record["payload"] = json.dumps(payload)
    await backends[0]._run(lambda redis: redis.set(key, json.dumps(record), ex=30))
    with pytest.raises(UploadTokenError):
        await stores[1].aconsume(token)
    with pytest.raises(UploadTokenError):
        await stores[0].aconsume(token)


@pytest.mark.asyncio
async def test_upload_business_fields_and_acl_round_trip(pair):
    from openviking.storage.acl import AclSpec

    _, backends = pair
    stores = [UploadTokenStore("upload") for _ in backends]
    for store, backend in zip(stores, backends, strict=True):
        store._backend = backend
    acl = AclSpec(acl_mode="restricted", entries=[{"principal": "user:bob", "level": "read"}])
    token, _ = await stores[0].aissue(
        "acct",
        "alice",
        30,
        role=Role.ADMIN,
        to="viking://resources/demo",
        parent="viking://resources",
        reason="synthetic",
        actor_peer_id="bot-a",
        processing_mode="vectors_only",
        tags=["first", "second"],
        tag_mode="append",
        parse_mode="default",
        acl=acl,
        from_oauth=True,
    )
    result = await stores[1].aconsume(token)
    assert result.acl == acl
    assert result.processing_mode == "vectors_only"
    assert result.tags == ["first", "second"] and result.tag_mode == "append"
    assert result.reason == "synthetic" and result.parent == "viking://resources"
    assert result.to == "viking://resources/demo" and result.actor_peer_id == "bot-a"
    assert result.from_oauth and result.skill_names is None


@pytest.mark.asyncio
async def test_token_survives_issuing_process_exit(pair):
    import json
    import sys

    stores, backends = pair
    # This child exits before the parent redeems; no process-local dictionary survives.
    script = """
import asyncio,json,sys
from openviking.server.transfer_token_backend import RedisTransferTokens
from openviking.server.upload_token_store import UploadTokenStore
async def run():
    backend=RedisTransferTokens(json.loads(sys.argv[1]),service_name="synthetic-transfer",key_prefix=sys.argv[2])
    await backend.initialize()
    try:
        store=UploadTokenStore("download"); store._backend=backend
        token, _=await store.aissue("acct","child",30,role="user",uri="viking://resources/child.bin")
        print("RESULT:"+token)
    finally:
        await backend.close()
asyncio.run(run())
"""
    prefix = backends[0]._prefix.split(":")[2]
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        json.dumps(backends[0]._params),
        prefix,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(child.communicate(), 15)
    assert child.returncode == 0, stderr.decode()
    token = next(
        line.removeprefix("RESULT:")
        for line in stdout.decode().splitlines()
        if line.startswith("RESULT:")
    )
    result = await stores[1].aconsume(token)
    assert result.user_id == "child" and result.uri.endswith("child.bin")


@pytest.mark.asyncio
async def test_mcp_download_to_different_http_owner_keeps_auth_boundaries(
    pair, client, fake_fs, api_key_manager, monkeypatch
):
    from openviking.server import auth, mcp_endpoint
    from tests.server.test_content_download_token import OTHER, TARGET, _mint

    stores, _ = pair
    monkeypatch.setattr(mcp_endpoint, "download_token_store", stores[0])
    monkeypatch.setattr(auth, "download_token_store", stores[1])
    token = await _mint()
    response = await client.get(
        "/api/v1/content/download",
        params={"token": token, "uri": OTHER},
        headers={"X-OpenViking-User": "spoofed", "X-OpenViking-Actor-Peer": "spoofed"},
    )
    assert response.status_code == 200
    assert fake_fs.reads[-1][0] == TARGET
    assert fake_fs.reads[-1][1].actor_peer_id == "bot-a"
    assert (
        await client.get("/api/v1/content/download", params={"token": token})
    ).status_code == 401
    token = await _mint()
    response = await client.get(
        "/api/v1/content/download",
        params={"token": token, "uri": OTHER},
        headers={"X-API-Key": api_key_manager.user_key},
    )
    assert response.status_code == 200 and fake_fs.reads[-1][0] == OTHER
    # The API-key route did not consume the independent token.
    assert (await stores[1].aconsume(token)).uri == TARGET
    token = await _mint()
    await api_key_manager.set_role("acct", "user", Role.USER)
    assert (
        await client.get("/api/v1/content/download", params={"token": token})
    ).status_code == 401


@pytest.mark.asyncio
async def test_mcp_resource_to_different_http_owner_keeps_acl_and_revocation(
    pair, upload_temp_dir, monkeypatch, upload_api_key_manager
):
    from openviking.server import auth, mcp_endpoint
    from tests.server.test_resources_temp_upload_token import (
        test_token_upload_auto_ingests_and_returns_result,
    )

    _, backends = pair
    stores = [UploadTokenStore("upload") for _ in backends]
    for store, backend in zip(stores, backends, strict=True):
        store._backend = backend
    monkeypatch.setattr(mcp_endpoint, "upload_token_store", stores[0])
    monkeypatch.setattr(auth, "upload_token_store", stores[1])
    # The existing full HTTP regression exercises ACL enforcement, OAuth origin,
    # actor scope, role downgrade/promotion and deletion fences through the new backend.
    await test_token_upload_auto_ingests_and_returns_result(
        upload_temp_dir, monkeypatch, upload_api_key_manager
    )


@pytest.mark.asyncio
async def test_mcp_skill_mint_preserves_kind_and_install_parameters(pair, monkeypatch):
    from types import SimpleNamespace
    from urllib.parse import parse_qs, urlparse

    from openviking.server import mcp_endpoint
    from openviking.server.identity import RequestContext
    from openviking_cli.session.user_id import UserIdentifier

    _, backends = pair
    stores = [UploadTokenStore("upload") for _ in backends]
    for store, backend in zip(stores, backends, strict=True):
        store._backend = backend
    monkeypatch.setattr(mcp_endpoint, "upload_token_store", stores[0])
    monkeypatch.setattr(mcp_endpoint, "get_server_config", lambda: ServerConfig())
    monkeypatch.setattr(mcp_endpoint, "get_service", lambda: SimpleNamespace())
    marker = mcp_endpoint._mcp_ctx.set(
        RequestContext(UserIdentifier("acct", "alice"), Role.USER, actor_peer_id="bot-a")
    )
    try:
        text = await mcp_endpoint.add_skill(
            path="/tmp/synthetic-skills.zip", skills=["demo"], list_only=True
        )
    finally:
        mcp_endpoint._mcp_ctx.reset(marker)
    url = next(line.strip() for line in text.splitlines() if "?token=" in line)
    token = parse_qs(urlparse(url).query)["token"][0]
    result = await stores[1].aconsume(token)
    assert result.kind == "skill" and result.skill_names == ["demo"]
    assert result.list_only and result.actor_peer_id == "bot-a"


@pytest.mark.asyncio
async def test_multiple_processes_compete_for_one_redemption(pair):
    import json
    import sys

    stores, backends = pair
    token, _ = await stores[0].aissue("acct", "alice", 30, role=Role.USER)
    script = """
import asyncio,json,sys
from openviking.server.transfer_token_backend import RedisTransferTokens
from openviking.server.upload_token_store import UploadTokenError,UploadTokenStore
async def run():
    backend=RedisTransferTokens(json.loads(sys.argv[1]),service_name="synthetic-transfer",key_prefix=sys.argv[2])
    await backend.initialize()
    try:
        store=UploadTokenStore("download"); store._backend=backend
        try:
            await store.aconsume(sys.argv[3]); print("RESULT:won")
        except UploadTokenError:
            print("RESULT:lost")
    finally:
        await backend.close()
asyncio.run(run())
"""
    prefix = backends[0]._prefix.split(":")[2]
    children = [
        await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            script,
            json.dumps(backends[0]._params),
            prefix,
            token,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        for _ in range(2)
    ]
    outcomes = await asyncio.wait_for(
        asyncio.gather(*(child.communicate() for child in children)), 20
    )
    for child, (_, stderr) in zip(children, outcomes, strict=True):
        assert child.returncode == 0, stderr.decode()
    assert sum(b"RESULT:won" in stdout for stdout, _ in outcomes) == 1
    assert sum(b"RESULT:lost" in stdout for stdout, _ in outcomes) == 1
    with pytest.raises(UploadTokenError):
        await stores[1].aconsume(token)


@pytest.mark.asyncio
async def test_real_disconnect_before_commit_fails_closed_without_burning(pair):
    from urllib.parse import urlparse

    stores, backends = pair
    token, _ = await stores[0].aissue("acct", "alice", 30, role=Role.USER)
    endpoint = urlparse(os.environ["OV_TEST_TRANSFER_REDIS"])
    drop = False
    handlers = set()

    async def proxy(reader, writer):
        handlers.add(asyncio.current_task())
        peer_writer = None
        reply_task = None
        try:
            peer_reader, peer_writer = await asyncio.open_connection(
                endpoint.hostname, endpoint.port
            )

            async def replies():
                while data := await peer_reader.read(65536):
                    writer.write(data)
                    await writer.drain()

            reply_task = asyncio.create_task(replies())
            while data := await reader.read(65536):
                if drop:
                    break  # Lose this request before Redis can execute it.
                peer_writer.write(data)
                await peer_writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            if peer_writer is not None:
                peer_writer.close()
                await peer_writer.wait_closed()
            if reply_task is not None:
                reply_task.cancel()
                await asyncio.gather(reply_task, return_exceptions=True)
            handlers.discard(asyncio.current_task())

    server = await asyncio.start_server(proxy, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    backend = RedisTransferTokens(
        {"endpoints": [f"redis://127.0.0.1:{port}"], "command_timeout_ms": 1000},
        service_name="synthetic-transfer",
        key_prefix=backends[0]._prefix.split(":")[2],
    )
    try:
        await backend.initialize()
        broken = UploadTokenStore("download")
        broken._backend = backend
        drop = True
        with pytest.raises(UnavailableError):
            await broken.aconsume(token)
        assert not broken._store
        assert (await stores[1].aconsume(token)).user_id == "alice"
    finally:
        await backend.close()
        server.close()
        await server.wait_closed()
        if handlers:
            await asyncio.wait_for(asyncio.gather(*handlers), 2)


@pytest.mark.asyncio
async def test_lifespan_selects_backend_and_closes_without_memory_fallback(pair, monkeypatch):
    from types import SimpleNamespace

    import openviking_cli.utils.config as config_owner
    from openviking.server.transfer_token_backend import transfer_token_lifespan
    from openviking.server.upload_token_store import download_token_store, upload_token_store

    _, backends = pair
    monkeypatch.setattr(
        config_owner,
        "get_openviking_config",
        lambda: SimpleNamespace(
            cache=SimpleNamespace(provider="redis", params=backends[0]._params),
            service_name="synthetic-transfer",
        ),
    )
    config = ServerConfig(
        transfer_tokens={"backend": "redis", "key_prefix": backends[0]._prefix.split(":")[2]}
    )
    async with transfer_token_lifespan(config) as backend:
        assert upload_token_store._backend is backend
        assert download_token_store._backend is backend
        token, _ = await upload_token_store.aissue("acct", "alice", 30, role=Role.USER)
    assert upload_token_store._backend is None and download_token_store._backend is None
    assert backend._handle is None
    # Expiring outstanding Redis entries are not migrated into the default memory store.
    with pytest.raises(UploadTokenError):
        upload_token_store.consume(token)
    async with transfer_token_lifespan(config):
        assert (await upload_token_store.aconsume(token)).user_id == "alice"


@pytest.mark.asyncio
async def test_redis_opt_in_requires_cache_and_does_not_silently_start_memory(monkeypatch):
    from types import SimpleNamespace

    import openviking_cli.utils.config as config_owner
    from openviking.server.transfer_token_backend import transfer_token_lifespan
    from openviking.server.upload_token_store import download_token_store, upload_token_store

    monkeypatch.setattr(config_owner, "get_openviking_config", lambda: SimpleNamespace(cache=None))
    with pytest.raises(InvalidArgumentError):
        async with transfer_token_lifespan(ServerConfig(transfer_tokens={"backend": "redis"})):
            pytest.fail("Redis opt-in must not start without Redis configuration")
    assert download_token_store._backend is None and upload_token_store._backend is None


@pytest.mark.asyncio
async def test_cancel_during_physical_command_waits_for_runtime_and_does_not_resurrect(
    pair, monkeypatch
):
    import threading

    stores, backends = pair
    token, _ = await stores[0].aissue("acct", "alice", 30, role=Role.USER)
    client = backends[0]._handle.client
    physical_eval = client.eval
    committed = threading.Event()

    async def hold_reply(*args):
        result = await physical_eval(*args)
        committed.set()
        await asyncio.sleep(60)
        return result

    monkeypatch.setattr(client, "eval", hold_reply)
    operation = asyncio.create_task(stores[0].aconsume(token))
    async with asyncio.timeout(2):
        while not committed.is_set():
            await asyncio.sleep(0.005)
    operation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(operation, 2)
    with pytest.raises(UploadTokenError):
        await stores[1].aconsume(token)
    # Runtime cancellation has finished before the public caller returns.
    assert await backends[0]._run(lambda redis: redis.ping())


def test_file_configuration_accepts_opt_in_and_checks_namespace(tmp_path):
    import json

    from pydantic import ValidationError

    from openviking.server.config import load_server_config

    path = tmp_path / "ov.conf"
    path.write_text(
        json.dumps(
            {"server": {"transfer_tokens": {"backend": "redis", "key_prefix": "example-token"}}}
        )
    )
    config = load_server_config(str(path))
    assert config.transfer_tokens.backend == "redis"
    assert config.transfer_tokens.key_prefix == "example-token"
    with pytest.raises(ValidationError):
        ServerConfig(transfer_tokens={"backend": "unknown"})
    with pytest.raises(ValidationError):
        ServerConfig(transfer_tokens={"backend": "redis", "key_prefix": "other:purpose"})
