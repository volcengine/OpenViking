# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Adapter that bridges the official MCP SDK OAuth provider Protocol to OpenViking's
``OAuthStore``.

The SDK ships a stdlib-only OAuth 2.1 server (RFC 6749 / 7591 / 8414) under
``mcp.server.auth``. It validates PKCE, parses grants, and emits standard
errors itself; we just supply a Provider implementation that knows how to
persist and retrieve the protocol's data structures. Tokens are opaque random
strings keyed by their SHA-256 hash, so this module contains no cryptography.
"""

from __future__ import annotations

import secrets
from typing import Callable, Optional
from urllib.parse import urlencode

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    RegistrationError,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl

from openviking.server.identity import Role
from openviking.server.oauth.otp import generate_otp
from openviking.server.oauth.storage import OAuthStore
from openviking_cli.utils import get_logger

# Mirrors auth.py:_ROLE_RANK; duplicated here to avoid the import cycle
# (auth.py already depends on this module via the Provider Protocol).
_ROLE_RANK = {Role.USER: 0, Role.ADMIN: 1, Role.ROOT: 2}

logger = get_logger(__name__)

# Access tokens carry this prefix so the auth.py bearer router can cheaply
# discriminate them from API keys without an extra DB lookup. The prefix is
# advisory (the actual auth is the DB lookup), but it keeps the hot path fast.
ACCESS_TOKEN_PREFIX = "ovat_"
REFRESH_TOKEN_PREFIX = "ovrt_"
AUTH_CODE_PREFIX = "ovac_"


class OVAuthorizationCode(AuthorizationCode):
    """AuthorizationCode subtype that pins the issuing identity."""

    account_id: str
    user_id: str
    role: str
    # SHA-256 fp of the API key that authorized this code; copied forward
    # into refresh/access tokens so rotation invalidates the entire chain.
    authorizing_key_fp: str | None = None


class OVRefreshToken(RefreshToken):
    account_id: str
    user_id: str
    role: str
    resource: str | None = None
    authorizing_key_fp: str | None = None


class OVAccessToken(AccessToken):
    account_id: str
    user_id: str
    role: str
    authorizing_key_fp: str | None = None


class OpenVikingOAuthProvider(
    OAuthAuthorizationServerProvider[OVAuthorizationCode, OVRefreshToken, OVAccessToken]
):
    """OpenViking implementation of the MCP SDK OAuth provider Protocol."""

    def __init__(
        self,
        *,
        store: OAuthStore,
        issuer: str,
        access_token_ttl_seconds: int = 3600,
        refresh_token_ttl_seconds: int = 30 * 24 * 3600,
        auth_code_ttl_seconds: int = 300,
        authorize_page_path: str = "/oauth/authorize/page",
        role_resolver: Optional[Callable[[str, str], "Role"]] = None,
    ) -> None:
        self._store = store
        self._issuer = issuer.rstrip("/")
        self._access_ttl = access_token_ttl_seconds
        self._refresh_ttl = refresh_token_ttl_seconds
        self._code_ttl = auth_code_ttl_seconds
        self._authorize_page = authorize_page_path
        # Optional callback to look up a user's current role at refresh time.
        # When supplied, exchange_refresh_token will reject if the embedded
        # role outranks the current role — mirrors the bearer-auth check so
        # that `set_role` demotion can't be laundered through token rotation.
        # Wired in app.py from the APIKeyManager.
        self._role_resolver = role_resolver

    # ---- Clients (DCR) ----

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        record = await self._store.get_client(client_id)
        if record is None:
            return None
        return OAuthClientInformationFull(
            client_id=record["client_id"],
            client_secret=None,  # we never expose stored secret hash; auth uses the hash check
            redirect_uris=[AnyUrl(u) for u in record["redirect_uris"]],
            token_endpoint_auth_method=record["token_endpoint_auth_method"],
            grant_types=record["grant_types"],
            response_types=record["response_types"],
            client_name=record.get("client_name"),
            client_id_issued_at=record["created_at"],
        )

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if client_info.client_id is None:  # pragma: no cover — SDK always sets it
            raise RegistrationError(
                error="invalid_client_metadata",
                error_description="client_id is required",
            )
        # OpenViking treats every registered client as public + PKCE.
        # We do not enforce client_secret on the token endpoint — get_client
        # returns client_secret=None unconditionally, so the SDK's
        # ClientAuthenticator skips secret validation regardless of what
        # auth method was declared. To prevent confidential clients from
        # *thinking* they're getting secret-based auth (silent security
        # downgrade), we normalize whatever the registrar requested to
        # "none" and log a warning. Real MCP clients use "none" anyway —
        # RFC 8252 §8.4 says native apps MUST NOT use client_secret —
        # but the OAuth 2.0 default that some SDKs fill in is
        # "client_secret_basic", which would otherwise fail the gate.
        requested = client_info.token_endpoint_auth_method
        if requested and requested != "none":
            logger.warning(
                "DCR: client %s requested token_endpoint_auth_method=%s; "
                "downgrading to 'none' (OpenViking only supports public PKCE clients).",
                client_info.client_id,
                requested,
            )
        auth_method = "none"
        try:
            await self._store.register_client(
                client_id=client_info.client_id,
                redirect_uris=[str(u) for u in (client_info.redirect_uris or [])],
                client_name=client_info.client_name,
                token_endpoint_auth_method=auth_method,
                grant_types=list(client_info.grant_types) if client_info.grant_types else None,
                response_types=list(client_info.response_types)
                if client_info.response_types
                else None,
                client_secret=None,  # public client; never stored
            )
        except ValueError as exc:
            raise RegistrationError(
                error="invalid_client_metadata", error_description=str(exc)
            ) from exc

    # ---- Authorize ----

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Mint a verification code and stash the AuthorizationParams.

        Returns the URL of our authorize page, which displays a 6-character
        verification code. The user re-types that code into the OpenViking
        console (where they're already authenticated), the console calls our
        ``/api/v1/auth/oauth-verify`` endpoint to bind the caller's identity
        to the pending row, and the page polls until verified to redirect
        back to the client's redirect_uri.
        """
        assert client.client_id is not None
        display_code = generate_otp()
        pending_id = await self._store.create_pending_authorization(
            client_id=client.client_id,
            redirect_uri=str(params.redirect_uri),
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            code_challenge=params.code_challenge,
            scopes=params.scopes,
            resource=params.resource,
            state=params.state,
            display_code=display_code,
            ttl_seconds=600,
        )
        return f"{self._issuer}{self._authorize_page}?{urlencode({'pending': pending_id})}"

    # ---- Authorization code lifecycle ----

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> OVAuthorizationCode | None:
        # We don't consume yet — SDK's TokenHandler verifies PKCE first, then
        # calls exchange_authorization_code. Use a non-destructive lookup.
        record = await self._store.peek_auth_code(authorization_code)
        if record is None:
            return None
        if record.get("client_id") != client.client_id:
            return None
        return OVAuthorizationCode(
            code=authorization_code,
            scopes=(record.get("scope") or "").split() if record.get("scope") else [],
            expires_at=float(record["expires_at"]),
            client_id=record["client_id"],
            code_challenge=record["code_challenge"],
            redirect_uri=AnyUrl(record["redirect_uri"]),
            redirect_uri_provided_explicitly=True,
            resource=record.get("resource"),
            account_id=record["account_id"],
            user_id=record["user_id"],
            role=record["role"],
            authorizing_key_fp=record.get("authorizing_key_fp"),
        )

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: OVAuthorizationCode,
    ) -> OAuthToken:
        # One-shot consumption: this is what binds the code to a single exchange.
        consumed = await self._store.consume_auth_code(authorization_code.code)
        if consumed is None:
            raise TokenError(error="invalid_grant", error_description="code is invalid or reused")
        return await self._issue_token_pair(
            client_id=authorization_code.client_id,
            account_id=authorization_code.account_id,
            user_id=authorization_code.user_id,
            role=authorization_code.role,
            scope=" ".join(authorization_code.scopes) if authorization_code.scopes else None,
            resource=authorization_code.resource,
            authorizing_key_fp=authorization_code.authorizing_key_fp,
        )

    # ---- Refresh tokens ----

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> OVRefreshToken | None:
        record = await self._store.peek_refresh(refresh_token)
        if record is None:
            return None
        if record.get("client_id") != client.client_id:
            return None
        return OVRefreshToken(
            token=refresh_token,
            client_id=record["client_id"],
            scopes=(record.get("scope") or "").split() if record.get("scope") else [],
            expires_at=record.get("expires_at"),
            account_id=record["account_id"],
            user_id=record["user_id"],
            role=record["role"],
            resource=record.get("resource"),
            authorizing_key_fp=record.get("authorizing_key_fp"),
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: OVRefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        # Role downgrade gate: if the embedded role outranks the user's
        # current role (post-`set_role` demotion), refuse to rotate. Same
        # invariant the bearer-auth path enforces; checked here so a
        # demoted user can't keep minting fresh access tokens at the old
        # privilege level just because they hold a long-lived refresh.
        if self._role_resolver is not None:
            try:
                token_role = Role(refresh_token.role)
                current_role = self._role_resolver(refresh_token.account_id, refresh_token.user_id)
            except (ValueError, Exception):  # noqa: BLE001
                token_role = None
                current_role = None
            if (
                token_role is not None
                and current_role is not None
                and _ROLE_RANK[token_role] > _ROLE_RANK[current_role]
            ):
                raise TokenError(
                    error="invalid_grant",
                    error_description=(
                        "user role has been downgraded; please re-authorize the client"
                    ),
                )
        new_refresh = self._mint_refresh()
        consumed = await self._store.consume_refresh(
            token_plain=refresh_token.token, replaced_by_plain=new_refresh
        )
        if consumed is None:
            # Replay detection: if a previously-issued refresh token is being
            # reused after consumption, RFC 9700 §4.14 requires invalidating
            # the entire token family. We go broader than per-(client, account,
            # user) chain and revoke ALL outstanding access + refresh + auth
            # codes for the (account, user) pair — replay implies the chain is
            # compromised, and we'd rather force a full reauthorization than
            # risk leaving sibling tokens live.
            if await self._store.is_refresh_known_but_consumed(refresh_token.token):
                logger.warning(
                    "OAuth refresh replay detected for client_id=%s account=%s user=%s",
                    client.client_id,
                    refresh_token.account_id,
                    refresh_token.user_id,
                )
                await self._store.revoke_user_tokens(
                    account_id=refresh_token.account_id,
                    user_id=refresh_token.user_id,
                )
            raise TokenError(error="invalid_grant", error_description="refresh token invalid")
        scope = " ".join(scopes) if scopes else None
        return await self._issue_token_pair(
            client_id=refresh_token.client_id,
            account_id=refresh_token.account_id,
            user_id=refresh_token.user_id,
            role=refresh_token.role,
            scope=scope or (refresh_token.scopes and " ".join(refresh_token.scopes)) or None,
            resource=refresh_token.resource,
            authorizing_key_fp=refresh_token.authorizing_key_fp,
            new_refresh_override=new_refresh,
        )

    # ---- Access tokens ----

    async def load_access_token(self, token: str) -> OVAccessToken | None:
        if not token.startswith(ACCESS_TOKEN_PREFIX):
            return None
        record = await self._store.load_access(token)
        if record is None:
            return None
        return OVAccessToken(
            token=token,
            client_id=record["client_id"],
            scopes=(record.get("scope") or "").split() if record.get("scope") else [],
            expires_at=record["expires_at"],
            resource=record.get("resource"),
            account_id=record["account_id"],
            user_id=record["user_id"],
            role=record["role"],
            authorizing_key_fp=record.get("authorizing_key_fp"),
        )

    async def revoke_token(self, token: OVAccessToken | OVRefreshToken) -> None:
        # Idempotent — no error if already revoked / unknown.
        if isinstance(token, AccessToken):
            await self._store.revoke_access(token.token)
        else:
            await self._store.consume_refresh(token_plain=token.token, replaced_by_plain=None)

    # ---- Helpers ----

    def _mint_access(self) -> str:
        return ACCESS_TOKEN_PREFIX + secrets.token_urlsafe(40)

    def _mint_refresh(self) -> str:
        return REFRESH_TOKEN_PREFIX + secrets.token_urlsafe(40)

    async def _issue_token_pair(
        self,
        *,
        client_id: str,
        account_id: str,
        user_id: str,
        role: str,
        scope: Optional[str],
        resource: Optional[str],
        authorizing_key_fp: Optional[str],
        new_refresh_override: Optional[str] = None,
    ) -> OAuthToken:
        access = self._mint_access()
        refresh = new_refresh_override or self._mint_refresh()
        # ``authorizing_key_fp`` should always be set — every legitimate flow
        # records the fingerprint at OTP/oauth-verify time. We tolerate None
        # only because the dataclass default is None for upgrade safety; the
        # bearer-auth path will fail-closed against a NULL stored value, so a
        # missing fp here just makes the resulting token unusable rather than
        # silently bypassing the binding.
        fp = authorizing_key_fp or ""
        await self._store.insert_access(
            token_plain=access,
            client_id=client_id,
            account_id=account_id,
            user_id=user_id,
            role=role,
            scope=scope,
            resource=resource,
            authorizing_key_fp=fp,
            ttl_seconds=self._access_ttl,
        )
        await self._store.insert_refresh(
            token_plain=refresh,
            client_id=client_id,
            account_id=account_id,
            user_id=user_id,
            role=role,
            scope=scope,
            resource=resource,
            authorizing_key_fp=fp,
            ttl_seconds=self._refresh_ttl,
        )
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=self._access_ttl,
            scope=scope,
            refresh_token=refresh,
        )

    # ---- Custom flow methods used by /oauth/authorize/page ----

    def mint_authorization_code(self) -> str:
        return AUTH_CODE_PREFIX + secrets.token_urlsafe(40)

    @property
    def code_ttl_seconds(self) -> int:
        return self._code_ttl
