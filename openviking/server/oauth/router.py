# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""OpenViking-side OAuth routes.

The OAuth 2.1 protocol surface (DCR, /authorize parsing, /token, well-known
metadata) is delegated to the official ``mcp.server.auth`` SDK (mounted from
``app.py``). This module only owns:

- ``GET /api/v1/auth/oauth/pending/{{pending_id}}`` — public metadata
  endpoint consumed by the Studio consent UI. Returns the minimum info
  needed to render the consent card (client_name, redirect_host, scopes);
  never the display_code or full redirect_uri.
- ``POST /api/v1/auth/oauth-verify`` — binds the caller's identity to a
  pending OAuth authorization. Accepts either ``pending_id`` (Studio
  consent path) or ``code`` (the 6-character display_code typed on a
  cross-device fallback page).
- ``GET /oauth/authorize/page`` — server-rendered cross-device fallback.
  Default ``provider.authorize()`` redirects to ``/studio/oauth/consent``
  instead; this HTML page is reached only when the user clicks "Use
  another device" or has no Studio access on the current device.
"""

from __future__ import annotations

import html
import os
from typing import Optional
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from mcp.shared.auth import ProtectedResourceMetadata
from pydantic import AnyHttpUrl, BaseModel, Field

from openviking.server.auth import get_request_context
from openviking.server.identity import RequestContext
from openviking.server.oauth.provider import OpenVikingOAuthProvider
from openviking.server.oauth.storage import OAuthStore
from openviking_cli.exceptions import (
    InvalidArgumentError,
    PermissionDeniedError,
    UnavailableError,
)
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


router = APIRouter(tags=["oauth"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_store_and_provider(request: Request) -> tuple[OAuthStore, OpenVikingOAuthProvider]:
    store: Optional[OAuthStore] = getattr(request.app.state, "oauth_store", None)
    provider: Optional[OpenVikingOAuthProvider] = getattr(request.app.state, "oauth_provider", None)
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
    .card {{ max-width: 460px; margin: 4rem auto; background: white;
             border-radius: 12px; padding: 2rem; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
    h1 {{ font-size: 1.25rem; margin: 0 0 0.5rem; }}
    .client {{ font-weight: 600; }}
    p {{ color: #515154; line-height: 1.5; margin: 0.75rem 0; }}
    .codebox {{ background: #f5f5f7; border-radius: 8px; padding: 1.25rem;
                margin: 1.5rem 0 1rem; text-align: center; }}
    .code {{ font-family: ui-monospace, monospace; font-size: 2.4rem;
             letter-spacing: 0.4rem; font-weight: 600; color: #1d1d1f; }}
    .studio-link {{ display: inline-block; margin-top: 0.75rem; padding: 0.4rem 0.85rem;
                    border-radius: 6px; background: #0071e3; color: white;
                    text-decoration: none; font-size: 0.9rem; word-break: break-all; }}
    .studio-link:hover {{ background: #0077ed; }}
    .hint {{ font-size: 0.85rem; color: #86868b; margin-top: 1rem; }}
    code {{ background: #f5f5f7; padding: 1px 6px; border-radius: 4px;
            font-family: ui-monospace, monospace; font-size: 0.85rem; }}
    .status {{ margin-top: 1rem; padding: 0.6rem 0.75rem; border-radius: 6px;
               font-size: 0.9rem; display: none; }}
    .status.visible {{ display: block; }}
    .status.error {{ background: #fff1f0; color: #b91c1c; }}
    .status.info {{ background: #e3f2fd; color: #1565c0; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>Authorize <span class="client">{client_name}</span></h1>
    <p>This is the cross-device authorization page. On another device that is
       signed in to OpenViking Studio, open the link below and enter the
       verification code.</p>

    <div class="codebox">
      <div class="code" id="displayCode">{display_code}</div>
      <a class="studio-link" href="{public_base_url}/studio/oauth/verify" target="_blank" rel="noopener">
        Open {public_base_url}/studio/oauth/verify
      </a>
    </div>

    <p class="hint">If you can open OpenViking Studio on <em>this</em> device,
      go back to the link your MCP client gave you — Studio will let you
      authorize without typing this code.</p>

    <div class="status" id="statusBox"></div>

    <p class="hint">Waiting for verification… this page will redirect automatically once you confirm.</p>
  </div>

  <script>
  (function() {{
    const PENDING = "{pending_id}";
    const STATUS_URL = "/oauth/authorize/page/status?pending=" + encodeURIComponent(PENDING);
    const statusEl = document.getElementById("statusBox");

    function showStatus(msg, kind) {{
      statusEl.textContent = msg;
      statusEl.className = "status visible " + (kind || "info");
    }}

    async function pollOnce() {{
      try {{
        const resp = await fetch(STATUS_URL, {{cache: "no-store"}});
        if (resp.status === 410) {{
          showStatus("This authorization has expired. Restart from your client.", "error");
          return false;
        }}
        const body = await resp.json();
        if (body.status === "approved" && body.redirect_url) {{
          window.location.replace(body.redirect_url);
          return false;
        }}
      }} catch (e) {{ /* transient failure; retry */ }}
      return true;
    }}
    (async function loop() {{
      while (await pollOnce()) {{
        await new Promise(function(r) {{ setTimeout(r, 2000); }});
      }}
    }})();
  }})();
  </script>
</body>
</html>"""


