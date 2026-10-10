# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Real RAGFS filesystem/pathlocks/QueueFS with local NLP and no model HTTP."""

import asyncio
import json

import pytest

from openviking.retrieve.memory_association import nlp, runtime
from openviking.retrieve.memory_association import store as store_module
from openviking.retrieve.memory_association.runtime import QUEUE
from openviking.server.identity import RequestContext, Role
from openviking.service.task_work_index import bind_task_context, extract_task_metadata
from openviking.storage.queuefs.queue_manager import init_queue_manager
from openviking.storage.viking_fs import init_viking_fs
from openviking.utils.agfs_utils import RagfsBindingConfig, create_agfs_client
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.agfs_config import AGFSConfig
from openviking_cli.utils.config.retrieval_config import RetrievalConfig


async def test_native_queue_and_shared_cue_lifecycle(tmp_path, monkeypatch):
    pytest.importorskip("spacy")
    model = nlp._load_model("en_core_web_sm")
    if model is None:
        pytest.skip("Optional spaCy model not installed")
    monkeypatch.setattr(nlp, "get_nlp_full", lambda name=None: model)
    monkeypatch.setattr(runtime, "get_nlp_full", lambda name=None: model)
    client = create_agfs_client(
        RagfsBindingConfig(agfs=AGFSConfig(path=str(tmp_path), backend="memory"))
    )
    queues = init_queue_manager(client)
    config = RetrievalConfig(memory_association={"enabled": True})
    fs = init_viking_fs(client, retrieval_config=config)
    ctx = RequestContext(user=UserIdentifier("binding-test", "alice"), role=Role.ROOT)
    root = "viking://user/alice/memories"
    a, b = root + "/events/a.md", root + "/events/b.md"
    with bind_task_context("primary-add", ctx.account_id, ctx.user.user_id):
        await fs.write_file(a, "Caroline is learning the piano.", ctx=ctx)
        await fs.write_file(b, "Caroline is learning the violin.", ctx=ctx)
    queue = queues.get_queue(QUEUE)
    jobs = [await queue.dequeue_raw(), await queue.dequeue_raw()]
    assert all(job and extract_task_metadata(job) is None for job in jobs)
    # Two separate store instances rely on the native shared pathlock, not a
    # Python lock pool owned by one instance.
    second = store_module.FileAssociationStore(fs, config.memory_association)
    await asyncio.gather(fs.memory_association.refresh(a, ctx), second.refresh(b, ctx))
    for job in jobs:
        await queue.process_dequeued(job)
        await queue.ack(job["id"], message=job)
    meta = json.loads(await fs.read_file(root + "/.association/Caroline/meta.json", ctx=ctx))
    assert {ref["uri"] for ref in meta["memories"]} == {a, b}
    assert any(
        hit["memory_uri"] == b
        for hit in await fs.memory_association.search("What does Caroline play?", [root], ctx)
    )
    await fs.write_file(a, "Melanie is learning the piano.", ctx=ctx)
    await queue.dequeue()
    assert {
        ref["uri"]
        for ref in json.loads(
            await fs.read_file(root + "/.association/Caroline/meta.json", ctx=ctx)
        )["memories"]
    } == {b}
    await fs.rm(b, ctx=ctx)
    await queue.dequeue()
    assert not await fs.exists(root + "/.association/Caroline", ctx=ctx)
    assert not await fs.exists(root + "/.association/.pending.json", ctx=ctx)
    c, d = root + "/events/c.md", root + "/events/d.md"
    await fs.cp(a, c, ctx=ctx)
    await queue.dequeue()
    await fs.mv(c, d, ctx=ctx)
    await queue.dequeue()
    await queue.dequeue()
    hits = await fs.memory_association.search("What does Melanie play?", [root], ctx)
    assert {hit["memory_uri"] for hit in hits} == {a, d}
    # A root copy must rebuild references for the destination owner rather
    # than expose the copied source-owner URIs.
    copied_root = "viking://user/bob/memories"
    await fs.mkdir("viking://user/bob", ctx=ctx)
    await fs.cp(root, copied_root, recursive=True, ctx=ctx)
    await queue.dequeue()
    copied = await fs.memory_association.search("What does Melanie play?", [copied_root], ctx)
    assert {hit["memory_uri"] for hit in copied} == {
        copied_root + "/events/a.md",
        copied_root + "/events/d.md",
    }
    await fs.rm(root + "/events", recursive=True, ctx=ctx)
    await queue.dequeue()
    assert await fs.memory_association.search("What does Melanie play?", [root], ctx) == []
    # No vector backend or embedding provider was configured at any stage.
    assert fs.vector_store is None and fs.query_embedder is None
    queues.stop()
