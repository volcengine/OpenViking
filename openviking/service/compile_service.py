# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Compile API models and external task provider."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any, Mapping

import httpx
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError, model_validator

from openviking.core.namespace import classify_uri, uri_parts
from openviking.core.path_variables import resolve_path_variables
from openviking.core.uri_validation import validate_request_viking_uri
from openviking.server.identity import RequestContext
from openviking.service.compile_targets import CompileLifecycle, UpdateSchedule, split_control_args
from openviking.service.external_task_service import (
    ExternalTaskError,
    ExternalTaskService,
    ExternalTaskSnapshot,
)
from openviking.service.fs_service import FSService
from openviking.service.memory_compile import MemoryCompileRunner
from openviking.service.task_tracker import SENSITIVE_TASK_KEYS, TaskRecord
from openviking.session.memory.utils.memory_fields import strip_memory_fields_trailer
from openviking.storage.acl import AclAction
from openviking_cli.exceptions import (
    ConflictError,
    InvalidArgumentError,
    NotFoundError,
    PermissionDeniedError,
    UnauthenticatedError,
    UnavailableError,
)
from openviking_cli.utils.config.open_viking_config import CompileApiConfig
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

_ACTIVE_STATUSES = frozenset({"accepted", "pending", "running", "committing"})
_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})

# Sentinel --skill value that routes a Compile request to in-process memory
# consolidation instead of the VikingBot agent path. It is not a real Skill URI.
MEMORY_COMPILE_SKILL = "memory"