def _render_page(
    *,
    pending_id: str,
    display_code: str,
    client_name: Optional[str],
    public_base_url: str,
) -> HTMLResponse:
    body = _AUTHORIZE_PAGE_TEMPLATE.format(
        client_name=html.escape(client_name or "MCP Client"),
        pending_id=html.escape(pending_id),
        display_code=html.escape(display_code),
        public_base_url=html.escape(public_base_url),
    )
    return HTMLResponse(
        body,
        headers={
            "Cache-Control": "no-store",
            # Allow inline script + style for our self-contained page; same-origin
            # only. frame-ancestors 'none' protects against clickjacking.
            "Content-Security-Policy": (
                "default-src 'self'; "
                "style-src 'unsafe-inline'; "
                "script-src 'unsafe-inline'; "
                "connect-src 'self'; "
                "form-action 'self'; "
                "frame-ancestors 'none'"
            ),
            "X-Frame-Options": "DENY",
        },
    )


# ---------------------------------------------------------------------------
# /.well-known/oauth-protected-resource (RFC 9728)
# ---------------------------------------------------------------------------


PUBLIC_BASE_URL_ENV = "OPENVIKING_PUBLIC_BASE_URL"


def _public_origin(request: Request) -> str:
    """Pick the public-facing origin for metadata responses.

    Resolution order:
      1. ``OPENVIKING_PUBLIC_BASE_URL`` environment variable (operator override)
      2. ``oauth.issuer`` from OAuthConfig if explicitly set
      3. ``X-Forwarded-Proto`` / ``X-Forwarded-Host`` (reverse-proxy chain)
      4. Request scheme + ``Host`` header (direct hit)

    The same helper is used by every URL the server publishes to clients
    (issuer, PRM resource, WWW-Authenticate, authorize-page links) so they
    all agree on a single public address.
    """
    env_value = os.environ.get(PUBLIC_BASE_URL_ENV, "").strip()
    if env_value:
        return env_value.rstrip("/")
    cfg = getattr(request.app.state, "oauth_config", None)
    configured = getattr(cfg, "issuer", None) if cfg else None
    if configured:
        return configured.rstrip("/")
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
        display_code=record["display_code"],
        client_name=client.client_name if client else None,
        public_base_url=_public_origin(request),
    )


