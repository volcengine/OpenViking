"""Real S3/Git publication, encrypted CAS and durable queue integration.

Set OV_ARTIFACT_S3_ENDPOINT to an isolated MinIO instance. Credentials below
are local test credentials, never used to access production resources.
"""

import asyncio
import json
import os
from dataclasses import replace
from uuid import uuid4

import pytest

from openviking.pyagfs import AsyncAGFSClient, get_binding_client
from openviking.server.identity import RequestContext, Role
from openviking.service.artifact_relation_links import extract_relation_source
from openviking.service.artifact_relations_service import ArtifactRelationsService
from openviking.service.compile_targets import CompileStateStore
from openviking.storage.queuefs.queue_manager import QueueManager
from openviking_cli.session.user_id import UserIdentifier
from tests.agfs.test_artifact_relations_service_binding import ROOT, NativeFS, save

ENDPOINT = os.getenv("OV_ARTIFACT_S3_ENDPOINT")
REDIS = os.getenv("OV_ARTIFACT_REDIS_URL")
pytestmark = pytest.mark.skipif(not ENDPOINT, reason="isolated S3 endpoint not configured")


@pytest.fixture(params=[False, True], ids=["plaintext", "encrypted"])
async def s3_environment(tmp_path, request):
    import boto3

    access = os.getenv("OV_ARTIFACT_S3_ACCESS_KEY", "artifact-test")
    secret = os.getenv("OV_ARTIFACT_S3_SECRET_KEY", "artifact-local-test-password")
    bucket = os.getenv("OV_ARTIFACT_S3_BUCKET", "artifact-relations-test")
    prefix = "artifact-relations/" + uuid4().hex
    s3 = boto3.client(
        "s3",
        endpoint_url=ENDPOINT,
        aws_access_key_id=access,
        aws_secret_access_key=secret,
        region_name="us-east-1",
    )
    try:
        await asyncio.to_thread(s3.head_bucket, Bucket=bucket)
    except s3.exceptions.ClientError as exc:
        if exc.response["Error"]["Code"] not in {"404", "NoSuchBucket"}:
            raise
        try:
            await asyncio.to_thread(s3.create_bucket, Bucket=bucket)
        except s3.exceptions.BucketAlreadyOwnedByYou:
            pass
    config_path = tmp_path / "ragfs.toml"
    config_path.write_text(
        '[git]\nenabled=true\nbackend="s3"\n[git.s3]\n'
        + "\n".join(
            f"{key}={json.dumps(value)}"
            for key, value in {
                "bucket": bucket,
                "prefix": prefix + "/git",
                "region": "us-east-1",
                "endpoint": ENDPOINT,
                "access_key": access,
                "secret_key": secret,
                "cas_mode": "native",
                "use_path_style": True,
            }.items()
        )
    )
    config = {"service_name": "artifact-s3-" + uuid4().hex}
    if request.param:
        config["encryption"] = {"root_key": bytes(range(32)), "provider_type": 0}
    if REDIS:
        config["cache"] = {
            "enabled": False,
            "runtime_enabled": True,
            "provider": "redis",
            "redis": {"endpoints": [REDIS]},
        }
        config["pathlock"] = {"provider": "cache"}
    binding, _ = get_binding_client()
    native = binding(config=config, git_config_path=str(config_path))
    native.mount(
        "s3fs",
        "/local",
        {
            "bucket": bucket,
            "prefix": prefix + "/fs",
            "region": "us-east-1",
            "endpoint": ENDPOINT,
            "access_key_id": access,
            "secret_access_key": secret,
            "use_path_style": True,
            "disable_ssl": ENDPOINT.startswith("http:"),
        },
    )
    native.mount(
        "queuefs",
        "/queue",
        (
            {"backend": "cache", "service_name": config["service_name"]}
            if REDIS
            else {"backend": "sqlite", "db_path": str(tmp_path / "queue.db")}
        ),
    )
    agfs = AsyncAGFSClient(native)
    store = CompileStateStore(agfs)
    async with store.edit("acme", ROOT, create=True):
        pass
    fs = NativeFS(agfs)
    manager = QueueManager(native)
    service = ArtifactRelationsService(store, fs, manager)
    manager.get_queue("ArtifactRelations", dequeue_handler=service, allow_create=True)
    ctx = RequestContext(UserIdentifier("acme", "alice"), Role.USER)
    calls = []
    original = agfs.run

    async def counted(method, *args, **kwargs):
        calls.append((method, kwargs.get("oids", [])))
        return await original(method, *args, **kwargs)

    agfs.run = counted
    yield service, fs, ctx, calls
    # Only remove objects from this run's UUID prefix.
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix + "/"):
        keys = [{"Key": item["Key"]} for item in page.get("Contents", [])]
        if keys:
            await asyncio.to_thread(s3.delete_objects, Bucket=bucket, Delete={"Objects": keys})


