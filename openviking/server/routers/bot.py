"""Bot API router for proxying requests to Vikingbot OpenAPIChannel.

This router provides endpoints for the Bot API that proxy requests to the
Vikingbot OpenAPIChannel when the --with-bot option is enabled.
"""

import json
from typing import AsyncGenerator, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from openviking.server.auth import get_request_context
from openviking.server.identity import RequestContext
from openviking_cli.utils.logger import get_logger

router = APIRouter(prefix="", tags=["bot"])

logger = get_logger(__name__)

# Bot API configuration - set when --with-bot is enabled
BOT_API_URL: Optional[str] = None  # e.g., "http://localhost:18791"


def set_bot_api_url(url: str) -> None:
    """Set the Bot API URL. Called by app.py when --with-bot is enabled."""
    global BOT_API_URL
    BOT_API_URL = url


def get_bot_url() -> str:
    """Get the Bot API URL, raising 503 if not configured."""
    if BOT_API_URL is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Bot service not enabled. Start server with --with-bot option.",
        )
    return BOT_API_URL


@router.get("/health")
async def health_check(request: Request):
    """Health check endpoint for Bot API.

    Returns 503 if --with-bot is not enabled.
    Proxies to Vikingbot health check if enabled.
    """
    bot_url = get_bot_url()

    try:
        async with httpx.AsyncClient() as client:
            print(f"url={f'{bot_url}/bot/v1/health'}")
            # Forward to Vikingbot OpenAPIChannel health endpoint
            response = await client.get(
                f"{bot_url}/bot/v1/health",
                timeout=5.0,
            )
            response.raise_for_status()
            return response.json()
    except httpx.RequestError as e:
        logger.error(f"Failed to connect to bot service at {bot_url}: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Bot service unavailable: {str(e)}",
        )
    except httpx.HTTPStatusError as e:
        logger.error(f"Bot service returned error: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Bot service error: {e.response.text}",
        )


def extract_auth_token(request: Request) -> Optional[str]:
    """Extract and return authorization token from request."""
    # Try X-API-Key header first
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return api_key

    # Try Authorization header (Bearer token)
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header[7:]  # Remove "Bearer " prefix

    return None


@router.post("/chat")
async def chat(
    request: Request,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Send a message to the bot and get a response.

    Proxies the request to Vikingbot OpenAPIChannel.
    """
    bot_url = get_bot_url()
    auth_token = extract_auth_token(request)

    # Read request body
    try:
        body = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON in request body",
        )

    try:
        async with httpx.AsyncClient() as client:
            # Build headers - only include X-API-Key if provided
            headers = {"Content-Type": "application/json"}
            if auth_token:
                headers["X-API-Key"] = auth_token

            # Forward to Vikingbot OpenAPIChannel chat endpoint
            response = await client.post(
                f"{bot_url}/bot/v1/chat",
                json=body,
                headers=headers,
                timeout=300.0,  # 5 minute timeout for chat
            )
            response.raise_for_status()
            return response.json()
    except httpx.RequestError as e:
        logger.error(f"Failed to connect to bot service: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Bot service unavailable: {str(e)}",
        )
    except httpx.HTTPStatusError as e:
        logger.error(f"Bot service returned error: {e}")
        # Forward the status code if it's a client error
        if e.response.status_code < 500:
            raise HTTPException(
                status_code=e.response.status_code,
                detail=e.response.text,
            )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Bot service error: {e.response.text}",
        )


@router.post("/chat/stream")
async def chat_stream(
    request: Request,
    _ctx: RequestContext = Depends(get_request_context),
):
    """Send a message to the bot and get a streaming response.

    Proxies the request to Vikingbot OpenAPIChannel with SSE streaming.
    """
    bot_url = get_bot_url()
    auth_token = extract_auth_token(request)

    # Read request body
    try:
        body = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON in request body",
        )

    async def event_stream() -> AsyncGenerator[str, None]:
        """Generate SSE events from bot response stream."""
        try:
            async with httpx.AsyncClient() as client:
                # Build headers - only include X-API-Key if provided
                headers = {"Content-Type": "application/json"}
                if auth_token:
                    headers["X-API-Key"] = auth_token

                # Forward to Vikingbot OpenAPIChannel stream endpoint
                async with client.stream(
                    "POST",
                    f"{bot_url}/bot/v1/chat/stream",
                    json=body,
                    headers=headers,
                    timeout=300.0,
                ) as response:
                    response.raise_for_status()

                    # Stream the response content
                    async for line in response.aiter_lines():
                        if line:
                            # Forward the SSE line as-is
                            yield f"{line}\n"
        except httpx.RequestError as e:
            logger.error(f"Failed to connect to bot service: {e}")
            error_event = {
                "event": "error",
                "data": json.dumps({"error": f"Bot service unavailable: {str(e)}"}),
            }
            yield f"data: {json.dumps(error_event)}\n\n"
        except httpx.HTTPStatusError as e:
            logger.error(f"Bot service returned error: {e}")
            error_event = {
                "event": "error",
                "data": json.dumps({"error": f"Bot service error: {e.response.text}"}),
            }
            yield f"data: {json.dumps(error_event)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
