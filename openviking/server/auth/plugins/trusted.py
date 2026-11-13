# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Trusted mode authentication plugin."""

from __future__ import annotations

import asyncio
import hmac
import random
import sys
from typing import Optional

from fastapi import Request

from openviking.core.identifiers import (
    validate_account_id,
    validate_identifier_length,
    validate_identifier_part,
    validate_user_id,
)
from openviking.server.account_stores.file import validate_file_account_id
from openviking.server.api_keys import APIKeyManager
from openviking.server.auth.plugin import AuthPlugin
from openviking.server.identity import ResolvedIdentity, Role
from openviking.server.store_assembly import build_api_key_manager
from openviking_cli.exceptions import InvalidArgumentError, UnauthenticatedError
from openviking_cli.utils import get_logger

_TRUSTED_ROLE_HEADER = "X-OpenViking-Role"
_TRUSTED_GROUPS_HEADER = "X-OpenViking-Groups"
_TRUSTED_ASSERTABLE_ROLES = {
    Role.USER: Role.USER,
    Role.ADMIN: Role.ADMIN,
}

logger = get_logger(__name__)


def _normalize_request_value(value: object) -> Optional[str]:
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return None


def _explicit_identity_from_request(request: Request) -> tuple[Optional[str], Optional[str]]:
    path_params = getattr(request, "path_params", {}) or {}
    query_params = request.query_params

    account_id = _normalize_request_value(path_params.get("account_id"))
    if account_id is None:
        account_id = _normalize_request_value(query_params.get("account_id"))

    user_id = _normalize_request_value(path_params.get("user_id"))
    if user_id is None:
        user_id = _normalize_request_value(query_params.get("user_id"))

    return account_id, user_id


def _validate_trusted_identity(account_id: str, user_id: str) -> None:
    """Reject invalid identity path components before registry lookup or enqueueing."""
    account_error = validate_account_id(account_id)
    if account_error:
        raise InvalidArgumentError(account_error)
    user_error = validate_user_id(user_id)
    if user_error:
        raise InvalidArgumentError(user_error)


def _is_trusted_admin_path(path: str) -> bool:
    return path == "/api/v1/admin" or path.startswith("/api/v1/admin/")


def _asserted_role_from_header(request: Request, *, allow_assertion: bool) -> Optional[Role]:
    raw_role = request.headers.get(_TRUSTED_ROLE_HEADER)
    normalized_role = _normalize_request_value(raw_role)
    if normalized_role is None:
        return None
    if not allow_assertion:
        raise InvalidArgumentError(
            f"{_TRUSTED_ROLE_HEADER} requires trusted mode with Root API Key enabled."
        )

    role = normalized_role.lower()
    if role not in _TRUSTED_ASSERTABLE_ROLES:
        raise InvalidArgumentError(f"{_TRUSTED_ROLE_HEADER} must be one of: user, admin.")
    return _TRUSTED_ASSERTABLE_ROLES[role]


def _group_ids_from_header(request: Request) -> tuple[str, ...]:
    raw_groups = request.headers.get(_TRUSTED_GROUPS_HEADER)
    if raw_groups is None:
        return ()
    group_ids: list[str] = []
    seen: set[str] = set()
    for raw_group_id in raw_groups.split(","):
        group_id = raw_group_id.strip()
        if not group_id:
            continue
        if error := validate_identifier_part(group_id, "group_id"):
            raise InvalidArgumentError(error)
        if group_id not in seen:
            group_ids.append(group_id)
            seen.add(group_id)
    return tuple(group_ids)


