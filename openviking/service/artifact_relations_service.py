# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Durable target notifications, snapshot worker, recovery and authorized queries."""

from __future__ import annotations

import asyncio
import json
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from contextvars import ContextVar
from datetime import datetime, timezone
from secrets import compare_digest

from openviking.service.artifact_relations_builder import ArtifactRelationsBuilder
from openviking.service.artifact_relations_store import (
    ArtifactRelationsStore,
    ArtifactRelationsStoreError,
)
from openviking.service.compile_targets import commit_snapshot, overlaps, task_ctx
from openviking.storage.queuefs.named_queue import DequeueHandlerBase
from openviking.storage.queuefs.process_result import ProcessResult
from openviking.utils.path_safety import normalize_storage_target_uri
from openviking_cli.exceptions import (
    ConflictError,
    FailedPreconditionError,
    NotFoundError,
    PermissionDeniedError,
)
from openviking_cli.utils import get_logger

logger = get_logger(__name__)
QUEUE = "ArtifactRelations"
_admission = ContextVar("artifact_relations_admission", default=None)


class ArtifactRelationsService(DequeueHandlerBase):
    def __init__(self, compile_store, fs, queue_manager) -> None:
        self._targets = compile_store
        self._agfs = compile_store._agfs
        self._fs = fs
        self.store = ArtifactRelationsStore(compile_store)
        self.builder = ArtifactRelationsBuilder(fs)
        self._manager = queue_manager
        self._recovery_task = None

    async def containing_targets(self, account_id: str, uris: list[str]) -> list[str]:
        return sorted(
            item["to"]
            for item in await self._targets.list_summaries(account_id)
            if item.get("status") == "active"
            and any(uri == "viking://" or overlaps(uri.rstrip("/"), item["to"]) for uri in uris)
        )

    @asynccontextmanager
    async def admission(self, account_id: str, uris: list[str]):
        """Serialize overlapping modification and Compile ownership transitions."""
        inherited = _admission.get()
        targets = await self.containing_targets(account_id, uris)
        if (
            inherited
            and inherited["held"]
            and inherited["account"] == account_id
            and set(targets) <= set(inherited["targets"])
        ):
            yield targets
            return
        async with AsyncExitStack() as stack:
            for to in targets:
                path = self._targets._target_dir(account_id, to) + "/artifact_graph_admission"
                await stack.enter_async_context(self._targets.locked(path))
            token = _admission.set(
                {
                    "account": account_id,
                    "targets": targets,
                    "held": True,
                    "registered": set(),
                    "stack": stack,
                }
            )
            try:
                yield targets
            finally:
                _admission.reset(token)

    async def authorize(self, account_id: str, targets: list[str], uris: list[str], ctx) -> None:
        matched = False
        for to in targets:
            target = await self._targets.get(account_id, to)
            operation = target.get("active_operation_id")
            if not operation:
                continue
            run = await self._targets.get_operation(account_id, to, operation)
            expected = (run or {}).get("meta", {}).get("write_token")
            supplied = ctx.compile_write_token
            if (
                not expected
                or not supplied
                or not compare_digest(expected, supplied)
                or run.get("actor") != ctx.user.user_id
                or run.get("finished_at") is not None
                or run["meta"].get("snapshot_committed")
                or any(not (uri == to or uri.startswith(to + "/")) for uri in uris)
            ):
                raise ConflictError(f"Compile is running on {to}", resource="COMPILE_RUNNING")
            matched = True
        if ctx.compile_write_token and not matched:
            raise ConflictError("Compile write authorization expired", resource="COMPILE_RUNNING")

    @asynccontextmanager
    async def mutation(self, uris: list[str], ctx):
        async with self.admission(ctx.account_id, uris) as targets:
            scope = _admission.get()
            await self.authorize(ctx.account_id, targets, uris, ctx)
            try:
                yield
            finally:
                # Includes partial success. A fixed subtree commit also captures
                # directory-only changes; Worker reads no mutable working bytes.
                # A directory move may create a Compile target at its new URI.
                # Include it even though it did not exist when admission began.
                final_targets = sorted(
                    set(targets) | set(await self.containing_targets(ctx.account_id, uris))
                )
                for to in final_targets:
                    if to in scope["registered"]:
                        continue
                    target = await self._targets.get(ctx.account_id, to)
                    if (
                        not target
                        or target.get("status") != "active"
                        or target.get("active_operation_id")
                    ):
                        continue
                    async with self.admission(ctx.account_id, [to]):
                        target = await self._targets.get(ctx.account_id, to)
                        if (
                            not target
                            or target.get("status") != "active"
                            or target.get("active_operation_id")
                        ):
                            continue
                        head = await commit_snapshot(
                            self._fs, [to], "artifact relations modification", ctx
                        )
                        if head:
                            await self.changed(ctx.account_id, to, head)

    async def register_writes(self, uris: list[str], lease_ref, ctx, *, release: bool = True):
        """Publish the write snapshot before handing content to async processors."""
        scope = _admission.get()
        if scope is None or scope["account"] != ctx.account_id:
            return
        if not ctx.compile_write_token and uris and scope["targets"]:
            head = await commit_snapshot(
                self._fs, uris, "artifact relations content write", ctx, lease_ref=lease_ref
            )
            if head:
                for to in scope["targets"]:
                    target = await self._targets.get(ctx.account_id, to)
                    if target and target.get("status") == "active":
                        await self.changed(ctx.account_id, to, head)
                        scope["registered"].add(to)
        elif ctx.compile_write_token:
            scope["registered"].update(scope["targets"])
        if release:
            scope["held"] = False
            await scope["stack"].aclose()

    async def merge_compile_sources(self, pages: dict, ctx) -> None:
        from openviking.service.artifact_relation_links import PARSER_VERSION

        for to in await self.containing_targets(ctx.account_id, list(pages)):
            target = await self._targets.get(ctx.account_id, to)
            operation = target.get("active_operation_id")
            if operation:
                selected = {
                    uri: source for uri, source in pages.items() if uri.startswith(to + "/")
                }
                if selected:
                    await self.store.merge_sources(
                        ctx.account_id,
                        to,
                        selected,
                        parser_version=PARSER_VERSION,
                        compile_operation_id=operation,
                    )

    async def _enqueue(self, account_id: str, to: str) -> None:
        queue = self._manager.get_queue(QUEUE, allow_create=True)
        await queue._ensure_initialized()
        payload = json.dumps(
            {"schema_version": 1, "account_id": account_id, "to": normalize_storage_target_uri(to)},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        await self._agfs.write(f"{queue.path}/enqueue_unique", payload)

    async def changed(
        self, account_id: str, to: str, snapshot_id: str, *, operation_id: str | None = None
    ) -> None:
        """Caller holds modification admission through commit and registration."""
        async with self._targets.edit(account_id, to) as target:
            if target.get("active_operation_id") != operation_id:
                raise ArtifactRelationsStoreError("Compile ownership changed")
            target["wiki_head"] = snapshot_id
        await self.store.request(account_id, to, snapshot_id, compile_operation_id=operation_id)
        await self._enqueue(account_id, to)

    async def ready(
        self,
        account_id: str,
        to: str,
        snapshot: str,
        operation: str,
        *,
        timeout_seconds: float | None = None,
    ) -> bool:
        state, graph = (
            await self.store.get_publication(account_id, to)
            if timeout_seconds is None
            else await self.store.check_compile_wait(
                account_id, to, snapshot, operation, timeout_seconds
            )
        )
        if (
            state is None
            or state.compile_operation_id != operation
            or state.target_snapshot_id != snapshot
        ):
            return False
        if state.status == "failed":
            raise FailedPreconditionError(
                f"Artifact relations build failed: {state.error}",
                details={"reason": state.error_code or "ARTIFACT_RELATIONS_FAILED"},
            )
        return state.status == "ready" and graph is not None

    async def query(self, to: str, ctx) -> dict:
        to = normalize_storage_target_uri(to)
        await self._fs._ensure_access(to, ctx)
        target = await self._targets.get(ctx.account_id, to)
        if target is None or target.get("status") != "active":
            raise NotFoundError(to, "compile target")
        state, graph = await self.store.get_publication(ctx.account_id, to)
        if state is not None and state.status == "failed":
            raise FailedPreconditionError(
                f"Artifact relations build failed: {state.error}",
                details={"reason": state.error_code or "ARTIFACT_RELATIONS_FAILED"},
            )
        if state is None or graph is None:
            raise FailedPreconditionError("Artifact relations are not ready")
        visible = []
        for node in graph.nodes:
            try:
                await self._fs._ensure_access(node.rstrip("/"), ctx)
            except PermissionDeniedError:
                continue
            visible.append(node)
        allowed = set(visible)
        return {
            "nodes": [{"uri": uri} for uri in visible],
            "generated_at": datetime.fromtimestamp(graph.generated_at, timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "edges": [
                edge.model_dump(by_alias=True)
                for edge in graph.edges
                if edge.from_uri in allowed and edge.to in allowed
            ],
        }

    async def process(self, account_id: str, to: str) -> None:
        # Separate from the short publication lock. Crash redelivery takes a new
        # attempt identity after acquiring this cross-instance execution lease.
        path = self._targets._target_dir(account_id, to) + "/artifact_graph_execution"
        async with self._targets.locked(path):
            target = await self._targets.get(account_id, to)
            if target is None or target.get("status") != "active":
                return
            state, sources, graph = await self.store.get_baseline(account_id, to)
            if state is None or state.status != "building":
                return
            if target.get("active_operation_id") != state.compile_operation_id:
                return
            if state.phase != "queued":
                state = await self.store.fail(
                    account_id, to, state, "Interrupted worker", retry=True
                )
            claimed = await self.store.claim(account_id, to)
            try:
                result_sources, result_graph = await self.builder.build(
                    to, claimed, sources, graph, task_ctx(account_id, "artifact-relations")
                )
                await self.store.publish(account_id, to, claimed, result_sources, result_graph)
            except Exception as exc:
                try:
                    state = await self.store.fail(
                        account_id, to, claimed, str(exc), retry=claimed.attempts < 3
                    )
                except ArtifactRelationsStoreError:
                    # New Compile or target lifecycle owns the state now.
                    return
                if state.status == "building":
                    await self._enqueue(account_id, to)
                return
            target = await self._targets.get(account_id, to)
            if target and not target.get("active_operation_id"):
                head = target.get("wiki_head")
                if head and head != claimed.target_snapshot_id:
                    await self.store.request(account_id, to, head)
                    await self._enqueue(account_id, to)

    async def on_dequeue(self, data) -> ProcessResult:
        payload = data.get("data", data)
        if isinstance(payload, str):
            payload = json.loads(payload)
        if payload.get("schema_version") != 1:
            return ProcessResult.failed("Unsupported artifact relations message schema")
        await self.process(payload["account_id"], normalize_storage_target_uri(payload["to"]))
        return ProcessResult.success()

    async def recover(self) -> None:
        """Restore durable notification obligations after any enqueue crash window."""
        for account in await self._targets.list_accounts():
            for summary in await self._targets.list_summaries(account):
                if summary.get("status") != "active":
                    continue
                to = summary["to"]
                try:
                    target = await self._targets.get(account, to)
                    operation = target.get("active_operation_id")
                    if operation:
                        run = await self._targets.get_operation(account, to, operation)
                        if not run or not run["meta"].get("snapshot_committed"):
                            continue
                        head = run["meta"].get("target_head")
                    else:
                        head = target.get("wiki_head")
                    if not head:
                        continue
                    state, _ = await self.store.get_publication(account, to)
                    if state and state.status == "failed" and state.target_snapshot_id == head:
                        continue
                    if (
                        state
                        and state.status == "ready"
                        and state.target_snapshot_id == head
                        and state.compile_operation_id == operation
                    ):
                        continue
                    await self.store.request(account, to, head, compile_operation_id=operation)
                    await self._enqueue(account, to)
                except Exception:
                    logger.exception("Artifact relations recovery failed for %s/%s", account, to)

    async def start(self) -> None:
        self._manager.get_queue(QUEUE, dequeue_handler=self, allow_create=True)
        self._recovery_task = asyncio.create_task(self._recover_loop())

    async def _recover_loop(self) -> None:
        while True:
            try:
                await self.recover()
            except Exception:
                logger.exception("Artifact relations recovery scan failed")
            await asyncio.sleep(10)

    async def stop(self) -> None:
        if self._recovery_task is not None:
            self._recovery_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._recovery_task
            self._recovery_task = None
