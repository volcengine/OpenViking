# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""OIDC (OpenID Connect) authentication plugin.

Supports validating JWT tokens from external OIDC providers (Okta, Auth0, Keycloak, etc.)
and mapping claims to OpenViking identities.
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
from typing import Any, Dict, Optional, TYPE_CHECKING

from fastapi import Request

from openviking.server.auth.identity_mapping import IdentityMapper
from openviking.server.auth.oidc_config import OIDCConfig
from openviking.server.auth.plugin import AuthPlugin
from openviking.server.identity import ResolvedIdentity, Role
from openviking_cli.exceptions import UnauthenticatedError

if TYPE_CHECKING:
    from jose.backends.base import Key

logger = logging.getLogger(__name__)

# Characters allowed in OpenViking user/account identifiers. Any character
# not in this set (e.g. Auth0's "|" in sub="auth0|123456") is replaced with
# "_" during identity mapping.
_ALLOWED_IDENTIFIER_CHARS = re.compile(r"[^a-zA-Z0-9_.@-]")

def _check_jwt_available() -> bool:
    """Lazy check for JWT library availability (cached after first call)."""
    global httpx, JWTError, jwk, jwt, Key, ExpiredSignatureError, JWTClaimsError

    if not hasattr(_check_jwt_available, "_cached"):
        try:
            import httpx  # noqa: F401
            from jose import JWTError, jwk, jwt  # noqa: F401
            from jose.backends.base import Key  # noqa: F401
            from jose.exceptions import ExpiredSignatureError, JWTClaimsError  # noqa: F401
            _check_jwt_available._cached = True
        except ImportError:
            logger.warning(
                "JWT libraries not available. OIDC authentication will not work. "
                "Install with: uv pip install python-jose[cryptography] httpx"
            )
            _check_jwt_available._cached = False
    return _check_jwt_available._cached


# Supported signing algorithms. Asymmetric algorithms are preferred for OIDC;
# HMAC algorithms are included for completeness but require a shared secret.
_SUPPORTED_ALGORITHMS = [
    "RS256", "RS384", "RS512",
    "ES256", "ES384", "ES512",
    "PS256", "PS384", "PS512",
    "HS256", "HS384", "HS512",
]

_DISCOVERY_PATH = "/.well-known/openid-configuration"
_DISCOVERY_TIMEOUT = 10.0