class TrustedAuthPlugin(AuthPlugin):
    """Trusted mode: trust X-OpenViking-Account/User headers.

    Requires a matching root_api_key on every authenticated request. Role is looked up from
    AccountStore if the user exists, otherwise defaults to USER.
    """

    auth_mode = "trusted"

    def __init__(self) -> None:
        self._api_key_manager: Optional[APIKeyManager] = None
        self._flush_interval_seconds = 300.0
        self._pending_max_size = 10_000
        self._pending: dict[str, set[str]] = {}
        self._in_flight: dict[str, set[str]] = {}
        self._pending_count = 0
        self._in_flight_count = 0
        self._flush_task: Optional[asyncio.Task] = None
        self._flush_lock = asyncio.Lock()

    async def resolve_identity(
        self,
        request: Request,
        *,
        api_key: Optional[str] = None,
        x_openviking_account: Optional[str] = None,
        x_openviking_user: Optional[str] = None,
    ) -> ResolvedIdentity:
        if api_key is None:
            api_key = request.headers.get("X-API-Key")
        configured_root_api_key = request.app.state.config.root_api_key
        if not api_key:
            raise UnauthenticatedError(
                "Missing API Key in trusted mode. Send X-API-Key with the value of "
                "server.root_api_key from the server's ov.conf."
            )
        if not configured_root_api_key or not hmac.compare_digest(api_key, configured_root_api_key):
            raise UnauthenticatedError(
                "Invalid API Key in trusted mode. The supplied key must match "
                "server.root_api_key in the server's ov.conf."
            )
        asserted_role = _asserted_role_from_header(request, allow_assertion=True)

        explicit_account_id, explicit_user_id = _explicit_identity_from_request(request)
        if (
            x_openviking_account
            and explicit_account_id
            and x_openviking_account != explicit_account_id
        ):
            raise InvalidArgumentError(
                "Trusted mode X-OpenViking-Account must match explicit account_id in the URL."
            )
        if x_openviking_user and explicit_user_id and x_openviking_user != explicit_user_id:
            raise InvalidArgumentError(
                "Trusted mode X-OpenViking-User must match explicit user_id in the URL."
            )

        effective_account_id = explicit_account_id or x_openviking_account
        effective_user_id = explicit_user_id or x_openviking_user

        # A verified Root key is the caller identity for admin routes. Path
        # parameters such as ``account_id`` identify the managed resource, so
        # they must not be mistaken for a partial caller identity.
        is_admin_path = _is_trusted_admin_path(request.url.path)
        # A key-only health probe reports control-plane identity. Probes with
        # tenant identity keep reporting the asserted/resolved data-plane role.
        is_control_probe = (
            request.url.path == "/health"
            and not effective_account_id
            and not effective_user_id
            and asserted_role is None
        )
        if is_admin_path or is_control_probe:
            return ResolvedIdentity(
                role=Role.ROOT,
                account_id=effective_account_id or "trusted",
                user_id=effective_user_id or "trusted",
            )

        missing_fields = []
        if not effective_account_id:
            missing_fields.append("X-OpenViking-Account or explicit account_id in the URL")
        if not effective_user_id:
            missing_fields.append("X-OpenViking-User or explicit user_id in the URL")
        if missing_fields:
            raise InvalidArgumentError(
                "Trusted mode requests must include " + " and ".join(missing_fields) + "."
            )

        _validate_trusted_identity(effective_account_id, effective_user_id)

        local_role = None
        if asserted_role is not None:
            trusted_role = asserted_role
        else:
            if self._api_key_manager and effective_account_id and effective_user_id:
                local_role = await self._api_key_manager.get_registered_user_role(
                    effective_account_id, effective_user_id
                )
            trusted_role = local_role or Role.USER

        identity = ResolvedIdentity(
            role=trusted_role,
            account_id=effective_account_id,
            user_id=effective_user_id,
            group_ids=_group_ids_from_header(request),
        )
        if (
            self.should_auto_register_trusted_identity(request)
            and not is_admin_path
            and effective_account_id
            and effective_user_id
        ):
            await self._queue_trusted_identity(effective_account_id, effective_user_id)
        return identity

    def validate_config(self, config) -> None:
        if config.root_api_key and config.root_api_key.strip():
            return
        logger.error(
            "Cannot start OpenViking: trusted mode requires a non-empty server.root_api_key, "
            "including on localhost. Set server.root_api_key in the server's ov.conf and "
            "restart. Configure clients and Web Studio to use the same key."
        )
        sys.exit(1)

    async def initialize(self, app, service, config) -> None:
        manager = build_api_key_manager(
            root_key=config.root_api_key,
            viking_fs=service.viking_fs,
            api_key_hashing_enabled=config.api_key_hashing_enabled,
            service_name=app.state.service_name,
            account_store_provider=config.account_store.provider,
            account_store_params=config.account_store.params,
            account_store_watch_enabled=config.api_key_watch_enabled,
            account_store_watch_interval_seconds=config.api_key_watch_interval_seconds,
            issue_user_keys=False,
        )
        await manager.load()
        self._api_key_manager = manager
        app.state.api_key_manager = manager
        self._flush_interval_seconds = config.trusted_identity_flush_interval_seconds
        self._pending_max_size = config.trusted_identity_pending_max_size
        if self._flush_interval_seconds > 0:
            self._flush_task = asyncio.create_task(self._flush_loop())

    def should_auto_register_trusted_identity(
        self,
        request: Request,
    ) -> bool:
        """Return whether this trusted request's identity should be batch registered."""
        return self._flush_interval_seconds > 0

    async def _queue_trusted_identity(self, account_id: str, user_id: str) -> None:
        manager = self._api_key_manager
        if manager is not None:
            if await manager.get_deletion(account_id, user_id) is not None:
                return
            if await manager.get_user(account_id, user_id) is not None:
                return
        if length_error := validate_identifier_length(user_id, "user_id"):
            raise InvalidArgumentError(length_error)
        if user_id in self._in_flight.get(account_id, set()):
            return
        if user_id in self._pending.get(account_id, set()):
            return
        try:
            validate_file_account_id(account_id)
        except InvalidArgumentError:
            # Existing remote-backed accounts may exceed the new-account limit.
            if manager is None or await manager.get_account(account_id) is None:
                raise
        if self._pending_count + self._in_flight_count >= self._pending_max_size:
            logger.warning(
                "Trusted identity registration queue is full; dropping %s/%s",
                account_id,
                user_id,
            )
            return
        self._pending.setdefault(account_id, set()).add(user_id)
        self._pending_count += 1

    async def flush_trusted_identities(self) -> None:
        async with self._flush_lock:
            await self._flush_batch()

    async def _flush_batch(self) -> None:
        if self._api_key_manager is None or not self._pending:
            return
        batch = self._pending
        self._pending = {}
        self._in_flight_count = self._pending_count
        self._pending_count = 0
        self._in_flight = batch
        try:
            await self._api_key_manager.ensure_trusted_identities(batch)
        except BaseException as exc:
            pending_after_failure = self._pending
            for account_id, user_ids in batch.items():
                pending_after_failure.setdefault(account_id, set()).update(user_ids)
            self._pending = pending_after_failure
            self._pending_count += self._in_flight_count
            if not isinstance(exc, Exception):
                raise
            logger.warning("Trusted identity batch registration failed; will retry", exc_info=True)
        finally:
            self._in_flight = {}
            self._in_flight_count = 0

    async def _flush_loop(self) -> None:
        try:
            await asyncio.sleep(
                self._flush_interval_seconds + random.uniform(0, self._flush_interval_seconds)
            )
            while True:
                await self.flush_trusted_identities()
                await asyncio.sleep(self._flush_interval_seconds)
        except asyncio.CancelledError:
            raise

    async def shutdown(self) -> None:
        if self._flush_task is not None:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
            finally:
                self._flush_task = None
        try:
            await self.flush_trusted_identities()
        finally:
            manager = self._api_key_manager
            try:
                if manager is not None:
                    await manager.close()
            finally:
                self._api_key_manager = None

    def can_skip_api_key_for_bot_proxy(self) -> bool:
        return True

    def get_request_context_checks(
        self,
        path: str,
        identity: ResolvedIdentity,
    ) -> None:
        is_admin_path = _is_trusted_admin_path(path)
        if not is_admin_path:
            if not identity.account_id:
                raise InvalidArgumentError(
                    "Trusted mode requests must include X-OpenViking-Account."
                )
            if not identity.user_id:
                raise InvalidArgumentError("Trusted mode requests must include X-OpenViking-User.")
