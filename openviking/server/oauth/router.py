# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""OpenViking-side OAuth routes.

The OAuth 2.1 protocol surface (DCR, /authorize parsing, /token, well-known
metadata) is delegated to the official ``mcp.server.auth`` SDK (mounted from
``app.py``). This module only owns:

- ``/oauth/authorize/page`` — HTML form where the user submits an OTP. The
  SDK's AuthorizationHandler returns this URL from ``provider.authorize()``;
  on successful OTP submission we mint an authorization code and 302 back
  to the client's redirect_uri.
- ``POST /api/v1/auth/otp`` — short-code issuance, authenticated with the
  caller's existing API key. The OTP is bound to that API key's identity
  (account / user / role) so it can be redeemed on the authorize page.
"""

from __future__ import annotations

import html
import logging
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from mcp.shared.auth import ProtectedResourceMetadata
from pydantic import AnyHttpUrl, BaseModel, Field

from openviking.server.auth import get_request_context
from openviking.server.identity import RequestContext
from openviking.server.oauth.otp import generate_otp
from openviking.server.oauth.provider import OpenVikingOAuthProvider
from openviking.server.oauth.storage import OAuthStore
from openviking_cli.exceptions import InvalidArgumentError, UnavailableError

logger = logging.getLogger(__name__)


router = APIRouter(tags=["oauth"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_store_and_provider(request: Request) -> tuple[OAuthStore, OpenVikingOAuthProvider]:
    store: Optional[OAuthStore] = getattr(request.app.state, "oauth_store", None)
    provider: Optional[OpenVikingOAuthProvider] = getattr(
        request.app.state, "oauth_provider", None
    )
    if store is None or provider is None:
        raise UnavailableError(service="oauth", reason="OAuth subsystem is not enabled")
    return store, provider


_AUTHORIZE_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Authorize {client_name}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
           background: #f5f5f7; margin: 0; padding: 2rem 1rem; color: #1d1d1f; }}
    .card {{ max-width: 420px; margin: 4rem auto; background: white;
             border-radius: 12px; padding: 2rem; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
    h1 {{ font-size: 1.25rem; margin: 0 0 0.5rem; }}
    .client {{ font-weight: 600; }}
    p {{ color: #515154; line-height: 1.5; margin: 0.75rem 0; }}
    label {{ display: block; font-size: 0.9rem; color: #515154; margin: 1rem 0 0.4rem; }}
    input[type=text] {{ width: 100%; padding: 0.6rem 0.75rem; font-size: 1.1rem;
                       border: 1px solid #d2d2d7; border-radius: 6px;
                       letter-spacing: 0.1em; font-family: ui-monospace, monospace; }}
    button {{ margin-top: 1.25rem; width: 100%; padding: 0.7rem; border: 0;
              border-radius: 6px; background: #0071e3; color: white;
              font-size: 1rem; cursor: pointer; }}
    button:hover {{ background: #0077ed; }}
    .error {{ background: #fff1f0; color: #b91c1c; padding: 0.6rem 0.75rem;
              border-radius: 6px; font-size: 0.9rem; margin: 1rem 0 0; }}
    .hint {{ font-size: 0.85rem; color: #86868b; margin-top: 1rem; }}
    code {{ background: #f5f5f7; padding: 1px 6px; border-radius: 4px;
            font-family: ui-monospace, monospace; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>Authorize <span class="client">{client_name}</span></h1>
    <p>This client wants to access your OpenViking workspace.
       Enter a one-time passcode generated from your CLI or REST API to continue.</p>
    {error_block}
    <form method="POST" action="{action}">
      <input type="hidden" name="pending" value="{pending_id}">
      <label for="otp">One-time passcode</label>
      <input type="text" id="otp" name="otp" autofocus autocomplete="off"
             pattern="[A-Za-z0-9]+" maxlength="12" placeholder="ABC234" required>
      <button type="submit">Authorize</button>
    </form>
    <p class="hint">To get an OTP, run<br>
      <code>curl -X POST -H "X-Api-Key: $KEY" {issuer}/api/v1/auth/otp</code></p>
  </div>
</body>
</html>"""


def _render_page(
    *,
    pending_id: str,
    client_name: Optional[str],
    issuer: str,
    error: Optional[str] = None,
) -> HTMLResponse:
    error_block = (
        f'<div class="error">{html.escape(error)}</div>' if error else ""
    )
    body = _AUTHORIZE_PAGE_TEMPLATE.format(
        client_name=html.escape(client_name or "MCP Client"),
        action="/oauth/authorize/page",
        pending_id=html.escape(pending_id),
        error_block=error_block,
        issuer=html.escape(issuer),
    )
    return HTMLResponse(
        body,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
                "frame-ancestors 'none'"
            ),
            "X-Frame-Options": "DENY",
        },
    )


# ---------------------------------------------------------------------------
# /.well-known/oauth-protected-resource (RFC 9728)
# ---------------------------------------------------------------------------


def _public_origin(request: Request) -> str:
    """Pick the public-facing origin for metadata responses.

    Prefers X-Forwarded-Proto/Host (set by typical reverse proxies) over
    the raw scope.scheme/Host so the URLs we publish match the address
    the client actually used. The MCP SDK's create_auth_routes already
    honors a configured ``issuer_url`` for /.well-known/oauth-authorization-server;
    we keep the same convention here for the resource metadata.
    """
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "http"
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if not host:
        host = request.url.netloc or "localhost"
    return f"{proto.split(',', 1)[0].strip()}://{host.split(',', 1)[0].strip()}"


