# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""API key operations over a complete account store."""

import base64
import hmac
import secrets
from typing import Optional, Tuple

from typing_extensions import deprecated

from openviking.server.account_stores.base import AccountStore
from openviking.server.api_keys.legacy import (
    derive_seeded_api_key_secret,
)
from openviking.server.identity import ResolvedIdentity, Role
from openviking_cli.exceptions import (
    FailedPreconditionError,
    NotFoundError,
    UnauthenticatedError,
)


def _encode_segment(data: str) -> str:
    encoded = base64.urlsafe_b64encode(data.encode("utf-8"))
    return encoded.decode("utf-8").rstrip("=")


def _decode_segment(encoded: str) -> str:
    padding_needed = 4 - (len(encoded) % 4)
    if padding_needed != 4:
        encoded += "=" * padding_needed
    return base64.urlsafe_b64decode(encoded.encode("utf-8")).decode("utf-8")


def is_new_format_key(api_key: str) -> bool:
    return bool(api_key) and len(api_key.split(".")) == 3


def parse_api_key(api_key: str) -> Tuple[str, str, str]:
    if not is_new_format_key(api_key):
        raise ValueError("Not a new format API key")
    account_id, user_id, secret = api_key.split(".")
    return (
        _decode_segment(account_id),
        _decode_segment(user_id),
        _decode_segment(secret),
    )


def generate_api_key(account_id: str, user_id: str, seed: Optional[str] = None) -> str:
    secret = (
        derive_seeded_api_key_secret(user_id, seed) if seed is not None else secrets.token_hex(32)
    )
    return ".".join(map(_encode_segment, (account_id, user_id, secret)))


