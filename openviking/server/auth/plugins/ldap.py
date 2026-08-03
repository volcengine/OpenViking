# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""LDAP authentication plugin.

Supports authenticating users against an LDAP server (Active Directory, OpenLDAP, etc.)
and mapping LDAP attributes to OpenViking identities.
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Dict, List, Optional

from fastapi import Request

from openviking.server.auth.identity_mapping import IdentityMapper
from openviking.server.auth.ldap_config import LDAPConfig
from openviking.server.auth.plugin import AuthPlugin
from openviking.server.identity import ResolvedIdentity, Role
from openviking_cli.exceptions import UnauthenticatedError

logger = logging.getLogger(__name__)

# Try to import LDAP library, provide helpful error if missing
try:
    import ldap
    import ldap.filter

    LDAP_AVAILABLE = True
except ImportError:
    LDAP_AVAILABLE = False
    logger.warning(
        "LDAP library not available. LDAP authentication will not work. "
        "Install with: uv pip install python-ldap"
    )


class LDAPAuthPlugin(AuthPlugin):
    """LDAP authentication plugin.

    Authenticates users against an LDAP server and maps attributes
    to OpenViking account/user identities.
    """

    auth_mode = "ldap"

    def __init__(self) -> None:
        self._config: Optional[LDAPConfig] = None
        self._mapper: Optional[IdentityMapper] = None

    async def resolve_identity(
        self,
        request: Request,
        *,
        api_key: Optional[str] = None,
        x_openviking_account: Optional[str] = None,
        x_openviking_user: Optional[str] = None,
    ) -> ResolvedIdentity:
        """Resolve identity from LDAP authentication."""
        if not LDAP_AVAILABLE:
            raise UnauthenticatedError(
                "LDAP authentication not available: missing python-ldap"
            )
        if self._config is None:
            raise RuntimeError("LDAP config not initialized")
        if self._mapper is None:
            raise RuntimeError("Identity mapper not initialized")

        # Extract credentials from request
        username, password = await self._extract_credentials(
            request, 
            api_key,
            x_openviking_user
        )
        if not username or not password:
            raise UnauthenticatedError("Missing LDAP credentials (username/password)")

        # Authenticate against LDAP server and return attributes
        try:
            attributes = await self._authenticate_ldap(username, password)
            logger.debug("Successfully authenticated LDAP user: %s", username)
            # Log available attributes for debugging
            if attributes:
                logger.debug("Available LDAP attributes: %s", list(attributes.keys()))
        except ldap.LDAPError as e:
            # Log detailed error information
            error_code = getattr(e, "args", [None])[0] if e.args else None
            logger.debug("LDAP authentication failed for user %s - Error: %s, Code: %s", username, str(e), error_code)
            raise UnauthenticatedError(f"LDAP authentication failed: {e}") from e

        # Map LDAP attributes to OpenViking identity
        try:
            # Normalize attributes for mapping
            normalized_attrs = self._normalize_attributes(attributes)
            # Get groups if available
            groups = self._extract_groups(attributes)
            # Get user DN from attributes
            dn_bytes = attributes.get("dn", [None])[0] if attributes else None
            dn = dn_bytes.decode("utf-8") if dn_bytes else None

            account_id = self._mapper.map_account_id(
                claims={}, attributes=normalized_attrs, dn=dn, groups=groups
            )
            user_id = self._mapper.map_user_id(
                claims={}, attributes=normalized_attrs, dn=dn, groups=groups
            )
            role = self._mapper.map_role(
                claims={}, attributes=normalized_attrs, dn=dn, groups=groups
            )
        except ValueError as e:
            logger.error("Failed to map LDAP attributes: %s", e)
            raise UnauthenticatedError(f"Failed to map attributes: {e}") from e

        logger.debug(
            "Successfully authenticated LDAP user: account=%s, user=%s, role=%s",
            account_id,
            user_id,
            role,
        )

        return ResolvedIdentity(role=role, account_id=account_id, user_id=user_id)

    async def _extract_credentials(
        self,
        request: Request,
        api_key: Optional[str] = None,
        x_openviking_user: Optional[str] = None
    ) -> tuple[Optional[str], Optional[str]]:
        """Extract username and password from request."""
        # Try Basic Auth first
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.lower().startswith("basic "):
            import base64

            try:
                encoded = auth_header.split(" ", 1)[1]
                decoded = base64.b64decode(encoded).decode("utf-8")
                username, password = decoded.split(":", 1)
                return username, password
            except Exception:  # noqa: BLE001
                pass

        # Try form data
        try:
            form = await request.form()
            username = form.get("username")
            password = form.get("password")
            if username and password:
                return str(username), str(password)
        except Exception:  # noqa: BLE001
            pass

        # Try query params
        query_params = request.query_params
        username = query_params.get("username")
        password = query_params.get("password")
        if username and password:
            return username, password

        # Fallback: use api_key as password if x_openviking_user is present
        if x_openviking_user and api_key:
            return x_openviking_user, api_key

        return None, None

    async def _authenticate_ldap(
        self, username: str, password: str
    ) -> Dict[str, List[bytes]]:
        """Authenticate user against LDAP server and return attributes."""
        if self._config is None:
            raise RuntimeError("LDAP config not initialized")

        # Build LDAP URL
        protocol = "ldaps" if self._config.use_ssl else "ldap"
        ldap_url = f"{protocol}://{self._config.host}:{self._config.port}"

        # Initialize LDAP connection
        conn = ldap.initialize(ldap_url)

        # Configure connection options
        conn.set_option(ldap.OPT_REFERRALS, 0)  # Disable referrals
        conn.set_option(ldap.OPT_PROTOCOL_VERSION, 3)  # Use LDAPv3

        if self._config.use_starttls and not self._config.use_ssl:
            try:
                conn.start_tls_s()
            except ldap.LDAPError:
                conn.unbind()
                raise

        # Bind with search user if configured, otherwise try direct bind
        user_dn = None
        user_attrs = {}

        try:
            if self._config.bind_dn and self._config.bind_password:
                # Bind with service account first
                logger.debug("Binding with service account: %s", self._config.bind_dn)
                conn.simple_bind_s(self._config.bind_dn, self._config.bind_password)

                # Search for user
                search_filter = self._config.user_search_filter % ldap.filter.escape_filter_chars(username)
                search_base = (
                    self._config.user_search_base or self._config.base_dn
                )
                logger.debug("Searching for user with filter: %s, base: %s", search_filter, search_base)

                result = conn.search_s(
                    search_base,
                    ldap.SCOPE_SUBTREE,
                    search_filter,
                    ["*", self._config.memberof_attribute],
                )

                if not result:
                    logger.debug("No user found with filter: %s", search_filter)
                    raise UnauthenticatedError(f"User {username} not found")

                # Get user DN and attributes
                for dn, attrs in result:
                    if dn:  # Skip referrals
                        user_dn = dn
                        user_attrs = attrs
                        break

                if not user_dn:
                    raise UnauthenticatedError(f"User {username} not found")

                # Unbind and rebind as user to validate password
                conn.unbind()

                # Reconnect and bind as user
                conn = ldap.initialize(ldap_url)
                conn.set_option(ldap.OPT_REFERRALS, 0)
                conn.set_option(ldap.OPT_PROTOCOL_VERSION, 3)

                if self._config.use_starttls and not self._config.use_ssl:
                    conn.start_tls_s()

            elif self._config.user_dn_pattern:
                # Direct bind with user DN pattern
                user_dn = self._config.user_dn_pattern % {
                    "username": username,
                    "user": username,
                }
                # Need to search for attributes after binding
                # First bind as user
            else:
                raise UnauthenticatedError(
                    "LDAP config requires either bind_dn/bind_password or user_dn_pattern"
                )

            # Bind as the user to validate credentials
            conn.simple_bind_s(user_dn, password)

            # If we did direct bind, search for attributes now
            if not user_attrs and self._config.bind_dn:
                # Need to rebind with service account to get attributes
                conn.unbind()
                conn = ldap.initialize(ldap_url)
                conn.set_option(ldap.OPT_REFERRALS, 0)
                conn.set_option(ldap.OPT_PROTOCOL_VERSION, 3)

                if self._config.use_starttls and not self._config.use_ssl:
                    conn.start_tls_s()

                conn.simple_bind_s(self._config.bind_dn, self._config.bind_password)

                search_filter = self._config.user_search_filter % ldap.filter.escape_filter_chars(username)
                search_base = (
                    self._config.user_search_base or self._config.base_dn
                )

                result = conn.search_s(
                    search_base,
                    ldap.SCOPE_SUBTREE,
                    search_filter,
                    ["*", self._config.memberof_attribute],
                )

                for dn, attrs in result:
                    if dn:
                        user_attrs = attrs
                        break

            # Store DN in attributes for mapping
            if user_dn:
                user_attrs["dn"] = [user_dn.encode("utf-8")]

            return user_attrs

        finally:
            try:
                conn.unbind()
            except Exception:  # noqa: BLE001
                pass

    def _normalize_attributes(
        self, attributes: Dict[str, List[bytes]]
    ) -> Dict[str, str]:
        """Normalize LDAP attributes from bytes to strings."""
        normalized = {}
        for key, values in attributes.items():
            if values:
                # Take first value and decode to string
                try:
                    normalized[key.lower()] = values[0].decode("utf-8")
                except UnicodeDecodeError:
                    try:
                        normalized[key.lower()] = values[0].decode("latin-1")
                    except UnicodeDecodeError:
                        normalized[key.lower()] = ""
        return normalized

    def _extract_groups(self, attributes: Dict[str, List[bytes]]) -> List[str]:
        """Extract group membership from attributes."""
        if self._config is None:
            return []

        groups_attr = self._config.memberof_attribute
        if groups_attr not in attributes:
            return []

        groups = []
        for group_dn_bytes in attributes[groups_attr]:
            try:
                group_dn = group_dn_bytes.decode("utf-8")
                groups.append(group_dn)
            except UnicodeDecodeError:
                try:
                    groups.append(group_dn_bytes.decode("latin-1"))
                except UnicodeDecodeError:
                    pass

        return groups

    def validate_config(self, config) -> None:
        """Validate LDAP configuration."""
        if config.ldap is None:
            logger.error(
                "auth_mode=ldap but 'ldap' section missing in config. "
                "Please configure the LDAP server settings."
            )
            sys.exit(1)

        if not config.ldap.host:
            logger.error("LDAP config missing 'host'")
            sys.exit(1)

        if not config.ldap.base_dn:
            logger.error("LDAP config missing 'base_dn'")
            sys.exit(1)

        if not config.ldap.bind_dn and not config.ldap.user_dn_pattern:
            logger.error(
                "LDAP config requires either 'bind_dn'/'bind_password' or 'user_dn_pattern'"
            )
            sys.exit(1)

        if config.ldap.bind_dn and not config.ldap.bind_password:
            logger.warning(
                "LDAP config has 'bind_dn' but missing 'bind_password' — searches may fail"
            )

        logger.info("LDAP config validated successfully")

    async def initialize(self, app, service, config) -> None:
        """Initialize LDAP plugin."""
        if config.ldap is None:
            raise RuntimeError("LDAP config missing")

        self._config = config.ldap
        self._mapper = IdentityMapper(self._config.identity)

        logger.info("LDAP auth plugin initialized with host=%s", self._config.host)

    def requires_api_key_manager(self) -> bool:
        """LDAP doesn't require API key manager by default."""
        return (
            self._config.require_root_api_key_for_admin if self._config else False
        )

    def can_skip_api_key_for_bot_proxy(self) -> bool:
        """Bot proxy can use LDAP."""
        return True