@router.get("/.well-known/oauth-protected-resource")
async def oauth_protected_resource(request: Request) -> JSONResponse:
    """RFC 9728 — protected resource metadata for /mcp.

    MCP clients reach this URL via the ``WWW-Authenticate: Bearer
    resource_metadata=..."`` hint emitted by the /mcp 401 path. The body
    points them at our authorization server so they can run discovery
    against /.well-known/oauth-authorization-server.
    """
    cfg = getattr(request.app.state, "oauth_config", None)
    issuer = (cfg.issuer if cfg and cfg.issuer else _public_origin(request)).rstrip("/")
    resource = f"{_public_origin(request)}/mcp"

    metadata = ProtectedResourceMetadata(
        resource=AnyHttpUrl(resource),
        authorization_servers=[AnyHttpUrl(issuer)],
        bearer_methods_supported=["header"],
        resource_name="OpenViking MCP",
    )
    return JSONResponse(
        metadata.model_dump(mode="json", exclude_none=True),
        headers={"Cache-Control": "public, max-age=3600"},
    )


# ---------------------------------------------------------------------------
# /oauth/authorize/page
# ---------------------------------------------------------------------------


@router.get("/oauth/authorize/page")
async def authorize_page_get(request: Request, pending: str = "") -> HTMLResponse:
    store, provider = _get_store_and_provider(request)
    if not pending:
        return HTMLResponse(
            "<h1>Bad request</h1><p>Missing 'pending' parameter.</p>", status_code=400
        )
    record = await store.load_pending_authorization(pending)
    if record is None:
        return HTMLResponse(
            "<h1>Authorization expired</h1><p>Please restart the connection from your client.</p>",
            status_code=410,
        )
    client = await provider.get_client(record["client_id"])
    return _render_page(
        pending_id=pending,
        client_name=client.client_name if client else None,
        issuer=str(request.base_url).rstrip("/"),
    )


@router.post("/oauth/authorize/page", response_model=None)
async def authorize_page_post(
    request: Request,
    pending: str = Form(...),
    otp: str = Form(...),
):
    store, provider = _get_store_and_provider(request)

    record = await store.load_pending_authorization(pending)
    if record is None:
        return HTMLResponse(
            "<h1>Authorization expired</h1><p>Please restart the connection from your client.</p>",
            status_code=410,
        )

    client = await provider.get_client(record["client_id"])
    issuer = str(request.base_url).rstrip("/")

    consumed = await store.consume_otp(otp.strip().upper())
    if consumed is None:
        return _render_page(
            pending_id=pending,
            client_name=client.client_name if client else None,
            issuer=issuer,
            error="That code is invalid or has already been used. Generate a new one and try again.",
        )

    # Mint and persist the authorization code, then redirect back to the
    # client's redirect_uri with code+state.
    auth_code = provider.mint_authorization_code()
    scope_str = " ".join(record["scopes"]) if record.get("scopes") else None
    await store.insert_auth_code(
        code_plain=auth_code,
        client_id=record["client_id"],
        redirect_uri=record["redirect_uri"],
        code_challenge=record["code_challenge"],
        code_challenge_method="S256",
        scope=scope_str,
        resource=record.get("resource"),
        account_id=consumed["account_id"],
        user_id=consumed["user_id"],
        role=consumed["role"],
        ttl_seconds=provider.code_ttl_seconds,
    )
    await store.delete_pending_authorization(pending)

    params: dict[str, str] = {"code": auth_code}
    if record.get("state"):
        params["state"] = record["state"]
    sep = "&" if "?" in record["redirect_uri"] else "?"
    return RedirectResponse(
        url=f"{record['redirect_uri']}{sep}{urlencode(params)}",
        status_code=302,
        headers={"Cache-Control": "no-store"},
    )


# ---------------------------------------------------------------------------
# POST /api/v1/auth/otp
# ---------------------------------------------------------------------------


class OTPRequest(BaseModel):
    ttl_seconds: Optional[int] = Field(
        default=None, ge=60, le=600, description="Override OTP lifetime (60-600 seconds)"
    )


class OTPResponse(BaseModel):
    otp: str
    expires_at: int
    ttl_seconds: int


@router.post("/api/v1/auth/otp", response_model=OTPResponse)
async def issue_otp(
    request: Request,
    body: Optional[OTPRequest] = None,
    ctx: RequestContext = Depends(get_request_context),
) -> JSONResponse:
    """Issue a one-time passcode bound to the caller's identity.

    The caller must already be authenticated with an API key (or other
    bearer accepted by ``resolve_identity``); the resulting OTP carries
    that account / user / role triple, so any OAuth client that submits
    the OTP on the authorize page is granted that identity.
    """
    store, provider = _get_store_and_provider(request)

    cfg = getattr(request.app.state, "oauth_config", None)
    default_ttl = getattr(cfg, "otp_ttl_seconds", 300) if cfg else 300
    ttl = body.ttl_seconds if body and body.ttl_seconds else default_ttl
    if ttl < 60 or ttl > 600:
        raise InvalidArgumentError("ttl_seconds must be between 60 and 600")

    otp = generate_otp()
    expires_at = await store.insert_otp(
        otp_plain=otp,
        account_id=ctx.user.account_id,
        user_id=ctx.user.user_id,
        role=ctx.role.value,
        ttl_seconds=ttl,
    )
    return JSONResponse(
        {"otp": otp, "expires_at": expires_at, "ttl_seconds": ttl},
        headers={"Cache-Control": "no-store"},
    )
