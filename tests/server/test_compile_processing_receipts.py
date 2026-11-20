"""An HTTP content receipt remains a Compile dependency until its real Work settles."""

import asyncio

from openviking.server.identity import RequestContext, Role
from openviking.service.compile_targets import CompileLifecycle
from openviking.service.external_task_service import ExternalTaskSnapshot
from openviking.service.task_tracker import TaskStatus, get_task_tracker
from openviking.storage.queuefs.content_write_processor import ContentWriteProcessor
from openviking.utils.compile_publication import PublicationReceipts
from openviking_cli.session.user_id import UserIdentifier


async def test_http_batch_write_task_blocks_compile_until_native_queue_completion(
    client_with_resource, service, monkeypatch, request
):
    client, target = client_with_resource
    tracker = get_task_tracker()
    # Match DevAuth's authenticated principal; test VLM/embedder fixtures are local.
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    source = "viking://resources/receipt-source"
    await service.viking_fs.write_file(source + "/evidence.md", "A synthetic fact.", ctx=ctx)
    task = await tracker.create(
        "compile",
        account_id=ctx.account_id,
        user_id=ctx.user.user_id,
        task_id="cmp_receipt-integration",
        resource_id=target,
        meta={
            "request": {
                "from": [source],
                "to": target,
                "skill": "viking://system/compile/example-skills/llm-wiki",
                "args": {},
            }
        },
        auth={"openviking_connection": {"role": "root"}},
    )
    lifecycle = service.compile.lifecycle
    assert await lifecycle.on_start(task) == "proceed"
    ready = asyncio.Event()
    request.addfinalizer(ready.set)
    refresh = ContentWriteProcessor._execute_refresh

    async def controlled_refresh(processor, *args, **kwargs):
        await ready.wait()
        return await refresh(processor, *args, **kwargs)

    monkeypatch.setattr(ContentWriteProcessor, "_execute_refresh", controlled_refresh)
    response = await client.post(
        "/api/v1/content/batch-write",
        json={
            "root_uri": target,
            "operations": [
                {
                    "uri": target + "/receipt-page.md",
                    "mode": "create",
                    "content": "# Result\nA synthetic fact.\n",
                }
            ],
            "wait": False,
        },
    )
    assert response.status_code == 200, response.text
    receipt = response.json()["result"]
    assert receipt["task_id"]
    processing = await tracker._store.get_durable_task(
        receipt["task_id"], account_id=ctx.account_id, user_id=ctx.user.user_id
    )
    assert processing.task_type == "content_batch_write"
    assert processing.status in {TaskStatus.PENDING, TaskStatus.RUNNING}
    collector = PublicationReceipts()
    collector.add(receipt["task_id"], target, "content_batch_write")
    snapshot = ExternalTaskSnapshot(
        status="completed", result={"publication": collector.snapshot()}
    )
    pending = await lifecycle.on_finish(task, snapshot)
    assert pending.status == "running" and pending.stage == "indexing"
    # Existing queue/Task owner drains real native messages; Compile adds no polling queue.
    ready.set()
    status = await service.resources.wait_processed()
    assert status["Embedding"]["error_count"] == 0
    processing = await tracker._store.get_durable_task(
        receipt["task_id"], account_id=ctx.account_id, user_id=ctx.user.user_id
    )
    assert processing.status == TaskStatus.COMPLETED
    restarted = CompileLifecycle(lifecycle.store, service.viking_fs)
    final = await restarted.on_finish(task, ExternalTaskSnapshot(status="completed"))
    assert final.status == "completed"
    assert final.result["publication"]["state"] == "complete"
    assert final.result["publication"]["receipts"][0]["task_id"] == receipt["task_id"]
    replay = await restarted.on_finish(task, snapshot)
    assert replay.result == final.result
