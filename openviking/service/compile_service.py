# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Compile API models and external task provider."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Mapping

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from openviking.core.identifiers import validate_user_id
from openviking.core.namespace import classify_uri, uri_parts
from openviking.core.path_variables import resolve_path_variables
from openviking.core.uri_validation import validate_request_viking_uri
from openviking.server.identity import RequestContext, Role
from openviking.service.compile_targets import (
    COMPILE_NAME_PATTERN,
    CompileLifecycle,
    UpdateSchedule,
    execution_signature,
    is_versioned,
    new_record,
    parse_schedule,
    split_control_args,
    validate_compile_name,
)
from openviking.service.external_task_service import (
    ExternalTaskCancellation,
    ExternalTaskError,
    ExternalTaskRetryExhaustedError,
    ExternalTaskService,
    ExternalTaskSnapshot,
)
from openviking.service.fs_service import FSService
from openviking.service.memory_compile import MemoryCompileRunner
from openviking.service.task_domain import sanitize_task_result
from openviking.service.task_tracker import TaskRecord
from openviking.storage.acl import AclAction
from openviking_cli.exceptions import (
    FailedPreconditionError,
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


class CompileInputs(BaseModel):
    """Complete compile inputs; scheduling controls are accepted only when saving a configuration."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: list[str] = Field(default_factory=list, alias="from")
    to: str = Field(min_length=1)
    skill: str = Field(min_length=1)
    instruction: str | None = None
    args: dict[str, Any] | None = None

    @property
    def is_memory_mode(self) -> bool:
        return self.skill == MEMORY_COMPILE_SKILL

    @model_validator(mode="after")
    def _normalize(self) -> "CompileInputs":
        sources: list[str] = []
        for source in self.from_:
            normalized = source.strip().rstrip("/")
            if not normalized:
                raise ValueError("from must not contain empty values")
            if normalized not in sources:
                sources.append(normalized)
        self.from_ = sources
        if self.to is not None:
            self.to = self.to.strip().rstrip("/")
        self.skill = self.skill.strip().rstrip("/")
        self.instruction = (
            self.instruction.strip() if self.instruction and self.instruction.strip() else None
        )
        self.args = dict(self.args) if self.args else None
        reserved = {"mode", "last_compile_time", "changes"}.intersection(self.args or {})
        if reserved:
            raise ValueError(
                f"Compile execution controls are server-owned: {', '.join(sorted(reserved))}"
            )
        if self.to is not None and not self.to:
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


class CompileCreateRequest(CompileInputs):
    """Save complete inputs under a caller-supplied configuration name unique to its creator within the account."""

    compile_name: str = Field(
        pattern=COMPILE_NAME_PATTERN,
        description="Case-sensitive configuration name: 1–64 ASCII letters, digits, underscores or hyphens. The first character cannot be a hyphen.",
    )


class CompileRequest(BaseModel):
    """Execute saved inputs for an optional owner, or full one-off inputs."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    compile_name: str | None = Field(
        default=None,
        pattern=COMPILE_NAME_PATTERN,
        description="Saved configuration name; omit for a one-off execution with complete inputs.",
    )
    from_: list[str] = Field(default_factory=list, alias="from")
    to: str | None = None
    skill: str | None = None
    instruction: str | None = None
    args: dict[str, Any] | None = Field(
        default=None,
        description="Named runs use only owner_user_id; other args are ignored.",
    )

    @property
    def is_memory_mode(self) -> bool:
        return self.skill == MEMORY_COMPILE_SKILL

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        """Check the request shape before parsing fields and normalize one-off execution inputs."""
        if not isinstance(data, Mapping):
            return data
        if "compile_name" in data:
            if set(data) - {"compile_name", "args"}:
                raise ValueError(
                    "To run a saved configuration, send compile_name and optional args.owner_user_id "
                    "to POST /api/v1/compile. "
                    "Save the execution inputs with compile_name using POST /api/v1/compile/create. "
                    "See https://docs.openviking.ai/en/api/23-agent-runtime"
                )
            if data["compile_name"] is None:
                raise ValueError("compile_name must not be null")
            args = data.get("args")
            if args is not None:
                if not isinstance(args, Mapping):
                    raise ValueError("Named Compile args must be a JSON object")
                if "owner_user_id" in args:
                    error = validate_user_id(args["owner_user_id"])
                    if error:
                        raise ValueError(f"Invalid args.owner_user_id: {error}")
                data = {**data, "args": {k: args[k] for k in ("owner_user_id",) if k in args}}
            return data
        inputs = CompileInputs.model_validate(data)
        if "owner_user_id" in (inputs.args or {}):
            raise ValueError("args.owner_user_id requires compile_name")
        scheduling = {"auto_update", "update_schedule"}.intersection(inputs.args or {})
        if scheduling:
            raise ValueError(
                f"ov compile (POST /api/v1/compile) does not accept scheduling options "
                f"{', '.join(sorted(scheduling))}. Remove them from args for a one-off run. "
                "To schedule a new configuration, use 'ov compile create --compile-name <name>' "
                "or POST /api/v1/compile/create with these options in args. "
                "For an existing configuration, send them as top-level fields "
                "to POST /api/v1/compile/config/update. "
                "See https://docs.openviking.ai/en/api/23-agent-runtime"
            )
        return inputs.model_dump(by_alias=True)