@router.get("/oauth/authorize/page/status")
async def authorize_page_status(request: Request, pending: str = "") -> JSONResponse:
    """Polled by the authorize page until verification + auth-code mint.

    Status values:
      - ``pending``: not yet verified
      - ``approved``: caller confirmed; ``redirect_url`` carries the auth code
      - ``expired``: pending row gone (TTL or denied)
    """
    store, provider = _get_store_and_provider(request)
    if not pending:
        return JSONResponse({"status": "expired"}, status_code=410)

    record = await store.load_pending_authorization(pending)
    if record is None:
        return JSONResponse({"status": "expired"}, status_code=410)

    if not record["verified"]:
        return JSONResponse({"status": "pending"}, headers={"Cache-Control": "no-store"})

    # Verified — mint auth code and tear down pending row.
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
        account_id=record["verified_account_id"],
        user_id=record["verified_user_id"],
        role=record["verified_role"],
        # Carry the verifier's API key fingerprint forward so every token
        # derived from this code is bound to the same key lifecycle.
        authorizing_key_fp=record.get("verified_key_fp") or "",
        ttl_seconds=provider.code_ttl_seconds,
    )
    await store.delete_pending_authorization(pending)

    params: dict[str, str] = {"code": auth_code}
    if record.get("state"):
        params["state"] = record["state"]
    sep = "&" if "?" in record["redirect_uri"] else "?"
    return JSONResponse(
        {
            "status": "approved",
            "redirect_url": f"{record['redirect_uri']}{sep}{urlencode(params)}",
        },
        headers={"Cache-Control": "no-store"},
    )


# ---------------------------------------------------------------------------
# GET /api/v1/auth/oauth/pending/{pending_id}
#
# Public metadata endpoint consumed by the Studio consent UI. Given a
# pending_id from the authorize URL, returns the minimum info the SPA needs
# to render the consent card (client_name, redirect_host, scopes).
#
# Intentionally does NOT return:
#   - display_code: would defeat the cross-device input-code brute-force
#     protection (a leaked pending_id could otherwise be converted into a
#     valid OTP).
#   - full redirect_uri: only the host portion is shown to the user. The
#     full URI lives server-side in the pending row and is only revealed
#     when the verify step turns into a 302 to the client.
# ---------------------------------------------------------------------------


class OAuthPendingInfo(BaseModel):
    client_id: str
    client_name: Optional[str] = None
    redirect_host: Optional[str] = None
    scopes: list[str] = Field(default_factory=list)


@router.get(
    "/api/v1/auth/oauth/pending/{pending_id}",
    response_model=OAuthPendingInfo,
)
async def oauth_pending_info(request: Request, pending_id: str) -> JSONResponse:
    """Return public-safe metadata about a pending OAuth authorization."""
    store, provider = _get_store_and_provider(request)
    record = await store.load_pending_authorization(pending_id)
    if record is None:
        return JSONResponse({"error": "expired"}, status_code=404)

    client = await provider.get_client(record["client_id"])
    redirect_uri = record.get("redirect_uri") or ""
    redirect_host = urlparse(redirect_uri).netloc or None

    return JSONResponse(
        OAuthPendingInfo(
            client_id=record["client_id"],
            client_name=client.client_name if client else None,
            redirect_host=redirect_host,
            scopes=list(record.get("scopes") or []),
        ).model_dump(mode="json"),
        headers={"Cache-Control": "no-store"},
    )


# ---------------------------------------------------------------------------
# POST /api/v1/auth/oauth-verify (authenticated; binds caller identity)
# ---------------------------------------------------------------------------


class OAuthVerifyRequest(BaseModel):
    # Exactly one of (code, pending_id) must be set. ``code`` is the
    # 6-character display_code used by the cross-device fallback page
    # (/oauth/authorize/page). ``pending_id`` is the opaque pending_id used
    # by the same-device Studio consent UI, which already has the id on
    # hand from the authorize URL and doesn't need a re-typed OTP.
    code: Optional[str] = Field(
        default=None,
        description="6-character display code (cross-device fallback path)",
    )
    pending_id: Optional[str] = Field(
        default=None,
        description="Opaque pending_id from the authorize URL (Studio consent path)",
    )
    decision: str = Field(
        default="approve",
        description="'approve' to authorize the client, 'deny' to reject",
    )


class OAuthVerifyResponse(BaseModel):
    status: str  # "approved" | "denied"
    client_id: Optional[str] = None
    client_name: Optional[str] = None


