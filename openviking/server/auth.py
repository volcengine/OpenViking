# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""API Key authentication for OpenViking HTTP Server."""

import hmac
from typing import Optional

from fastapi import Header, Request

from openviking_cli.exceptions import UnauthenticatedError


async def verify_api_key(
    request: Request,
    x_api_key: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
) -> bool:
    """Verify API Key.

    Supports two ways to pass API Key:
    - X-API-Key: your-key
    - Authorization: Bearer your-key

    Authentication strategy:
    - If config.api_key is None, skip authentication (local dev mode)
    - Otherwise, verify the key in the request matches config.api_key

    Args:
        request: FastAPI request object
        x_api_key: API key from X-API-Key header
        authorization: API key from Authorization header

    Returns:
        True if authenticated

    Raises:
        HTTPException: If authentication fails
    """
    config_api_key = request.app.state.api_key

    # If no API key configured, skip authentication
    if config_api_key is None:
        return True

    # Extract API key from request
    request_api_key = x_api_key
    if not request_api_key and authorization:
        if authorization.startswith("Bearer "):
            request_api_key = authorization[7:]

    # Verify key
    if not request_api_key or not hmac.compare_digest(request_api_key, config_api_key):
        raise UnauthenticatedError("Invalid API Key")

    return True


def get_user_header(
    x_openviking_user: Optional[str] = Header(None, alias="X-OpenViking-User"),
) -> Optional[str]:
    """Get user from request header."""
    return x_openviking_user


def get_agent_header(
    x_openviking_agent: Optional[str] = Header(None, alias="X-OpenViking-Agent"),
) -> Optional[str]:
    """Get agent from request header."""
    return x_openviking_agent
