"""Compile task lifecycle, collection pipelines and namespace-specific submissions."""

from __future__ import annotations

import asyncio
import shutil
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Mapping

from loguru import logger

from openviking.core.namespace import classify_uri, relative_uri_path, uri_parts
from openviking.core.skill_loader import validate_skill_format
from openviking.utils.path_safety import (
    sanitize_relative_viking_path,
)
from openviking_cli.exceptions import OpenVikingError
from openviking_cli.utils.config.vlm_config import VLMConfig
from vikingbot.agent.loop import AgentLoop
from vikingbot.agent.tools.base import ToolContext
from vikingbot.compile import file_ops, simple
from vikingbot.compile.hashing import content_hash
from vikingbot.compile.models import (
    COMPILE_STAGING_ROOT,
    DEFAULT_COMPILE_INSTRUCTION,
    TERMINAL_STATUSES,
    CompileAccepted,
    CompileErrorInfo,
    CompileFailure,
    CompileLimits,
    CompileRequest,
    CompileResult,
    CompileTask,
    SanitizedCompileRequest,
    has_directory_plan,
    utc_now,
)
from vikingbot.compile.pipeline import Pipeline
from vikingbot.compile.pipeline_agent import agent_runner as pipeline_agent_runner
from vikingbot.compile.renderer import RenderedBundle
from vikingbot.compile.sandbox import CompileSandboxManager
from vikingbot.compile.sources import (
    pack_source_batches,
    read_source,
    verify_recovery_sources,
)
from vikingbot.compile.store import CompileTaskStore
from vikingbot.config.schema import SandboxBackend, SandboxMode, SessionKey
from vikingbot.openviking_mount.ov_server import VikingClient
from vikingbot.providers.base import LLMProvider, LLMResponse, LLMStreamEvent
from vikingbot.sandbox import SandboxManager
from vikingbot.sandbox.base import SandboxBackend as WorkspaceSandbox

_COMPILE_ISOLATED_EXEC_BACKENDS = frozenset(
    {
        SandboxBackend.SRT,
        SandboxBackend.DOCKER,
        SandboxBackend.OPENSANDBOX,
        SandboxBackend.AIOSANDBOX,
    }
)
_SKILL_EXCLUDED_FILES = frozenset(
    {".abstract.md", ".overview.md", ".relations.json", ".source.json"}
)


def _consume_background_result(future: asyncio.Future[Any], *, label: str) -> None:
    try:
        future.result()
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.warning("Compile {} failed after its grace deadline: {}", label, exc)


async def _renew_compile_sandbox(
    sandbox: WorkspaceSandbox,
    interval: float,
    owner: asyncio.Task[Any],
    *,
    fail_task: Callable[[CompileFailure], Awaitable[None]],
) -> None:
    """Renew the current sandbox immediately and every interval seconds until cancelled.

    Each health/renewal request is bounded by interval. On failure, fail_task
    persists the outcome before owner cancellation, independently of worker
    cleanup. The owner is cancelled even if persistence raises an error.
    """
    while True:
        try:
            if not await asyncio.wait_for(sandbox.is_healthy(), timeout=interval):
                raise RuntimeError("Sandbox is not healthy")
        except Exception as exc:
            failure = CompileFailure(
                "UNAVAILABLE",
                f"Sandbox renewal failed: {str(exc) or type(exc).__name__}",
                stage="sandbox",
            )
            try:
                await fail_task(failure)
            finally:
                owner.cancel()
            raise failure from exc
        await asyncio.sleep(interval)


async def _await_with_hard_timeout(
    awaitable: Awaitable[Any],
    *,
    timeout: float,
    label: str,
) -> Any:
    """Give bounded fallback work its full grace period, but never wait past it."""
    future = asyncio.ensure_future(awaitable)
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        remaining = max(0.0, deadline - asyncio.get_running_loop().time())
        try:
            done, _pending = await asyncio.wait({future}, timeout=remaining)
        except asyncio.CancelledError:
            # The task runtime may expire while an iteration-limit salvage is
            # already underway. Preserve the independent grace period.
            continue
        except BaseException:
            future.cancel()
            future.add_done_callback(
                lambda completed: _consume_background_result(completed, label=label)
            )
            raise
        if future in done:
            return future.result()
        future.cancel()
        future.add_done_callback(
            lambda completed: _consume_background_result(completed, label=label)
        )
        raise asyncio.TimeoutError


@dataclass(frozen=True)
class CompileCapabilities:
    exec_enabled: bool


class _CompileProvider(LLMProvider):
    """Share model-call capacity across Compile tasks."""

    def __init__(self, provider: LLMProvider, slots: asyncio.Semaphore):
        super().__init__()
        self._provider = provider
        self._slots = slots

    async def chat(self, *args: Any, **kwargs: Any) -> LLMResponse:
        """Forward a request under the shared capacity limit."""
        async with self._slots:
            return await self._provider.chat(*args, **kwargs)

    async def chat_stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[LLMStreamEvent]:
        """Hold capacity until the stream closes."""
        async with self._slots:
            async with aclosing(self._provider.chat_stream(*args, **kwargs)) as stream:
                async for event in stream:
                    yield event

    def should_inline_tool_result_media(self, model: str | None = None) -> bool:
        """Send original evidence; model capability is decided by the endpoint."""
        return True

    def get_default_model(self) -> str:
        return self._provider.get_default_model()


