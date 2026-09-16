"""Root-managed, account-scoped Studio bot administration."""

import os

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from openviking.server.auth import get_api_key_manager_or_raise, get_request_context
from openviking.server.config import get_server_url_from_server_data
from openviking.server.identity import RequestContext, Role
from openviking.server.routers import bot

router = APIRouter()


async def manager(ctx: RequestContext = Depends(get_request_context)):
    # App installations are process-wide. Account admins cannot mutate host credentials.
    if ctx.role != Role.ROOT:
        raise HTTPException(403, "Only the server administrator can manage Bot connections")
    return ctx


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


@router.get("/capabilities")
async def capabilities(ctx: RequestContext = Depends(get_request_context)):
    return {
        "status": "ok",
        "result": {
            "enabled": bot.BOT_API_URL is not None,
            "can_manage": ctx.role == Role.ROOT
            and bool(os.environ.get("OPENVIKING_BOT_STUDIO_TOKEN") or bot.BOT_API_KEY),
            "channels": ["feishu"],
        },
    }


@router.get("/connections")
async def connections(ctx: RequestContext = Depends(manager)):
    return await dispatch(ctx, "list")


async def account_users(request, ctx):
    registry = get_api_key_manager_or_raise(request)
    await registry.refresh_account_users_from_store(ctx.account_id)
    return registry.get_users(ctx.account_id, limit=None, role_filter="user", expose_key=True)


@router.get("/users")
async def users(request: Request, ctx: RequestContext = Depends(manager)):
    rows = await account_users(request, ctx)
    return {
        "status": "ok",
        "result": [
            {"user_id": row["user_id"], "available": bool(row.get("api_key"))} for row in rows
        ],
    }


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


@router.post("/connections")
async def create(request: Request, ctx: RequestContext = Depends(manager)):
    body = await request.json()
    connection = await selected_identity(request, ctx, body.get("user_id"))
    return await dispatch(ctx, "create", body, identity=connection)


@router.patch("/connections/{connection_id}")
async def update(connection_id: str, request: Request, ctx: RequestContext = Depends(manager)):
    body = await request.json()
    body.pop("identity", None)
    if body.get("action") == "credentials":
        user_id = body.pop("user_id", None)
        body["identity"] = await selected_identity(request, ctx, user_id)
    return await dispatch(ctx, "update", body, connection_id)


@router.get("/connections/{connection_id}/conversations")
async def conversations(connection_id: str, ctx: RequestContext = Depends(manager)):
    return await dispatch(ctx, "conversations", connection_id=connection_id)


@router.get("/connections/{connection_id}/messages")
async def messages(
    connection_id: str, conversation: str, before: int = 0, ctx: RequestContext = Depends(manager)
):
    return await dispatch(
        ctx, "messages", {"conversation": conversation, "before": before}, connection_id
    )


@router.post("/onboarding")
async def start_onboarding(request: Request, ctx: RequestContext = Depends(manager)):
    body = await request.json()
    identity = await selected_identity(request, ctx, body.get("user_id"))
    return await dispatch(ctx, "onboarding_start", body, identity=identity)


@router.get("/onboarding")
async def current_onboarding(type: str = "feishu", ctx: RequestContext = Depends(manager)):
    return await dispatch(ctx, "onboarding_current", {"type": type})


@router.get("/onboarding/{identifier}")
async def get_onboarding(identifier: str, ctx: RequestContext = Depends(manager)):
    return await dispatch(ctx, "onboarding_get", {"id": identifier})


@router.patch("/onboarding/{identifier}")
async def update_onboarding(
    identifier: str, request: Request, ctx: RequestContext = Depends(manager)
):
    body = await request.json()
    return await dispatch(
        ctx, "onboarding_update", {"id": identifier, "action": body.get("action")}
    )