class OIDCAuthPlugin(AuthPlugin):
    """OIDC authentication plugin.

    Validates JWT tokens from an external OIDC provider and maps claims
    to OpenViking account/user identities.
    """

    auth_mode = "oidc"

    def __init__(self) -> None:
        self._config: Optional[OIDCConfig] = None
        self._jwks: Dict[str, Key] = {}
        self._mapper: Optional[IdentityMapper] = None
        self._jwks_uri: Optional[str] = None
        self._discovery_lock = asyncio.Lock()

    async def resolve_identity(
        self,
        request: Request,
        *,
        api_key: Optional[str] = None,
        x_openviking_account: Optional[str] = None,
        x_openviking_user: Optional[str] = None,
    ) -> ResolvedIdentity:
        """Resolve identity from OIDC JWT token."""
        if not _check_jwt_available():
            raise UnauthenticatedError(
                "OIDC authentication not available: missing python-jose and httpx"
            )
        if self._config is None:
            raise RuntimeError("OIDC config not initialized")
        if self._mapper is None:
            raise RuntimeError("Identity mapper not initialized")

        # Extract token from request
        token = self._extract_token(request, api_key)
        if not token:
            raise UnauthenticatedError("Missing OIDC JWT token")

        # Validate token and get claims
        try:
            claims = await self._validate_token(token)
            # Log available claims (safely - don't log sensitive values)
            logger.debug("Available claims from token: %s", list(claims.keys()))
            # Log a safe subset of claims for debugging
            safe_claims = ["iss", "sub", "aud", "exp", "iat", "azp", "kid"]
            for claim in safe_claims:
                if claim in claims:
                    logger.debug("Claim '%s': %s", claim, claims[claim])
        except (JWTError, UnauthenticatedError) as e:
            logger.debug("Token validation failed: %s", e)
            raise UnauthenticatedError(f"Invalid OIDC token: {e}") from e

        # Map claims to OpenViking identity.
        # Role is always USER for OIDC-authenticated identities. External
        # identity providers determine authentication, not authorization;
        # operators who need admin access should use the root API key mechanism.
        try:
            account_id = self._mapper.map_account_id(claims=claims)
            user_id = self._mapper.map_user_id(claims=claims)
            # Sanitize: replace characters not allowed in OpenViking identifiers
            # (e.g. Auth0's "|" in sub="auth0|123456") so that valid OIDC
            # tokens from supported providers always produce a usable identity.
            user_id = _ALLOWED_IDENTIFIER_CHARS.sub("_", user_id)
            account_id = _ALLOWED_IDENTIFIER_CHARS.sub("_", account_id)
        except ValueError as e:
            logger.error("Failed to map claims: %s", e)
            raise UnauthenticatedError(f"Failed to map claims: {e}") from e

        logger.debug(
            "Successfully authenticated OIDC user: account=%s, user=%s, role=%s",
            account_id,
            user_id,
            Role.USER,
        )

        return ResolvedIdentity(role=Role.USER, account_id=account_id, user_id=user_id)

    def _extract_token(
        self, request: Request, api_key: Optional[str] = None
    ) -> Optional[str]:
        """Extract JWT token from request."""
        if self._config is None:
            return None

        # Try Authorization header first
        if self._config.token_location == "header":
            auth_header = request.headers.get(self._config.token_header_name)
            if auth_header:
                prefix = self._config.token_header_prefix
                if prefix and auth_header.startswith(prefix):
                    return auth_header[len(prefix):].strip()
                # Without a configured prefix, accept raw bearer token
                return auth_header.strip()

        # Try query parameter
        if self._config.token_location == "query":
            query_params = request.query_params
            token = query_params.get("access_token")
            if token:
                return token

        # Fallback: check if api_key is a JWT (to support SDK use)
        if api_key and api_key.count(".") == 2:
            # Looks like a JWT (header.payload.signature)
            return api_key

        return None

    async def _validate_token(self, token: str) -> Dict[str, Any]:
        """Validate JWT token and return claims."""
        if self._config is None:
            raise RuntimeError("OIDC config not initialized")

        # Get key ID from token header
        try:
            header = jwt.get_unverified_header(token)
        except JWTError as e:
            raise UnauthenticatedError("Invalid token format") from e

        kid = header.get("kid")
        if not kid:
            raise UnauthenticatedError("Token header missing 'kid'")

        # Get key for validation
        key = await self._get_key(kid)

        # audience is guaranteed non-None after validate_config() — either the
        # operator configured it explicitly or we fell back to client_id.
        # Audience validation must NEVER be skipped: an IdP commonly issues
        # tokens for many audiences, and accepting a token intended for another
        # application would be a cross-client trust violation.
        assert self._effective_audience is not None

        # Validate token
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=_SUPPORTED_ALGORITHMS,
                options={
                    "verify_aud": True,
                    "verify_exp": True,
                    "verify_iss": True,
                },
                issuer=self._config.issuer,
                audience=self._effective_audience,
            )
            return claims
        except ExpiredSignatureError:
            raise UnauthenticatedError("Token expired")
        except JWTClaimsError as e:
            raise UnauthenticatedError(f"Invalid claims: {e}")
        except JWTError as e:
            raise UnauthenticatedError(f"Token validation failed: {e}")

    async def _discover_jwks_uri(self) -> str:
        """Perform OIDC Discovery to locate the JWKS endpoint.

        Standard flow (per OpenID Connect Discovery 1.0):
        1. GET ``{issuer}/.well-known/openid-configuration``
        2. Read the ``jwks_uri`` field from the returned metadata.

        If an explicit ``jwks_uri`` is configured, it takes precedence and
        no discovery request is made.
        """
        if self._config is None:
            raise RuntimeError("OIDC config not initialized")

        if self._config.jwks_uri:
            return self._config.jwks_uri

        async with self._discovery_lock:
            # Re-check after acquiring the lock to avoid redundant fetches.
            if self._jwks_uri:
                return self._jwks_uri

            discovery_url = f"{self._config.issuer}{_DISCOVERY_PATH}"
            logger.info("Performing OIDC discovery at %s", discovery_url)
            try:
                async with httpx.AsyncClient(timeout=_DISCOVERY_TIMEOUT) as client:
                    response = await client.get(discovery_url)
                    response.raise_for_status()
                    metadata = response.json()
            except (httpx.HTTPError, ValueError) as e:
                logger.error("OIDC discovery failed for %s: %s", discovery_url, e)
                raise UnauthenticatedError(
                    f"Failed to fetch OIDC discovery document from {discovery_url}: {e}"
                ) from e

            jwks_uri = metadata.get("jwks_uri")
            if not jwks_uri or not isinstance(jwks_uri, str):
                logger.error(
                    "OIDC discovery document at %s did not contain a jwks_uri",
                    discovery_url,
                )
                raise UnauthenticatedError(
                    "OIDC discovery document is missing 'jwks_uri'"
                )

            logger.info("OIDC discovery resolved jwks_uri=%s", jwks_uri)
            self._jwks_uri = jwks_uri
            return jwks_uri

    async def _get_key(self, kid: str) -> Key:
        """Get JWK key for signature validation."""
        if self._config is None:
            raise RuntimeError("OIDC config not initialized")

        # Return cached key if available
        if kid in self._jwks:
            return self._jwks[kid]

        jwks_uri = await self._discover_jwks_uri()

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(jwks_uri)
                response.raise_for_status()
                jwks = response.json()
        except (httpx.HTTPError, ValueError) as e:
            logger.error("Failed to fetch JWKS from %s: %s", jwks_uri, e)
            raise UnauthenticatedError("Failed to fetch JWKS") from e

        # Process keys and cache them
        for key_dict in jwks.get("keys", []):
            key_kid = key_dict.get("kid")
            if key_kid:
                try:
                    self._jwks[key_kid] = jwk.construct(key_dict)
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "Failed to construct key for kid=%s: %s", key_kid, e
                    )

        if kid not in self._jwks:
            raise UnauthenticatedError(f"Unknown key ID: {kid}")

        return self._jwks[kid]

    def validate_config(self, config) -> None:
        """Validate OIDC configuration.

        Enforces that an audience can be resolved (explicit ``audience`` or
        ``client_id``) so that tokens issued for other clients are rejected.
        """
        if config.oidc is None:
            logger.error(
                "auth_mode=oidc but 'oidc' section missing in config. "
                "Please configure the OIDC provider settings."
            )
            sys.exit(1)

        if not config.oidc.issuer:
            logger.error("OIDC config missing 'issuer'")
            sys.exit(1)

        if not config.oidc.audience and not config.oidc.client_id:
            logger.error(
                "OIDC config requires either 'audience' or 'client_id' to "
                "validate the token's 'aud' claim. Refusing to start without "
                "audience validation."
            )
            sys.exit(1)

        logger.info("OIDC config validated successfully")

    async def initialize(self, app, service, config) -> None:
        """Initialize OIDC plugin."""
        if config.oidc is None:
            raise RuntimeError("OIDC config missing")

        self._config = config.oidc
        self._mapper = IdentityMapper(self._config.identity)

        # Resolve the effective audience once. Explicit audience wins;
        # otherwise fall back to client_id (standard OIDC ID token contract).
        self._effective_audience: Optional[str] = (
            self._config.audience or self._config.client_id
        )

        logger.info(
            "OIDC auth plugin initialized with issuer=%s, audience=%s",
            self._config.issuer,
            self._effective_audience,
        )

    def requires_api_key_manager(self) -> bool:
        """OIDC does not map external identities to admin roles, so no
        APIKeyManager is needed. Admin API access is gated by role checks."""
        return False

    def can_skip_api_key_for_bot_proxy(self) -> bool:
        """OIDC does not declare bot-proxy compatibility until VikingBot
        natively understands the ``oidc`` auth mode and can propagate the
        bearer token to downstream tool calls.

        Returning False here makes ``--with-bot`` fail fast with a clear
        error instead of producing a silent auth-mode mismatch at runtime.
        """
        return False