@router.post("/api/v1/auth/oauth-verify", response_model=OAuthVerifyResponse)
async def oauth_verify(
    request: Request,
    body: OAuthVerifyRequest,
    ctx: RequestContext = Depends(get_request_context),
) -> JSONResponse:
    """Bind the caller's identity to a pending OAuth authorization.

    Two entry points converge here:

    - Same-device Studio consent: ``/studio/oauth/consent`` posts ``pending_id``
      using the user's already-loaded Studio session as Bearer.
    - Cross-device fallback: the user reads a 6-character display_code off the
      MCP client's authorize page (``/oauth/authorize/page``) and re-types it
      on another already-signed-in device at ``/studio/oauth/verify``, which
      posts ``code``.

    On approve we write the caller's (account, user, role) into the pending
    row; the authorize page's polling catches that and redirects the client
    back to ``redirect_uri`` with a fresh authorization code.
    """
    store, provider = _get_store_and_provider(request)

    # Privilege-elevation gate: an OAuth-issued access token must NOT be
    # able to mint new OAuth state. Otherwise a stolen short-lived bearer
    # could launder itself into a fresh 30-day refresh chain whose fp is
    # bound to the still-valid key. Force this endpoint to require primary
    # auth (raw API key or console session, both of which set
    # from_oauth=False).
    if ctx.from_oauth:
        raise PermissionDeniedError(
            "OAuth-issued tokens cannot authorize new OAuth clients. "
            "Use your API key or sign in to OpenViking Studio to verify."
        )

    decision = body.decision.lower().strip()
    if decision not in {"approve", "deny"}:
        raise InvalidArgumentError("decision must be 'approve' or 'deny'")

    # Two callers, two lookup paths: Studio's consent SPA has the pending_id
    # straight off the authorize URL; the cross-device fallback page asks
    # the user to type the 6-char display_code on a different device. Both
    # converge to the same pending row.
    if body.pending_id:
        record = await store.load_pending_authorization(body.pending_id)
        if record is None or record.get("verified"):
            raise InvalidArgumentError("Invalid or expired pending authorization")
    elif body.code:
        record = await store.find_pending_by_display_code(body.code)
        if record is None:
            raise InvalidArgumentError("Invalid or expired verification code")
    else:
        raise InvalidArgumentError("Must provide either 'pending_id' or 'code'")

    if decision == "deny":
        await store.delete_pending_authorization(record["pending_id"])
        return JSONResponse({"status": "denied"})

    # Bind the verifier's current API-key fingerprint into the pending row.
    # The fp is propagated through auth_code → access/refresh tokens, and
    # every OAuth bearer auth re-checks it against the user's current key.
    # If the verifier has no resolvable key (ROOT, trusted-mode requester
    # without a real key), refuse to mint OAuth: there's no key to bind to,
    # so we cannot honor the "OAuth lifetime ≤ key lifetime" invariant.
    api_key_manager = getattr(request.app.state, "api_key_manager", None)
    verifier_fp: Optional[str] = None
    if api_key_manager is not None and hasattr(api_key_manager, "get_user_key_fingerprint"):
        verifier_fp = api_key_manager.get_user_key_fingerprint(
            ctx.user.account_id, ctx.user.user_id
        )
    if not verifier_fp:
        raise InvalidArgumentError(
            "OAuth authorization requires a verifier with a registered API key "
            "(ROOT or trusted-mode identities cannot authorize OAuth clients)."
        )

    ok = await store.mark_pending_verified(
        pending_id=record["pending_id"],
        account_id=ctx.user.account_id,
        user_id=ctx.user.user_id,
        role=str(ctx.role),
        verified_key_fp=verifier_fp,
    )
    if not ok:
        raise InvalidArgumentError("Verification raced — please restart from the authorize page")

    client = await provider.get_client(record["client_id"])
    return JSONResponse(
        {
            "status": "approved",
            "client_id": record["client_id"],
            "client_name": client.client_name if client else None,
        }
    )
