"""Private gateway endpoints, callable only by the authenticated server proxy."""

import os
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Request


def create_router(channel, service):
    async def authorize(request: Request, x_gateway_token: str = Header(default="")):
        token = os.environ.get("OPENVIKING_BOT_STUDIO_TOKEN") or channel._gateway_token()
        if not (
            token
            and secrets.compare_digest(token, x_gateway_token)
            and channel._is_loopback_request(request)
        ):
            raise HTTPException(403, "Studio management requires the internal gateway token")

    router = APIRouter(prefix="/studio", dependencies=[Depends(authorize)], include_in_schema=False)

    @router.post("/dispatch")
    async def dispatch(request: Request):
        body = await request.json()
        account = body["account"]
        action = body["action"]
        payload = body.get("payload", {})
        if action == "onboarding_start":
            return await service.onboarding.start(account, payload, body["identity"])
        if action == "onboarding_current":
            return service.onboarding.current(account, payload.get("type", "feishu"))
        if action == "onboarding_get":
            return service.onboarding.public(service.onboarding.get(account, payload.get("id")))
        if action == "onboarding_update":
            return await service.onboarding.update(
                account, payload.get("id"), payload.get("action")
            )
        if action == "list":
            return [service.public(r) for r in service.store.connections(account)]
        if action == "create":
            return await service.create(account, payload, body["identity"])
        record = service.get(account, body.get("connection_id"))
        if action == "update":
            return await service.update(account, record["id"], payload)
        if action == "conversations":
            return service.store.conversations(record["id"])
        if action == "messages":
            before = max(0, int(payload.get("before", 0)))
            runtime = service.runtime(record)
            if runtime and hasattr(runtime, "history_with_names"):
                return await runtime.history_with_names(payload.get("conversation", ""), before)
            return service.store.history(record["id"], payload.get("conversation", ""), before)
        raise HTTPException(400, "Unknown operation")

    return router
