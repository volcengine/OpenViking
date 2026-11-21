# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Exercise Git Watch recovery with the native file and vector stores."""

import asyncio
import json
import os
import shutil
import socket
import subprocess
import time
from typing import Any

import pytest

from openviking.models.embedder.base import DenseEmbedderBase, EmbedResult
from openviking.parse.accessors.git_accessor import GitAccessor
from openviking.resource.watch_manager import WatchManager
from openviking.server.identity import RequestContext, Role
from openviking.service.core import OpenVikingService
from openviking.service.task_tracker import get_task_tracker, set_task_tracker
from openviking.storage import collection_schemas
from openviking.storage.resource_target import AgfsResourceTarget
from openviking.storage.vector_ids import vector_record_id
from openviking.storage.viking_fs import _base as viking_fs_module
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.embedding_config import EmbeddingConfig
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton
from tests.utils.watch_fixtures import shape_watch

SOURCE = "https://github.com/example/watch-recovery.git"
TARGET = "viking://resources/watch-index-recovery"
BEFORE = "refund within seven days"
AFTER = "refund within thirty days"


class FakeEmbedder(DenseEmbedderBase):
    def __init__(self):
        super().__init__(model_name="watch-test")

    def embed(self, text: str | list[dict[str, Any]], is_query: bool = False) -> EmbedResult:
        text = str(text)
        slot = 1 if AFTER in text else 2 if BEFORE in text else 0
        return EmbedResult(dense_vector=[float(i == slot) for i in range(1024)])

    def get_dimension(self) -> int:
        return 1024


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("git") is None, reason="Git is required")
async def test_git_watch_manual_recovery_after_exhaustion(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()

    git("init", "-b", "main")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (repo / "refund.md").write_text(BEFORE, encoding="utf-8")
    count = int(os.environ.get("OV_D9_FIXTURE_FILES", "2"))
    for i in range(count - 1):
        (repo / f"control-{i}.md").write_text(f"Control paragraph {i}.", encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "Initial refund policy")

    metrics = {"clones": 0, "formal_writes": 0, "embedding_calls": 0}
    phase = {}

    def sampled(name):
        phase[name] = dict(metrics)
        for key in metrics:
            metrics[key] = 0

    real_run = asyncio.create_subprocess_exec
    resolve = socket.getaddrinfo
    is_github_url = GitAccessor._is_github_url
    real_write = AgfsResourceTarget.write_file
    real_embed = collection_schemas.embed_compat
    fail_embedding = False

    def lookup(host, port, *args, **kwargs):
        if host == "github.com":
            host = "93.184.215.14"
        return resolve(host, port, *args, **kwargs)

    async def run_git(*args, **kwargs):
        if args[:2] == ("git", "clone") and SOURCE in args:
            metrics["clones"] += 1
        if args[:1] == ("git",) and SOURCE in args:
            args = tuple(repo.as_uri() if arg == SOURCE else arg for arg in args)
        return await real_run(*args, **kwargs)

    async def write_file(target, rel_path, data):
        metrics["formal_writes"] += 1
        return await real_write(target, rel_path, data)

    async def embed(embedder, text, is_query=False):
        if not is_query:
            metrics["embedding_calls"] += 1
        if fail_embedding and not is_query and AFTER in str(text):
            raise ValueError("Terminal embedding failure")
        return await real_embed(embedder, text, is_query=is_query)

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", run_git)
    monkeypatch.setattr(
        GitAccessor,
        "_is_github_url",
        staticmethod(lambda url: url != SOURCE and is_github_url(url)),
    )
    monkeypatch.setattr(AgfsResourceTarget, "write_file", write_file)
    monkeypatch.setattr(collection_schemas, "embed_compat", embed)
    monkeypatch.setattr(EmbeddingConfig, "get_embedder", lambda self: FakeEmbedder())

    user = UserIdentifier.the_default_user()
    ctx = RequestContext(user=user, role=Role.parse(Role.ROOT))
    workspace = tmp_path / "workspace"
    previous_fs = viking_fs_module._instance
    OpenVikingConfigSingleton.reset_instance()
    OpenVikingConfigSingleton.initialize(
        config_dict={
            "enable_watch_scheduler": False,
            "storage": {
                "workspace": str(workspace),
                "agfs": {"backend": "local"},
                "vectordb": {"backend": "local"},
            },
            "embedding": {
                "dense": {
                    "provider": "openai",
                    "model": "watch-test",
                    "api_key": "test-key",
                    "dimension": 1024,
                }
            },
        }
    )
    service = OpenVikingService(path=str(workspace), user=user)
    owner = {"account_id": ctx.account_id, "user_id": user.user_id}

    async def run_watch(manager: WatchManager, watch_id: str, *, manual: bool = True):
        receipt = await manager.trigger(watch_id, manual=manual)
        if receipt is None:
            return None

        async def wait():
            while True:
                record = await get_task_tracker().get(receipt.task_id, **owner)
                if record is not None and record.is_terminal():
                    await manager.settle(watch_id, receipt.task_id)
                    return record
                await asyncio.sleep(0.05)

        return await asyncio.wait_for(wait(), timeout=45)

    try:
        await service.initialize()
        manager = service.watch_manager
        fs = service.viking_fs
        db = service.vikingdb_manager
        assert manager is not None and fs is not None and db is not None
        await service.resources.add_resource(
            ctx=ctx, path=SOURCE, to=TARGET, watch_interval=1, summarize=False, wait=True
        )
        sampled("initial")
        watch = await manager.get_task_by_uri(TARGET, **owner, role=str(ctx.role))
        assert watch is not None
        if watch.source_checkpoint is None:
            await run_watch(manager, watch.task_id)
            sampled("bootstrap")
        (repo / "refund.md").write_text(AFTER, encoding="utf-8")
        git("commit", "-qam", "Extend refund policy")
        sha = git("rev-parse", "HEAD")

        fail_embedding = True
        failed = await run_watch(manager, watch.task_id)
        assert failed is not None and failed.status.value == "failed"
        saved = await manager.store.get_task(watch.task_id)
        assert saved is not None and saved.last_status == "failed"
        assert saved.source_checkpoint is not None
        assert saved.source_checkpoint["head_sha"] == sha
        assert saved.recovery_attempts == 0
        for expected_attempts in (1, 2):
            await shape_watch(manager.store, watch.task_id, next_run_at=time.time() - 1)
            retry = await run_watch(manager, watch.task_id, manual=False)
            assert retry is not None and retry.status.value == "failed"
            saved = await manager.store.get_task(watch.task_id)
            assert saved is not None and saved.recovery_attempts == expected_attempts
        uri = f"{TARGET}/refund.md"
        assert AFTER in await fs.read_file(uri, ctx=ctx)
        vector_id = vector_record_id(ctx.account_id, uri, 2)
        old = await db.get([vector_id], ctx=ctx)
        assert old[0]["vector"][2] == 1
        sampled("failed")

        fail_embedding = False
        recovered = await run_watch(manager, watch.task_id, manual=True)
        sampled("recovery")
        assert recovered is not None and recovered.status.value == "completed"
        saved = await manager.store.get_task(watch.task_id)
        assert saved is not None and saved.recovery_attempts == 0
        new = await db.get([vector_id], ctx=ctx)
        assert new[0]["vector"][1] == 1
        result = await service.search.find(query=AFTER, ctx=ctx, target_uri=TARGET, level=[2])
        assert uri in {match.uri for match in result.resources}
        await shape_watch(manager.store, watch.task_id, next_run_at=time.time() - 1)
        assert await run_watch(manager, watch.task_id, manual=False) is None
        sampled("skip")
        assert phase["skip"] == dict.fromkeys(metrics, 0)
        if count > 2:
            print(json.dumps({"source_files": count, "vlm_enabled": False, "phases": phase}))
    finally:
        await service.close()
        set_task_tracker(None)
        viking_fs_module._instance = previous_fs
        OpenVikingConfigSingleton.reset_instance()