class CompileConfigUpdate(CompileCreateRequest):
    """Replace a named configuration while keeping its output directory and execution timestamps."""

    to: str | None = Field(
        default=None,
        min_length=1,
        description="Output directory; omission or null preserves the saved directory.",
    )
    auto_update: bool
    update_schedule: UpdateSchedule | None = None
    updated_by: str | None = Field(
        default=None, description="User credited with this update. Defaults to the requesting user."
    )


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

    async def archive(
        self,
        session_id: str,
        *,
        connection: Mapping[str, Any],
        args: Mapping[str, Any] | None = None,
    ) -> None:
        """Acknowledge archival with the original session args; propagate Runtime errors."""
        await self._request(
            "POST",
            "/runtime/v1/tasks/archive",
            json={"session_id": session_id, **({"args": dict(args)} if args is not None else {})},
            headers=self._headers(connection),
        )

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
        self._scheduler: Any = None

    def configure_lifecycle(self, lifecycle: CompileLifecycle) -> None:
        """Install persistent configurations and recoverable full/incremental execution."""
        self._lifecycle = lifecycle

    @property
    def lifecycle(self) -> CompileLifecycle | None:
        return self._lifecycle

    def configure_memory_runner(
        self,
        vikingdb: Any = None,
        *,
        runtime_config_manager: Any = None,
        vlm_resolver=None,
    ) -> None:
        """Install the in-process memory-consolidation runner for `--skill memory`."""
        self._memory_runner = MemoryCompileRunner(
            self._fs,
            vikingdb=vikingdb,
            runtime_config_manager=runtime_config_manager,
            vlm_resolver=vlm_resolver,
        )

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

    def configure_scheduler(self, scheduler: Any) -> None:
        """Install scheduling for saved configurations; execution remains available without it."""
        self._scheduler = scheduler

    async def _encrypt_private_args(
        self, account_id: str, private: dict[str, Any]
    ) -> dict[str, str]:
        """Encrypt private input subtrees; configuration state contains only their ciphertext."""
        try:
            encrypted = await self._tasks._credential_encryptor().encrypt(
                account_id, json.dumps(private, ensure_ascii=False).encode()
            )
        except Exception as exc:
            raise FailedPreconditionError("Cannot encrypt Compile configuration inputs") from exc
        return {"ciphertext": base64.b64encode(encrypted).decode("ascii")}

    async def _private_args(self, account_id: str, value: Mapping[str, Any]) -> dict[str, Any]:
        """Decrypt saved private inputs only for authenticated execution, never for public views."""
        if not value:
            return {}
        try:
            ciphertext = base64.b64decode(value["ciphertext"], validate=True)
            plaintext = await self._tasks._credential_encryptor().decrypt(account_id, ciphertext)
            decoded = json.loads(plaintext)
            if not isinstance(decoded, dict) or not isinstance(decoded.get("args"), dict):
                raise ValueError("Invalid private Compile inputs")
            return decoded
        except Exception as exc:
            raise FailedPreconditionError("Cannot decrypt Compile configuration inputs") from exc

    async def _configure_schedule(
        self, record: dict[str, Any], ctx: RequestContext, auto_update: bool, schedule: dict | None
    ) -> None:
        """Validate credentials and calculate the first future slot without submitting work."""
        from datetime import datetime

        from openviking.service.compile_scheduler import TZ, next_slot

        changed = auto_update != record["auto_update"] or schedule != record["update_schedule"]
        if auto_update:
            if schedule is None:
                raise InvalidArgumentError("update_schedule is required when auto_update is true")
            if self._scheduler is None or self._scheduler._expiry is None:
                raise FailedPreconditionError("Scheduled Compile requires a configured scheduler")
            record["execution_context"]["credential"] = await self._scheduler.save_credential(ctx)
            if changed or record["next_triggered_slot"] is None:
                record["next_triggered_slot"] = next_slot(schedule, datetime.now(TZ)).isoformat()
                record["last_triggered_slot"] = None
        else:
            record["execution_context"]["credential"] = None
            record["next_triggered_slot"] = None
        record["auto_update"] = auto_update
        record["update_schedule"] = schedule

    async def _settings(
        self, request: CompileInputs, ctx: RequestContext, owner_user_id: str, compile_name: str
    ) -> dict[str, Any]:
        """Save the caller's executor identity independently of scheduling."""
        if request.is_memory_mode:
            raise InvalidArgumentError("Persistent Compile does not support skill=memory")
        normalized = await self._normalize_request(request, ctx)
        CompileLifecycle.validate_layout(normalized.to, normalized.from_)
        public, private = self._split_payload(normalized)
        control, runtime_args = split_control_args(public.get("args"))
        # Configuration ownership does not enter the Skill's execution arguments.
        runtime_args.pop("owner_user_id", None)
        schedule = control.get("update_schedule")
        if schedule is not None:
            schedule = parse_schedule(schedule, (ctx.account_id, owner_user_id, compile_name))
        public["args"] = runtime_args
        record = new_record(
            public,
            execution_context={
                "executor": {
                    "account_id": ctx.account_id,
                    "user_id": ctx.user.user_id,
                    "role": str(ctx.role),
                    "group_ids": list(ctx.group_ids),
                }
            },
        )
        if private:
            record["execution_context"]["private_args"] = await self._encrypt_private_args(
                ctx.account_id, private
            )
        await self._configure_schedule(
            record, ctx, control.get("auto_update", schedule is not None), schedule
        )
        return record

    async def create_compile_task(
        self, request: CompileCreateRequest, *, ctx: RequestContext
    ) -> dict[str, str]:
        """Save configuration and enqueue target tagging without executing Compile or snapshots."""
        lifecycle = self._require_lifecycle()
        compile_name = validate_compile_name(request.compile_name)
        owner_user_id = (request.args or {}).get("owner_user_id", ctx.user.user_id)
        record = await self._settings(request, ctx, owner_user_id, compile_name)

        async def prepare_target_and_schedule() -> None:
            await self._fs.ensure_compile_target(record["to"], ctx)
            if self._scheduler is not None:
                await self._scheduler.reschedule(
                    ctx.account_id, owner_user_id, compile_name, record, raise_on_error=True
                )

        await lifecycle.store.create(
            ctx.account_id,
            owner_user_id,
            record,
            compile_name=compile_name,
            before_publish=prepare_target_and_schedule,
        )
        return {"compile_name": compile_name}

    async def create(
        self,
        request: CompileRequest,
        *,
        connection: Mapping[str, Any],
        ctx: RequestContext,
        idempotency_key: str | None = None,
        trigger: str = "manual",
        locked_record: dict[str, Any] | None = None,
        scheduled_slot: str | None = None,
    ) -> TaskRecord:
        """Execute frozen configuration inputs or a full request; scheduled callers share this path.

        ``locked_record`` is supplied only by the scheduler while it owns the configuration lock.
        Submission retries recover the original task and do not refresh its invocation timestamps.
        New executions require a search-visible target tag before task creation and enqueueing.
        """
        if trigger not in {"manual", "scheduled"}:
            raise InvalidArgumentError("Invalid Compile trigger")
        if request.is_memory_mode:
            if self._memory_runner is None:
                raise UnavailableError(
                    "memory compile", "memory consolidation runner is not configured"
                )
            return await self._memory_runner.create(
                target=request.to, instruction=request.instruction, ctx=ctx
            )
        compile_name = request.compile_name
        owner_user_id = (request.args or {}).get("owner_user_id", ctx.user.user_id)
        if compile_name and locked_record is None:
            configuration = await self._configuration(
                compile_name, ctx, action=AclAction.WRITE, owner_user_id=owner_user_id
            )
            async with self._require_lifecycle().store.edit(
                ctx.account_id, configuration["execution_context"]["owner_user_id"], compile_name
            ) as record:
                return await self.create(
                    request,
                    connection=connection,
                    ctx=ctx,
                    idempotency_key=idempotency_key,
                    trigger=trigger,
                    locked_record=record,
                    scheduled_slot=scheduled_slot,
                )
        if compile_name:
            record = locked_record
            inputs = CompileInputs.model_validate(
                {key: record[key] for key in ("from", "to", "skill", "instruction", "args")}
            )
            submitted, submitted_private = {"compile_name": compile_name}, {}
            # Omitted and explicit self owners share a retry identity.
            if owner_user_id != ctx.user.user_id:
                submitted["args"] = {"owner_user_id": owner_user_id}
        else:
            record = None
            inputs = self._normalize_request_uris(request, ctx)
            submitted, submitted_private = self._split_payload(inputs)
        if idempotency_key:
            existing = await self._tasks.recover_submission(
                self.task_type, submitted, submitted_private, ctx, idempotency_key
            )
            if existing is not None:
                if compile_name:
                    await self._require_lifecycle().record_queued(
                        existing, locked_record=locked_record
                    )
                return existing
        endpoint = self._endpoint()
        if not endpoint.local and not str(connection.get("api_key") or "").strip():
            raise UnauthenticatedError("Compile requires a forwardable OpenViking API key")
        normalized = await self._normalize_request(inputs, ctx)
        assert normalized.to is not None
        CompileLifecycle.validate_layout(normalized.to, normalized.from_)
        payload, private_payload = self._split_payload(normalized)
        payload["compile"] = {"trigger": trigger}
        if trigger == "scheduled":
            payload["compile"]["scheduled_slot"] = scheduled_slot
        if record is not None:
            private_payload = await self._private_args(
                ctx.account_id, record["execution_context"].get("private_args") or {}
            )
            payload["compile"].update(
                compile_name=compile_name,
                owner_user_id=record["execution_context"]["owner_user_id"],
                auto_update=record["auto_update"],
                update_schedule=record["update_schedule"],
                input_signature=execution_signature(record),
            )

        async def record_created(task: TaskRecord) -> None:
            await self._require_lifecycle().record_queued(task, locked_record=locked_record)

        await self._fs.ensure_compile_target(normalized.to, ctx)
        # Private task state retains the authenticated executor for asynchronous filesystem access.
        return await self._tasks.create(
            self.task_type,
            resource_id=", ".join(normalized.from_),
            payload=payload,
            private_payload=private_payload,
            connection={**connection, "role": str(ctx.role), "group_ids": list(ctx.group_ids)},
            ctx=ctx,
            idempotency_key=idempotency_key,
            submission_payload=submitted if idempotency_key else None,
            submission_private_payload={} if compile_name else None,
            on_task_created=record_created if compile_name else None,
        )

    async def on_start(self, task: TaskRecord) -> str | ExternalTaskSnapshot | None:
        if self._lifecycle is None:
            return "proceed"
        return await self._lifecycle.on_start(task)

    async def on_finish(
        self,
        task: TaskRecord,
        snapshot: ExternalTaskSnapshot,
    ) -> ExternalTaskSnapshot:
        """Return the finalized snapshot; Runtime archival errors only produce a warning."""
        session_id = task.auth.get("external_task_id")
        if session_id and snapshot.status in _TERMINAL_STATUSES:
            try:
                await self._client().archive(
                    session_id,
                    connection={
                        **(task.auth.get("openviking_connection") or {}),
                        "account_id": task.account_id,
                    },
                    args=self._merge_args(
                        task.meta.get("request"), task.auth.get("external_request_private")
                    ),
                )
            except ExternalTaskError as exc:
                logger.warning("Failed to archive Compile session %s: %s", session_id, exc.code)
        if self._lifecycle is None:
            return snapshot
        return await self._lifecycle.on_finish(task, snapshot)

    @staticmethod
    def submission_meta(_external_task_id: str) -> dict[str, Any]:
        return {}

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
                payload,
                str(connection.get("account_id") or ""),
                ov_task_id,
                user_id=str(connection.get("user_id") or ""),
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

    async def on_cancel(
        self,
        task: TaskRecord,
        *,
        external_task_id: str | None,
        payload: Mapping[str, Any],
        private_payload: Mapping[str, Any],
        connection: Mapping[str, Any],
        failure: Exception | None,
    ) -> ExternalTaskCancellation:
        """Recover an uncertain Compile submission, then stop its Runtime session."""
        outcome = (
            ExternalTaskSnapshot(
                status="failed",
                error_code=getattr(failure, "code", None) or "INTERNAL",
                error_message=str(failure) or type(failure).__name__,
            )
            if failure is not None
            else ExternalTaskSnapshot(status="cancelled")
        )
        rejected = (
            task.stage == "submitting"
            and isinstance(failure, ExternalTaskError)
            and not isinstance(failure, ExternalTaskRetryExhaustedError)
            and not failure.transient
            and failure.code
            in {
                "INVALID_ARGUMENT",
                "NOT_FOUND",
                "PERMISSION_DENIED",
                "UNAUTHENTICATED",
                "OperationConflict",
            }
        )
        if external_task_id is None and (task.stage in {None, "queued"} or rejected):
            return ExternalTaskCancellation(None, outcome)
        if external_task_id is None:
            external_task_id = await self.submit(
                task.task_id,
                payload,
                private_payload,
                connection,
            )
        snapshot = await self.cancel(
            external_task_id,
            connection,
            payload=payload,
            private_payload=private_payload,
        )
        return ExternalTaskCancellation(
            external_task_id,
            snapshot,
            terminal_snapshot=outcome if failure is not None else None,
        )

    # ---------- saved configurations and run history ----------

    def _require_lifecycle(self) -> CompileLifecycle:
        if self._lifecycle is None:
            raise UnavailableError("compile settings", "Compile lifecycle is not configured")
        return self._lifecycle

    async def _configuration(
        self,
        compile_name: str,
        ctx: RequestContext,
        *,
        action: AclAction = AclAction.READ,
        owner_user_id: str | None = None,
    ) -> dict[str, Any]:
        """Load the selected owner's config and check the caller's output ACL."""
        validate_compile_name(compile_name)
        lifecycle = self._require_lifecycle()
        record = await lifecycle.store.get(
            ctx.account_id,
            ctx.user.user_id if owner_user_id is None else owner_user_id,
            compile_name,
        )
        if record is None:
            raise NotFoundError(compile_name, "compile configuration")
        await lifecycle._fs._ensure_access(record["to"], ctx, action=action)
        return record

    @staticmethod
    def _config_view(compile_name: str, record: Mapping[str, Any]) -> dict[str, Any]:
        """Expose configuration inputs and invocation/slot times without private execution context."""
        fields = (
            "from",
            "to",
            "skill",
            "instruction",
            "args",
            "auto_update",
            "update_schedule",
            "task_first_create_time",
            "last_run_time",
            "last_triggered_slot",
            "next_triggered_slot",
        )
        return {"compile_name": compile_name, **{key: record[key] for key in fields}}

    async def get_config(self, compile_name: str, ctx: RequestContext) -> dict[str, Any]:
        return self._config_view(compile_name, await self._configuration(compile_name, ctx))

    async def update_config(self, body: CompileConfigUpdate, ctx: RequestContext) -> dict[str, Any]:
        """Replace mutable settings under the state lock; target changes raise InvalidArgumentError."""
        from copy import deepcopy

        configuration = await self._configuration(
            body.compile_name,
            ctx,
            action=AclAction.WRITE,
            owner_user_id=(body.args or {}).get("owner_user_id", ctx.user.user_id),
        )
        if body.to is None:
            body = body.model_copy(update={"to": configuration["to"]})
        target = self._normalize_request_uris(body, ctx).to
        if target != configuration["to"]:
            raise InvalidArgumentError("to must match the saved compile configuration's directory")
        owner_user_id = configuration["execution_context"]["owner_user_id"]
        args = dict(body.args or {})
        if {"auto_update", "update_schedule"}.intersection(args):
            raise InvalidArgumentError(
                "Use top-level scheduling fields when updating a configuration"
            )
        args.update(
            auto_update=body.auto_update,
            update_schedule=body.update_schedule or configuration["update_schedule"],
        )
        inputs = CompileInputs.model_validate(
            {
                "from": body.from_,
                "to": target,
                "skill": body.skill,
                "instruction": body.instruction,
                "args": args,
            }
        )
        replacement = await self._settings(inputs, ctx, owner_user_id, body.compile_name)
        await self._fs.ensure_compile_target(replacement["to"], ctx)
        lifecycle = self._require_lifecycle()
        async with lifecycle.store.edit(ctx.account_id, owner_user_id, body.compile_name) as record:
            await lifecycle._fs._ensure_access(record["to"], ctx, action=AclAction.WRITE)
            replacement = deepcopy(replacement)
            replacement["updated_by"] = (
                ctx.user.user_id if body.updated_by is None else body.updated_by
            )
            replacement["execution_context"]["owner_user_id"] = owner_user_id
            replacement["conflict_num"] = record["conflict_num"]
            old_private = await self._private_args(
                ctx.account_id, record["execution_context"].get("private_args") or {}
            )
            new_private = await self._private_args(
                ctx.account_id, replacement["execution_context"].get("private_args") or {}
            )
            retained = dict(new_private.get("args") or {})
            for key, value in (old_private.get("args") or {}).items():
                if key in retained:
                    continue
                if key in record["args"] and replacement["args"].get(key) != record["args"][key]:
                    if key in replacement["args"]:
                        raise InvalidArgumentError(
                            f"Changing args.{key} requires resubmitting its private fields"
                        )
                    continue
                retained[key] = value
            merged_private = {"args": retained} if retained else {}
            if merged_private == old_private:
                replacement["execution_context"]["private_args"] = record["execution_context"].get(
                    "private_args"
                )
            elif merged_private:
                replacement["execution_context"]["private_args"] = await self._encrypt_private_args(
                    ctx.account_id, merged_private
                )
            else:
                replacement["execution_context"].pop("private_args", None)
            for field in ("task_first_create_time", "last_run_time"):
                replacement[field] = record[field]
            if (record["auto_update"], record["update_schedule"]) == (
                replacement["auto_update"],
                replacement["update_schedule"],
            ):
                for field in ("last_triggered_slot", "next_triggered_slot"):
                    replacement[field] = record[field]
            replacement["progress"] = record["progress"]
            if self._scheduler is not None:
                await self._scheduler.reschedule(
                    ctx.account_id,
                    owner_user_id,
                    body.compile_name,
                    replacement,
                    raise_on_error=True,
                )
            record.clear()
            record.update(replacement)
        return self._config_view(body.compile_name, record)

    async def list_configs(
        self,
        ctx: RequestContext,
        *,
        limit: int,
        cursor: str | None,
        scope: str = "user",
        compile_name: str | None = None,
    ) -> dict[str, Any]:
        """Page readable configs by name, then order each page by latest accepted execution."""
        if scope not in {"user", "account"}:
            raise InvalidArgumentError("scope must be user or account")
        if scope == "account" and ctx.role != Role.ADMIN:
            raise PermissionDeniedError("Account Compile listing requires ADMIN")
        if compile_name is not None:
            validate_compile_name(compile_name)
        if cursor is not None:
            if scope == "user":
                validate_compile_name(cursor)
            elif ":" not in cursor:
                raise InvalidArgumentError("Account Compile cursor must include an owner")
        lifecycle = self._require_lifecycle()
        items = []
        async for name, record in lifecycle.store.iter_records(
            ctx.account_id,
            user_id=ctx.user.user_id if scope == "user" else None,
            cursor=cursor,
            compile_name=compile_name,
        ):
            try:
                await lifecycle._fs._ensure_access(record["to"], ctx)
            except PermissionDeniedError:
                continue
            context = record["execution_context"]
            items.append(
                {
                    **self._config_view(name, record),
                    "owner_user_id": context["owner_user_id"],
                    "executor_user_id": (context.get("executor") or {}).get("user_id"),
                    "updated_by": record.get("updated_by"),
                    "conflict_num": record["conflict_num"],
                }
            )
            if len(items) > limit:
                break
        page = items[:limit]
        next_cursor = None
        if len(items) > limit:
            next_cursor = page[-1]["compile_name"]
            if scope == "account":
                next_cursor += ":" + page[-1]["owner_user_id"]
        # shortcut: time ordering is page-local, use time cursors when global ordering is required.
        page.sort(
            key=lambda item: (item["last_run_time"] is not None, item["last_run_time"]),
            reverse=True,
        )
        return {
            "configs": page,
            "next_cursor": next_cursor,
        }

    async def list_runs(
        self, compile_name: str, ctx: RequestContext, *, limit: int, cursor: str | None
    ) -> dict[str, Any]:
        record = await self._configuration(compile_name, ctx)
        lifecycle = self._require_lifecycle()
        runs = []
        for run in await lifecycle.store.list_runs(
            ctx.account_id,
            record["execution_context"]["owner_user_id"],
            compile_name,
            cursor=cursor,
        ):
            try:
                await lifecycle._fs._ensure_access(run["params"]["to"], ctx)
            except PermissionDeniedError:
                continue
            runs.append(run)
            if len(runs) > limit:
                break
        page = runs[:limit]
        return {"runs": page, "next_cursor": page[-1]["task_id"] if len(runs) > limit else None}

    async def get_run(self, compile_name: str, task_id: str, ctx: RequestContext) -> dict[str, Any]:
        """Read a frozen run under the caller's current configuration read permission."""
        record = await self._configuration(compile_name, ctx)
        run = await self._require_lifecycle().store.get_run(
            ctx.account_id, record["execution_context"]["owner_user_id"], compile_name, task_id
        )
        if run is None:
            raise NotFoundError(task_id, "compile run")
        await self._require_lifecycle()._fs._ensure_access(run["params"]["to"], ctx)
        return run

    async def run_outputs(
        self,
        compile_name: str,
        task_id: str,
        ctx: RequestContext,
        *,
        limit: int,
        cursor: str | None,
    ) -> dict[str, Any]:
        """Page authorized run outputs with each file's latest commit, or null for no history."""
        run = await self.get_run(compile_name, task_id, ctx)
        target = run["params"]["to"]
        fs = self._require_lifecycle()._fs
        await fs._ensure_access(target, ctx)
        start, finish = run["meta"]["start_snapshot"], run["meta"]["finish_snapshot"]
        changes = (
            await fs.diff_paths(dirs=[target], base=start, head=finish, ctx=ctx)
            if start and finish
            else []
        )
        outputs = [
            {"uri": item["uri"], "snapshot_id": finish, "change_type": item["change_type"]}
            for item in sorted(changes, key=lambda item: item["uri"])
            if item["change_type"] != "deleted"
            and is_versioned(item["uri"])
            and (not cursor or item["uri"] > cursor)
        ]
        allowed = await fs._can_access_many([item["uri"] for item in outputs], ctx)
        outputs = [item for item in outputs if allowed.get(item["uri"], False)]
        page = outputs[:limit]
        for item in page:
            history = await fs.log(ctx=ctx, paths=[item["uri"]], limit=1)
            item["latest_snapshot_id"] = history[0]["oid"] if history else None
        return {"outputs": page, "next_cursor": page[-1]["uri"] if len(outputs) > limit else None}

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
        self, request: CompileInputs | CompileRequest, ctx: RequestContext
    ) -> CompileInputs | CompileRequest:
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
                raise InvalidArgumentError("Compile source must not be in the system namespace")
        skill = request.skill.removesuffix("/SKILL.md")
        skill = validate_request_viking_uri(
            resolve_path_variables(skill), ctx, field_name="skill"
        ).rstrip("/")
        if not classify_uri(skill).is_skill_root:
            raise InvalidArgumentError(
                "The input skill is not in a supported location. "
                "Select a Skill directory or its SKILL.md, "
                "e.g. viking://user/<user_id>/skills/<skill_name>."
            )
        target = validate_request_viking_uri(
            resolve_path_variables(request.to), ctx, field_name="to"
        ).rstrip("/")
        self._validate_target(target)
        return request.model_copy(update={"from_": sources, "to": target, "skill": skill})

    async def _normalize_request(
        self, request: CompileInputs | CompileRequest, ctx: RequestContext
    ) -> CompileInputs | CompileRequest:
        """Validate inputs for new admissions without revalidating recovered submissions."""
        request = self._normalize_request_uris(request, ctx)
        assert request.to is not None
        parts = uri_parts(request.to)
        if parts[:1] == ["user"] and parts[2:4] == ["memories", "experiences"]:
            raise InvalidArgumentError(
                "Compile target must not be inside the user memories/experiences subtree"
            )
        for uri in request.from_:
            try:
                stat = await self._fs.stat(uri, ctx, skip_count=True)
            except NotFoundError as exc:
                raise NotFoundError(
                    uri,
                    "compile source directory",
                    reason=(
                        "The source directory is not found. "
                        "Update the Compile configuration's source directories before retrying."
                    ),
                ) from exc
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
    def _split_payload(
        request: CompileInputs | CompileRequest,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        payload = request.model_dump(
            mode="json", by_alias=True, exclude_none=True, exclude={"compile_name"}
        )
        args = payload.get("args")
        if not isinstance(args, dict):
            return payload, {}
        public_args = sanitize_task_result(args)
        private_args = {
            key: value
            for key, value in args.items()
            if key not in public_args or value != public_args[key]
        }
        if not private_args:
            return payload, {}
        if public_args:
            payload["args"] = public_args
        else:
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
    "CompileConfigUpdate",
    "CompileCreateRequest",
    "CompileInputs",
    "CompileRequest",
    "CompileService",
    "CompileSessionAccepted",
    "CompileSessionStatus",
]
