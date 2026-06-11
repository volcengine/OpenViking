# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Feishu user-token state for watch tasks."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

FEISHU_AUTH_PROVIDER = "feishu"
FEISHU_ACCESS_TOKEN_ARG = "feishu_access_token"
FEISHU_REFRESH_TOKEN_ARG = "feishu_refresh_token"
FEISHU_REFRESH_GRANT_TYPE = "refresh_token"
FEISHU_REFRESH_SKEW = timedelta(minutes=5)


@dataclass(frozen=True)
class FeishuAppCredentials:
    app_id: str
    app_secret: str
    domain: str
    request_timeout: float


@dataclass(frozen=True)
class FeishuRefreshedToken:
    access_token: str
    refresh_token: str
    expires_in: int


class FeishuTokenRefreshError(Exception):
    """Raised when a Feishu user token refresh fails."""

    def __init__(self, message: str, *, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent


def load_feishu_app_credentials() -> FeishuAppCredentials:
    """Load Feishu app credentials from OpenViking config or environment."""
    from openviking_cli.utils.config import get_openviking_config

    config = get_openviking_config().feishu
    app_id = (config.app_id or os.getenv("FEISHU_APP_ID", "")).strip()
    app_secret = (config.app_secret or os.getenv("FEISHU_APP_SECRET", "")).strip()
    if not app_id or not app_secret:
        raise ValueError(
            "Feishu credentials not configured. Set FEISHU_APP_ID and "
            "FEISHU_APP_SECRET environment variables, or configure in ov.conf."
        )
    return FeishuAppCredentials(
        app_id=app_id,
        app_secret=app_secret,
        domain=(config.domain or "https://open.feishu.cn").strip(),
        request_timeout=float(config.request_timeout or 30.0),
    )


def create_feishu_auth_state(access_token: str, refresh_token: str) -> Dict[str, Any]:
    """Create the private watch auth state for Feishu user-token watch tasks."""
    return {
        "provider": FEISHU_AUTH_PROVIDER,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": None,
    }


def is_feishu_auth_state(auth_state: Optional[Dict[str, Any]]) -> bool:
    return isinstance(auth_state, dict) and auth_state.get("provider") == FEISHU_AUTH_PROVIDER


def feishu_auth_state_needs_refresh(
    auth_state: Dict[str, Any],
    *,
    now: Optional[datetime] = None,
) -> bool:
    expires_at = auth_state.get("expires_at")
    if not expires_at:
        return True

    parsed = _parse_expires_at(expires_at)
    if parsed is None:
        return True

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return parsed <= current + FEISHU_REFRESH_SKEW


def apply_feishu_refreshed_token(
    auth_state: Dict[str, Any],
    refreshed: FeishuRefreshedToken,
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    expires_at = current + timedelta(seconds=max(0, int(refreshed.expires_in)))
    return {
        **auth_state,
        "provider": FEISHU_AUTH_PROVIDER,
        "access_token": refreshed.access_token,
        "refresh_token": refreshed.refresh_token,
        "expires_at": expires_at.isoformat(),
    }


class FeishuOAuthClient:
    """Small wrapper around lark-oapi user token refresh."""

    def __init__(self, credentials: FeishuAppCredentials):
        self._credentials = credentials
        self._client = None

    @classmethod
    def from_config(cls) -> "FeishuOAuthClient":
        return cls(load_feishu_app_credentials())

    async def refresh_user_access_token(self, refresh_token: str) -> FeishuRefreshedToken:
        if not isinstance(refresh_token, str) or not refresh_token.strip():
            raise FeishuTokenRefreshError(
                "Feishu refresh token is missing for watch task.",
                permanent=True,
            )
        return await asyncio.to_thread(self._refresh_user_access_token_sync, refresh_token.strip())

    def _refresh_user_access_token_sync(self, refresh_token: str) -> FeishuRefreshedToken:
        try:
            from lark_oapi.api.authen.v1 import (
                CreateRefreshAccessTokenRequest,
                CreateRefreshAccessTokenRequestBody,
            )
        except ImportError as exc:
            raise FeishuTokenRefreshError(
                "lark-oapi is required to refresh Feishu user tokens. "
                "Install it with: pip install 'openviking[bot-feishu]'",
                permanent=True,
            ) from exc

        request = (
            CreateRefreshAccessTokenRequest.builder()
            .request_body(
                CreateRefreshAccessTokenRequestBody.builder()
                .grant_type(FEISHU_REFRESH_GRANT_TYPE)
                .refresh_token(refresh_token)
                .build()
            )
            .build()
        )

        try:
            response = self._get_client().authen.v1.refresh_access_token.create(request)
        except FeishuTokenRefreshError:
            raise
        except Exception as exc:
            raise FeishuTokenRefreshError(
                f"Failed to refresh Feishu user token: {exc}",
                permanent=False,
            ) from exc

        if not response.success():
            code = getattr(response, "code", None)
            msg = getattr(response, "msg", "") or ""
            raise FeishuTokenRefreshError(
                f"Feishu user token refresh failed: code={code}, msg={msg}",
                permanent=_is_permanent_refresh_error(code, msg),
            )

        data = getattr(response, "data", None)
        access_token = (getattr(data, "access_token", None) or "").strip()
        new_refresh_token = (getattr(data, "refresh_token", None) or refresh_token).strip()
        expires_in = getattr(data, "expires_in", None)
        if not access_token or not new_refresh_token or not isinstance(expires_in, int):
            raise FeishuTokenRefreshError(
                "Feishu user token refresh response is missing access_token, "
                "refresh_token, or expires_in.",
                permanent=False,
            )

        return FeishuRefreshedToken(
            access_token=access_token,
            refresh_token=new_refresh_token,
            expires_in=expires_in,
        )

    def _get_client(self):
        if self._client is None:
            try:
                import lark_oapi as lark
            except ImportError as exc:
                raise FeishuTokenRefreshError(
                    "lark-oapi is required to refresh Feishu user tokens. "
                    "Install it with: pip install 'openviking[bot-feishu]'",
                    permanent=True,
                ) from exc

            self._client = (
                lark.Client.builder()
                .app_id(self._credentials.app_id)
                .app_secret(self._credentials.app_secret)
                .domain(self._credentials.domain)
                .timeout(self._credentials.request_timeout)
                .build()
            )
        return self._client


def _parse_expires_at(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _is_permanent_refresh_error(code: Any, msg: str) -> bool:
    text = f"{code or ''} {msg or ''}".lower()
    permanent_terms = (
        "invalid",
        "expired",
        "revoked",
        "unauthorized",
        "not exist",
        "not found",
        "mismatch",
        "refresh token",
        "refresh_token",
    )
    return any(term in text for term in permanent_terms)