class APIKeyManager:
    """Coordinate API key operations over an already-constructed account store."""

    def __init__(
        self,
        root_key: str,
        store: AccountStore,
        *,
        issue_user_keys: bool = True,
    ) -> None:
        self._store = store
        self._root_api_key = root_key
        self._issue_user_keys = issue_user_keys

    async def load(self) -> None:
        try:
            await self._store.load()
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        await self._store.close()

    async def _require_authenticatable_user(self, account_id: str, user_id: str):
        user = await self.get_user(account_id, user_id)
        if user is None:
            raise UnauthenticatedError("Unknown account or user")
        deletion = await self.get_deletion(account_id, user_id)
        if deletion is not None:
            raise UnauthenticatedError("Invalid API key")
        return user

    async def _require_manageable_user(self, account_id: str, user_id: str):
        account = await self.get_account(account_id)
        if account is None:
            raise NotFoundError(account_id, "account")
        if account["status"] != "active":
            raise FailedPreconditionError(
                "Account deletion is in progress",
                details={"task_id": account.get("task_id")},
            )
        user = await self.get_user_for_management(account_id, user_id)
        if user is None:
            raise NotFoundError(user_id, "user")
        deletion = await self.get_deletion(account_id, user_id)
        if deletion is not None:
            raise FailedPreconditionError(
                "User deletion is in progress",
                details={"task_id": deletion.get("task_id")},
            )
        return user

    async def resolve_identity(self, api_key: str) -> ResolvedIdentity:
        if self._root_api_key and hmac.compare_digest(api_key, self._root_api_key):
            return ResolvedIdentity(role=Role.ROOT)
        account_id, user_id = await self._verify_key(api_key)
        user = await self._require_authenticatable_user(account_id, user_id)
        return ResolvedIdentity(
            role=Role(user["role"]),
            account_id=account_id,
            user_id=user_id,
        )

    async def _verify_key(self, key: str):
        account_id = None
        user_id = None
        if is_new_format_key(key):
            try:
                account_id, user_id, _ = parse_api_key(key)
            except (ValueError, UnicodeError):
                raise UnauthenticatedError("Invalid API key") from None
        binding = await self._store.verify_api_key(
            key, account_id_hint=account_id, user_id_hint=user_id
        )
        if binding is not None:
            return binding
        raise UnauthenticatedError("Invalid API key")

    async def create_account(
        self,
        account_id: str,
        admin_user_id: str,
        seed: Optional[str] = None,
    ) -> str | None:
        if not self._issue_user_keys:
            await self._store.create_account(account_id, admin_user_id)
            return None

        key = generate_api_key(account_id, admin_user_id, seed)
        await self._store.create_account_with_api_key(account_id, admin_user_id, key)
        return key

    async def delete_account(self, account_id: str) -> None:
        await self._store.delete_account(account_id)

    async def register_user(
        self,
        account_id: str,
        user_id: str,
        role: str = "user",
        seed: Optional[str] = None,
    ) -> str | None:
        if not self._issue_user_keys:
            await self._store.create_user(account_id, user_id, role)
            return None

        key = generate_api_key(account_id, user_id, seed)
        await self._store.create_user_with_api_key(account_id, user_id, role, key)
        return key

    async def regenerate_key(self, account_id, user_id, seed=None):
        if not self._issue_user_keys:
            raise FailedPreconditionError("Personal API keys are disabled in trusted mode")
        await self._require_manageable_user(account_id, user_id)
        key = generate_api_key(account_id, user_id, seed)
        await self._store.replace_active_user_api_key(account_id, user_id, key)
        return key

    async def get_registered_user_role(self, account_id: str, user_id: str) -> Role | None:
        user = await self.get_user(account_id, user_id)
        return Role(user["role"]) if user else None

    async def resolve_oauth_identity(
        self, account_id: str, user_id: str, role: Role
    ) -> ResolvedIdentity:
        user = await self._require_authenticatable_user(account_id, user_id)
        if role.rank > Role(user["role"]).rank:
            raise UnauthenticatedError(
                "OAuth token's embedded role exceeds the user's current role; "
                "please re-authorize the client."
            )
        return ResolvedIdentity(
            role=role,
            account_id=account_id,
            user_id=user_id,
            from_oauth=True,
        )

    async def ensure_trusted_identities(self, identities):
        return await self._store.ensure_trusted_identities(identities)

    async def begin_deletion(self, account_id, user_id, **owner):
        return await self._store.begin_deletion(account_id, user_id, **owner)

    async def replace_deletion_task(self, account_id, user_id, **owner):
        return await self._store.replace_deletion_task(account_id, user_id, **owner)

    async def finish_deletion(self, account_id, user_id, task_id):
        return await self._store.finish_deletion(account_id, user_id, task_id)

    async def get_deletion(self, account_id, user_id=None):
        return await self._store.get_deletion(account_id, user_id)

    async def iter_deletions(self):
        return await self._store.iter_deletions()

    async def get_account(self, account_id):
        return await self._store.get_account(account_id)

    async def get_user(self, account_id, user_id):
        return await self._store.get_user(account_id, user_id)

    async def get_user_membership(self, account_id, user_id):
        return await self._store.get_user_membership(account_id, user_id)

    @deprecated(
        "get_user_for_management is a FileStore compatibility path and will be removed.",
        category=None,
    )
    async def get_user_for_management(self, account_id, user_id):
        return await self._store.get_user_for_management(account_id, user_id)

    async def list_accounts(self, **filters):
        return await self._store.list_accounts(**filters)

    async def list_users_page(self, account_id, **filters):
        expose_key = filters.pop("expose_key", True)
        store_filters = {
            name: filters[name]
            for name in (
                "limit",
                "name_filter",
                "role_filter",
                "page",
                "query_filter",
                "fresh",
            )
            if name in filters
        }
        page = await self._store.list_users_page(
            account_id, **store_filters
        )
        if not expose_key or not self._issue_user_keys:
            page["key_count"] = 0
            for user in page["users"]:
                user.pop("api_key", None)
                user.pop("key_prefix", None)
        return page

    async def has_user(self, account_id, user_id):
        return await self.get_user(account_id, user_id) is not None

    async def get_user_group_ids(self, account_id, user_id):
        return await self._store.get_user_group_ids(account_id, user_id)

    async def set_role(self, account_id, user_id, role):
        await self._store.set_role(account_id, user_id, role)

    async def create_group(self, account_id, group_id):
        return await self._store.create_group(account_id, group_id)

    async def get_groups(self, account_id):
        return await self._store.get_groups(account_id)

    async def get_group_members(self, account_id, group_id):
        return await self._store.get_group_members(account_id, group_id)

    async def add_group_member(self, account_id, group_id, user_id):
        return await self._store.add_group_member(account_id, group_id, user_id)

    async def remove_group_member(self, account_id, group_id, user_id):
        return await self._store.remove_group_member(account_id, group_id, user_id)

    async def delete_group(self, account_id, group_id):
        await self._store.delete_group(account_id, group_id)

    async def get_user_key_fingerprint(self, account_id, user_id):
        return await self._store.get_user_key_fingerprint(account_id, user_id)
