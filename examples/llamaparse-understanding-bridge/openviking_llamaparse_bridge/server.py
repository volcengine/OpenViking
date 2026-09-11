# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Understanding API-compatible HTTP server backed by LlamaParse v2."""

from __future__ import annotations

import asyncio
import hmac
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional, Tuple

from fastapi import Depends, FastAPI, Form, Header, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from .artifacts import ArtifactCache, ArtifactSigner, build_artifact
from .config import Settings, load_settings
from .llamaparse import LlamaParseClient, LlamaParseError

MAX_CONCURRENT_ARTIFACT_BUILDS = 2


class BridgeError(RuntimeError):
    """A client-facing bridge error."""

    def __init__(self, status_code: int, code: str, message: str):
        self.status_code = status_code
        self.code = code
        super().__init__(message)


def _source_from_request(payload: Dict[str, Any]) -> Tuple[str, str]:
    inputs = payload.get("input")
    if not isinstance(inputs, list) or len(inputs) != 1 or not isinstance(inputs[0], dict):
        raise BridgeError(400, "invalid_request", "input must contain one message")
    content = inputs[0].get("content")
    if not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], dict):
        raise BridgeError(400, "invalid_request", "input message must contain one item")

    item = content[0]
    content_type = item.get("type")
    if content_type == "file":
        file_object = item.get("file")
        file_id = file_object.get("file_id") if isinstance(file_object, dict) else None
        if isinstance(file_id, str) and file_id.strip():
            return "file_id", file_id.strip()
        raise BridgeError(400, "invalid_request", "file.file_id is required")
    url_fields = {
        "input_file": "file_url",
        "input_image": "image_url",
        "input_audio": "audio_url",
    }
    url_field = url_fields.get(content_type) if isinstance(content_type, str) else None
    if url_field is not None:
        source_url = item.get(url_field)
        if isinstance(source_url, str) and source_url.startswith(("http://", "https://")):
            return "source_url", source_url.strip()
        raise BridgeError(
            400,
            "invalid_request",
            f"{content_type}.{url_field} must be an HTTP URL",
        )
    raise BridgeError(400, "unsupported_input", f"unsupported input type: {content_type}")


def _job_status(payload: Dict[str, Any]) -> Tuple[str, str]:
    job = payload.get("job")
    if not isinstance(job, dict):
        raise LlamaParseError(502, "LlamaParse response has no job object")
    status = str(job.get("status", "")).upper()
    return status, str(job.get("error_message") or f"LlamaParse job ended as {status}")


async def _bridge_error_handler(_: Request, error: Exception) -> JSONResponse:
    if not isinstance(error, BridgeError):
        raise error
    return JSONResponse(
        status_code=error.status_code,
        content={"error": {"code": error.code, "message": str(error)}},
    )


async def _llamaparse_error_handler(_: Request, error: Exception) -> JSONResponse:
    if not isinstance(error, LlamaParseError):
        raise error
    return JSONResponse(
        status_code=error.status_code,
        content={
            "error": {
                "code": f"llamaparse_http_{error.status_code}",
                "message": str(error),
            }
        },
    )