class BotCompileService:
    def __init__(
        self,
        *,
        agent_loop: AgentLoop,
        limits: CompileLimits | None = None,
    ):
        self.agent_loop = agent_loop
        self.config = agent_loop.config
        self.store = CompileTaskStore(self.config.bot_data_path)
        root_vlm = getattr(self.config, "get_root_vlm_config", lambda: None)()
        model_concurrency = (root_vlm or VLMConfig()).max_concurrent
        if model_concurrency < 1:
            raise ValueError("Compile requires vlm.max_concurrent >= 1")
        self.limits = limits or CompileLimits(
            source_concurrency=self.config.compile.map_concurrency or model_concurrency,
            shuffle_concurrency=self.config.compile.shuffle_concurrency or model_concurrency,
            shuffle_batch_size=self.config.compile.shuffle_batch_size,
            merge_concurrency=self.config.compile.reduce_concurrency or model_concurrency,
        )
        self._semaphore = asyncio.Semaphore(self.limits.concurrent_tasks)
        self._model_slots = asyncio.Semaphore(model_concurrency)
        self._target_locks: dict[tuple[str, str], tuple[asyncio.Lock, int]] = {}
        self._target_locks_guard = asyncio.Lock()
        self._admission_guard = asyncio.Lock()
        self._admitted_tasks = 0
        self._admitted_by_principal: dict[str, int] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._start_lock = asyncio.Lock()
        self._started = False

    async def start(self) -> None:
        async with self._start_lock:
            if self._started:
                return
            await self.store.mark_interrupted_failed()
            await self._prune_terminal_tasks()
            self._started = True

    async def close(self) -> None:
        """Cancel active work and let task finally blocks release their sandboxes."""
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def create_task(
        self,
        request: CompileRequest,
        *,
        principal_scope: str,
        task_id: str | None = None,
    ) -> CompileAccepted:
        await self.start()
        if task_id is not None:
            try:
                existing = await self.store.get(task_id)
            except ValueError as exc:
                raise CompileFailure(
                    "INVALID_ARGUMENT",
                    "Idempotency-Key must be a valid Compile task ID.",
                    stage="queued",
                ) from exc
            if existing is not None:
                if existing.principal_scope != principal_scope:
                    raise CompileFailure(
                        "PERMISSION_DENIED",
                        "Compile session belongs to another principal.",
                        stage="queued",
                    )
                return CompileAccepted(
                    session_id=existing.task_id,
                    task_id=existing.task_id,
                    status="accepted",
                    to=existing.sanitized_request.to,
                )
        await self._admit(principal_scope)
        runner_started = False
        try:
            connection = (
                request.openviking_connection.model_dump(exclude_none=True)
                if request.openviking_connection is not None
                else None
            )
            if not connection and self._openviking_auth_mode() != "dev":
                raise CompileFailure(
                    "UNAVAILABLE",
                    "Compile requires an authenticated OpenViking connection.",
                    stage="queued",
                )
            connection = connection or {}
            account_id = connection.get("account_id")
            if self._openviking_auth_mode() != "dev" and not (account_id or "").strip():
                raise CompileFailure(
                    "UNAUTHENTICATED", "Compile requires account_id.", stage="queued"
                )
            normalized_request = await self._normalize_request(request, connection=connection)
            task_id = task_id or "cmp_" + uuid.uuid4().hex
            now = utc_now()
            task = CompileTask(
                task_id=task_id,
                principal_scope=principal_scope,
                sanitized_request=normalized_request,
                meta={"mode": self.config.compile.mode},
                status="accepted",
                stage="queued",
                created_at=now,
                updated_at=now,
            )
            try:
                await self.store.create(task)
            except FileExistsError:
                existing = await self.store.get(task_id)
                if existing is None or existing.principal_scope != principal_scope:
                    raise CompileFailure(
                        "PERMISSION_DENIED",
                        "Compile session belongs to another principal.",
                        stage="queued",
                    )
                return CompileAccepted(
                    session_id=existing.task_id,
                    task_id=existing.task_id,
                    status="accepted",
                    to=existing.sanitized_request.to,
                )
            runner = asyncio.create_task(
                self._run_admitted_task(
                    task_id,
                    normalized_request,
                    connection,
                    principal_scope,
                ),
                name=f"compile:{task_id}",
            )
            self._tasks.add(runner)
            runner.add_done_callback(self._tasks.discard)
            runner_started = True
            return CompileAccepted(
                session_id=task_id,
                task_id=task_id,
                to=normalized_request.to,
            )
        finally:
            if not runner_started:
                await self._release_admission(principal_scope)

    async def get_task(
        self, task_id: str, *, principal_scope: str, include_recovery_proof: bool = False
    ) -> dict[str, Any] | None:
        await self.start()
        try:
            task = await self.store.get(task_id)
        except ValueError:
            return None
        if task is None or task.principal_scope != principal_scope:
            return None
        return task.public_dict(include_recovery_proof=include_recovery_proof)

    async def cancel_task(self, task_id: str, *, principal_scope: str) -> dict[str, Any] | None:
        """Request cooperative cancellation of one principal-owned Compile task."""
        await self.start()
        try:
            task = await self.store.get(task_id)
        except ValueError:
            return None
        if task is None or task.principal_scope != principal_scope:
            return None
        if task.status in TERMINAL_STATUSES:
            return task.public_dict()

        def request_cancellation(current: CompileTask) -> None:
            if current.principal_scope != principal_scope or current.status in TERMINAL_STATUSES:
                return
            current.status = "cancelling"

        task = await self.store.update(task_id, request_cancellation)
        if task.principal_scope != principal_scope:
            return None
        if task.status in TERMINAL_STATUSES:
            return task.public_dict()

        runner = next(
            (
                candidate
                for candidate in self._tasks
                if candidate.get_name() == f"compile:{task_id}"
            ),
            None,
        )
        if runner is None or not runner.cancel():
            await self._finish_cancellation(task_id)
        latest = await self.store.get(task_id)
        return latest.public_dict() if latest is not None else None

    def _openviking_auth_mode(self) -> str:
        ov_server = getattr(self.config, "ov_server", None)
        return str(getattr(ov_server, "effective_auth_mode", "") or "").strip().lower()

    def _compile_capabilities(self) -> CompileCapabilities:
        sandbox = getattr(self.config, "sandbox", None)
        try:
            backend = SandboxBackend(getattr(sandbox, "backend", None))
        except (TypeError, ValueError):
            return CompileCapabilities(exec_enabled=False)
        if backend == SandboxBackend.DIRECT:
            direct = sandbox.backends.direct
            return CompileCapabilities(exec_enabled=direct.allow_exec and direct.allow_compile_exec)
        return CompileCapabilities(exec_enabled=backend in _COMPILE_ISOLATED_EXEC_BACKENDS)

    async def _admit(self, principal_scope: str) -> None:
        async with self._admission_guard:
            principal_tasks = self._admitted_by_principal.get(principal_scope, 0)
            if (
                self._admitted_tasks >= self.limits.accepted_tasks
                or principal_tasks >= self.limits.accepted_tasks_per_principal
            ):
                raise CompileFailure(
                    "RESOURCE_EXHAUSTED",
                    "Compile task admission limit exceeded.",
                    stage="queued",
                )
            self._admitted_tasks += 1
            self._admitted_by_principal[principal_scope] = principal_tasks + 1

    async def _release_admission(self, principal_scope: str) -> None:
        async with self._admission_guard:
            principal_tasks = self._admitted_by_principal.get(principal_scope, 0)
            if principal_tasks == 0:
                return
            if principal_tasks <= 1:
                self._admitted_by_principal.pop(principal_scope, None)
            else:
                self._admitted_by_principal[principal_scope] = principal_tasks - 1
            self._admitted_tasks -= 1

    async def _prune_terminal_tasks(self) -> None:
        await self.store.prune_terminal(
            retention_seconds=self.limits.terminal_task_retention_seconds,
            max_records=self.limits.terminal_task_records,
        )

    async def _run_admitted_task(
        self,
        task_id: str,
        request: SanitizedCompileRequest,
        connection: dict[str, Any],
        principal_scope: str,
    ) -> None:
        try:
            await self._run_task(task_id, request, connection)
        except asyncio.CancelledError:
            task = await self.store.get(task_id)
            if task is None or task.status != "cancelling":
                raise
        finally:
            try:
                await self._finish_cancellation(task_id)
            finally:
                await self._release_admission(principal_scope)
                await self._prune_terminal_tasks()

    async def _finish_cancellation(self, task_id: str) -> None:
        def finish(task: CompileTask) -> None:
            if task.status != "cancelling":
                return
            task.status = "cancelled"
            task.stage = "cancelled"
            task.result = None
            task.error = None

        try:
            await self.store.update(task_id, finish)
        except (FileNotFoundError, ValueError):
            return

    async def _normalize_request(
        self,
        request: CompileRequest,
        *,
        connection: Mapping[str, Any],
    ) -> SanitizedCompileRequest:
        args = request.args or {}
        if args.keys() - {"changes"}:
            raise CompileFailure(
                "INVALID_ARGUMENT",
                "VikingBot Compile does not implement provider-specific args.",
                stage="queued",
            )
        changes = args.get("changes")
        if changes is not None:
            valid = (
                has_directory_plan(changes)
                and "base" in changes
                and "head" in changes
                and all(
                    isinstance(changes[key], list)
                    and all(isinstance(uri, str) and uri.strip() for uri in changes[key])
                    for key in ("diff_dirs", "full_dirs")
                )
            )
            valid = valid and all(
                changes.get(key) is None
                or isinstance(changes[key], str)
                and bool(changes[key].strip())
                for key in ("base", "head")
            )
            execution = changes.get("execution") if isinstance(changes, dict) else None
            valid = (
                valid
                and isinstance(execution, dict)
                and execution.get("mode") in ("full", "incremental")
            )
            if not valid:
                raise CompileFailure(
                    "INVALID_ARGUMENT", "Invalid args.changes directory plan", stage="queued"
                )
            if changes["diff_dirs"] and (
                not changes.get("base") or not changes.get("head") or execution["mode"] == "full"
            ):
                raise CompileFailure(
                    "INVALID_ARGUMENT", "diff_dirs require incremental base/head", stage="queued"
                )
            if not changes.get("head") and execution["mode"] != "full":
                raise CompileFailure(
                    "INVALID_ARGUMENT",
                    "Unavailable snapshots require full execution",
                    stage="queued",
                )
        raw_sources = [str(value).strip() for value in request.from_]
        if not raw_sources or any(not value for value in raw_sources):
            raise CompileFailure(
                "INVALID_ARGUMENT", "from must contain files or directories", stage="queued"
            )
        client = await VikingClient.create(connection=connection, config=self.config)
        try:
            sources: list[str] = []
            for raw_uri in raw_sources:
                attrs = await client.attrs(raw_uri)
                canonical = str(attrs.get("uri") or "").rstrip("/")
                if canonical not in sources:
                    sources.append(canonical)
            if changes is not None:
                diff_dirs, full_dirs = changes["diff_dirs"], changes["full_dirs"]
                if set(diff_dirs) & set(full_dirs) or set(diff_dirs + full_dirs) != set(sources):
                    raise CompileFailure(
                        "INVALID_ARGUMENT",
                        "Directory plan must partition the canonical from list",
                        stage="queued",
                    )
            skill_uri = request.skill.strip().rstrip("/")
            if skill_uri.endswith("/SKILL.md"):
                skill_uri = skill_uri[: -len("/SKILL.md")]
            skill_attrs = await client.attrs(skill_uri)
            canonical_skill = str(skill_attrs.get("uri") or "").rstrip("/")
            skill_stat = await client.stat(canonical_skill)
            if not skill_stat.get("isDir"):
                raise CompileFailure(
                    "SKILL_INVALID",
                    "--skill must resolve to a Skill directory or SKILL.md",
                    stage="queued",
                )
            self._skill_name_and_target(canonical_skill)

            raw_target = request.to.strip().rstrip("/")
            try:
                target_stat = await client.stat(raw_target)
            except OpenVikingError as exc:
                if exc.code != "NOT_FOUND":
                    raise
                self._validate_target_directory(raw_target, {"isDir": True})
                await client.mkdir(raw_target, description="compile target folder")
                target_stat = await client.stat(raw_target)
            target = str(target_stat.get("uri") or "").rstrip("/")
            self._validate_target_directory(target, target_stat)
        except CompileFailure:
            raise
        except OpenVikingError as exc:
            raise CompileFailure(exc.code, str(exc), stage="queued") from exc
        except Exception as exc:
            raise CompileFailure("INVALID_ARGUMENT", str(exc), stage="queued") from exc
        finally:
            await client.close()

        instruction = (request.instruction or "").strip()
        return SanitizedCompileRequest(
            **{
                "from": sources,
                "to": target,
                "instruction": instruction or DEFAULT_COMPILE_INSTRUCTION,
                "skill": canonical_skill,
                "changes": changes,
            }
        )

    @staticmethod
    def _validate_target_directory(target: str, stat: Mapping[str, Any]) -> None:
        if not stat.get("isDir"):
            raise CompileFailure(
                "INVALID_ARGUMENT", "Compile target must be a directory", stage="queued"
            )
        if target.rsplit("/", 1)[-1] in _SKILL_EXCLUDED_FILES:
            raise CompileFailure(
                "INVALID_ARGUMENT",
                "Compile target must not be an OpenViking derived directory",
                stage="queued",
            )
        classification = classify_uri(target)
        parts = uri_parts(target)
        if classification.context_type == "skill":
            if not classification.is_skill_namespace or (
                classification.scope == "agent" and parts != ["agent", "skills"]
            ):
                raise CompileFailure(
                    "INVALID_ARGUMENT",
                    "Compile Skill target must be a supported skills namespace",
                    stage="queued",
                )
            return
        if classification.context_type not in {"resource", "memory"}:
            raise CompileFailure(
                "INVALID_ARGUMENT",
                "Compile target must be a resource, memory, or skills directory",
                stage="queued",
            )
        if classification.context_type == "memory":
            if (
                classification.content_index is None
                or len(parts) <= classification.content_index + 1
            ):
                raise CompileFailure(
                    "INVALID_ARGUMENT",
                    "Compile target must be inside a memory type directory",
                    stage="queued",
                )
        elif parts == ["resources"] or (
            classification.content_index is not None
            and len(parts) <= classification.content_index + 1
        ):
            raise CompileFailure(
                "INVALID_ARGUMENT",
                "Compile target must be inside a resource directory",
                stage="queued",
            )

    @staticmethod
    def _skill_name_and_target(skill_uri: str) -> tuple[str, str]:
        classification = classify_uri(skill_uri)
        if not classification.is_skill:
            raise CompileFailure(
                "SKILL_INVALID", "Skill URI is outside a skills namespace", stage="queued"
            )
        if not classification.is_skill_root:
            raise CompileFailure(
                "SKILL_INVALID", "Skill URI must identify one Skill root", stage="queued"
            )
        assert classification.content_index is not None
        parts = classification.parts
        return (
            parts[-1],
            "viking://" + "/".join(parts[: classification.content_index + 1]),
        )

    async def _retain_target_lock(self, target: tuple[str, str]) -> asyncio.Lock:
        """Retain a task lock keyed by effective account and canonical target URI."""
        async with self._target_locks_guard:
            lock, references = self._target_locks.get(target, (asyncio.Lock(), 0))
            self._target_locks[target] = (lock, references + 1)
            return lock

    async def _release_target_lock(self, target: tuple[str, str], lock: asyncio.Lock) -> None:
        """Remove an account-target lock after its last active or waiting task exits."""
        async with self._target_locks_guard:
            current, references = self._target_locks.get(target, (lock, 0))
            if current is not lock:
                return
            if references <= 1:
                self._target_locks.pop(target, None)
            else:
                self._target_locks[target] = (lock, references - 1)

    async def _acquire_execution_slot(self, target_lock: asyncio.Lock) -> None:
        await target_lock.acquire()
        try:
            await self._semaphore.acquire()
        except BaseException:
            target_lock.release()
            raise

    async def _run_task(
        self,
        task_id: str,
        request: SanitizedCompileRequest,
        connection: dict[str, Any],
    ) -> None:
        account_id = (
            "default" if self._openviking_auth_mode() == "dev" else connection["account_id"]
        )
        target = (account_id, request.to)
        task_lock = await self._retain_target_lock(target)
        acquired = False
        try:
            await self._acquire_execution_slot(task_lock)
            acquired = True

            try:
                await self._execute_task(task_id, request, connection)
            except CompileFailure as exc:
                await self._fail(task_id, exc)
            except Exception as exc:
                logger.exception("Compile task {} failed", task_id)
                task = await self.store.get(task_id)
                stage = task.stage if task else "agent"
                code = self._unexpected_error_code(exc, stage=stage)
                await self._fail(task_id, CompileFailure(code, str(exc), stage=stage))
        finally:
            if acquired:
                self._semaphore.release()
                task_lock.release()
            await self._release_target_lock(target, task_lock)

    async def _execute_task(
        self,
        task_id: str,
        request: SanitizedCompileRequest,
        connection: dict[str, Any],
    ) -> None:
        snapshot = (request.changes or {}).get("skill_snapshot")
        if snapshot is not None:
            if not isinstance(snapshot, dict) or snapshot.get("uri") != request.skill:
                raise CompileFailure(
                    "SKILL_INVALID", "Invalid Compile Skill snapshot", stage="agent"
                )
            # Task-local connection follows existing parent, child and tool client propagation.
            connection = {**connection, "skill_snapshot": snapshot}
        target_type = classify_uri(request.to).context_type
        session_key = SessionKey(type="compile", channel_id=task_id, chat_id=task_id)
        task_config = self.config.model_copy(
            update={
                "skills": [],
                "sandbox": self.config.sandbox.model_copy(deep=True),
            }
        )
        task_config.sandbox.mode = SandboxMode.PER_SESSION
        if target_type in {"resource", "skill"}:
            task_config.agents = task_config.agents.model_copy(
                update={"subagent_max_concurrency": self.limits.source_concurrency}
            )
        workspace_parent = self.config.bot_data_path / "compile_workspaces" / task_id
        if task_config.uses_managed_opensandbox:
            workspace_parent = task_config.opensandbox_workspaces_path / "compile" / task_id
        sandbox_manager = CompileSandboxManager(
            task_config, workspace_parent, task_config.workspace_path, connection=connection
        )
        workspace = sandbox_manager.get_workspace_path(session_key)
        client: VikingClient | None = None
        sandbox: WorkspaceSandbox | None = None
        compile_started_at = time.monotonic()
        agent_usage: dict[str, int] = {}
        preserve_workspace = False
        renewal_task = None
        try:
            await self._set_state(task_id, status="running", stage="collecting_context")
            client = await VikingClient.create(connection=connection, config=self.config)
            simple_mode = (await self.store.get(task_id)).meta["mode"] == "simple"
            if simple_mode and target_type == "skill":
                raise CompileFailure(
                    "INVALID_ARGUMENT",
                    "Publishing a Skill package requires bot.compile.mode=complex.",
                    stage="agent",
                )
            source_files = None
            execution = (request.changes or {}).get("execution") or {}
            full_execution = execution.get("mode", "full") == "full"
            if not simple_mode and full_execution and not (request.changes or {}).get("head"):
                source_files = await self._list_source_files(
                    client, request.from_, allow_empty=True
                )
            skill_text = await client.read_raw(f"{request.skill}/SKILL.md")
            if not skill_text.strip():
                raise ValueError("Compile Skill is empty")
            # An empty task workspace prevents bootstrap files from becoming outputs.
            workspace.mkdir(parents=True, exist_ok=True)
            sandbox = await sandbox_manager.get_sandbox(session_key)
            if task_config.sandbox.backend == SandboxBackend.OPENSANDBOX:
                interval = task_config.sandbox.backends.opensandbox.runtime.timeout / 3
                renewal_task = asyncio.create_task(
                    _renew_compile_sandbox(
                        sandbox,
                        interval,
                        asyncio.current_task(),
                        fail_task=lambda failure: self._fail(task_id, failure),
                    )
                )
            if simple_mode:
                await self._set_state(task_id, status="running", stage="agent")
                loop = AgentLoop(
                    bus=self.agent_loop.bus,
                    provider=_CompileProvider(self.agent_loop.provider, self._model_slots),
                    workspace=workspace,
                    model=self.agent_loop.model,
                    temperature=self.agent_loop.temperature,
                    max_iterations=self.limits.simple_agent_iterations,
                    sandbox_manager=sandbox_manager,
                    config=task_config,
                )

                async def save_progress(writer):
                    """Persist acknowledged and in-flight target URIs across cancellation or restart."""

                    def record(task):
                        task.meta["outputs"] = dict(writer.outcomes)
                        task.meta["unconfirmed"] = sorted(writer.unconfirmed)

                    await self.store.update(task_id, record)

                result = await simple.run(
                    loop,
                    client,
                    request,
                    request.from_,
                    skill_text,
                    session_key,
                    connection,
                    save_progress,
                    agent_usage,
                    exec_enabled=self._compile_capabilities().exec_enabled,
                )

                def finish(task):
                    if task.status == "running":
                        task.status = task.stage = "completed"
                        task.result = result

                await self.store.update(task_id, finish)
                return
            focused_loop = None

            async def run_focused_agent(*args, **kwargs):
                """Create the shared child-loop context only for required complex transforms."""
                nonlocal focused_loop
                if focused_loop is None:
                    focused_loop = AgentLoop(
                        bus=self.agent_loop.bus,
                        provider=_CompileProvider(self.agent_loop.provider, self._model_slots),
                        workspace=workspace,
                        model=self.agent_loop.model,
                        temperature=self.agent_loop.temperature,
                        sandbox_manager=sandbox_manager,
                        config=task_config,
                    )
                return await pipeline_agent_runner(
                    focused_loop, session_key, connection, self.limits
                )(*args, **kwargs)

            preserve_workspace = await self._run_pipeline(
                task_id=task_id,
                request=request,
                client=client,
                sandbox=sandbox,
                skill_text=skill_text,
                source_files=source_files,
                usage=agent_usage,
                agent_runner=run_focused_agent,
                tool_context=ToolContext(session_key=session_key, openviking_connection=connection),
            )
        except BaseException as exc:
            # Source and merge evidence must survive failed or cancelled pipeline execution.
            preserve_workspace = preserve_workspace or target_type in {"resource", "skill"}
            if (
                isinstance(exc, asyncio.CancelledError)
                and renewal_task is not None
                and renewal_task.done()
            ):
                renewal_task.result()
            raise
        finally:
            if renewal_task is not None:
                renewal_task.cancel()
                await asyncio.gather(renewal_task, return_exceptions=True)
            await self._record_usage(task_id, agent_usage)
            self._log_compile_usage(
                task_id,
                elapsed_seconds=time.monotonic() - compile_started_at,
                usage=agent_usage,
            )
            await self._cleanup_execution_resources(
                sandbox_manager=sandbox_manager,
                session_key=session_key,
                client=client,
                workspace_parent=workspace_parent,
                preserve_workspace=preserve_workspace,
            )

    async def _run_pipeline(
        self,
        *,
        task_id,
        request,
        client,
        sandbox,
        skill_text,
        source_files,
        usage,
        agent_runner=None,
        tool_context=None,
    ) -> bool:
        """Run the shared collection pipeline and publish through the target namespace API.

        The API/task identity, admission, cancellation and target locks are owned by
        the surrounding lifecycle. Acknowledged partial output completes with diagnostic errors.
        Return whether the workspace must remain available for audit and replay.
        The surrounding lifecycle also preserves shards on failure/cancellation.
        """
        pipeline = Pipeline(
            client=client,
            sandbox=sandbox,
            provider=_CompileProvider(self.agent_loop.provider, self._model_slots),
            model=self.agent_loop.model,
            temperature=self.agent_loop.temperature,
            limits=self.limits,
            request=request,
            skill=skill_text,
            usage=usage,
        )
        pipeline.model.agent_runner = agent_runner
        pipeline.tools = {
            name: tool
            for name in ("openviking_search", "openviking_multi_read")
            if (tool := self.agent_loop.tools.get(name)) is not None
        }
        pipeline.tool_context = tool_context

        def record_metrics(task):
            """Expose token totals and existing error summaries, without per-call counters."""
            task.meta["pipeline"] = {
                "tokens": {
                    "input": usage.get("prompt_tokens", 0),
                    "output": usage.get("completion_tokens", 0),
                    "cached": usage.get("cache_read_input_tokens", 0),
                },
                "errors": list(
                    dict.fromkeys(e[:300] for e in pipeline.failures + pipeline.warnings)
                ),
            }

        await self._set_state(task_id, status="running", stage="pipeline")
        ordered = (
            source_files
            if source_files is not None
            else await self._list_pipeline_source_files(client, request)
        )
        await pipeline.files.put("inputs", dict.fromkeys(ordered, "pending"))

        source_limits = self.limits.model_copy(
            update={"source_batch_chars": min(self.limits.source_batch_chars, 10_000)}
        )
        recovery = (request.changes or {}).get("recovery_sources")
        source_hashes: dict[str, str] = {}

        async def source_batches():
            """Retain at most one read batch of source bodies while seeding task shards."""
            for start in range(0, len(ordered), 8):
                uris = ordered[start : start + 8]
                contents = await asyncio.gather(
                    *(read_source(client, uri) for uri in uris),
                    return_exceptions=True,
                )
                readable = []
                for uri, content in zip(uris, contents, strict=True):
                    if isinstance(content, Exception):
                        manifest = await pipeline.files.get("inputs")
                        manifest[uri] = "failed"
                        await pipeline.files.put("inputs", manifest)
                        pipeline.failures.append(f"Source read failed: {uri}: {str(content)[:400]}")
                    elif isinstance(content, BaseException):
                        raise content
                    else:
                        if recovery and isinstance(content, str):
                            source_hashes[uri] = content_hash(content)
                        readable.append((uri, content))
                for batch in pack_source_batches(readable, source_limits):
                    yield batch

        try:
            rendered = await pipeline.run(source_batches())
            # Cancellation must stop recovery before any write, including a queued cancel.
            current = await self.store.get(task_id)
            if current.status in TERMINAL_STATUSES or current.status == "cancelling":
                raise asyncio.CancelledError()
            await self._set_state(task_id, status="committing", stage="writing")
            skill_target = classify_uri(request.to).context_type == "skill"
            result = {"created": [], "updated": [], "unchanged": []}
            if skill_target and not pipeline.noop:
                result = await self._write_skill_bundle(
                    client=client, target_uri=request.to, rendered=rendered
                )
            elif rendered.operations:
                result = await client.batch_write(
                    root_uri=request.to,
                    operations=rendered.operations,
                    wait=False,
                    skip_conflicts=True,
                )
            conflicts = result.get("conflicts", [])
            for conflict in conflicts:
                pipeline.warnings.append(
                    f"Target left unchanged due to conflict: {conflict['uri']}: {conflict['message']}"
                )
            published = (
                [*rendered.created, *rendered.updated, *rendered.unchanged]
                if skill_target
                else [
                    *result.get("created", []),
                    *result.get("updated", []),
                    *result.get("unchanged", []),
                    *rendered.unchanged,
                ]
            )
            await pipeline.write_coverage(published)
            counts = (await pipeline.files.get("coverage") or {}).get("counts", {})
            partial = bool(pipeline.failures or conflicts) or any(
                counts.get(state, 0) for state in ("failed", "pending", "unreferenced")
            )
            recovery_proof = None
            if (
                recovery
                and not partial
                and not pipeline.warnings
                and not rendered.operations
                and counts.get("prepared") == len(ordered)
                and pipeline.evidence.keys() <= pipeline.recovery_evidence
            ):
                recovery_proof = await verify_recovery_sources(
                    client, request.changes, source_hashes
                )
            summary = await pipeline.files.get("summary")
            summary["committed"] = True
            summary["warnings"] = pipeline.warnings
            summary["conflicts"] = conflicts
            await pipeline.files.put("summary", summary)
            compiled = CompileResult(
                from_=request.from_,
                to=request.to,
                skill=request.skill,
                created=result.get("created", rendered.created),
                updated=result.get("updated", rendered.updated),
                conflicts=conflicts,
                unchanged=list(
                    dict.fromkeys(
                        [
                            *([] if skill_target else rendered.unchanged),
                            *result.get("unchanged", []),
                        ]
                    )
                ),
                page_count=len(set(published)),
                link_count=rendered.link_count,
                warnings=pipeline.warnings,
                completion_status="partial" if partial else None,
                recovery_proof=recovery_proof,
            )

            def complete(task):
                if task.status not in TERMINAL_STATUSES and task.status != "cancelling":
                    record_metrics(task)
                    task.status = "completed" if published or pipeline.noop else "failed"
                    task.stage = "completed" if published or pipeline.noop else "writing"
                    task.result = compiled
                    task.error = (
                        CompileErrorInfo(
                            code="COMPILE_INCOMPLETE", message="No output was acknowledged."
                        )
                        if not published and not pipeline.noop
                        else None
                    )

            await self.store.update(task_id, complete)
            # Retain accepted artifacts and call evidence for audit and same-workspace replay.
            return True
        except OpenVikingError as exc:
            code = "WRITE_CONFLICT" if exc.code in {"CONFLICT", "ALREADY_EXISTS"} else exc.code
            stage = (
                "refreshing" if exc.code in {"REFRESH_FAILED", "DEADLINE_EXCEEDED"} else "writing"
            )
            raise CompileFailure(code, str(exc), stage=stage) from exc
        finally:
            await self.store.update(task_id, record_metrics)

    @staticmethod
    def _log_compile_usage(
        task_id: str,
        *,
        elapsed_seconds: float,
        usage: Mapping[str, Any],
    ) -> None:
        input_tokens = int(usage.get("prompt_tokens", 0) or 0)
        cached_input_tokens = int(usage.get("cache_read_input_tokens", 0) or 0)
        output_tokens = int(usage.get("completion_tokens", 0) or 0)
        logger.info(
            "Compile {} finished in {:.1f}s — input_tokens={} "
            "cached_input_tokens={} output_tokens={}",
            task_id,
            elapsed_seconds,
            input_tokens,
            cached_input_tokens,
            output_tokens,
        )

    async def _cleanup_execution_resources(
        self,
        *,
        sandbox_manager: SandboxManager,
        session_key: SessionKey,
        client: VikingClient | None,
        workspace_parent: Path,
        preserve_workspace: bool = False,
    ) -> None:
        async def cleanup() -> None:
            try:
                if isinstance(sandbox_manager, CompileSandboxManager):
                    await sandbox_manager.clear_credentials()
                if preserve_workspace:
                    sandbox = await sandbox_manager.get_sandbox(session_key)
                    workspace = sandbox_manager.get_workspace_path(session_key)
                    for entry in await sandbox.list_files(max_entries=None):
                        if entry.path.startswith(f"{COMPILE_STAGING_ROOT}/"):
                            # Remote sandboxes can discard files on stop; retain drafts locally.
                            path = workspace / sanitize_relative_viking_path(entry.path)
                            if sandbox.local_file_path(entry.path) == path:
                                continue
                            payload = await sandbox.read_file_bytes(entry.path)
                            path.parent.mkdir(parents=True, exist_ok=True)
                            path.write_bytes(payload)
                # Destructive sandbox cleanup requires a complete recovery copy.
                await sandbox_manager.cleanup_session(session_key)
            finally:
                try:
                    if client is not None:
                        await client.close()
                finally:
                    if not preserve_workspace:
                        await asyncio.to_thread(
                            shutil.rmtree,
                            workspace_parent,
                            ignore_errors=True,
                        )

        try:
            await _await_with_hard_timeout(
                cleanup(),
                timeout=self.limits.cleanup_grace_seconds,
                label="cleanup",
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Compile cleanup exceeded its {}-second grace limit",
                self.limits.cleanup_grace_seconds,
            )
        except Exception:
            logger.exception(
                "Compile cleanup failed; incomplete recovery copies retain their sandbox"
            )

    async def _materialize_skill_package(
        self,
        *,
        client: VikingClient,
        skill_result: Mapping[str, Any],
        skill_dir: Path,
        stage: str = "loading_skill",
    ) -> None:
        """Copy a Skill package and reject attachment paths outside skill_dir."""
        skill_dir.mkdir(parents=True, exist_ok=True)
        content = str(skill_result.get("content") or "")
        (skill_dir / "SKILL.md").write_bytes(content.encode("utf-8"))

        files = skill_result.get("files") or []
        for item in files:
            if not isinstance(item, Mapping) or item.get("is_dir"):
                continue
            relative = str(item.get("path") or "")
            if relative == "SKILL.md" or Path(relative).name in _SKILL_EXCLUDED_FILES:
                continue
            try:
                relative = sanitize_relative_viking_path(relative)
                local = (skill_dir / relative).resolve()
                if skill_dir.resolve() not in local.parents:
                    raise ValueError("path escapes Skill root")
            except ValueError as exc:
                raise CompileFailure("SKILL_INVALID", str(exc), stage=stage) from exc
            data = await client.download_bytes(str(item.get("uri") or ""))
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(data)

    async def _write_skill_bundle(
        self,
        *,
        client: VikingClient,
        target_uri: str,
        rendered: RenderedBundle,
    ) -> dict[str, list[str]]:
        """Publish one validated package, retaining untouched existing attachments.

        The returned URIs identify Skill roots, as required by Compile's Skill result.
        """
        result = {"created": [], "updated": [], "unchanged": []}
        uris = [*(op["uri"] for op in rendered.operations), *rendered.unchanged]
        if not uris:
            return result
        paths = [relative_uri_path(target_uri, uri) for uri in uris]
        if any(not path or "/" not in path for path in paths):
            raise CompileFailure(
                "AGENT_OUTPUT_INVALID", "Skill files require <skill-name>/ paths", stage="rendering"
            )
        names = {sanitize_relative_viking_path(path).split("/")[0] for path in paths}
        if len(names) != 1:
            raise CompileFailure(
                "AGENT_OUTPUT_INVALID", "Compile requires one Skill package", stage="rendering"
            )
        skill_name = names.pop()
        root_uri = f"{target_uri.rstrip('/')}/{skill_name}"
        if not rendered.operations:
            result["unchanged"].append(root_uri)
            return result
        with TemporaryDirectory(prefix="openviking-compile-skill-") as temp_dir:
            temp_root = Path(temp_dir).resolve()
            skill_dir = temp_root / skill_name
            try:
                stat = await client.stat(root_uri)
                if not stat.get("isDir"):
                    raise CompileFailure(
                        "WRITE_CONFLICT",
                        f"Skill target is not a directory: {root_uri}",
                        stage="writing",
                    )
                exists = True
            except OpenVikingError as exc:
                if exc.code != "NOT_FOUND":
                    raise
                exists = False
            if exists:
                await self._materialize_skill_package(
                    client=client,
                    skill_result=await client.get_skill(skill_name, target_uri=target_uri),
                    skill_dir=skill_dir,
                    stage="writing",
                )
            for operation in rendered.operations:
                relative = sanitize_relative_viking_path(
                    relative_uri_path(target_uri, operation["uri"])
                )
                local = temp_root / relative
                if operation["mode"] == "create" and local.exists():
                    raise CompileFailure(
                        "WRITE_CONFLICT", f"Skill file changed: {operation['uri']}", stage="writing"
                    )
                local.parent.mkdir(parents=True, exist_ok=True)
                local.write_bytes(file_ops.file_bytes(operation))
            skill_md = skill_dir / "SKILL.md"
            validation = validate_skill_format(
                skill_md.read_text(encoding="utf-8") if skill_md.is_file() else "",
                strict=True,
                skill_dir_name=skill_name,
                source_path=str(skill_md),
            )
            if not validation["valid"]:
                raise CompileFailure(
                    "AGENT_OUTPUT_INVALID",
                    "; ".join(issue["message"] for issue in validation["errors"]),
                    stage="rendering",
                )
            if exists:
                published = await client.update_skill(
                    skill_name,
                    str(skill_dir),
                    target_uri=target_uri,
                    wait=True,
                )
            else:
                published = await client.add_skill(str(skill_dir), target_uri=target_uri, wait=True)
            result["updated" if exists else "created"].append(
                str(published.get("root_uri") or published.get("uri") or root_uri)
            )
            return result

    async def _list_pipeline_source_files(
        self, client: VikingClient, request: SanitizedCompileRequest
    ) -> list[str]:
        """Select unique full-scan and added/modified inputs; listing errors propagate."""
        changes = request.changes
        if not (has_directory_plan(changes) and changes.get("head")):
            return await self._list_source_files(client, request.from_)
        files = set(await self._list_source_files(client, changes["full_dirs"], allow_empty=True))
        roots = changes["diff_dirs"]
        if roots:
            offset = 0
            while True:
                page = await client.diff_paths(
                    roots, base=changes["base"], head=changes["head"], offset=offset
                )
                for change in page["changes"]:
                    uri = str(change.get("uri") or "").rstrip("/")
                    if not uri or not any(
                        uri == root or relative_uri_path(root, uri) for root in roots
                    ):
                        raise ValueError(f"Source inventory returned an out-of-scope URI: {uri}")
                    if change["change_type"] in {"added", "modified"}:
                        files.add(uri)
                if not page["has_more"]:
                    break
                next_offset = page["next_offset"]
                if not isinstance(next_offset, int) or next_offset <= offset:
                    raise ValueError("Source inventory pagination did not advance")
                offset = next_offset
        return sorted(files)

    async def _list_source_files(
        self,
        client: VikingClient,
        roots: list[str],
        *,
        allow_empty: bool = False,
    ) -> list[str]:
        """List unique sources within the requested roots; full reprocessing permits empty roots."""

        async def inventory(root: str) -> list[dict[str, Any]]:
            entry = await client.stat(root)
            if not entry.get("isDir", entry.get("is_dir", False)):
                return [{**entry, "uri": root}]
            entries = []
            pending, seen = [root], {root}
            while pending:
                directory = pending.pop()
                offset = 0
                while True:
                    page = await client.list_resources(directory, node_limit=500, offset=offset)
                    for item in page:
                        uri = str(item.get("uri") or "").rstrip("/")
                        if not uri or (uri != root and not relative_uri_path(root, uri)):
                            raise ValueError(
                                f"Source inventory returned an out-of-scope URI: {uri}"
                            )
                        entries.append(item)
                        if item.get("isDir", item.get("is_dir", False)) and uri not in seen:
                            pending.append(uri)
                            seen.add(uri)
                    offset += len(page)
                    if len(page) < 500:
                        break
            return entries

        files: set[str] = set()
        for root in dict.fromkeys(roots):
            entries = await inventory(root)
            for entry in entries:
                uri = str(entry.get("uri") or "").rstrip("/")
                if not uri or (uri != root and not relative_uri_path(root, uri)):
                    raise ValueError(f"Source inventory returned an out-of-scope URI: {uri}")
                if entry.get("isDir", entry.get("is_dir", False)):
                    continue
                files.add(uri)
        if not files and not allow_empty:
            raise ValueError("Compile sources contain no files")
        return sorted(files)

    async def _set_state(self, task_id: str, *, status: str, stage: str) -> None:
        def mutate(task: CompileTask) -> None:
            if task.status in TERMINAL_STATUSES or task.status == "cancelling":
                return
            task.status = status  # type: ignore[assignment]
            task.stage = stage

        await self.store.update(task_id, mutate)

    async def _record_usage(self, task_id: str, usage: Mapping[str, Any]) -> None:
        input_tokens = int(usage.get("prompt_tokens", 0) or 0)
        output_tokens = int(usage.get("completion_tokens", 0) or 0)
        if input_tokens == 0 and output_tokens == 0:
            return

        def mutate(task: CompileTask) -> None:
            if "pipeline" in task.meta:
                return
            task.meta["token_usage"] = {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
            }

        try:
            await self.store.update(task_id, mutate)
        except (FileNotFoundError, ValueError):
            return

    async def _fail(self, task_id: str, failure: CompileFailure) -> None:
        def mutate(task: CompileTask) -> None:
            if task.status in TERMINAL_STATUSES or task.status == "cancelling":
                return
            task.status = "failed"
            task.stage = failure.stage
            task.result = None
            task.error = CompileErrorInfo(code=failure.code, message=str(failure))

        await self.store.update(task_id, mutate)

    @staticmethod
    def _unexpected_error_code(exc: Exception, *, stage: str) -> str:
        if isinstance(exc, OpenVikingError):
            if exc.code == "CONFLICT" and stage in {"writing", "refreshing"}:
                return "WRITE_CONFLICT"
            if stage in {"writing", "refreshing"}:
                return "WRITE_FAILED"
            return exc.code
        if stage in {"writing", "refreshing"}:
            return "WRITE_FAILED"
        if stage == "agent":
            return "MODEL_UNAVAILABLE"
        return "INTERNAL"


__all__ = ["BotCompileService"]