@pytest.mark.asyncio
async def test_s3_compile_edit_publication_and_stale_cas(s3_environment):
    service, fs, ctx, calls = s3_environment
    token = "local-compile-token"
    async with service._targets.edit("acme", ROOT) as target:
        target.update(active_operation_id="op", active_task_id="task")
    await service._targets.put_operation(
        "acme",
        ROOT,
        {
            "operation_id": "op",
            "actor": "alice",
            "finished_at": None,
            "meta": {"write_token": token},
        },
    )
    trusted = replace(ctx, compile_write_token=token)
    files = {ROOT + "/a.md": b"[future](b.md)", ROOT + "/c.md": b"unchanged"}
    await save(service, fs, trusted, files)
    await service.merge_compile_sources(
        {uri: extract_relation_source(body) for uri, body in files.items()}, trusted
    )
    head = (await fs.commit(paths=[ROOT], ctx=trusted, message="compile"))["commit_oid"]
    await service.changed("acme", ROOT, head, operation_id="op")
    calls.clear()
    queue = service._manager.get_queue("ArtifactRelations")
    await queue.dequeue()
    assert await service.ready("acme", ROOT, head, "op")
    assert not any(method == "git_snapshot_blobs" for method, _ in calls)
    async with service._targets.edit("acme", ROOT) as target:
        target.update(active_operation_id=None, active_task_id=None)
    await save(service, fs, ctx, {ROOT + "/b.md": b"created"})
    calls.clear()
    await queue.dequeue()
    reads = [oid for method, oids in calls if method == "git_snapshot_blobs" for oid in oids]
    assert reads == [extract_relation_source(b"created").blob_oid]
    calls.clear()
    graph = await service.query(ROOT, ctx)
    assert any(
        edge["type"] == "references" and edge["to"] == ROOT + "/b.md" for edge in graph["edges"]
    )
    assert not any(method.startswith("git_snapshot") for method, _ in calls)
    if os.getenv("OV_ARTIFACT_METRICS"):
        _, sources, stored_graph = await service.store.get_baseline("acme", ROOT)
        print(
            json.dumps(
                {
                    "queue_backend": "redis" if REDIS else "sqlite",
                    "nodes": len(stored_graph.nodes),
                    "edges": len(stored_graph.edges),
                    "compile_blob_reads": 0,
                    "edit_blob_reads": len(reads),
                    "query_blob_reads": 0,
                    "sources_json_bytes": len(sources.model_dump_json().encode()),
                    "graph_json_bytes": len(stored_graph.model_dump_json(by_alias=True).encode()),
                },
                sort_keys=True,
            )
        )
    # Real If-Match / If-None-Match, including encrypted envelopes.
    agfs = fs._async_agfs
    path = "/local/acme/cas.json"
    await agfs.ensure_parent_dirs(path)
    assert await agfs.conditional_write(path, None, b"first")
    assert not await agfs.conditional_write(path, None, b"duplicate")
    assert await agfs.conditional_write(path, b"first", b"latest")
    assert not await agfs.conditional_write(path, b"first", b"late")
    assert await agfs.read(path) == b"latest"


@pytest.mark.asyncio
async def test_s3_pending_unique_notifications_keep_active_followup(s3_environment):
    service, _, _, _ = s3_environment
    queue = service._manager.get_queue("ArtifactRelations")
    await asyncio.gather(*(service._enqueue("acme", ROOT) for _ in range(20)))
    assert await queue.size() == 1
    delivery = await queue.dequeue_raw()
    await asyncio.gather(*(service._enqueue("acme", ROOT) for _ in range(20)))
    assert await queue.size() == 1
    await queue.ack(delivery["id"], delivery)
    assert await queue.size() == 1
