# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Compile API models and external task provider."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from openviking.core.namespace import classify_uri, uri_parts
from openviking.core.path_variables import resolve_path_variables
from openviking.core.uri_validation import validate_request_viking_uri
from openviking.server.identity import RequestContext
from openviking.service.external_task_service import (
    ExternalTaskError,
    ExternalTaskService,
    ExternalTaskSnapshot,
)
from openviking.service.fs_service import FSService
from openviking.service.task_tracker import SENSITIVE_TASK_KEYS, TaskRecord
from openviking_cli.exceptions import (
    InvalidArgumentError,
    NotFoundError,
    UnauthenticatedError,
    UnavailableError,
)
from openviking_cli.utils.config.open_viking_config import CompileApiConfig

_ACTIVE_STATUSES = frozenset({"accepted", "pending", "running", "committing"})
_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


class CompileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: list[str] = Field(alias="from", min_length=1)
    to: str = Field(min_length=1)
    skill: str = Field(min_length=1)
    instruction: str | None = None
    args: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def _accept_reason(cls, data: Any) -> Any:
        if isinstance(data, dict) and "reason" in data:
            data = dict(data)
            data.setdefault("instruction", data.pop("reason"))
        return data

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
        """Build Runtime headers from saved connection data; legacy tasks may lack request_id."""
        headers = {
            "Content-Type": "application/json",
        }
        if self._endpoint.gateway_token:
            headers["X-Gateway-Token"] = self._endpoint.gateway_token
        api_key = str(connection.get("api_key") or "").strip()
        if api_key:
            headers["X-API-Key"] = api_key
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
    ) -> TaskRecord:
        request = self._normalize_request_uris(request, ctx)
        if idempotency_key:
            payload, private_payload = self._split_payload(request)
            existing = await self._tasks.recover_submission(
                self.task_type, payload, private_payload, ctx, idempotency_key
            )
            if existing is not None:
                return existing
        endpoint = self._endpoint()
        if not endpoint.local and not str(connection.get("api_key") or "").strip():
            raise UnauthenticatedError("Compile requires a forwardable OpenViking API key")
        normalized = await self._normalize_request(request, ctx)
        payload, private_payload = self._split_payload(normalized)
        return await self._tasks.create(
            self.task_type,
            resource_id=", ".join(normalized.from_),
            payload=payload,
            private_payload=private_payload,
            connection=connection,
            ctx=ctx,
            **({"idempotency_key": idempotency_key} if idempotency_key else {}),
        )

    async def submit(
        self,
        ov_task_id: str,
        payload: Mapping[str, Any],
        private_payload: Mapping[str, Any],
        connection: Mapping[str, Any],
    ) -> str:
        request_payload = dict(payload)
        args = self._merge_args(payload, private_payload)
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
            stat = await self._fs.stat(uri, ctx)
            if not stat.get("isDir"):
                raise InvalidArgumentError(f"Compile source must be a directory: {uri}")
        skill_stat = await self._fs.stat(request.skill, ctx)
        if not skill_stat.get("isDir"):
            raise InvalidArgumentError("skill must resolve to a Skill directory or SKILL.md")
        skill_file = await self._fs.stat(f"{request.skill}/SKILL.md", ctx)
        if skill_file.get("isDir"):
            raise InvalidArgumentError("Skill directory must contain a SKILL.md file")
        await self._fs.ensure_write_access(request.to, ctx)
        try:
            target_stat = await self._fs.stat(request.to, ctx)
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
        if classification.context_type == "skill":
            if not classification.is_skill_namespace or (
                classification.scope == "agent" and parts != ["agent", "skills"]
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
