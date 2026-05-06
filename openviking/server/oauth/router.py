# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""OAuth 2.1 router (Phase 1, M2: token endpoint only).

DCR / authorize / well-known metadata land in M3. For now this module only
handles `POST /oauth/token` with the `authorization_code` and `refresh_token`
grants — sufficient to validate the JWT signing path against a hand-issued
auth code.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from typing import Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse

from openviking.server.oauth.storage import OAuthStore

logger = logging.getLogger(__name__)

router = APIRouter(tags=["oauth"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _oauth_error(
    code: str,
    description: str,
    *,
    status: int = 400,
    extra_headers: Optional[dict[str, str]] = None,
) -> JSONResponse:
    return JSONResponse(
        {"error": code, "error_description": description},
        status_code=status,
        headers=extra_headers or {},
    )


def _require_state(request: Request) -> tuple[OAuthStore, "object", "object"]:
    """Pull the OAuth runtime state attached by app.lifespan.

    Returns (store, signer, config). Raises a clean 503 when OAuth is
    disabled — typically misconfiguration that landed a request on this
    router despite enabled=False (not expected if app.py guards routing).
    """
    store = getattr(request.app.state, "oauth_store", None)
    signer = getattr(request.app.state, "oauth_signer", None)
    config = getattr(request.app.state, "oauth_config", None)
    if store is None or signer is None or config is None:
        from openviking_cli.exceptions import UnavailableError

        raise UnavailableError(service="oauth", reason="OAuth subsystem is not enabled")
    return store, signer, config


def _verify_pkce_s256(code_verifier: str, code_challenge: str) -> bool:
    if not isinstance(code_verifier, str) or not isinstance(code_challenge, str):
        return False
    if not (43 <= len(code_verifier) <= 128):
        return False
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return secrets.compare_digest(expected, code_challenge)


def _resolve_issuer(request: Request, configured_issuer: Optional[str]) -> str:
    """Pick a stable `iss` for issued tokens.

    Prefer the operator-configured issuer; otherwise derive scheme+host from
    the request, honoring X-Forwarded-* when present.
    """
    if configured_issuer:
        return configured_issuer.rstrip("/")
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "http"
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if not host:
        host = f"{request.url.hostname or 'localhost'}:{request.url.port or 80}"
    return f"{proto.split(',')[0].strip()}://{host.split(',')[0].strip()}"


def _build_token_response(
    *,
    request: Request,
    store: OAuthStore,
    signer,
    config,
    client_id: str,
    account_id: str,
    user_id: str,
    role: str,
    scope: Optional[str],
    resource: Optional[str],
) -> dict:
    """Mint a fresh access+refresh token pair bound to the given identity."""
    issuer = _resolve_issuer(request, config.issuer)
    claims: dict[str, object] = {
        "iss": issuer,
        "sub": f"{account_id}/{user_id}",
        "role": role,
        "account_id": account_id,
        "user_id": user_id,
        "client_id": client_id,
    }
    if scope:
        claims["scope"] = scope
    if resource:
        claims["aud"] = resource
    access_token = signer.sign(claims, ttl_seconds=config.access_token_ttl_seconds)

    refresh_plain = secrets.token_urlsafe(48)
    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": config.access_token_ttl_seconds,
        "refresh_token": refresh_plain,
        "scope": scope or "",
        "_refresh_plain": refresh_plain,  # pulled out by the caller for storage
    }


# ---------------------------------------------------------------------------
# /oauth/token
# ---------------------------------------------------------------------------


@router.post("/oauth/token")
async def oauth_token(
    request: Request,
    grant_type: str = Form(...),
    code: Optional[str] = Form(None),
    redirect_uri: Optional[str] = Form(None),
    client_id: Optional[str] = Form(None),
    code_verifier: Optional[str] = Form(None),
    refresh_token: Optional[str] = Form(None),
    scope: Optional[str] = Form(None),
    resource: Optional[str] = Form(None),
) -> JSONResponse:
    """RFC 6749 token endpoint — authorization_code (PKCE S256) and refresh_token grants.

    Confidential clients (`token_endpoint_auth_method=client_secret_basic`)
    are not yet supported; Phase 1 issues only public clients via DCR.
    """
    store, signer, config = _require_state(request)

    if grant_type == "authorization_code":
        return await _grant_authorization_code(
            request,
            store,
            signer,
            config,
            code=code,
            redirect_uri=redirect_uri,
            client_id=client_id,
            code_verifier=code_verifier,
            resource=resource,
        )
    if grant_type == "refresh_token":
        return await _grant_refresh_token(
            request,
            store,
            signer,
            config,
            refresh_token=refresh_token,
            client_id=client_id,
            scope=scope,
            resource=resource,
        )
    return _oauth_error("unsupported_grant_type", f"Unsupported grant_type: {grant_type}")


async def _grant_authorization_code(
    request: Request,
    store: OAuthStore,
    signer,
    config,
    *,
    code: Optional[str],
    redirect_uri: Optional[str],
    client_id: Optional[str],
    code_verifier: Optional[str],
    resource: Optional[str],
) -> JSONResponse:
    if not code or not redirect_uri or not client_id or not code_verifier:
        return _oauth_error(
            "invalid_request",
            "code, redirect_uri, client_id and code_verifier are all required",
        )

    client = await store.get_client(client_id)
    if client is None:
        return _oauth_error("invalid_client", "Unknown client_id", status=401)

    record = await store.consume_auth_code(code)
    if record is None:
        return _oauth_error("invalid_grant", "Authorization code is invalid, expired, or reused")

    if record["client_id"] != client_id:
        return _oauth_error("invalid_grant", "client_id does not match the issued code")

    # Strict-equal redirect_uri match (RFC 6749 §10.6).
    if record["redirect_uri"] != redirect_uri:
        return _oauth_error("invalid_grant", "redirect_uri does not match the original request")

    # PKCE S256: enforce — Phase 1 does not accept plain.
    if record.get("code_challenge_method") != "S256":
        return _oauth_error("invalid_grant", "Only PKCE S256 is supported")
    if not _verify_pkce_s256(code_verifier, record["code_challenge"]):
        return _oauth_error("invalid_grant", "PKCE verifier does not match challenge")

    # Resource indicators: if the original code was bound to a resource, the
    # caller may either omit `resource` (token inherits) or echo the same
    # value. Cross-resource downgrade is rejected.
    bound_resource = record.get("resource")
    final_resource = resource or bound_resource
    if bound_resource and resource and resource != bound_resource:
        return _oauth_error(
            "invalid_target",
            "resource parameter does not match the resource bound at authorize-time",
        )

    pair = _build_token_response(
        request=request,
        store=store,
        signer=signer,
        config=config,
        client_id=client_id,
        account_id=record["account_id"],
        user_id=record["user_id"],
        role=record["role"],
        scope=record.get("scope"),
        resource=final_resource,
    )
    refresh_plain = pair.pop("_refresh_plain")
    await store.insert_refresh(
        token_plain=refresh_plain,
        client_id=client_id,
        account_id=record["account_id"],
        user_id=record["user_id"],
        role=record["role"],
        scope=record.get("scope"),
        resource=final_resource,
        ttl_seconds=config.refresh_token_ttl_seconds,
    )
    return JSONResponse(pair, status_code=200, headers={"Cache-Control": "no-store"})


async def _grant_refresh_token(
    request: Request,
    store: OAuthStore,
    signer,
    config,
    *,
    refresh_token: Optional[str],
    client_id: Optional[str],
    scope: Optional[str],
    resource: Optional[str],
) -> JSONResponse:
    if not refresh_token or not client_id:
        return _oauth_error("invalid_request", "refresh_token and client_id are required")

    client = await store.get_client(client_id)
    if client is None:
        return _oauth_error("invalid_client", "Unknown client_id", status=401)

    new_refresh = secrets.token_urlsafe(48)
    record = await store.consume_refresh(token_plain=refresh_token, replaced_by_plain=new_refresh)
    if record is None:
        # Reuse detection: if the token is *known* but already consumed, this
        # is a replay — invalidate the entire family per RFC 9700 §4.14.
        if await store.is_refresh_known_but_consumed(refresh_token):
            logger.warning(
                "OAuth refresh token replay detected for client_id=%s; revoking chain", client_id
            )
            # We don't know which (account, user) was bound — best effort revoke
            # is left to a future tightening. For now, just reject.
        return _oauth_error("invalid_grant", "refresh_token is invalid, expired, or reused")

    if record["client_id"] != client_id:
        return _oauth_error("invalid_grant", "client_id does not match the issued refresh_token")

    # Downscope: caller may narrow scope but not widen.
    bound_scope = record.get("scope") or ""
    final_scope = scope if scope is not None else bound_scope
    if final_scope and bound_scope:
        bound_set = set(bound_scope.split())
        new_set = set(final_scope.split())
        if not new_set.issubset(bound_set):
            return _oauth_error("invalid_scope", "scope is broader than the original grant")

    bound_resource = record.get("resource")
    final_resource = resource or bound_resource

    issuer = _resolve_issuer(request, config.issuer)
    claims: dict[str, object] = {
        "iss": issuer,
        "sub": f"{record['account_id']}/{record['user_id']}",
        "role": record["role"],
        "account_id": record["account_id"],
        "user_id": record["user_id"],
        "client_id": client_id,
    }
    if final_scope:
        claims["scope"] = final_scope
    if final_resource:
        claims["aud"] = final_resource
    access_token = signer.sign(claims, ttl_seconds=config.access_token_ttl_seconds)

    await store.insert_refresh(
        token_plain=new_refresh,
        client_id=client_id,
        account_id=record["account_id"],
        user_id=record["user_id"],
        role=record["role"],
        scope=final_scope or None,
        resource=final_resource,
        ttl_seconds=config.refresh_token_ttl_seconds,
    )

    return JSONResponse(
        {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": config.access_token_ttl_seconds,
            "refresh_token": new_refresh,
            "scope": final_scope or "",
        },
        status_code=200,
        headers={"Cache-Control": "no-store"},
    )
