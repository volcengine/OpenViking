# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Snapshot/git-like version control mixin for VikingFS."""

import asyncio
import sys
from typing import Any, Dict, List, Optional, Union

from openviking.pyagfs.exceptions import (
    AGFSInvalidOperationError,
    AGFSNotFoundError,
    AGFSPathNotFoundError,
    AGFSResourceExhaustedError,
)
from openviking.server.error_mapping import is_not_found_error, map_exception
from openviking.server.identity import RequestContext
from openviking.storage.viking_fs._base import (
    _prepare_snapshot_diff,
    logger,
)
from openviking_cli.exceptions import (
    InvalidArgumentError,
    NotFoundError,
    ResourceExhaustedError,
)


def _pkg():
    return sys.modules[__package__]

class _SnapshotMixin:
    """Snapshot/git-like version control (commit/restore/show/diff/log)."""

    async def system_sync_status(
        self, uri: str, ctx: Optional[RequestContext] = None
    ) -> Dict[str, Any]:
        """Return multi-write sync status for one Viking URI subtree."""
        self._ensure_access(uri, ctx)
        real_ctx = self._ctx_or_default(ctx)
        path = self._uri_to_path(uri, ctx=ctx)
        return await self._async_agfs.system_sync_status(
            path,
            fs_ctx={"account_id": real_ctx.account_id},
        )

    async def system_sync_retry(
        self, uri: str, ctx: Optional[RequestContext] = None
    ) -> Dict[str, Any]:
        """Retry multi-write sync for one Viking URI subtree."""
        self._ensure_mutable_access(uri, ctx)
        real_ctx = self._ctx_or_default(ctx)
        path = self._uri_to_path(uri, ctx=ctx)
        return await self._async_agfs.system_sync_retry(
            path,
            fs_ctx={"account_id": real_ctx.account_id},
        )

    async def get_gitignore(self, ctx: Optional[RequestContext] = None) -> str:
        """Return the account-level .ovgitignore content, or an empty string if absent.

        Raises ``AGFSInvalidOperationError`` if the stored content is not valid
        UTF-8, mirroring the Rust layer's reject-non-UTF-8 behavior at commit
        time rather than leaking a raw ``UnicodeDecodeError``.
        """
        path = self._gitignore_agfs_path(ctx)
        try:
            raw = await self._async_agfs.read(path, 0, -1)
        except Exception as exc:
            if is_not_found_error(exc):
                return ""
            mapped = map_exception(exc, resource=path)
            if mapped is not None:
                raise mapped from exc
            raise
        if isinstance(raw, bytes):
            data = raw
        elif raw is not None and hasattr(raw, "content"):
            data = raw.content
        else:
            data = b""
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AGFSInvalidOperationError(
                f"invalid ignore file {path}: must be UTF-8: {exc}"
            ) from exc

    async def set_gitignore(
        self,
        content: str,
        ctx: Optional[RequestContext] = None,
    ) -> None:
        """Write the account-level .ovgitignore control file without semantic indexing.

        Validates the size limit up front so a too-large file can never be
        persisted only to make every later ``commit`` fail. Syntax (negation,
        escaping) is still validated at commit time by the Rust layer.
        """
        if not isinstance(content, str):
            raise TypeError("content must be a string")
        path = self._gitignore_agfs_path(ctx)
        data = content.encode("utf-8")
        if len(data) > self._OVGITIGNORE_MAX_BYTES:
            raise AGFSInvalidOperationError(
                f"ignore file too large: {path} is {len(data)} bytes, "
                f"limit {self._OVGITIGNORE_MAX_BYTES} bytes"
            )
        await self._ensure_parent_dirs(path, ctx=ctx)
        await self._async_agfs.write(path, data)

    async def delete_gitignore(self, ctx: Optional[RequestContext] = None) -> None:
        """Delete the account-level .ovgitignore control file. Missing is success."""
        path = self._gitignore_agfs_path(ctx)
        try:
            await self._async_agfs.rm(path, recursive=False)
        except Exception as exc:
            if is_not_found_error(exc):
                return
            mapped = map_exception(exc, resource=path)
            if mapped is not None:
                raise mapped from exc
            raise

    async def commit(
        self,
        *,
        message: str,
        paths: Optional[List[str]] = None,
        branch: str = "main",
        author_name: Optional[str] = None,
        author_email: Optional[str] = None,
        ctx: Optional[RequestContext] = None,
    ) -> Dict[str, Any]:
        """Create a git snapshot of the account's tree.

        Args:
            message: Commit message.
            paths: Optional list of ``viking://`` URIs to scope the commit to;
                entries may be files or directories. Directories are expanded
                recursively with the snapshot pruning rules applied. ``None``
                (default) enumerates the whole account tree. An empty list is
                forwarded as an explicit empty path list (no-op commit). A
                path that exists in neither the VFS nor the previous snapshot
                logs a warning and is treated as a no-op deletion.
            branch: Branch to advance. Defaults to ``"main"``.
            author_name / author_email: Override the default bot author.
            ctx: Request context (provides ``account_id``).

        Returns:
            Dict with ``result`` (``"created"`` / ``"noop"``) and ``commit_oid``.
            When ``result == "created"`` it also includes ``changed`` (paths
            added/updated/removed). Both variants include ``ignored``: the
            number of candidate paths skipped by the account ``.ovgitignore``
            rules (system pruning is not counted). Noop omits ``changed``.
        """
        real_ctx = self._ctx_or_default(ctx)
        account = real_ctx.account_id
        if paths is None:
            tree_paths: Optional[List[str]] = None
        else:
            tree_paths = [self._uri_to_tree_path(p, ctx=real_ctx) for p in paths]
        return await self._async_agfs.run(
            "git_commit",
            account=account,
            branch=branch,
            message=message,
            paths=tree_paths,
            author_name=author_name or self._DEFAULT_GIT_AUTHOR_NAME,
            author_email=author_email or self._DEFAULT_GIT_AUTHOR_EMAIL,
        )

    async def restore(
        self,
        *,
        project_dir: Optional[str] = None,
        source_commit: str,
        branch: str = "main",
        dry_run: bool = False,
        message: Optional[str] = None,
        author_name: Optional[str] = None,
        author_email: Optional[str] = None,
        ctx: Optional[RequestContext] = None,
    ) -> Dict[str, Any]:
        """Restore a project subtree to the state at ``source_commit``.

        Generates a new commit whose parent is the current HEAD (not the
        source commit) and writes the diff through the VFS. Paths outside
        ``project_dir`` are left untouched.

        Args:
            project_dir: ``viking://`` URI of the subtree, e.g.
                ``"viking://resources/proj_a"``. May also be passed as a
                short form like ``"resources/proj_a"``. Trailing slashes are
                stripped. Internal scopes are rejected with ``ValueError``.
                If None, restore the entire tree.
            source_commit: 40-hex OID, branch name, or full ref path.
            branch: Branch to advance. Defaults to ``"main"``.
            dry_run: If True, returns the planned diff without writing.
            message: Optional commit message; defaults to a generated string.
            author_name / author_email: Override the default bot author.
            ctx: Request context (provides ``account_id``).

        Returns:
            Dict containing ``result`` (``"applied"`` / ``"noop"`` / ``"dry_run"``)
            and corresponding oid / diff fields. When an ``Applied`` result has
            vector side-effects, a ``task_id`` is included for polling the
            background reindex via ``GET /api/v1/tasks/{task_id}``.

        After an ``Applied`` result, this method schedules background vector
        maintenance for the affected paths via :class:`ReindexExecutor`:
        directory markers (``.abstract.md`` / ``.overview.md``) recompute or
        delete only that directory's L0/L1 vector; source files reindex (write)
        or delete (removal) their DETAIL vector. ``.relations.json`` has no
        vector side-effect. The rebuild is tracked as a single task
        (``snapshot_restore_reindex``); per-path failures are logged and do not
        block the return value. The task reaches ``completed`` only after each
        affected vector has been written to (or deleted from) the index, so
        polling ``task_id`` to ``completed`` guarantees subsequent ``find``
        reads see the restored state.
        """
        real_ctx = self._ctx_or_default(ctx)
        account = real_ctx.account_id
        tree_dir: Optional[str]
        if project_dir is None:
            tree_dir = None
        else:
            tree_dir = self._uri_to_tree_path(project_dir, ctx=real_ctx).rstrip("/")
            if not tree_dir:
                raise ValueError(f"project_dir must not be empty: {project_dir!r}")
        # Build kwargs dynamically, only include project_dir if it's not None
        kwargs = {
            "account": account,
            "branch": branch,
            "source_commit": source_commit,
            "dry_run": dry_run,
            "message": message,
            "author_name": author_name or self._DEFAULT_GIT_AUTHOR_NAME,
            "author_email": author_email or self._DEFAULT_GIT_AUTHOR_EMAIL,
        }
        if tree_dir is not None:
            kwargs["project_dir"] = tree_dir
        # dry_run only computes the diff; it never writes the VFS, so it needs
        # no lock.
        if dry_run:
            return await self._async_agfs.run("git_restore", **kwargs)

        from openviking.pyagfs.exceptions import GitRestoreWritebackPartialError
        from openviking.storage.errors import LockAcquisitionError, ResourceBusyError

        # Serialize the writeback against concurrent VFS mutations on the same
        # subtree. A scoped restore tree-locks project_dir; a full restore
        # (project_dir is None) locks the account root so only one restore runs
        # per account at a time. The lock covers only the writeback — the
        # background reindex is scheduled after release, otherwise its per-path
        # child locks would conflict with this tree lock.
        lock_path = (
            self._uri_to_path(project_dir, ctx=real_ctx)
            if project_dir is not None
            else f"/local/{account}"
        )
        partial_exc: Optional[GitRestoreWritebackPartialError] = None
        try:
            lease = await self._async_agfs.pathlock_acquire_tree(lock_path)
        except LockAcquisitionError:
            raise ResourceBusyError(
                f"Resource is being processed: {project_dir or '*'}",
                uri=project_dir or "*",
            )

        try:
            try:
                result = await self._async_agfs.run("git_restore", **kwargs)
            except GitRestoreWritebackPartialError as exc:
                # The ref already advanced — capture the exception and
                # finish the lock scope cleanly. Reindex scheduling and
                # re-raise happen below, outside the tree lock.
                partial_exc = exc
                result = None
        finally:
            await self._async_agfs.pathlock_release(lease)

        if partial_exc is not None:
            # HEAD has moved forward but some VFS writes/deletes failed.
            # Still schedule reindex for the paths that *did* reach the VFS so
            # the vector index doesn't stay stale, then re-raise so the caller
            # learns about the partial failure (and can inspect failed_writes
            # / failed_deletes / task_id on the exception).
            try:
                partial_exc.task_id = await self._schedule_restore_reindex_for_paths(
                    written_paths=partial_exc.written_paths,
                    deleted_paths=partial_exc.deleted_paths,
                    project_dir=project_dir,
                    real_ctx=real_ctx,
                )
            except Exception:
                logger.exception(
                    "[VikingFS] git restore partial: reindex scheduling failed; "
                    "HEAD advanced but reindex was not queued"
                )
            raise partial_exc

        if result.get("result") != "applied":
            return result

        written = list(result.get("written_paths") or [])
        deleted = list(result.get("deleted_paths") or [])
        if written or deleted:
            try:
                task_id = await self._schedule_restore_reindex_for_paths(
                    written_paths=written,
                    deleted_paths=deleted,
                    project_dir=project_dir,
                    real_ctx=real_ctx,
                )
                if task_id is not None:
                    result["task_id"] = task_id
            except Exception:
                logger.exception(
                    "[VikingFS] git restore reindex task creation failed; "
                    "falling back to fire-and-forget rebuild"
                )
                self._schedule_vector_rebuild(written=written, deleted=deleted, ctx=real_ctx)
        return result

    async def _schedule_restore_reindex_for_paths(
        self,
        *,
        written_paths: List[str],
        deleted_paths: List[str],
        project_dir: Optional[str],
        real_ctx: RequestContext,
    ) -> Optional[str]:
        """Classify ``written``/``deleted`` paths into vector tasks and queue
        them as a single tracked background rebuild. Returns the task id, or
        ``None`` if there is nothing to do.

        Shared by the applied path and the partial-writeback recovery path —
        both schedule reindex for paths that actually reached the VFS.
        """
        if not written_paths and not deleted_paths:
            return None
        tasks = self._collect_restore_vector_tasks(written_paths, deleted_paths)
        if not tasks:
            return None

        from openviking.service.task_tracker import get_task_tracker

        tracker = get_task_tracker()
        task = await tracker.create(
            "snapshot_restore_reindex",
            resource_id=project_dir or "*",
            account_id=real_ctx.account_id,
            user_id=real_ctx.user.user_id,
        )
        await tracker.update_stage(
            task.task_id,
            "queued",
            account_id=real_ctx.account_id,
            user_id=real_ctx.user.user_id,
        )
        background = asyncio.create_task(
            self._run_restore_rebuild_tracked(task.task_id, tasks, real_ctx),
            name=f"vikingfs-git-restore-reindex:{task.task_id}",
        )
        self._background_tasks.add(background)
        background.add_done_callback(self._background_tasks.discard)
        return task.task_id

    async def show(
        self,
        target_ref: str,
        *,
        path: Optional[str] = None,
        max_blob_bytes: Optional[int] = None,
        ctx: Optional[RequestContext] = None,
    ) -> Union[Dict[str, Any], bytes]:
        """Read a commit's metadata or a single blob.

        ``path=None`` returns the commit metadata dict (oid, tree, parents,
        author, committer, message). ``path=str`` returns the blob bytes
        directly, stripping the oid/size envelope returned by the binding.
        """
        real_ctx = self._ctx_or_default(ctx)
        account = real_ctx.account_id
        tree_path = self._uri_to_tree_path(path, ctx=real_ctx) if path else None
        kwargs: Dict[str, Any] = {
            "account": account,
            "target_ref": target_ref,
            "path": tree_path,
        }
        if max_blob_bytes is not None:
            kwargs["max_blob_bytes"] = max_blob_bytes
        resp = await self._async_agfs.run("git_show", **kwargs)
        if path is not None and isinstance(resp, dict) and "bytes" in resp:
            return resp["bytes"]
        return resp

    async def show_blob_raw(
        self,
        target_ref: str,
        *,
        path: str,
        ctx: Optional[RequestContext] = None,
    ) -> Dict[str, Any]:
        """Like ``show(target_ref, path=...)`` but returns the full envelope.

        Returns ``{"oid": str, "size": int, "bytes": bytes}`` without
        stripping. Used by the HTTP snapshot router to populate
        ``X-Snapshot-Oid`` / ``X-Snapshot-Size`` response headers.
        """
        real_ctx = self._ctx_or_default(ctx)
        account = real_ctx.account_id
        tree_path = self._uri_to_tree_path(path, ctx=real_ctx)
        resp = await self._async_agfs.run(
            "git_show",
            account=account,
            target_ref=target_ref,
            path=tree_path,
        )
        if not isinstance(resp, dict) or "bytes" not in resp:
            raise TypeError(
                f"git_show returned unexpected shape for blob path: {type(resp).__name__}"
            )
        return resp

    async def diff(
        self,
        *,
        path: str,
        from_ref: Optional[str],
        to_ref: str,
        raw: bool = True,
        ctx: Optional[RequestContext] = None,
    ) -> Dict[str, Any]:
        """Return a unified text diff for one path between two snapshots."""
        real_ctx = self._ctx_or_default(ctx)
        self._ensure_access(path, real_ctx)

        from_meta: Optional[Dict[str, Any]] = None
        if from_ref:
            try:
                from_meta = await self.show(from_ref, ctx=real_ctx)
            except AGFSNotFoundError as exc:
                raise NotFoundError(from_ref, "git_ref") from exc
        try:
            to_meta = await self.show(to_ref, ctx=real_ctx)
        except AGFSNotFoundError as exc:
            raise NotFoundError(to_ref, "git_ref") from exc

        from_oid = str(from_meta.get("oid", from_ref)) if isinstance(from_meta, dict) else ""
        to_oid = str(to_meta.get("oid", to_ref)) if isinstance(to_meta, dict) else to_ref

        async def read_optional(ref: str) -> Optional[bytes]:
            try:
                value = await self.show(
                    ref,
                    path=path,
                    max_blob_bytes=_pkg().SNAPSHOT_DIFF_MAX_FILE_BYTES,
                    ctx=real_ctx,
                )
            except AGFSPathNotFoundError:
                return None
            except AGFSResourceExhaustedError as exc:
                raise ResourceExhaustedError(
                    f"snapshot diff file size limit exceeded "
                    f"({_pkg().SNAPSHOT_DIFF_MAX_FILE_BYTES} bytes)",
                    details={"limit_bytes": _pkg().SNAPSHOT_DIFF_MAX_FILE_BYTES, "path": path},
                ) from exc
            if not isinstance(value, bytes):
                raise TypeError(f"git_show returned unexpected blob type: {type(value).__name__}")
            return value

        before_bytes = await read_optional(from_oid) if from_ref else None
        after_bytes = await read_optional(to_oid)
        if before_bytes is None and after_bytes is None:
            raise NotFoundError(path, "git_blob")

        for value in (before_bytes, after_bytes):
            if value is not None and len(value) > _pkg().SNAPSHOT_DIFF_MAX_FILE_BYTES:
                raise ResourceExhaustedError(
                    f"snapshot diff file size limit exceeded ({_pkg().SNAPSHOT_DIFF_MAX_FILE_BYTES} bytes)",
                    details={"limit_bytes": _pkg().SNAPSHOT_DIFF_MAX_FILE_BYTES, "path": path},
                )

        if not raw:
            from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils

            def visible_content(value: Optional[bytes]) -> Optional[bytes]:
                if value is None:
                    return None
                try:
                    text = value.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise InvalidArgumentError("raw=false requires UTF-8 snapshot content") from exc
                return MemoryFileUtils.read(text, uri=path).content.encode("utf-8")

            before_bytes = visible_content(before_bytes)
            after_bytes = visible_content(after_bytes)

        change_type, before, after = await asyncio.to_thread(
            _prepare_snapshot_diff,
            path=path,
            before_bytes=before_bytes,
            after_bytes=after_bytes,
            max_lines=_pkg().SNAPSHOT_DIFF_MAX_LINES,
        )
        try:
            diff_text = await self._async_agfs.run(
                "git_diff_text",
                before=before,
                after=after,
                fromfile=f"{path}@{from_oid}",
                tofile=f"{path}@{to_oid}",
                timeout_ms=_pkg().SNAPSHOT_DIFF_TIMEOUT_MS,
                max_output_bytes=_pkg().SNAPSHOT_DIFF_MAX_OUTPUT_BYTES,
            )
        except AGFSResourceExhaustedError as exc:
            raise ResourceExhaustedError(
                f"snapshot diff output size limit exceeded "
                f"({_pkg().SNAPSHOT_DIFF_MAX_OUTPUT_BYTES} bytes)",
                details={"limit_bytes": _pkg().SNAPSHOT_DIFF_MAX_OUTPUT_BYTES, "path": path},
            ) from exc
        return {
            "path": path,
            "from_commit": from_oid,
            "to_commit": to_oid,
            "change_type": change_type,
            "diff_text": diff_text,
        }

    async def log(
        self,
        *,
        branch: str = "main",
        limit: int = 20,
        paths: Optional[List[str]] = None,
        ctx: Optional[RequestContext] = None,
    ) -> List[Dict[str, Any]]:
        """Walk back from ``branch``'s HEAD along ``parents[0]`` up to ``limit`` commits.

        Returns a list of commit metadata dicts (same shape as ``show(target_ref)``).
        """
        if limit <= 0:
            return []
        real_ctx = self._ctx_or_default(ctx)
        account = real_ctx.account_id
        tree_paths = (
            None if not paths else [self._uri_to_tree_path(path, ctx=real_ctx) for path in paths]
        )
        return await self._async_agfs.run(
            "git_log",
            account=account,
            branch=branch,
            limit=limit,
            paths=tree_paths,
        )

    def _collect_restore_vector_tasks(
        self,
        written: List[str],
        deleted: List[str],
    ) -> set[tuple]:
        """Classify restore-affected paths into deduplicated ``(op, uri, level)`` tasks.

        Tasks are deduplicated on the exact ``(op, uri, level)`` key (no ancestor
        subsumption — each change is handled independently because no operation
        recurses into descendants).
        """
        tasks: set[tuple] = set()
        for tree_path in written:
            try:
                task = self._classify_restore_path(tree_path, deleted=False)
            except ValueError:
                continue
            if task is not None:
                tasks.add(task)
        for tree_path in deleted:
            try:
                task = self._classify_restore_path(tree_path, deleted=True)
            except ValueError:
                continue
            if task is not None:
                tasks.add(task)
        return tasks

    def _schedule_vector_rebuild(
        self,
        *,
        written: List[str],
        deleted: List[str],
        ctx: RequestContext,
    ) -> None:
        """Fire-and-forget precise vector maintenance for a git restore.

        Each affected path is classified by :py:meth:`_classify_restore_path`
        into a ``(op, uri, level)`` task and scheduled independently:

        - ``reindex_marker`` — recompute only a directory's L0/L1 vector.
        - ``reindex_file`` — recompute only a source file's DETAIL vector
          (non-recursive ``execute(mode="vectors_only")``).
        - ``delete`` — remove only the ``(uri, level)`` vector (directory
          marker removal, or deleted source file).

        Failures are logged and never propagate.
        """
        try:
            from openviking.service.reindex_executor import get_reindex_executor
        except Exception:
            logger.exception("[VikingFS] ReindexExecutor import failed; skipping rebuild")
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("[VikingFS] git restore vector rebuild skipped: no running event loop")
            return

        tasks = self._collect_restore_vector_tasks(written, deleted)
        if not tasks:
            return

        executor = get_reindex_executor()
        for op, uri, level in tasks:
            loop.create_task(
                self._run_vector_rebuild(executor, op, uri, level, ctx),
                name=f"vikingfs-git-{op}:{uri}:{int(level)}",
            )

    async def _run_restore_rebuild_tracked(
        self,
        task_id: str,
        tasks: set[tuple],
        ctx: RequestContext,
    ) -> None:
        """Background worker driving a tracked restore vector rebuild.

        Runs all classified ``(op, uri, level)`` rebuild tasks concurrently and
        drives the task through start → complete/fail. Per-task failures are
        swallowed (and logged) inside :py:meth:`_run_vector_rebuild`, preserving
        the "failures do not block" semantics.
        """
        from openviking.service.task_tracker import get_task_tracker
        from openviking.service.task_work_index import bind_task_context

        tracker = get_task_tracker()
        tracker.register_running_task(task_id)
        try:
            await tracker.start(
                task_id,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
                stage="reindexing",
            )
            from openviking.service.reindex_executor import get_reindex_executor

            executor = get_reindex_executor()
            with bind_task_context(task_id, ctx.account_id, ctx.user.user_id):
                await asyncio.gather(
                    *[
                        self._run_vector_rebuild(executor, op, uri, level, ctx)
                        for (op, uri, level) in tasks
                    ]
                )
            await tracker.complete(
                task_id,
                {"status": "completed", "task_count": len(tasks)},
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )
        except asyncio.CancelledError:
            # TaskWorkIndex finalizes after this active task and its queue work settle.
            return
        except Exception as exc:
            await tracker.fail(
                task_id,
                str(exc),
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            )
        finally:
            await tracker.unregister_running_task(task_id)

    async def _run_vector_rebuild(
        self,
        executor: Any,
        op: str,
        uri: str,
        level: Any,
        ctx: RequestContext,
    ) -> None:
        """Wrapper coroutine: dispatch one vector task and swallow errors."""
        try:
            if op == "reindex_marker":
                await executor.reindex_directory_marker(dir_uri=uri, level=level, ctx=ctx)
            elif op == "reindex_file":
                await executor.execute(uri=uri, mode="vectors_only", wait=True, ctx=ctx)
            elif op == "delete":
                await executor.delete_uri_level(uri=uri, level=level, ctx=ctx)
            else:  # pragma: no cover - defensive
                logger.warning("[VikingFS] unknown vector rebuild op %r for %s", op, uri)
        except Exception:
            logger.exception("[VikingFS] git restore vector task %s failed for %s", op, uri)
