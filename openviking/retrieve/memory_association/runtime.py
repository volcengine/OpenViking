# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Optional filesystem adapter and durable, independently retried derived work."""

import asyncio
import json

from openviking.core.memory_association import is_association_uri, is_memory_source, memory_root
from openviking.server.identity import RequestContext, Role
from openviking.service.task_work_index import detach_task_context
from openviking.storage.queuefs.named_queue import DequeueHandlerBase
from openviking.storage.queuefs.process_result import ProcessResult
from openviking.storage.queuefs.queue_manager import get_queue_manager
from openviking.storage.viking_fs import VikingFS
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger

from .nlp import get_nlp_full
from .store import FileAssociationStore

logger = get_logger(__name__)
QUEUE = "MemoryAssociation"


class AssociationProcessor(DequeueHandlerBase):
    def __init__(self, store, queues):
        self.store = store
        self.queues = queues

    async def on_dequeue(self, data):
        payload = data.get("data", data)
        if isinstance(payload, str):
            payload = json.loads(payload)
        attempt = payload.get("attempt", 0)
        ctx = RequestContext(
            user=UserIdentifier(payload["account_id"], payload["user_id"]), role=Role.ROOT
        )
        try:
            if attempt:
                await asyncio.sleep(min(2**attempt, 30))
            await self.store.refresh_tree(payload["uri"], ctx)
            return ProcessResult.success()
        except Exception:
            logger.exception(
                "Association refresh failed for %s (attempt %s)", payload["uri"], attempt
            )
            if attempt >= 7:
                # Leave the durable delivery unacknowledged for operator repair;
                # do not replay Add or pretend the derived update succeeded.
                raise
            with detach_task_context():
                await self.queues.enqueue(QUEUE, {**payload, "attempt": attempt + 1})
            return ProcessResult.requeued()


class AssociationVikingFS(VikingFS):
    """Selected only at startup when enabled; ordinary VikingFS is untouched."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if get_nlp_full(self.retrieval_config.memory_association.nlp_model) is None:
            raise RuntimeError(
                "Memory association requires openviking[nlp] and the configured spaCy model"
            )
        self.memory_association = FileAssociationStore(
            self, self.retrieval_config.memory_association
        )
        self._association_queues = get_queue_manager()
        self._association_queues.get_queue(
            QUEUE,
            dequeue_handler=AssociationProcessor(self.memory_association, self._association_queues),
            allow_create=True,
        )

    async def _notify(self, uri, ctx, *, tree=False):
        try:
            ctx = self._ctx_or_default(ctx)
            uri = self._path_to_uri(self._uri_to_path(uri, ctx=ctx), ctx=ctx)
            if is_association_uri(uri) or not (
                memory_root(uri) and (tree or is_memory_source(uri))
            ):
                return
            with detach_task_context():
                await self._association_queues.enqueue(
                    QUEUE, {"uri": uri, "account_id": ctx.account_id, "user_id": ctx.user.user_id}
                )
        except Exception:
            # Primary mutation already committed. Never turn a derived enqueue
            # error into an ambiguous Add failure and cause duplicate imports.
            logger.exception(
                "Association enqueue failed after committed write: %s; refresh required", uri
            )

    async def write_file(self, uri, content, ctx=None, lease_ref=None, auto_pathlock=True):
        result = await super().write_file(uri, content, ctx, lease_ref, auto_pathlock)
        await self._notify(uri, ctx)
        return result

    async def write(self, uri, data, ctx=None):
        result = await super().write(uri, data, ctx)
        await self._notify(uri, ctx)
        return result

    async def write_file_bytes(self, uri, content, ctx=None, lease_ref=None, auto_pathlock=True):
        result = await super().write_file_bytes(uri, content, ctx, lease_ref, auto_pathlock)
        await self._notify(uri, ctx)
        return result

    async def append_file(self, uri, content, ctx=None, lease_ref=None):
        result = await super().append_file(uri, content, ctx, lease_ref)
        await self._notify(uri, ctx)
        return result

    async def rm(self, uri, recursive=False, ctx=None, lease_ref=None, auto_pathlock=True):
        result = await super().rm(uri, recursive, ctx, lease_ref, auto_pathlock)
        await self._notify(uri, ctx, tree=True)
        return result

    async def cp(self, old_uri, new_uri, recursive=False, ctx=None, lease_ref=None):
        result = await super().cp(old_uri, new_uri, recursive, ctx, lease_ref)
        await self._notify(new_uri, ctx, tree=True)
        return result

    async def mv(self, old_uri, new_uri, ctx=None, lease_ref=None):
        result = await super().mv(old_uri, new_uri, ctx=ctx, lease_ref=lease_ref)
        await self._notify(old_uri, ctx, tree=True)
        await self._notify(new_uri, ctx, tree=True)
        return result
