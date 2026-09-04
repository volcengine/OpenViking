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
from openviking.service.task_tracker import TaskRecord
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
    reason: str | None = None
    args: dict[str, Any] | None = None

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
        self.reason = self.reason.strip() if self.reason and self.reason.strip() else None
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
    """Client for the Compile Server session protocol."""

    def __init__(self, endpoint: _CompileEndpoint) -> None:
        self._endpoint = endpoint

    def _headers(
        self,
        connection: Mapping[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
        }
        if self._endpoint.gateway_token:
            headers["X-Gateway-Token"] = self._endpoint.gateway_token
        api_key = str(connection.get("api_key") or "").strip()
        if api_key:
            headers["X-API-Key"] = api_key
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
            "/bot/v1/compile",
            json=payload,
            headers=self._headers(connection, idempotency_key=idempotency_key),
        )
        return self._validate(CompileSessionAccepted, response)

    async def get(
        self,
        session_id: str,
        *,
        connection: Mapping[str, Any],
    ) -> CompileSessionStatus:
        body = await self._request(
            "POST",
            "/compile/status",
            json={"session_id": session_id},
            headers=self._headers(connection),
        )
        return self._validate(CompileSessionStatus, body)

    async def cancel(
        self,
        session_id: str,
        *,
        connection: Mapping[str, Any],
    ) -> CompileSessionStatus:
        body = await self._request(
            "POST",
            "/compile/cancel",
            json={"session_id": session_id},
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
            raise ExternalTaskError("UNAVAILABLE", str(exc), transient=True) from exc

        try:
            body = response.json()
        except ValueError as exc:
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
    runtime_timeout_seconds = 60 * 60

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

    def configure_local_backend(self, base_url: str, gateway_token: str) -> None:
        if self._config.base_url:
            return
        self._local_endpoint = _CompileEndpoint(
            base_url=base_url.rstrip("/"),
            gateway_token=gateway_token,
            http_timeout_seconds=10.0,
            poll_interval_ms=3000,
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

    async def create(
        self,
        request: CompileRequest,
        *,
        connection: Mapping[str, Any],
        ctx: RequestContext,
    ) -> TaskRecord:
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
        )

    async def submit(
        self,
        ov_task_id: str,
        payload: Mapping[str, Any],
        private_payload: Mapping[str, Any],
        connection: Mapping[str, Any],
    ) -> str:
        request_payload = dict(payload)
        public_args = request_payload.get("args")
        private_args = private_payload.get("args")
        if isinstance(public_args, dict) or isinstance(private_args, dict):
            request_payload["args"] = {
                **(public_args if isinstance(public_args, dict) else {}),
                **(private_args if isinstance(private_args, dict) else {}),
            }
        accepted = await self._client().create(
            request_payload,
            connection=connection,
            idempotency_key=ov_task_id,
        )
        return accepted.session_id

    async def get(
        self,
        external_task_id: str,
        connection: Mapping[str, Any],
    ) -> ExternalTaskSnapshot:
        return self._snapshot(await self._client().get(external_task_id, connection=connection))

    async def cancel(
        self,
        external_task_id: str,
        connection: Mapping[str, Any],
    ) -> ExternalTaskSnapshot:
        return self._snapshot(await self._client().cancel(external_task_id, connection=connection))

    async def _normalize_request(
        self,
        request: CompileRequest,
        ctx: RequestContext,
    ) -> CompileRequest:
        sources: list[str] = []
        for index, source in enumerate(request.from_):
            uri = validate_request_viking_uri(
                resolve_path_variables(source),
                ctx,
                field_name=f"from[{index}]",
            ).rstrip("/")
            stat = await self._fs.stat(uri, ctx)
            if not stat.get("isDir"):
                raise InvalidArgumentError(f"Compile source must be a directory: {uri}")
            canonical = str(stat.get("uri") or uri).rstrip("/")
            if canonical not in sources:
                sources.append(canonical)

        skill = request.skill
        if skill.endswith("/SKILL.md"):
            skill = skill[: -len("/SKILL.md")]
        skill = validate_request_viking_uri(
            resolve_path_variables(skill),
            ctx,
            field_name="skill",
        ).rstrip("/")
        if not classify_uri(skill).is_skill_root:
            raise InvalidArgumentError("skill must resolve to a Skill directory or SKILL.md")
        skill_stat = await self._fs.stat(skill, ctx)
        if not skill_stat.get("isDir"):
            raise InvalidArgumentError("skill must resolve to a Skill directory or SKILL.md")
        skill = str(skill_stat.get("uri") or skill).rstrip("/")
        skill_file = await self._fs.stat(f"{skill}/SKILL.md", ctx)
        if skill_file.get("isDir"):
            raise InvalidArgumentError("Skill directory must contain a SKILL.md file")

        target = validate_request_viking_uri(
            resolve_path_variables(request.to),
            ctx,
            field_name="to",
        ).rstrip("/")
        self._validate_target(target)
        await self._fs.ensure_write_access(target, ctx)
        try:
            target_stat = await self._fs.stat(target, ctx)
        except NotFoundError:
            pass
        else:
            if not target_stat.get("isDir"):
                raise InvalidArgumentError("Compile target must be a directory")
            target = str(target_stat.get("uri") or target).rstrip("/")

        return request.model_copy(
            update={"from_": sources, "to": target, "skill": skill},
        )

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
        if not isinstance(args, dict) or "user_key" not in args:
            return payload, {}
        private_payload = {"args": {"user_key": args.pop("user_key")}}
        if not args:
            payload.pop("args", None)
        return payload, private_payload

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
