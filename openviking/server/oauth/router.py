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
import os
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from mcp.shared.auth import ProtectedResourceMetadata
from pydantic import AnyHttpUrl, BaseModel, Field

from openviking.server.auth import get_request_context
from openviking.server.identity import RequestContext
from openviking.server.oauth.otp import generate_otp
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
    .console-link {{ display: inline-block; margin-top: 0.5rem; padding: 0.4rem 0.85rem;
                     border-radius: 6px; background: #0071e3; color: white;
                     text-decoration: none; font-size: 0.9rem; }}
    .console-link:hover {{ background: #0077ed; }}
    .hint {{ font-size: 0.85rem; color: #86868b; margin-top: 1rem; }}
    code {{ background: #f5f5f7; padding: 1px 6px; border-radius: 4px;
            font-family: ui-monospace, monospace; font-size: 0.85rem; }}
    .same-origin-panel {{ display: none; background: #f1f8e9; border: 1px solid #c5e1a5;
                          border-radius: 8px; padding: 1rem; margin: 1rem 0; }}
    .same-origin-panel.visible {{ display: block; }}
    .same-origin-panel h2 {{ margin: 0 0 0.5rem; font-size: 1rem; color: #33691e; }}
    .same-origin-panel button {{ margin-top: 0.75rem; padding: 0.6rem 1.25rem; border: 0;
                                 border-radius: 6px; background: #33691e; color: white;
                                 font-size: 0.95rem; cursor: pointer; }}
    .same-origin-panel button:hover {{ background: #3d7a22; }}
    .same-origin-panel button:disabled {{ background: #aab; cursor: not-allowed; }}
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
    <p>This client is requesting access to your OpenViking workspace. To continue,
       go to the OpenViking console and enter the verification code below.</p>

    <div class="codebox">
      <div class="code" id="displayCode">{display_code}</div>
      <a class="console-link" href="{public_base_url}/console" target="_blank" rel="noopener">
        Open OpenViking console →
      </a>
    </div>

    <div class="same-origin-panel" id="sameOriginPanel">
      <h2>Quick authorize</h2>
      <p style="margin: 0;">You're signed in to the console in this browser.
        Click below to authorize <strong>{client_name}</strong> with that identity.</p>
      <button id="quickAuthBtn" type="button">Authorize</button>
    </div>

    <div class="status" id="statusBox"></div>

    <p class="hint">Waiting for verification… this page will redirect automatically once you confirm.</p>
  </div>

  <script>
  (function() {{
    const PENDING = "{pending_id}";
    const DISPLAY_CODE = "{display_code}";
    const STATUS_URL = "/oauth/authorize/page/status?pending=" + encodeURIComponent(PENDING);
    // Same-origin verify endpoint exposed by the 8020 console proxy. When the
    // console isn't reverse-proxied to the same origin as this page, the
    // detection below will simply not find a key and the panel stays hidden.
    const VERIFY_URL = "/console/api/v1/ov/auth/oauth-verify";
    const SESSION_KEY = "ov_console_api_key";
    const statusEl = document.getElementById("statusBox");
    const panelEl = document.getElementById("sameOriginPanel");
    const buttonEl = document.getElementById("quickAuthBtn");

    function showStatus(msg, kind) {{
      statusEl.textContent = msg;
      statusEl.className = "status visible " + (kind || "info");
    }}
    function clearStatus() {{
      statusEl.className = "status";
    }}

    // Show the quick-authorize panel only if we can find an API key in this
    // browser's localStorage (i.e. the console is on the same origin and
    // the user is signed in). The console persists the API key here for
    // cross-tab use; sessionStorage holds a per-tab copy that this page
    // (a different tab) can't see, so we deliberately read localStorage.
    // Click still requires explicit confirmation.
    let sameOriginKey = null;
    try {{
      sameOriginKey = window.localStorage.getItem(SESSION_KEY)
                   || window.sessionStorage.getItem(SESSION_KEY);
    }} catch (e) {{ /* storage may be unavailable; ignore */ }}
    if (sameOriginKey) {{
      panelEl.classList.add("visible");
      buttonEl.addEventListener("click", async function() {{
        buttonEl.disabled = true;
        clearStatus();
        try {{
          const resp = await fetch(VERIFY_URL, {{
            method: "POST",
            headers: {{
              "Content-Type": "application/json",
              "X-Api-Key": sameOriginKey,
            }},
            body: JSON.stringify({{code: DISPLAY_CODE, decision: "approve"}}),
          }});
          if (!resp.ok) {{
            const text = await resp.text();
            showStatus("Authorize failed: " + text.slice(0, 200), "error");
            buttonEl.disabled = false;
            return;
          }}
          showStatus("Authorized — redirecting…", "info");
        }} catch (err) {{
          showStatus("Network error: " + err.message, "error");
          buttonEl.disabled = false;
        }}
      }});
    }}

    // Poll the status endpoint until verified, then redirect.
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
# POST /api/v1/auth/oauth-verify (authenticated; binds caller identity)
# ---------------------------------------------------------------------------


class OAuthVerifyRequest(BaseModel):
    code: str = Field(..., description="The 6-character display code from the authorize page")
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

    The user reads a 6-character verification code off the MCP client's
    authorize page, then submits it here from a session that's already
    authenticated (typically via the OpenViking console). On approve we
    write the caller's (account, user, role) into the pending row; the
    authorize page's polling then catches that and redirects the client
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
            "Use your API key or sign in to the console to verify."
        )

    decision = body.decision.lower().strip()
    if decision not in {"approve", "deny"}:
        raise InvalidArgumentError("decision must be 'approve' or 'deny'")

    record = await store.find_pending_by_display_code(body.code)
    if record is None:
        raise InvalidArgumentError("Invalid or expired verification code")

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
        role=ctx.role.value,
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

    # Same gate as oauth_verify: an OAuth-issued bearer cannot mint a new
    # OTP and re-launch the authorization flow. See router note on
    # /api/v1/auth/oauth-verify.
    if ctx.from_oauth:
        raise PermissionDeniedError(
            "OAuth-issued tokens cannot issue OTPs. Use your API key or sign in to the console."
        )

    cfg = getattr(request.app.state, "oauth_config", None)
    default_ttl = getattr(cfg, "otp_ttl_seconds", 300) if cfg else 300
    ttl = body.ttl_seconds if body and body.ttl_seconds else default_ttl
    if ttl < 60 or ttl > 600:
        raise InvalidArgumentError("ttl_seconds must be between 60 and 600")

    # See oauth_verify for the rationale: an OTP that can't be tied back to
    # a real, current API key cannot uphold the lifecycle-binding invariant.
    api_key_manager = getattr(request.app.state, "api_key_manager", None)
    caller_fp: Optional[str] = None
    if api_key_manager is not None and hasattr(api_key_manager, "get_user_key_fingerprint"):
        caller_fp = api_key_manager.get_user_key_fingerprint(ctx.user.account_id, ctx.user.user_id)
    if not caller_fp:
        raise InvalidArgumentError(
            "OTP issuance requires a caller with a registered API key "
            "(ROOT or trusted-mode identities cannot issue OAuth OTPs)."
        )

    otp = generate_otp()
    expires_at = await store.insert_otp(
        otp_plain=otp,
        account_id=ctx.user.account_id,
        user_id=ctx.user.user_id,
        role=ctx.role.value,
        authorizing_key_fp=caller_fp,
        ttl_seconds=ttl,
    )
    return JSONResponse(
        {"otp": otp, "expires_at": expires_at, "ttl_seconds": ttl},
        headers={"Cache-Control": "no-store"},
    )
