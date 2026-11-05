# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Three-file Artifact Relations persistence, independent of queues and builders.

Payloads are immutable per build; a backend CAS of the state publishes them.
Generation-scoped directories isolate deleted/recreated targets. PathLock
coordinates normal work; conditional state writes fence delayed remote I/O.
"""

from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from openviking.pyagfs.async_client import fs_ctx_from_agfs_path
from openviking.pyagfs.exceptions import AGFSNotFoundError
from openviking.service.artifact_relations_models import (
    ArtifactRelations,
    ArtifactRelationsState,
    RelationSource,
    RelationSources,
    can_recover_publication,
)
from openviking.service.compile_targets import LOCK_TIMEOUT_SECONDS, CompileStateStore
from openviking.utils.path_safety import normalize_storage_target_uri
from openviking_cli.utils import get_logger

logger = get_logger(__name__)
T = TypeVar("T", bound=BaseModel)


class ArtifactRelationsStoreError(RuntimeError):
    """Stored data or build ownership does not satisfy the publication contract."""


class ArtifactRelationsStore:
    def __init__(self, compile_store: CompileStateStore) -> None:
        self._store = compile_store
        self._agfs = compile_store._agfs

    def _directory(self, account_id: str, to: str) -> str:
        if not account_id or "/" in account_id or "\\" in account_id or account_id in {".", ".."}:
            raise ValueError("account_id must be a single path segment")
        to = normalize_storage_target_uri(to)
        return f"{self._store._target_dir(account_id, to)}/artifact_graph"

    @asynccontextmanager
    async def _locked(self, account_id: str, to: str) -> AsyncIterator[tuple[str, dict, dict]]:
        directory = self._directory(account_id, to)
        record_path = directory.rsplit("/", 1)[0] + "/state.json"
        ctx = fs_ctx_from_agfs_path(directory)
        lease = await self._agfs.pathlock_acquire_batch(
            [
                {"path": record_path, "kind": "exact"},
                {"path": directory, "kind": "tree"},
            ],
            timeout_secs=LOCK_TIMEOUT_SECONDS,
            fs_ctx=ctx,
        )
        try:
            target = await self._store.read_json(record_path)
            if target is None or target.get("status") != "active":
                raise ArtifactRelationsStoreError("Compile target is missing or deleted")
            directory += "/" + target.get("generation", "legacy")
            ctx = {**ctx, "lease_ref": lease["lease_ref"]}
            path = f"{directory}/artifact_relations_state.json"
            try:
                expected = await self._agfs.read(path, fs_ctx=ctx)
            except (AGFSNotFoundError, FileNotFoundError):
                expected = None
            ctx["_state_expected"] = expected
            yield directory, ctx, target
        finally:
            await self._agfs.pathlock_release(
                lease, fs_ctx={key: val for key, val in ctx.items() if not key.startswith("_")}
            )

    async def _read(self, directory: str, name: str, model: type[T]) -> T | None:
        value = await self._store.read_json(f"{directory}/{name}.json")
        return model.model_validate(value) if value is not None else None

    async def _replace(self, directory: str, name: str, value: BaseModel, ctx: dict) -> None:
        path = f"{directory}/{name}.json"
        fs_ctx = {key: val for key, val in ctx.items() if not key.startswith("_")}
        if name == "artifact_relations_state":
            # Each transition has a fresh revision, preventing an ABA match
            # when identical requests or retries revisit the same phase.
            value.revision = uuid4().hex
            data = value.model_dump_json(by_alias=True).encode("utf-8")
            await self._agfs.ensure_parent_dirs(path, fs_ctx=fs_ctx)
            try:
                changed = await self._agfs.conditional_write(
                    path, ctx["_state_expected"], data, fs_ctx=fs_ctx
                )
            except Exception:
                if await self._agfs.read(path, fs_ctx=fs_ctx) != data:
                    raise
                changed = True
            if not changed:
                raise ArtifactRelationsStoreError("Publication ownership changed during commit")
            ctx["_state_expected"] = data
            return
        temporary = f"{directory}/.replace-{uuid4().hex}.tmp"
        data = value.model_dump_json(by_alias=True).encode("utf-8")
        try:
            await self._agfs.ensure_parent_dirs(temporary, fs_ctx=fs_ctx)
            await self._agfs.write(temporary, data, fs_ctx=fs_ctx)
            try:
                await self._agfs.replace(temporary, path, fs_ctx=fs_ctx)
            except Exception:
                # A lost response (including a post-operation lock error) is
                # ambiguous. Confirm the exact candidate while still coordinated.
                if await self._store.read_json(path) != value.model_dump(
                    mode="json", by_alias=True
                ):
                    raise
        finally:
            # A failed write may leave a partial temp. Never delete a business file.
            try:
                await self._agfs.rm(temporary, fs_ctx=fs_ctx)
            except (AGFSNotFoundError, FileNotFoundError):
                pass
            except Exception:
                logger.warning("Artifact Relations temporary cleanup failed: %s", temporary)

    @staticmethod
    def _check_owner(target: dict, operation_id: str | None, generation: str | None = None) -> None:
        if generation is not None and target.get("generation", "legacy") != generation:
            raise ArtifactRelationsStoreError("Compile ownership changed: target generation")
        if target.get("active_operation_id") != operation_id or (
            operation_id is None and target.get("active_task_id")
        ):
            raise ArtifactRelationsStoreError("Compile ownership changed")

    async def _discard_build(self, directory: str, build_id: str | None, ctx: dict) -> None:
        """Remove only a known obsolete identity, never enumerate live builds.

        A delayed cleanup can therefore never remove a later build. Cleanup
        failure does not change publication success.
        """
        if build_id is None:
            return
        try:
            await self._agfs.rm(
                f"{directory}/builds/{build_id}",
                recursive=True,
                fs_ctx={key: val for key, val in ctx.items() if not key.startswith("_")},
            )
        except (AGFSNotFoundError, FileNotFoundError):
            pass
        except Exception:
            logger.warning("Obsolete artifact build cleanup failed: %s/%s", directory, build_id)

    async def _discard_compile_sources(
        self, directory: str, operation_id: str | None, ctx: dict
    ) -> None:
        """Remove only staging sources owned by the published Compile."""
        if operation_id is None:
            return
        try:
            sources = await self._read(directory, "relation_sources", RelationSources)
            if sources is None or sources.compile_operation_id != operation_id:
                return
            await self._agfs.rm(
                f"{directory}/relation_sources.json",
                fs_ctx={key: val for key, val in ctx.items() if not key.startswith("_")},
            )
        except (AGFSNotFoundError, FileNotFoundError):
            pass
        except Exception:
            logger.warning("Compile relation source cleanup failed: %s", directory)

    @staticmethod
    def _check_state(state: ArtifactRelationsState) -> None:
        if state.status == "building":
            if state.phase is None or (state.phase != "queued" and state.build_id is None):
                raise ArtifactRelationsStoreError("Invalid building state")
            if state.error is not None:
                raise ArtifactRelationsStoreError("Building state cannot have a terminal error")
        elif state.phase is not None or (state.status == "ready" and state.build_id is None):
            raise ArtifactRelationsStoreError("Invalid terminal state")
        if state.status == "ready" and state.error is not None:
            raise ArtifactRelationsStoreError("Ready state cannot have an error")
        if state.status == "ready" and state.published_build_id != state.build_id:
            raise ArtifactRelationsStoreError("Ready state does not match the published graph")

    async def _publication(
        self, directory: str, ctx: dict, target: dict, *, include_graph: bool = True
    ) -> tuple[ArtifactRelationsState | None, ArtifactRelations | None]:
        # _locked already read the exact state bytes for CAS. Reuse that read.
        raw = ctx["_state_expected"]
        state = ArtifactRelationsState.model_validate(json.loads(raw)) if raw is not None else None
        if state is not None and state.target_generation != target.get("generation", "legacy"):
            return None, None
        graph = (
            await self._read(
                f"{directory}/builds/{state.published_build_id}",
                "artifact_relations",
                ArtifactRelations,
            )
            if include_graph and state is not None and state.published_build_id
            else None
        )
        if state is None:
            if graph is not None:
                raise ArtifactRelationsStoreError("Graph exists without publication state")
            return None, None
        self._check_state(state)
        candidate = None
        if state.status == "building" and state.phase == "publishing":
            candidate = await self._read(
                f"{directory}/builds/{state.build_id}", "artifact_relations", ArtifactRelations
            )
        if can_recover_publication(state, candidate):
            graph = candidate
            state = state.model_copy(
                update={
                    "status": "ready",
                    "phase": None,
                    "error": None,
                    "published_build_id": state.build_id,
                }
            )
            await self._replace(directory, "artifact_relations_state", state, ctx)
        if (
            include_graph
            and state.status == "ready"
            and (
                graph is None
                or graph.build_id != state.build_id
                or graph.target_snapshot_id != state.target_snapshot_id
            )
        ):
            raise ArtifactRelationsStoreError("Ready state does not match the published graph")
        return state, graph

    async def get_state(self, account_id: str, to: str) -> ArtifactRelationsState | None:
        """Read status without graph payloads, except to recover publishing.

        Queries and builders still use get_publication/get_baseline to validate
        the selected payload. Recovery scans only need the durable obligation.
        """
        async with self._locked(account_id, to) as (directory, ctx, target):
            state, _ = await self._publication(directory, ctx, target, include_graph=False)
            return state

    async def get_publication(
        self, account_id: str, to: str
    ) -> tuple[ArtifactRelationsState | None, ArtifactRelations | None]:
        """Coherent status and graph; query decides failed/not-ready behavior.

        Does not read sources or content. Repairs only a confirmed publication.
        """
        async with self._locked(account_id, to) as (directory, ctx, target):
            return await self._publication(directory, ctx, target)

    async def get_baseline(
        self, account_id: str, to: str
    ) -> tuple[ArtifactRelationsState | None, RelationSources | None, ArtifactRelations | None]:
        async with self._locked(account_id, to) as (directory, ctx, target):
            state, graph = await self._publication(directory, ctx, target, include_graph=False)
            source_dir = directory
            if state is not None and state.compile_operation_id is None:
                state, graph = await self._publication(directory, ctx, target)
            if state and not state.compile_operation_id and state.published_build_id:
                source_dir += "/builds/" + state.published_build_id
            try:
                sources = await self._read(source_dir, "relation_sources", RelationSources)
            except (json.JSONDecodeError, ValidationError):
                logger.warning("Invalid relation source baseline; rebuilding %s", directory)
                sources = None
            return state, sources, graph

    async def check_compile_wait(
        self,
        account_id: str,
        to: str,
        snapshot_id: str,
        operation_id: str,
        timeout_seconds: float,
    ) -> tuple[ArtifactRelationsState | None, ArtifactRelations | None]:
        """Check completion or expire the pinned build under the publication lock.

        Do not acquire the execution lock: a stuck Worker must not block expiry.
        A completed publication wins the race; otherwise CAS of failed fences
        an in-flight Worker. Legacy requests start their persisted clock once.
        """
        async with self._locked(account_id, to) as (directory, ctx, target):
            state, graph = await self._publication(directory, ctx, target, include_graph=False)
            if (
                state is None
                or state.compile_operation_id != operation_id
                or state.target_snapshot_id != snapshot_id
            ):
                return state, graph
            if state.status == "ready" and graph is None:
                state, graph = await self._publication(directory, ctx, target)
            if state.status != "building":
                return state, graph
            self._check_owner(target, operation_id, state.target_generation)
            now = time.time()
            if state.requested_at is None:
                state.requested_at = now
                await self._replace(directory, "artifact_relations_state", state, ctx)
            elif now - state.requested_at >= timeout_seconds:
                state = state.model_copy(
                    update={
                        "status": "failed",
                        "phase": None,
                        "error_code": "ARTIFACT_RELATIONS_TIMEOUT",
                        "error": f"Artifact relations build timed out after {timeout_seconds:g} seconds",
                    }
                )
                await self._replace(directory, "artifact_relations_state", state, ctx)
            return state, graph

    async def merge_sources(
        self,
        account_id: str,
        to: str,
        pages: dict[str, RelationSource],
        *,
        parser_version: str,
        compile_operation_id: str,
    ) -> None:
        """Merge one successful Compile batch and invalidate the old baseline stamp."""
        # Validate before acquiring locks or changing stored data.
        candidate = RelationSources(
            parser_version=parser_version,
            compile_operation_id=compile_operation_id,
            pages=pages,
        )
        async with self._locked(account_id, to) as (directory, ctx, target):
            self._check_owner(target, compile_operation_id)
            state = await self._read(directory, "artifact_relations_state", ArtifactRelationsState)
            if (
                state is not None
                and state.target_generation == target.get("generation", "legacy")
                and state.compile_operation_id == compile_operation_id
            ):
                raise ArtifactRelationsStoreError("Compile output snapshot is already registered")
            sources = await self._read(directory, "relation_sources", RelationSources)
            if (
                sources is not None
                and sources.parser_version == parser_version
                and sources.compile_operation_id == compile_operation_id
            ):
                candidate.pages = {**sources.pages, **candidate.pages}
            await self._replace(directory, "relation_sources", candidate, ctx)

    async def request_transition(
        self,
        account_id: str,
        to: str,
        snapshot_id: str,
        *,
        compile_operation_id: str | None = None,
    ) -> tuple[ArtifactRelationsState, bool]:
        """Persist intent and report whether a new queued notification is required."""
        candidate = ArtifactRelationsState(
            status="building",
            phase="queued",
            target_snapshot_id=snapshot_id,
            compile_operation_id=compile_operation_id,
            requested_at=time.time(),
        )
        async with self._locked(account_id, to) as (directory, ctx, target):
            self._check_owner(target, compile_operation_id)
            candidate.target_generation = target.get("generation", "legacy")
            state, graph = await self._publication(directory, ctx, target, include_graph=False)
            if state is not None and state.status == "ready" and graph is None:
                state, graph = await self._publication(directory, ctx, target)
            if state is not None:
                candidate.published_build_id = state.published_build_id
            if (
                compile_operation_id is not None
                and state is not None
                and state.status == "failed"
                and (
                    state.compile_operation_id == compile_operation_id
                    and state.target_snapshot_id == snapshot_id
                )
            ):
                return state, False
            if (
                state is not None
                and state.status == "ready"
                and graph is not None
                and graph.target_snapshot_id == snapshot_id
            ):
                if state.compile_operation_id != compile_operation_id:
                    state = state.model_copy(update={"compile_operation_id": compile_operation_id})
                    await self._replace(directory, "artifact_relations_state", state, ctx)
                return state, False
            if state is not None and state.compile_operation_id == compile_operation_id:
                if compile_operation_id is not None and state.target_snapshot_id != snapshot_id:
                    raise ArtifactRelationsStoreError(
                        "Compile snapshot cannot change during a build"
                    )
                if (
                    state.status == "building"
                    and state.phase == "queued"
                    and state.target_snapshot_id == snapshot_id
                ):
                    return state, False
                if state.status == "building" and state.phase != "queued":
                    pending = state.pending or state.target_snapshot_id != snapshot_id
                    if pending != state.pending:
                        state = state.model_copy(update={"pending": pending})
                        await self._replace(directory, "artifact_relations_state", state, ctx)
                    return state, False
                if state.status == "ready" and state.target_snapshot_id == snapshot_id:
                    return state, False
            await self._replace(directory, "artifact_relations_state", candidate, ctx)
            return candidate, True

    async def request(
        self,
        account_id: str,
        to: str,
        snapshot_id: str,
        *,
        compile_operation_id: str | None = None,
    ) -> ArtifactRelationsState:
        """Persist intent; retained for callers that do not own queue notification."""
        state, _ = await self.request_transition(
            account_id,
            to,
            snapshot_id,
            compile_operation_id=compile_operation_id,
        )
        return state

    async def claim(self, account_id: str, to: str) -> ArtifactRelationsState:
        """Claim a queued request. Worker execution serialization belongs to the queue layer."""
        async with self._locked(account_id, to) as (directory, ctx, target):
            state, _ = await self._publication(directory, ctx, target, include_graph=False)
            if state is None or state.status != "building" or state.phase != "queued":
                raise ArtifactRelationsStoreError("No queued build to claim")
            self._check_owner(target, state.compile_operation_id)
            state = state.model_copy(
                update={
                    "phase": "computing",
                    "build_id": uuid4().hex,
                    "attempts": state.attempts + 1,
                }
            )
            await self._replace(directory, "artifact_relations_state", state, ctx)
            return state

    @staticmethod
    def _check_build(
        current: ArtifactRelationsState | None, claimed: ArtifactRelationsState
    ) -> None:
        if (
            current is None
            or current.status != "building"
            or current.phase not in {"computing", "publishing"}
            or claimed.build_id is None
            or current.build_id != claimed.build_id
            or current.target_snapshot_id != claimed.target_snapshot_id
            or current.compile_operation_id != claimed.compile_operation_id
        ):
            raise ArtifactRelationsStoreError("Build ownership changed")

    async def publish(
        self,
        account_id: str,
        to: str,
        claimed: ArtifactRelationsState,
        sources: RelationSources,
        graph: ArtifactRelations,
    ) -> ArtifactRelationsState:
        """Write immutable payloads; CAS of the state is the publication point."""
        if (
            sources.build_id != graph.build_id
            or graph.build_id != claimed.build_id
            or graph.target_snapshot_id != claimed.target_snapshot_id
        ):
            raise ArtifactRelationsStoreError("Candidate identities do not match")
        async with self._locked(account_id, to) as (directory, ctx, target):
            self._check_owner(target, claimed.compile_operation_id, claimed.target_generation)
            state, existing = await self._publication(directory, ctx, target)
            if state is not None and state.status == "ready" and existing == graph:
                await self._discard_compile_sources(directory, claimed.compile_operation_id, ctx)
                return state
            self._check_build(state, claimed)
            publishing = state.model_copy(update={"phase": "publishing"})
            await self._replace(directory, "artifact_relations_state", publishing, ctx)
            build_dir = f"{directory}/builds/{claimed.build_id}"
            await self._replace(build_dir, "relation_sources", sources, ctx)
            await self._replace(build_dir, "artifact_relations", graph, ctx)
            ready = publishing.model_copy(
                update={
                    "status": "ready",
                    "phase": None,
                    "published_build_id": claimed.build_id,
                }
            )
            await self._replace(directory, "artifact_relations_state", ready, ctx)
            await self._discard_compile_sources(directory, claimed.compile_operation_id, ctx)
            if publishing.published_build_id != claimed.build_id:
                await self._discard_build(directory, publishing.published_build_id, ctx)
            return ready

    async def fail(
        self,
        account_id: str,
        to: str,
        claimed: ArtifactRelationsState,
        error: str,
        *,
        retry: bool = False,
    ) -> ArtifactRelationsState:
        """Requeue or finally fail an unpublished attempt; preserve attempt count."""
        async with self._locked(account_id, to) as (directory, ctx, target):
            self._check_owner(target, claimed.compile_operation_id, claimed.target_generation)
            state, graph = await self._publication(directory, ctx, target)
            if (
                state is not None
                and state.status == "ready"
                and graph is not None
                and graph.build_id == claimed.build_id
            ):
                return state
            self._check_build(state, claimed)
            state = state.model_copy(
                update=(
                    {"status": "building", "phase": "queued", "build_id": None, "error": None}
                    if retry
                    else {"status": "failed", "phase": None, "error": error}
                )
            )
            await self._replace(directory, "artifact_relations_state", state, ctx)
            if claimed.build_id != state.published_build_id:
                await self._discard_build(directory, claimed.build_id, ctx)
            return state