class BridgeRoutes:
    """Implement the HTTP routes for one configured LlamaParse client."""

    def __init__(self, settings: Settings, client: LlamaParseClient, owns_client: bool):
        self.settings = settings
        self.client = client
        self.owns_client = owns_client
        self.signer = ArtifactSigner(settings.bridge_api_key, settings.artifact_ttl_seconds)
        self.artifacts = ArtifactCache(
            settings.artifact_cache_dir,
            settings.artifact_ttl_seconds,
            settings.artifact_cache_max_bytes,
        )
        self._artifact_tasks: Dict[str, asyncio.Task[None]] = {}
        # Limit concurrent ZIP memory and CPU use across completed jobs.
        self._artifact_builds = asyncio.Semaphore(MAX_CONCURRENT_ARTIFACT_BUILDS)

    async def _prepare_artifact(self, job_id: str) -> None:
        async with self._artifact_builds:
            content = await build_artifact(self.client, job_id)
            await self.artifacts.store(job_id, content)

    def _artifact_task_done(self, job_id: str, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is None and self._artifact_tasks.get(job_id) is task:
            self._artifact_tasks.pop(job_id, None)

    async def _artifact_is_ready(self, job_id: str) -> bool:
        if await self.artifacts.contains(job_id):
            self._artifact_tasks.pop(job_id, None)
            return True

        task = self._artifact_tasks.get(job_id)
        if task is None:
            task = asyncio.create_task(
                self._prepare_artifact(job_id), name=f"prepare-artifact-{job_id}"
            )
            task.add_done_callback(lambda completed: self._artifact_task_done(job_id, completed))
            self._artifact_tasks[job_id] = task
            return False
        if not task.done():
            return False

        self._artifact_tasks.pop(job_id, None)
        await task
        return await self.artifacts.contains(job_id)

    async def _close_artifact_tasks(self) -> None:
        tasks = list(self._artifact_tasks.values())
        self._artifact_tasks.clear()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    @asynccontextmanager
    async def lifespan(self, _: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await self._close_artifact_tasks()
            if self.owns_client:
                await self.client.aclose()

    async def require_auth(self, authorization: Optional[str] = Header(default=None)) -> None:
        expected = f"Bearer {self.settings.bridge_api_key}"
        supplied = (authorization or "").encode("utf-8")
        if not hmac.compare_digest(supplied, expected.encode("utf-8")):
            raise BridgeError(401, "unauthorized", "invalid bridge API key")

    async def health(self) -> Dict[str, Any]:
        return {
            "status": "ok",
            "provider": "llamaparse-v2",
            "tier": self.settings.tier,
            "cost_optimizer": self.settings.cost_optimizer,
        }

    async def create_file(
        self, file: UploadFile, purpose: str = Form(default="user_data")
    ) -> Dict[str, Any]:
        del purpose  # The bridge always uses LlamaCloud's parse retention policy.
        filename = Path((file.filename or "upload").replace("\\", "/")).name
        if filename in {"", ".", ".."}:
            filename = "upload"
        result = await self.client.upload_file(filename, file.file, file.content_type)
        if not isinstance(result.get("id"), str) or not result["id"]:
            raise LlamaParseError(502, "LlamaParse file response has no id")
        return result

    async def create_response(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        source_name, source_value = _source_from_request(payload)
        if source_name == "file_id":
            result = await self.client.create_job(file_id=source_value)
        else:
            result = await self.client.create_job(source_url=source_value)
        response_id = result.get("id")
        if not isinstance(response_id, str) or not response_id:
            raise LlamaParseError(502, "LlamaParse create response has no id")
        return {
            "id": response_id,
            "object": "response",
            "status": "in_progress",
        }

    async def get_response(self, response_id: str) -> Dict[str, Any]:
        result = await self.client.get_job(response_id)
        status, error_message = _job_status(result)
        response: Dict[str, Any] = {"id": response_id, "object": "response"}
        if status == "COMPLETED":
            if await self._artifact_is_ready(response_id):
                response["status"] = "completed"
                response["result"] = {
                    "zip_url": self.signer.create_url(self.settings.public_url, response_id)
                }
            else:
                response["status"] = "in_progress"
        elif status in {"FAILED", "CANCELLED"}:
            response["status"] = "failed"
            response["output"] = [{"content": [{"type": "output_text", "text": error_message}]}]
        elif status in {"PENDING", "RUNNING"}:
            response["status"] = "in_progress"
        else:
            raise LlamaParseError(502, f"LlamaParse returned unknown job status: {status}")
        return response

    async def get_artifact(
        self, job_id: str, expires: str = Query(...), signature: str = Query(...)
    ) -> Response:
        if not self.signer.verify(job_id, expires, signature):
            raise BridgeError(
                403, "invalid_artifact_signature", "artifact URL is invalid or expired"
            )
        content = await self.artifacts.read(job_id)
        if content is None:
            raise BridgeError(404, "artifact_not_found", "artifact is not ready or has expired")
        return Response(
            content=content,
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="result.zip"'},
        )


def create_app(
    settings: Optional[Settings] = None, client: Optional[LlamaParseClient] = None
) -> FastAPI:
    """Create the bridge application. Tests can inject settings and a client."""
    resolved = settings or load_settings()
    routes = BridgeRoutes(resolved, client or LlamaParseClient(resolved), client is None)
    app = FastAPI(
        title="OpenViking LlamaParse bridge",
        version="0.1.0",
        description="LlamaParse v2 adapter for the OpenViking Understanding API.",
        lifespan=routes.lifespan,
    )
    app.add_exception_handler(BridgeError, _bridge_error_handler)
    app.add_exception_handler(LlamaParseError, _llamaparse_error_handler)
    auth = [Depends(routes.require_auth)]
    app.add_api_route("/health", routes.health, methods=["GET"], tags=["meta"])
    app.add_api_route("/api/v3/files", routes.create_file, methods=["POST"], dependencies=auth)
    app.add_api_route(
        "/api/v3/responses", routes.create_response, methods=["POST"], dependencies=auth
    )
    app.add_api_route(
        "/api/v3/responses/{response_id}",
        routes.get_response,
        methods=["GET"],
        dependencies=auth,
    )
    app.add_api_route("/artifacts/{job_id}.zip", routes.get_artifact, methods=["GET"])

    return app
