# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""LDAP authentication plugin.

Supports authenticating users against an LDAP server (Active Directory, OpenLDAP, etc.)
and mapping LDAP attributes to OpenViking identities.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any, Dict, List, Optional, Tuple

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


# Network/operation timeout (seconds) for synchronous LDAP calls. The calls
# are offloaded to a thread executor (see _authenticate_ldap), but a hung LDAP
# server must not be allowed to tie up a thread indefinitely.
_LDAP_NETWORK_TIMEOUT = 10.0
_LDAP_OPERATION_TIMEOUT = 10.0


def _apply_ldap_connection_options(conn: Any) -> None:
    """Apply standard connection options including network timeouts."""
    conn.set_option(ldap.OPT_REFERRALS, 0)  # Disable chasing referrals
    conn.set_option(ldap.OPT_PROTOCOL_VERSION, 3)  # LDAPv3
    # Bound blocking I/O — without these a dead LDAP server can hang the
    # worker thread for the OS default (often minutes).
    conn.set_option(ldap.OPT_NETWORK_TIMEOUT, _LDAP_NETWORK_TIMEOUT)
    conn.set_option(ldap.OPT_TIMEOUT, _LDAP_OPERATION_TIMEOUT)


def _format_user_dn(pattern: str, username: str) -> str:
    """Render a user DN pattern.

    Supports two idioms:

    * ``"%s"`` positional substitution, the classic LDAP template style
      (``uid=%s,ou=users,dc=example,dc=com``).
    * ``"%(username)s"`` / ``"%(user)s"`` mapping substitution.
    """
    if "%(" in pattern:
        return pattern % {"username": username, "user": username}
    # Only substitute a single %s; treat %% as an escaped %.
    return pattern % (username,)


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
            x_openviking_user,
        )
        if not username or not password:
            raise UnauthenticatedError("Missing LDAP credentials (username/password)")

        # Authenticate against LDAP server and return attributes.
        # python-ldap is a synchronous binding to OpenLDAP's libldap; every
        # network call blocks. Offload to the default thread executor so a
        # slow/unreachable directory cannot stall the event loop (and thus
        # unrelated requests on the same worker).
        try:
            attributes = await asyncio.to_thread(
                self._authenticate_ldap, username, password
            )
            logger.debug("Successfully authenticated LDAP user: %s", username)
            # Log available attributes for debugging
            if attributes:
                logger.debug(
                    "Available LDAP attributes: %s", list(attributes.keys())
                )
        except ldap.LDAPError as e:
            error_code = getattr(e, "args", [None])[0] if e.args else None
            logger.debug(
                "LDAP authentication failed for user %s - Error: %s, Code: %s",
                username,
                str(e),
                error_code,
            )
            raise UnauthenticatedError(f"LDAP authentication failed: {e}") from e

        # Map LDAP attributes to OpenViking identity.
        # Role is always USER for LDAP-authenticated identities. This is by
        # design: LDAP authenticates directory users, and their role in the
        # directory (e.g., membership in an "admins" group) should not
        # automatically grant OpenViking admin privileges. Operators who
        # need admin access should use the root API key mechanism.
        try:
            normalized_attrs = self._normalize_attributes(attributes)
            dn_bytes = attributes.get("dn", [None])[0] if attributes else None
            dn = dn_bytes.decode("utf-8") if dn_bytes else None

            account_id = self._mapper.map_account_id(
                claims={}, attributes=normalized_attrs, dn=dn
            )
            user_id = self._mapper.map_user_id(
                claims={}, attributes=normalized_attrs, dn=dn
            )
        except ValueError as e:
            logger.error("Failed to map LDAP attributes: %s", e)
            raise UnauthenticatedError(f"Failed to map attributes: {e}") from e

        logger.debug(
            "Successfully authenticated LDAP user: account=%s, user=%s, role=%s",
            account_id,
            user_id,
            Role.USER,
        )

        return ResolvedIdentity(role=Role.USER, account_id=account_id, user_id=user_id)

    async def _extract_credentials(
        self,
        request: Request,
        api_key: Optional[str] = None,
        x_openviking_user: Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[str]]:
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

        # Fallback: use api_key as password if x_openviking_user is present
        if x_openviking_user and api_key:
            return x_openviking_user, api_key

        return None, None

    def _new_connection(self, ldap_url: str) -> Any:
        conn = ldap.initialize(ldap_url)
        _apply_ldap_connection_options(conn)
        if self._config.use_starttls and not self._config.use_ssl:
            conn.start_tls_s()
        return conn

    def _search_user_attributes(
        self,
        conn: Any,
        username: str,
    ) -> Tuple[Optional[str], Dict[str, List[bytes]]]:
        """Search for a user's DN and attributes using the bound connection.

        Used both by the search+bind flow (with the service account) and the
        direct-bind flow (where the bound user typically has read access to
        their own entry).
        """
        assert self._config is not None
        safe_username = ldap.filter.escape_filter_chars(username)
        search_filter = self._config.user_search_filter % safe_username
        search_base = (
            self._config.user_search_base or self._config.base_dn
        )
        logger.debug(
            "Searching for user with filter: %s, base: %s",
            search_filter,
            search_base,
        )

        result = conn.search_s(
            search_base,
            ldap.SCOPE_SUBTREE,
            search_filter,
            ["*"],
        )

        for dn, attrs in result:
            if dn:  # Skip referrals / continuation references
                return dn, attrs
        return None, {}

    def _authenticate_ldap(
        self, username: str, password: str
    ) -> Dict[str, List[bytes]]:
        """Authenticate user against LDAP server and return attributes.

        This is a synchronous method; callers must run it in a worker thread.
        """
        if self._config is None:
            raise RuntimeError("LDAP config not initialized")

        protocol = "ldaps" if self._config.use_ssl else "ldap"
        ldap_url = f"{protocol}://{self._config.host}:{self._config.port}"

        user_dn: Optional[str] = None
        user_attrs: Dict[str, List[bytes]] = {}

        if self._config.bind_dn and self._config.bind_password:
            # --- Search + Bind flow -----------------------------------------
            # 1. Bind as a service account, search for the user entry.
            # 2. Rebind as the user to verify the supplied password.
            # 3. Re-bind as the service account to read attributes (the user
            #    bind may not have read access to its own object in some
            #    deployments).
            search_conn = self._new_connection(ldap_url)
            try:
                search_conn.simple_bind_s(
                    self._config.bind_dn, self._config.bind_password
                )
                user_dn, user_attrs = self._search_user_attributes(
                    search_conn, username
                )
            finally:
                try:
                    search_conn.unbind()
                except Exception:  # noqa: BLE001
                    pass

            if not user_dn:
                raise UnauthenticatedError(f"User {username} not found")

            # Validate the user's credentials.
            user_conn = self._new_connection(ldap_url)
            try:
                user_conn.simple_bind_s(user_dn, password)
            finally:
                try:
                    user_conn.unbind()
                except Exception:  # noqa: BLE001
                    pass

            # Re-bind as the service account to fetch attributes if the first
            # search didn't return them (shouldn't normally happen).
            if not user_attrs:
                attr_conn = self._new_connection(ldap_url)
                try:
                    attr_conn.simple_bind_s(
                        self._config.bind_dn, self._config.bind_password
                    )
                    user_dn, user_attrs = self._search_user_attributes(
                        attr_conn, username
                    )
                finally:
                    try:
                        attr_conn.unbind()
                    except Exception:  # noqa: BLE001
                        pass

        elif self._config.user_dn_pattern:
            # --- Direct Bind flow -------------------------------------------
            # No service account: construct the user's DN from the pattern,
            # bind as the user directly, then attempt to read their own
            # attributes using the bound connection.
            user_dn = _format_user_dn(self._config.user_dn_pattern, username)

            conn = self._new_connection(ldap_url)
            try:
                conn.simple_bind_s(user_dn, password)
                # Best-effort attribute read: most directory ACLs permit a
                # user to read their own entry. If the server forbids it, we
                # fall back to synthesizing attributes from the DN so that
                # the documented ``user_id <- uid`` default still works for
                # ``uid=<username>,...`` patterns.
                try:
                    _, found_attrs = self._search_user_attributes(conn, username)
                    if found_attrs:
                        user_attrs = found_attrs
                except ldap.LDAPError as e:
                    logger.debug(
                        "Direct bind: unable to read own attributes for %s: %s",
                        username,
                        e,
                    )
            finally:
                try:
                    conn.unbind()
                except Exception:  # noqa: BLE001
                    pass

            if not user_attrs:
                # Fallback: derive the RDN attribute/value pair from the DN.
                # For ``uid=alice,ou=users,...`` this yields {"uid": [b"alice"]}.
                user_attrs = self._attrs_from_dn(user_dn)
        else:
            raise UnauthenticatedError(
                "LDAP config requires either bind_dn/bind_password or "
                "user_dn_pattern"
            )

        # Ensure DN is present in attributes for identity mapping.
        if user_dn:
            user_attrs.setdefault("dn", [user_dn.encode("utf-8")])
            # Always make the DN RDN value available under its attribute name
            # so the default user_id <- uid mapping works even if the server
            # returned no attributes.
            rdn_attr, rdn_value = self._parse_first_rdn(user_dn)
            if rdn_attr and rdn_value:
                user_attrs.setdefault(
                    rdn_attr.lower(), [rdn_value.encode("utf-8")]
                )

        return user_attrs

    @staticmethod
    def _parse_first_rdn(dn: str) -> Tuple[Optional[str], Optional[str]]:
        """Parse the first RDN of a DN, returning (attr_name, value)."""
        if not dn:
            return None, None
        first = dn.split(",", 1)[0]
        if "=" not in first:
            return None, None
        attr, _, value = first.partition("=")
        return attr.strip(), value.strip()

    @classmethod
    def _attrs_from_dn(cls, dn: str) -> Dict[str, List[bytes]]:
        """Build a minimal attribute dict from a DN.

        Used as a fallback when a direct-bind user cannot read their own
        entry. Extracts the first RDN pair (e.g. ``uid=alice``).
        """
        attr, value = cls._parse_first_rdn(dn)
        if not attr or not value:
            return {}
        return {attr.lower(): [value.encode("utf-8")]}

    def _normalize_attributes(
        self, attributes: Dict[str, List[bytes]]
    ) -> Dict[str, str]:
        """Normalize LDAP attributes from bytes to strings."""
        normalized: Dict[str, str] = {}
        for key, values in attributes.items():
            if values:
                try:
                    normalized[key.lower()] = values[0].decode("utf-8")
                except UnicodeDecodeError:
                    try:
                        normalized[key.lower()] = values[0].decode("latin-1")
                    except UnicodeDecodeError:
                        normalized[key.lower()] = ""
        return normalized

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
                "LDAP config requires either 'bind_dn'/'bind_password' or "
                "'user_dn_pattern'"
            )
            sys.exit(1)

        if config.ldap.bind_dn and not config.ldap.bind_password:
            logger.warning(
                "LDAP config has 'bind_dn' but missing 'bind_password' — "
                "searches may fail"
            )

        logger.info("LDAP config validated successfully")

    async def initialize(self, app, service, config) -> None:
        """Initialize LDAP plugin."""
        if config.ldap is None:
            raise RuntimeError("LDAP config missing")

        self._config = config.ldap

        # LDAPConfig supplies LDAP-specific defaults (user_id <- uid,
        # fixed USER role) via default_factory, so no fallback is needed
        # here. Operators can still override via the `identity` config block.
        self._mapper = IdentityMapper(self._config.identity)

        logger.info(
            "LDAP auth plugin initialized with host=%s", self._config.host
        )

    def requires_api_key_manager(self) -> bool:
        """LDAP does not map external identities to admin roles, so no
        APIKeyManager is needed. Admin API access is gated by role checks."""
        return False

    def can_skip_api_key_for_bot_proxy(self) -> bool:
        """LDAP does not declare bot-proxy compatibility until VikingBot
        natively understands the ``ldap`` auth mode (mode inference, Basic
        Auth credential propagation, and identity forwarding to bot tools).

        Returning False here makes ``--with-bot`` fail fast with a clear
        error instead of producing a silent auth-mode mismatch at runtime.
        """
        return False