class CompileConfigUpdate(BaseModel):
    """Whole update settings for one Compile target (POST /compile/config/update)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    to: str = Field(min_length=1)
    from_: list[str] = Field(alias="from", min_length=1)
    skill: str = Field(min_length=1)
    instruction: str | None = None
    auto_update: bool
    update_schedule: UpdateSchedule | None = None

    @property
    def instruction_sent(self) -> bool:
        return "instruction" in self.model_fields_set


class CompileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: list[str] = Field(default_factory=list, alias="from")
    to: str = Field(min_length=1)
    skill: str = Field(min_length=1)
    instruction: str | None = None
    args: dict[str, Any] | None = None
    # Explicit instruction/reason updates the saved instruction only when the
    # request also configures scheduling; ordinary manual instructions are per-run.
    _instruction_sent: bool = PrivateAttr(default=False)

    @property
    def is_memory_mode(self) -> bool:
        return self.skill == MEMORY_COMPILE_SKILL

    @model_validator(mode="before")
    @classmethod
    def _accept_reason(cls, data: Any) -> Any:
        if isinstance(data, dict) and "reason" in data:
            data = dict(data)
            data.setdefault("instruction", data.pop("reason"))
        return data

    @model_validator(mode="wrap")
    @classmethod
    def _track_instruction(cls, data: Any, handler: Any) -> "CompileRequest":
        model = handler(data)
        if isinstance(data, dict):
            model._instruction_sent = "instruction" in data or "reason" in data
        return model

    @model_validator(mode="after")
    def _normalize(self) -> "CompileRequest":
        sources: list[str] = []
        for source in self.from_:
            normalized = source.strip().rstrip("/")
            if not normalized:
                raise ValueError("from must not contain empty values")
            if normalized not in sources:
                sources.append(normalized)
        self.from_ = sources
        self.to = self.to.strip().rstrip("/")
        self.skill = self.skill.strip().rstrip("/")
        self.instruction = (
            self.instruction.strip() if self.instruction and self.instruction.strip() else None
        )
        self.args = dict(self.args) if self.args else None
        if not self.to:
            raise ValueError("to must not be empty")
        if not self.skill:
            raise ValueError("skill must not be empty")
        # Memory mode consolidates the --to space in place and takes no sources;
        # every other skill still requires at least one --from directory.
        if not self.is_memory_mode and not self.from_:
            raise ValueError("from must contain at least one directory")
        if self.is_memory_mode and self.from_:
            raise ValueError("--skill memory consolidates --to in place and takes no --from")
        return self


class CompileErrorInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: str
    message: str


class CompileSessionAccepted(BaseModel):
    model_config = ConfigDict(extra="ignore")

    session_id: str


class CompileSessionStatus(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str | None = None
    stage: str
    error: CompileErrorInfo | str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None


@dataclass(frozen=True)
class _CompileEndpoint:
    base_url: str
    gateway_token: str
    http_timeout_seconds: float
    poll_interval_ms: int
    local: bool = False


class CompileAPIClient:
    """Client for submitting Compile tasks through the Runtime API."""

    def __init__(self, endpoint: _CompileEndpoint) -> None:
        self._endpoint = endpoint

    def _headers(
        self,
        connection: Mapping[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, str]:
        """Build Runtime headers from saved identity, optional request ID, and retry key."""
        headers = {
            "Content-Type": "application/json",
        }
        if self._endpoint.gateway_token:
            headers["X-Gateway-Token"] = self._endpoint.gateway_token
        for field, header in (
            ("api_key", "X-API-Key"),
            ("account_id", "X-OpenViking-Account"),
            ("user_id", "X-OpenViking-User"),
            ("actor_peer_id", "X-OpenViking-Actor-Peer"),
        ):
            value = str(connection.get(field) or "").strip()
            if value:
                headers[header] = value
        request_id = connection.get("request_id")
        if request_id:
            headers["X-Tt-Logid"] = request_id
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    async def create(
        self,
        payload: Mapping[str, Any],
        *,
        connection: Mapping[str, Any],
        idempotency_key: str,
    ) -> CompileSessionAccepted:
        response = await self._request(
            "POST",
            "/runtime/v1/tasks",
            json=payload,
            headers=self._headers(connection, idempotency_key=idempotency_key),
        )
        return self._validate(CompileSessionAccepted, response)

    async def get(
        self,
        session_id: str,
        *,
        connection: Mapping[str, Any],
        args: Mapping[str, Any] | None = None,
    ) -> CompileSessionStatus:
        """Query a session with its original args; omit args for legacy tasks."""
        body = await self._request(
            "POST",
            "/runtime/v1/tasks/status",
            json={"session_id": session_id, **({"args": dict(args)} if args is not None else {})},
            headers=self._headers(connection),
        )
        return self._validate(CompileSessionStatus, body)

    async def cancel(
        self,
        session_id: str,
        *,
        connection: Mapping[str, Any],
        args: Mapping[str, Any] | None = None,
    ) -> CompileSessionStatus:
        """Cancel a session with its original args; omit args for legacy tasks."""
        body = await self._request(
            "POST",
            "/runtime/v1/tasks/cancel",
            json={"session_id": session_id, **({"args": dict(args)} if args is not None else {})},
            headers=self._headers(connection),
        )
        return self._validate(CompileSessionStatus, body)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str],
        json: Mapping[str, Any],
    ) -> Any:
        try:
            async with httpx.AsyncClient(
                timeout=self._endpoint.http_timeout_seconds,
                trust_env=not self._endpoint.local,
                follow_redirects=False,
            ) as client:
                response = await client.request(
                    method,
                    f"{self._endpoint.base_url}{path}",
                    headers=dict(headers),
                    json=dict(json),
                )
        except httpx.RequestError as exc:
            reason = str(exc).strip() or type(exc).__name__
            message = f"Failed to reach the compile kernel service: {reason}"
            raise ExternalTaskError("UNAVAILABLE", message, transient=True) from exc

        try:
            body = response.json()
        except ValueError as exc:
            if not response.is_success:
                raise ExternalTaskError(
                    "UNAVAILABLE" if response.status_code >= 500 else "INVALID_ARGUMENT",
                    "Compile API request failed",
                    transient=response.status_code in {408, 425, 429}
                    or response.status_code >= 500,
                ) from exc
            raise ExternalTaskError(
                "INVALID_RESPONSE",
                "Compile API returned a non-JSON response",
                transient=False,
            ) from exc
        if response.is_success:
            return body

        detail = body.get("detail") if isinstance(body, dict) else None
        error = body.get("error") if isinstance(body, dict) else None
        source = detail if isinstance(detail, dict) else error if isinstance(error, dict) else {}
        default_code = "UNAVAILABLE" if response.status_code >= 500 else "INVALID_ARGUMENT"
        code = str(source.get("code") or default_code)
        message = str(source.get("message") or detail or error or "Compile API request failed")
        raise ExternalTaskError(
            code,
            message,
            transient=response.status_code in {408, 425, 429} or response.status_code >= 500,
        )

    @staticmethod
    def _validate(model: type[BaseModel], body: Any) -> Any:
        try:
            return model.model_validate(body)
        except ValidationError as exc:
            raise ExternalTaskError(
                "INVALID_RESPONSE",
                f"Compile API returned an invalid response: {exc}",
                transient=False,
            ) from exc


class CompileService:
    """Validate Compile requests and own their external task lifecycle."""

    task_type = "compile"
    task_id_prefix = "cmp_"
    poll_max_attempts = 3

    def __init__(
        self,
        config: CompileApiConfig,
        tasks: ExternalTaskService,
        fs: FSService,
    ) -> None:
        self._config = config
        self._tasks = tasks
        self._fs = fs
        self._local_endpoint: _CompileEndpoint | None = None
        self._memory_runner: MemoryCompileRunner | None = None
        self._lifecycle: CompileLifecycle | None = None
        # Keep submission history tasks alive until their storage operations finish.
        self._history_tasks: set[asyncio.Task[None]] = set()

    def configure_lifecycle(self, lifecycle: CompileLifecycle) -> None:
        """Enable target registry, incremental planning and scheduled updates."""
        self._lifecycle = lifecycle

    @property
    def lifecycle(self) -> CompileLifecycle | None:
        return self._lifecycle

    def configure_memory_runner(self, vikingdb: Any = None) -> None:
        """Install the in-process memory-consolidation runner for `--skill memory`."""
        self._memory_runner = MemoryCompileRunner(self._fs, vikingdb=vikingdb)

    @property
    def poll_interval_seconds(self) -> float:
        return self._endpoint().poll_interval_ms / 1000.0

    def serialization_key(self, payload: Mapping[str, Any]) -> str:
        return payload["to"]

    def configure_local_backend(self, base_url: str, gateway_token: str) -> None:
        if self._config.base_url:
            return
        self._local_endpoint = _CompileEndpoint(
            base_url=base_url.rstrip("/"),
            gateway_token=gateway_token,
            http_timeout_seconds=10.0,
            poll_interval_ms=30000,
            local=True,
        )

    def _endpoint(self) -> _CompileEndpoint:
        if self._config.base_url:
            return _CompileEndpoint(
                base_url=self._config.base_url,
                gateway_token=self._config.gateway_token,
                http_timeout_seconds=self._config.http_timeout_seconds,
                poll_interval_ms=self._config.poll_interval_ms,
            )
        if self._local_endpoint is not None:
            return self._local_endpoint
        raise UnavailableError("compile API", "compile_api.base_url is not configured")

    def _client(self) -> CompileAPIClient:
        return CompileAPIClient(self._endpoint())

    def capabilities(self, ctx: RequestContext) -> dict[str, Any]:
        try:
            endpoint = self._endpoint()
        except UnavailableError:
            return {"configured": False, "can_create": False, "reason_code": "NOT_CONFIGURED"}
        allowed = endpoint.local or bool(ctx.api_key)
        return {
            "configured": True,
            "can_create": allowed,
            "reason_code": None if allowed else "API_KEY_REQUIRED",
        }

    async def create(
        self,
        request: CompileRequest,
        *,
        connection: Mapping[str, Any],
        ctx: RequestContext,
        idempotency_key: str | None = None,
    ) -> TaskRecord | dict[str, Any]:
        """Save explicit update settings synchronously, or queue one immediate Compile run."""
        control, _ = split_control_args(request.args)
        configures_schedule = "auto_update" in control or "update_schedule" in control
        if request.is_memory_mode:
            if configures_schedule:
                raise InvalidArgumentError("Scheduled updates do not support --skill memory")
            if self._memory_runner is None:
                raise UnavailableError(
                    "memory compile", "memory consolidation runner is not configured"
                )
            return await self._memory_runner.create(
                target=request.to,
                instruction=request.instruction,
                ctx=ctx,
            )
        request = self._normalize_request_uris(request, ctx)
        CompileLifecycle.validate_layout(request.to, request.from_)
        if configures_schedule:
            lifecycle = self._require_lifecycle()
            normalized = await self._normalize_request(request, ctx)
            submitted, _ = self._split_payload(normalized)
            control, runtime_args = split_control_args(submitted.get("args"))
            record = await lifecycle.save_submission(
                submitted, control, runtime_args, request._instruction_sent, ctx
            )
            return self._config_view(record)
        if idempotency_key:
            payload, private_payload = self._split_payload(request)
            existing = await self._tasks.recover_submission(
                self.task_type, payload, private_payload, ctx, idempotency_key
            )
            if existing is not None:
                self._schedule_history(existing)
                return existing
        endpoint = self._endpoint()
        if not endpoint.local and not str(connection.get("api_key") or "").strip():
            raise UnauthenticatedError("Compile requires a forwardable OpenViking API key")
        normalized = await self._normalize_request(request, ctx)
        submitted, private_payload = self._split_payload(normalized)
        payload = submitted
        preparation = None
        if self._lifecycle is not None:
            control, runtime_args = split_control_args(submitted.get("args"))
            payload = {key: value for key, value in submitted.items() if key != "args"}
            if runtime_args:
                payload["args"] = runtime_args
            payload["compile"] = {
                "mode": control.get("mode", "incremental"),
                "trigger": "manual",
            }
            preparation = asyncio.create_task(
                self._lifecycle.prepare(submitted, ctx, instruction_set=request._instruction_sent)
            )
        return await self._create_task(
            payload,
            submitted,
            private_payload,
            normalized,
            connection,
            ctx,
            idempotency_key,
            preparation=preparation,
        )

    async def create_scheduled(
        self,
        record: dict[str, Any],
        *,
        ctx: RequestContext,
        connection: Mapping[str, Any],
        idempotency_key: str,
    ) -> TaskRecord:
        """Submit an incremental task from a locked target record (see CompileScheduler)."""
        assert self._lifecycle is not None
        request = CompileRequest.model_validate(
            {
                "from": record["from"],
                "to": record["to"],
                "skill": record["skill"],
                "instruction": record["instruction"],
                "args": {"mode": "incremental", **record["execution_context"]["args"]},
            }
        )
        normalized = await self._normalize_request(self._normalize_request_uris(request, ctx), ctx)
        submitted, private_payload = self._split_payload(normalized)
        payload = await self._lifecycle.prepare(
            submitted, ctx, instruction_set=False, scheduled_record=record
        )
        return await self._create_task(
            payload,
            submitted,
            private_payload,
            normalized,
            connection,
            ctx,
            idempotency_key,
            locked_record=record,
        )

    async def _create_task(
        self,
        payload: dict[str, Any],
        submitted: dict[str, Any],
        private_payload: dict[str, Any],
        normalized: CompileRequest,
        connection: Mapping[str, Any],
        ctx: RequestContext,
        idempotency_key: str | None,
        *,
        locked_record: dict[str, Any] | None = None,
        preparation: asyncio.Task[Any] | None = None,
    ) -> TaskRecord:
        """Enqueue work, recording scheduled history before releasing its caller-owned state lock."""
        task = await self._tasks.create(
            self.task_type,
            resource_id=", ".join(normalized.from_),
            payload=payload,
            private_payload=private_payload,
            connection=connection,
            ctx=ctx,
            **(
                {"idempotency_key": idempotency_key, "submission_payload": submitted}
                if idempotency_key
                else {}
            ),
        )
        if locked_record is not None:
            await self._lifecycle.record_queued(task, locked_record=locked_record)
        else:
            self._schedule_history(task, preparation=preparation)
        return task

    def _schedule_history(
        self, task: TaskRecord, *, preparation: asyncio.Task[Any] | None = None
    ) -> None:
        """Record history after optional preparation, logging background failures."""
        lifecycle = self._lifecycle
        if lifecycle is None:
            return

        async def record_history() -> None:
            try:
                if preparation is not None:
                    await preparation
                await lifecycle.record_queued(task)
            except Exception:
                logger.exception(
                    "Failed to record Compile submission history task_id=%s", task.task_id
                )

        background = asyncio.create_task(record_history())
        self._history_tasks.add(background)
        background.add_done_callback(self._history_tasks.discard)

    async def on_start(self, task: TaskRecord) -> Any:
        if self._lifecycle is None:
            return "proceed"
        return await self._lifecycle.on_start(task)

    async def on_finish(self, task: TaskRecord, snapshot: ExternalTaskSnapshot) -> Any:
        if self._lifecycle is None:
            return snapshot
        return await self._lifecycle.on_finish(task, snapshot)

    async def submit(
        self,
        ov_task_id: str,
        payload: Mapping[str, Any],
        private_payload: Mapping[str, Any],
        connection: Mapping[str, Any],
    ) -> str:
        request_payload = dict(payload)
        request_payload.pop("compile", None)
        args = self._merge_args(payload, private_payload)
        if self._lifecycle is not None:
            changes = await self._lifecycle.runtime_changes(
                payload, str(connection.get("account_id") or ""), ov_task_id
            )
            if changes is not None:
                args = {**(args or {}), "changes": changes}
        if args is not None:
            request_payload["args"] = args
        accepted = await self._client().create(
            {"task_type": self.task_type, "payload": request_payload},
            connection=connection,
            idempotency_key=ov_task_id,
        )
        return accepted.session_id

    async def get(
        self,
        external_task_id: str,
        connection: Mapping[str, Any],
        *,
        payload: Mapping[str, Any] | None = None,
        private_payload: Mapping[str, Any] | None = None,
    ) -> ExternalTaskSnapshot:
        """Query Runtime using args from the task's persisted public and private payloads."""
        return self._snapshot(
            await self._client().get(
                external_task_id,
                connection=connection,
                args=self._merge_args(payload, private_payload),
            )
        )

    async def cancel(
        self,
        external_task_id: str,
        connection: Mapping[str, Any],
        *,
        payload: Mapping[str, Any] | None = None,
        private_payload: Mapping[str, Any] | None = None,
    ) -> ExternalTaskSnapshot:
        """Cancel Runtime work using the original task payloads, including restored secrets."""
        return self._snapshot(
            await self._client().cancel(
                external_task_id,
                connection=connection,
                args=self._merge_args(payload, private_payload),
            )
        )

    # ---------- update settings and run history ----------

    def _require_lifecycle(self) -> CompileLifecycle:
        if self._lifecycle is None:
            raise UnavailableError("compile settings", "Compile target registry is not configured")
        return self._lifecycle

    async def _target(
        self,
        to: str,
        ctx: RequestContext,
        *,
        include_deleted: bool = False,
        action: AclAction = AclAction.READ,
    ) -> dict[str, Any]:
        """Resolve an account's registered target after checking the requested ACL action."""
        lifecycle = self._require_lifecycle()
        target = validate_request_viking_uri(resolve_path_variables(to), ctx, field_name="to")
        await lifecycle._fs._ensure_access(target.rstrip("/"), ctx, action=action)
        record = await lifecycle.store.get_by_to(
            ctx.account_id, target.rstrip("/"), include_deleted=include_deleted
        )
        if record is None:
            raise NotFoundError(to, "compile target")
        return record

    @staticmethod
    def _config_view(record: Mapping[str, Any]) -> dict[str, Any]:
        from openviking.service.compile_scheduler import next_run_at

        return {
            "status": record["status"],
            "to": record["to"],
            "initialized": record["initialized"],
            "initial_actor": record.get("initial_actor"),
            "from": record["from"],
            "skill": record["skill"],
            "instruction": record["instruction"],
            "auto_update": record["auto_update"],
            "update_schedule": record["update_schedule"],
            "next_run_at": next_run_at(dict(record)),
            "last_run": record["last_run"],
            "last_error": record["last_error"],
            "content_updated_at": record.get("content_updated_at"),
            "first_compile_created_at": record.get("first_compile_created_at"),
            "compile_type": record.get("compile_type", "compile"),
            "conflicts_num": record.get("conflicts_num", 0),
        }

    async def get_config(self, to: str, ctx: RequestContext) -> dict[str, Any]:
        return self._config_view(await self._target(to, ctx))

    async def update_config(self, body: "CompileConfigUpdate", ctx: RequestContext) -> dict:
        lifecycle = self._require_lifecycle()
        record = await self._target(body.to, ctx, action=AclAction.WRITE)
        identity = (record["to"], record["deleted_at"])
        request = await self._normalize_request(
            self._normalize_request_uris(
                CompileRequest.model_validate(
                    {"from": body.from_, "to": record["to"], "skill": body.skill}
                ),
                ctx,
            ),
            ctx,
        )
        lifecycle.validate_layout(record["to"], request.from_)
        async with lifecycle.store.edit(ctx.account_id, record["to"]) as record:
            if record["status"] != "active" or (record["to"], record["deleted_at"]) != identity:
                raise ConflictError(
                    "Compile target changed during configuration", resource="COMPILE_TARGET"
                )
            lifecycle.apply_sources(record, request.from_, request.skill)
            if body.instruction_sent:
                record["instruction"] = (body.instruction or "").strip() or None
            await lifecycle.apply_schedule(
                record,
                ctx,
                body.auto_update,
                body.update_schedule.model_dump() if body.update_schedule is not None else None,
            )
            if lifecycle.reschedule is not None:
                await lifecycle.reschedule(ctx.account_id, record, raise_on_error=True)
        return self._config_view(record)

    async def list_configs(
        self, ctx: RequestContext, *, limit: int, cursor: str | None
    ) -> dict[str, Any]:
        """Read active target states lazily in SHA256(to) order, with one readable lookahead."""
        from openviking.service.compile_scheduler import next_run_at

        lifecycle = self._require_lifecycle()
        items = []
        async for item in lifecycle.store.iter_summaries(ctx.account_id, cursor=cursor):
            if item["status"] == "deleted":
                continue
            try:
                await lifecycle._fs._ensure_access(item["to"], ctx)
            except PermissionDeniedError:
                continue
            items.append(
                {
                    "status": item["status"],
                    "to": item["to"],
                    "initialized": item["initialized"],
                    "running": item["running"],
                    "auto_update": item["auto_update"],
                    "update_schedule": item["update_schedule"],
                    "next_run_at": next_run_at(item),
                    "content_updated_at": item.get("content_updated_at"),
                    "first_compile_created_at": item.get("first_compile_created_at"),
                    "compile_type": item.get("compile_type", "compile"),
                    "conflicts_num": item.get("conflicts_num", 0),
                }
            )
            if len(items) > limit:
                break
        page = items[:limit]
        return {
            "targets": page,
            "next_cursor": page[-1]["to"] if len(items) > limit else None,
        }

    async def list_operations(
        self, to: str, ctx: RequestContext, *, limit: int, cursor: str | None
    ) -> dict[str, Any]:
        """Page authorized target operations with public Compile metadata only.

        Common operation fields retain their stored structure. Compile checkpoints
        remain private even when they share the persisted metadata dictionary.
        """
        record = await self._target(to, ctx, include_deleted=True)
        operations = await self._require_lifecycle().store.list_operations(
            ctx.account_id, record["to"], limit=limit + 1, cursor=cursor
        )
        page = operations[:limit]
        public = [self._operation_view(operation) for operation in page]
        next_cursor = page[-1]["operation_id"] if len(operations) > limit else None
        return {"operations": public, "next_cursor": next_cursor}

    @staticmethod
    def _operation_view(operation: Mapping[str, Any]) -> dict[str, Any]:
        """Expose operation fields and public metadata while excluding private checkpoints."""
        return {
            key: operation[key]
            for key in (
                "operation_id",
                "operation_type",
                "actor",
                "created_at",
                "status",
                "started_at",
                "finished_at",
                "params",
                "result",
                "error",
            )
        } | {
            "meta": {
                key: operation["meta"][key]
                for key in (
                    "task_id",
                    "trigger",
                    "execution",
                    "skill_snapshot",
                    "base",
                    "head",
                    "changes",
                )
                if key in operation["meta"]
            },
        }

    async def get_operation(
        self,
        to: str,
        operation_id: str,
        ctx: RequestContext,
    ) -> dict[str, Any]:
        """Read one authorized operation, including deleted targets and public metadata only."""
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:._-]{0,127}", operation_id):
            raise InvalidArgumentError("Invalid operation_id")
        record = await self._target(to, ctx, include_deleted=True)
        operation = await self._require_lifecycle().store.get_operation(
            ctx.account_id, record["to"], operation_id
        )
        if operation is None:
            raise NotFoundError(operation_id, "operation")
        return self._operation_view(operation)

    async def operation_outputs(
        self,
        to: str,
        operation_id: str,
        ctx: RequestContext,
        *,
        limit: int,
        cursor: str | None,
    ) -> dict[str, Any]:
        """Read one authorized operation's immutable-path version references, ordered by URI."""
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:._-]{0,127}", operation_id):
            raise InvalidArgumentError("Invalid operation_id")
        record = await self._target(to, ctx)
        store = self._require_lifecycle().store
        if await store.get_operation(ctx.account_id, record["to"], operation_id) is None:
            raise NotFoundError(operation_id, "operation")
        manifest = await store.get_outputs(ctx.account_id, record["to"], operation_id)
        if manifest is None:
            raise NotFoundError(operation_id, "operation outputs not recorded")
        outputs = sorted(manifest["outputs"], key=lambda item: item["uri"])
        if cursor:
            outputs = [item for item in outputs if item["uri"] > cursor]
        page = outputs[:limit]
        return {"outputs": page, "next_cursor": page[-1]["uri"] if len(outputs) > limit else None}

    async def file_versions(
        self, uri: str, ctx: RequestContext, *, limit: int, before: str | None
    ) -> dict[str, Any]:
        return await self._require_lifecycle().versions.versions(
            uri, ctx, limit=limit, before=before
        )

    async def restore_file_version(
        self, uri: str, restore_commit_id: str, current_commit_id: str, ctx: RequestContext
    ) -> dict[str, Any]:
        """Return restored version metadata; stale file commits or active Compile raise ConflictError."""

        async def write(content: bytes, commit_content: Any) -> None:
            # Use normal refresh while recording a restore version.
            try:
                body: str | bytes = content.decode("utf-8")
            except UnicodeDecodeError:
                body = content
            if isinstance(body, str) and classify_uri(uri).is_memory:
                body = strip_memory_fields_trailer(body)
            await self._fs.write(
                uri,
                body,
                ctx,
                edit_source="compile",
                commit_content=commit_content,
            )

        return await self._require_lifecycle().versions.restore(
            uri=uri,
            restore_commit_id=restore_commit_id,
            current_commit_id=current_commit_id,
            ctx=ctx,
            write=write,
        )

    @staticmethod
    def _merge_args(
        payload: Mapping[str, Any] | None,
        private_payload: Mapping[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Recombine saved args without mutation, or return None when absent.

        Private values take precedence. The result can contain credentials and
        is only for Runtime request bodies, never logs or public task metadata.
        """
        public_args = (payload or {}).get("args")
        private_args = (private_payload or {}).get("args")
        if not isinstance(public_args, dict) and not isinstance(private_args, dict):
            return None
        return {
            **(public_args if isinstance(public_args, dict) else {}),
            **(private_args if isinstance(private_args, dict) else {}),
        }

    def _normalize_request_uris(
        self, request: CompileRequest, ctx: RequestContext
    ) -> CompileRequest:
        """Normalize identity-bound names without requiring live resources."""
        sources = list(
            dict.fromkeys(
                validate_request_viking_uri(
                    resolve_path_variables(source), ctx, field_name=f"from[{index}]"
                ).rstrip("/")
                for index, source in enumerate(request.from_)
            )
        )
        for source in sources:
            if classify_uri(source).is_system:
                raise InvalidArgumentError(
                    "Compile source must not be in the system namespace"
                )
        skill = request.skill.removesuffix("/SKILL.md")
        skill = validate_request_viking_uri(
            resolve_path_variables(skill), ctx, field_name="skill"
        ).rstrip("/")
        if not classify_uri(skill).is_skill_root:
            raise InvalidArgumentError("skill must resolve to a Skill directory or SKILL.md")
        target = validate_request_viking_uri(
            resolve_path_variables(request.to), ctx, field_name="to"
        ).rstrip("/")
        self._validate_target(target)
        return request.model_copy(update={"from_": sources, "to": target, "skill": skill})

    async def _normalize_request(
        self, request: CompileRequest, ctx: RequestContext
    ) -> CompileRequest:
        request = self._normalize_request_uris(request, ctx)
        for uri in request.from_:
            stat = await self._fs.stat(uri, ctx, skip_count=True)
            if not stat.get("isDir"):
                raise InvalidArgumentError(f"Compile source must be a directory: {uri}")
        skill_stat = await self._fs.stat(request.skill, ctx, skip_count=True)
        if not skill_stat.get("isDir"):
            raise InvalidArgumentError("skill must resolve to a Skill directory or SKILL.md")
        skill_file = await self._fs.stat(f"{request.skill}/SKILL.md", ctx, skip_count=True)
        if skill_file.get("isDir"):
            raise InvalidArgumentError("Skill directory must contain a SKILL.md file")
        await self._fs.ensure_write_access(request.to, ctx)
        try:
            target_stat = await self._fs.stat(request.to, ctx, skip_count=True)
        except NotFoundError:
            pass
        else:
            if not target_stat.get("isDir"):
                raise InvalidArgumentError("Compile target must be a directory")
        return request

    @staticmethod
    def _validate_target(target: str) -> None:
        classification = classify_uri(target)
        parts = uri_parts(target)
        if classification.is_system:
            raise InvalidArgumentError("Compile target must not be in the system namespace")
        if classification.context_type == "skill":
            if (
                not classification.is_skill_namespace
                or classification.scope not in {"user", "agent"}
                or (classification.scope == "agent" and parts != ["agent", "skills"])
            ):
                raise InvalidArgumentError(
                    "Compile Skill target must be a supported skills namespace"
                )
            return
        if classification.context_type not in {"resource", "memory"}:
            raise InvalidArgumentError(
                "Compile target must be a resource, memory, or skills directory"
            )
        if classification.context_type == "memory":
            if (
                classification.content_index is None
                or len(parts) <= classification.content_index + 1
            ):
                raise InvalidArgumentError("Compile target must be inside a memory type directory")
        elif parts == ["resources"] or (
            classification.content_index is not None
            and len(parts) <= classification.content_index + 1
        ):
            raise InvalidArgumentError("Compile target must be inside a resource directory")

    @staticmethod
    def _split_payload(request: CompileRequest) -> tuple[dict[str, Any], dict[str, Any]]:
        payload = request.model_dump(mode="json", by_alias=True, exclude_none=True)
        args = payload.get("args")
        if not isinstance(args, dict):
            return payload, {}
        private_args = {key: args.pop(key) for key in SENSITIVE_TASK_KEYS if key in args}
        if not private_args:
            return payload, {}
        if not args:
            payload.pop("args", None)
        return payload, {"args": private_args}

    @staticmethod
    def _snapshot(task: CompileSessionStatus) -> ExternalTaskSnapshot:
        status = (task.status or "").strip().lower()
        if not status:
            normalized_stage = task.stage.strip().lower().replace(" ", "")
            if "cancelled" in normalized_stage or "canceled" in normalized_stage:
                status = "cancelled"
            elif task.error is not None or any(
                marker in normalized_stage for marker in ("failed", "error")
            ):
                status = "failed"
            elif any(
                marker in normalized_stage
                for marker in ("completed", "succeeded", "success", "finished")
            ):
                status = "completed"
            else:
                status = "running"
        if status in _ACTIVE_STATUSES:
            status = "running"
        elif status == "cancelling":
            pass
        elif status not in _TERMINAL_STATUSES:
            raise ExternalTaskError(
                "INVALID_RESPONSE",
                f"Unknown Compile task status: {status}",
                transient=False,
            )

        if isinstance(task.error, CompileErrorInfo):
            error_code = task.error.code
            error_message = task.error.message
        elif task.error:
            error_code = "UNKNOWN"
            error_message = str(task.error)
        else:
            error_code = None
            error_message = None
        return ExternalTaskSnapshot(
            status=status,
            stage=task.stage,
            result=task.result,
            meta=task.meta,
            error_code=error_code,
            error_message=error_message,
        )


__all__ = [
    "CompileAPIClient",
    "CompileRequest",
    "CompileService",
    "CompileSessionAccepted",
    "CompileSessionStatus",
]
