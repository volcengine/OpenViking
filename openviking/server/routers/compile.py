# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Compile task and routing embedding APIs."""

import asyncio
import math

from fastapi import APIRouter, Depends, Header, Path, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from openviking.core.path_variables import resolve_path_variables
from openviking.core.uri_validation import validate_request_viking_uri
from openviking.server.auth import get_request_context
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
from openviking.server.models import Response
from openviking.service.compile_service import CompileRequest
from openviking_cli.exceptions import InvalidArgumentError

router = APIRouter(prefix="/api/v1", tags=["compile"])


@router.post("/compile", status_code=status.HTTP_202_ACCEPTED)
async def create_compile(
    body: CompileRequest,
    ctx: RequestContext = Depends(get_request_context),
    idempotency_key: str | None = Header(
        None, min_length=16, max_length=128, pattern=r"^[A-Za-z0-9:._-]+$"
    ),
    x_request_id: str | None = Header(None),
):
    """Queue a Compile task with the authenticated caller's forwarding identity."""
    connection = {"api_key": ctx.api_key} if ctx.api_key else {}
    connection.update(account_id=ctx.account_id, user_id=ctx.user.user_id)
    if ctx.actor_peer_id:
        connection["actor_peer_id"] = ctx.actor_peer_id
    # Persist the submission's request ID for all asynchronous Runtime calls.
    if x_request_id:
        connection["request_id"] = x_request_id
    task = await get_service().compile.create(
        body,
        connection=connection,
        ctx=ctx,
        **({"idempotency_key": idempotency_key} if idempotency_key else {}),
    )
    return Response(status="ok", result=task.to_dict())


__all__ = ["router"]


@router.get("/compile/capabilities")
async def compile_capabilities(ctx: RequestContext = Depends(get_request_context)):
    return Response(status="ok", result=get_service().compile.capabilities(ctx))


@router.get("/compile/submissions/{key}")
async def get_submission(
    key: str = Path(..., min_length=16, max_length=128, pattern=r"^[A-Za-z0-9:._-]+$"),
    ctx: RequestContext = Depends(get_request_context),
):
    from openviking.service.external_task_service import submission_task_id
    from openviking.service.task_tracker import get_task_tracker
    from openviking_cli.exceptions import NotFoundError

    task_id = submission_task_id(ctx, "compile", "cmp_", key)
    task = await get_task_tracker().get(
        task_id, account_id=ctx.account_id, user_id=ctx.user.user_id
    )
    if task is None:
        raise NotFoundError(key, "submission")
    return Response(status="ok", result=task.to_dict())


class CompileEmbeddingRequest(BaseModel):
    """Transient Compile routing text; an empty batch requests model identity only."""

    model_config = ConfigDict(extra="forbid")
    target_uri: str
    texts: list[str] = Field(default_factory=list, max_length=32)
    expected_model: str | None = None

    @model_validator(mode="after")
    def bound_text(self):
        """Bound each routing description and the total before contacting the provider."""
        if any(not text.strip() or len(text) > 1024 for text in self.texts):
            raise ValueError("Compile embedding texts require 1..1024 characters")
        return self


@router.post("/compile/embeddings")
async def compile_embeddings(
    request: CompileEmbeddingRequest,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Embed task-local routing candidates without indexing or retaining their content.

    Target validation uses the same tenant identity and ACL checks as file reads.
    The configured embedder supplies provider concurrency/rate controls. Only model
    identity and vectors cross the Bot/Server boundary; credentials never do.
    """
    from openviking.core.namespace import classify_uri
    from openviking.models.embedder.base import embed_compat

    target = validate_request_viking_uri(resolve_path_variables(request.target_uri), _ctx)
    classification = classify_uri(target)
    if classification.context_type != "resource" and not classification.is_skill_namespace:
        raise InvalidArgumentError(
            "Compile embeddings require a resource or Skill namespace target"
        )
    fs = get_service().viking_fs
    await fs.stat(target, ctx=_ctx, skip_count=True)
    embedder = fs._get_embedder(_ctx)
    # The account's effective embedding settings identify task-local cached vectors.
    model = (await get_service().embedding_provider.get_status(_ctx.account_id)).fingerprint
    if request.expected_model and request.expected_model != model:
        raise InvalidArgumentError("Compile embedding model changed during the task")
    # Small chunks also bound providers whose embed_async implementation has no limiter.
    vectors = []
    for start in range(0, len(request.texts), 4):
        batch = await asyncio.gather(
            *(embed_compat(embedder, text) for text in request.texts[start : start + 4])
        )
        for result in batch:
            vector = result.dense_vector
            if not vector or len(vector) > 8192 or not all(math.isfinite(x) for x in vector):
                raise InvalidArgumentError("Compile requires finite dense embedding vectors")
            vectors.append(vector)
    return Response(status="ok", result={"model": model, "vectors": vectors}).model_dump()
