"""Root-managed, account-scoped Studio bot administration."""

import os
from dataclasses import replace
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from openviking.server.auth import get_api_key_manager_or_raise, get_request_context
from openviking.server.config import get_server_url_from_server_data
from openviking.server.identity import RequestContext, Role
from openviking.server.routers import bot
from openviking_cli.session.user_id import UserIdentifier

# Studio's internal UI contract is not part of the public OpenAPI schema.
router = APIRouter(prefix="/api/v1/admin", tags=["admin"], include_in_schema=False)
ACCOUNT_BOT = "/accounts/{account_id}/bot"


class RevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)


class UpdateConnectionRequest(RevisionRequest):
    enabled: bool | None = None
    settings: dict | None = None

    @model_validator(mode="after")
    def one_change(self):
        if (self.enabled is None) == (self.settings is None):
            raise ValueError("Provide either enabled or settings")
        return self


class CredentialsRequest(RevisionRequest):
    credentials: dict[str, str]
    user_id: str


class CreateConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    user_id: str
    credentials: dict[str, str]
    settings: dict = Field(default_factory=dict)


class OnboardingActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["retry", "cancel", "manual"]


class StartOnboardingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    user_id: str
    name: str = "VikingBot"
    request_id: str
    settings: dict = Field(default_factory=dict)


async def manager(
    account_id: str,
    ctx: RequestContext = Depends(get_request_context),
):
    # App installations are process-wide. Account admins cannot mutate host credentials.
    if ctx.role != Role.ROOT:
        raise HTTPException(403, "Only the server administrator can manage Bot connections")
    try:
        return replace(ctx, user=UserIdentifier(account_id, ctx.user.user_id))
    except ValueError as exc:
        raise HTTPException(400, "Invalid account ID") from exc


async def dispatch(ctx, action, payload=None, connection_id=None, identity=None):
    token = os.environ.get("OPENVIKING_BOT_STUDIO_TOKEN") or bot.BOT_API_KEY
    if not token:
        raise HTTPException(503, "Managed Bot gateway authentication is unavailable")
    try:
        async with bot._create_bot_proxy_client() as client:
            result = await client.post(
                bot.get_bot_url() + "/bot/v1/studio/dispatch",
                headers={"X-Gateway-Token": token},
                timeout=25,
                json={
                    "account": ctx.account_id,
                    "action": action,
                    "payload": payload or {},
                    "connection_id": connection_id,
                    "identity": identity,
                },
            )
        data = result.json()
        if result.is_error:
            raise HTTPException(result.status_code, data.get("detail", "Bot operation failed"))
        return {"status": "ok", "result": data}
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, "Cannot reach the managed Bot gateway") from exc


@router.get("/bot/capabilities")
async def capabilities(ctx: RequestContext = Depends(get_request_context)):
    return {
        "status": "ok",
        "result": {
            "enabled": bot.BOT_API_URL is not None,
            "can_manage": ctx.role == Role.ROOT
            and bool(os.environ.get("OPENVIKING_BOT_STUDIO_TOKEN") or bot.BOT_API_KEY),
        },
    }


@router.get(ACCOUNT_BOT + "/connections")
async def connections(ctx: RequestContext = Depends(manager)):
    return await dispatch(ctx, "list")


async def account_users(request, ctx):
    registry = get_api_key_manager_or_raise(request)
    await registry.refresh_account_users_from_store(ctx.account_id)
    return registry.get_users(ctx.account_id, limit=None, role_filter="user", expose_key=True)


async def selected_identity(request, ctx, user_id):
    rows = await account_users(request, ctx)
    row = next((row for row in rows if row["user_id"] == user_id), None)
    if row is None:
        raise HTTPException(400, "Select an ordinary user in the current account")
    if not row.get("api_key"):
        raise HTTPException(409, "This user's credential cannot be bound automatically")
    return {
        "account_id": ctx.account_id,
        "user_id": row["user_id"],
        "role": "user",
        "api_key_type": "user",
        "api_key": row["api_key"],
        "agent_id": "vikingbot",
        "server_url": get_server_url_from_server_data(getattr(request.app.state, "config", None)),
    }


@router.post(ACCOUNT_BOT + "/connections")
async def create(
    request: Request, body: CreateConnectionRequest, ctx: RequestContext = Depends(manager)
):
    connection = await selected_identity(request, ctx, body.user_id)
    return await dispatch(ctx, "create", body.model_dump(), identity=connection)


@router.patch(ACCOUNT_BOT + "/connections/{connection_id}")
async def update(
    connection_id: str, body: UpdateConnectionRequest, ctx: RequestContext = Depends(manager)
):
    return await dispatch(
        ctx,
        "update",
        {
            "action": "settings"
            if body.settings is not None
            else ("resume" if body.enabled else "pause"),
            "revision": body.revision,
            **({"settings": body.settings} if body.settings is not None else {}),
        },
        connection_id,
    )


@router.delete(ACCOUNT_BOT + "/connections/{connection_id}")
async def delete_connection(
    connection_id: str,
    revision: int = Query(..., ge=1),
    ctx: RequestContext = Depends(manager),
):
    return await dispatch(ctx, "update", {"action": "delete", "revision": revision}, connection_id)


@router.post(ACCOUNT_BOT + "/connections/{connection_id}/credentials")
async def update_credentials(
    connection_id: str,
    body: CredentialsRequest,
    request: Request,
    ctx: RequestContext = Depends(manager),
):
    identity = await selected_identity(request, ctx, body.user_id)
    return await dispatch(
        ctx,
        "update",
        {
            "action": "credentials",
            "revision": body.revision,
            "credentials": body.credentials,
            "identity": identity,
        },
        connection_id,
    )


@router.post(ACCOUNT_BOT + "/connections/{connection_id}/verifications")
async def verify_connection(
    connection_id: str, body: RevisionRequest, ctx: RequestContext = Depends(manager)
):
    return await dispatch(
        ctx,
        "update",
        {
            "action": "verify",
            "revision": body.revision,
        },
        connection_id,
    )


@router.get(ACCOUNT_BOT + "/connections/{connection_id}/conversations")
async def conversations(connection_id: str, ctx: RequestContext = Depends(manager)):
    return await dispatch(ctx, "conversations", connection_id=connection_id)


@router.get(ACCOUNT_BOT + "/connections/{connection_id}/messages")
async def messages(
    connection_id: str, conversation: str, before: int = 0, ctx: RequestContext = Depends(manager)
):
    return await dispatch(
        ctx, "messages", {"conversation": conversation, "before": before}, connection_id
    )


@router.post(ACCOUNT_BOT + "/onboarding-runs")
async def start_onboarding(
    request: Request, body: StartOnboardingRequest, ctx: RequestContext = Depends(manager)
):
    identity = await selected_identity(request, ctx, body.user_id)
    return await dispatch(ctx, "onboarding_start", body.model_dump(), identity=identity)


@router.get(ACCOUNT_BOT + "/onboarding-runs/current")
async def current_onboarding(type: str, ctx: RequestContext = Depends(manager)):
    return await dispatch(ctx, "onboarding_current", {"type": type})


@router.get(ACCOUNT_BOT + "/onboarding-runs/{identifier}")
async def get_onboarding(identifier: str, ctx: RequestContext = Depends(manager)):
    return await dispatch(ctx, "onboarding_get", {"id": identifier})


@router.post(ACCOUNT_BOT + "/onboarding-runs/{identifier}/actions")
async def update_onboarding(
    identifier: str, body: OnboardingActionRequest, ctx: RequestContext = Depends(manager)
):
    return await dispatch(ctx, "onboarding_update", {"id": identifier, "action": body.action})
