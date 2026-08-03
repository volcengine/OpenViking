# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""OIDC (OpenID Connect) authentication plugin.

Supports validating JWT tokens from external OIDC providers (Okta, Auth0, Keycloak, etc.)
and mapping claims to OpenViking identities.
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Dict, Optional

from fastapi import Request

from openviking.server.auth.identity_mapping import IdentityMapper
from openviking.server.auth.oidc_config import OIDCConfig
from openviking.server.auth.plugin import AuthPlugin
from openviking.server.identity import ResolvedIdentity, Role
from openviking_cli.exceptions import UnauthenticatedError

logger = logging.getLogger(__name__)

# Try to import JWT libraries, provide helpful error if missing
try:
    import httpx
    from jose import JWTError, jwk, jwt
    from jose.backends.base import Key
    from jose.exceptions import ExpiredSignatureError, JWTClaimsError

    JWT_AVAILABLE = True
except ImportError:
    JWT_AVAILABLE = False
    logger.warning(
        "JWT libraries not available. OIDC authentication will not work. "
        "Install with: uv pip install python-jose[cryptography] httpx"
    )


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

    async def resolve_identity(
        self,
        request: Request,
        *,
        api_key: Optional[str] = None,
        x_openviking_account: Optional[str] = None,
        x_openviking_user: Optional[str] = None,
    ) -> ResolvedIdentity:
        """Resolve identity from OIDC JWT token."""
        if not JWT_AVAILABLE:
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

        # Map claims to OpenViking identity
        try:
            account_id = self._mapper.map_account_id(claims=claims)
            user_id = self._mapper.map_user_id(claims=claims)
            role = self._mapper.map_role(claims=claims)
        except ValueError as e:
            logger.error("Failed to map claims: %s", e)
            raise UnauthenticatedError(f"Failed to map claims: {e}") from e

        logger.debug(
            "Successfully authenticated OIDC user: account=%s, user=%s, role=%s",
            account_id,
            user_id,
            role,
        )

        return ResolvedIdentity(role=role, account_id=account_id, user_id=user_id)

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
                if auth_header.startswith(prefix):
                    return auth_header[len(prefix) :].strip()
                # Also try raw bearer token without prefix
                return auth_header.strip()

        # Try query parameter
        if self._config.token_location == "query":
            query_params = request.query_params
            token = query_params.get("access_token")
            if token:
                return token

        # Fallback: check if api_key is a JWT (to support SDK use)
        if api_key and "." in api_key:
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

        # Validate token
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "HS256", "HS384", "HS512"],
                options={
                    "verify_aud": bool(self._config.audience),
                    "verify_exp": True,
                    "verify_iss": True,
                },
                issuer=self._config.issuer,
                audience=self._config.audience,
            )
            return claims
        except ExpiredSignatureError:
            raise UnauthenticatedError("Token expired")
        except JWTClaimsError as e:
            raise UnauthenticatedError(f"Invalid claims: {e}")
        except JWTError as e:
            raise UnauthenticatedError(f"Token validation failed: {e}")

    async def _get_key(self, kid: str) -> Key:
        """Get JWK key for signature validation."""
        if self._config is None:
            raise RuntimeError("OIDC config not initialized")

        # Return cached key if available
        if kid in self._jwks:
            return self._jwks[kid]

        # Fetch JWKS from issuer
        jwks_uri = self._config.jwks_uri or f"{self._config.issuer}/.well-known/jwks.json"

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(jwks_uri, timeout=10)
                response.raise_for_status()
                jwks = response.json()
        except httpx.HTTPError as e:
            logger.error("Failed to fetch JWKS: %s", e)
            raise UnauthenticatedError("Failed to fetch JWKS") from e

        # Process keys and cache them
        for key_dict in jwks.get("keys", []):
            key_kid = key_dict.get("kid")
            if key_kid:
                try:
                    self._jwks[key_kid] = jwk.construct(key_dict)
                except Exception as e:  # noqa: BLE001
                    logger.warning("Failed to construct key for kid=%s: %s", key_kid, e)

        if kid not in self._jwks:
            raise UnauthenticatedError(f"Unknown key ID: {kid}")

        return self._jwks[kid]

    def validate_config(self, config) -> None:
        """Validate OIDC configuration."""
        if config.oidc is None:
            logger.error(
                "auth_mode=oidc but 'oidc' section missing in config. "
                "Please configure the OIDC provider settings."
            )
            sys.exit(1)

        if not config.oidc.issuer:
            logger.error("OIDC config missing 'issuer'")
            sys.exit(1)

        logger.info("OIDC config validated successfully")

    async def initialize(self, app, service, config) -> None:
        """Initialize OIDC plugin."""
        if config.oidc is None:
            raise RuntimeError("OIDC config missing")

        self._config = config.oidc
        self._mapper = IdentityMapper(self._config.identity)

        logger.info("OIDC auth plugin initialized with issuer=%s", self._config.issuer)

    def requires_api_key_manager(self) -> bool:
        """OIDC doesn't require API key manager by default."""
        return (
            self._config.require_root_api_key_for_admin if self._config else False
        )

    def can_skip_api_key_for_bot_proxy(self) -> bool:
        """Bot proxy can use OIDC."""
        return True
