# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Legacy API Key management (original implementation)."""

import asyncio
import copy
import fnmatch
import hashlib
import hmac
import json
import secrets
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from openviking.pyagfs import AGFSAlreadyExistsError, AGFSNotFoundError, AsyncAGFSClient
from openviking.server.api_keys.models import AccountInfo, UserKeyEntry
from openviking.server.identity import ResolvedIdentity, Role
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import (
    AlreadyExistsError,
    FailedPreconditionError,
    InvalidArgumentError,
    NotFoundError,
    UnauthenticatedError,
)
from openviking_cli.session.user_id import validate_account_id, validate_user_id
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

ACCOUNTS_PATH = "/local/_system/accounts.json"
USERS_PATH_TEMPLATE = "/local/{account_id}/_system/users.json"
GROUPS_PATH_TEMPLATE = "/local/{account_id}/_system/groups.json"


# Argon2id parameters - export with LEGACY_ prefix for reuse in new.py
ARGON2_TIME_COST = 3
ARGON2_MEMORY_COST = 65536
ARGON2_PARALLELISM = 2
ARGON2_HASH_LENGTH = 32

# Also export with LEGACY_ prefix for clarity when imported by new.py
LEGACY_ARGON2_TIME_COST = ARGON2_TIME_COST
LEGACY_ARGON2_MEMORY_COST = ARGON2_MEMORY_COST
LEGACY_ARGON2_PARALLELISM = ARGON2_PARALLELISM
LEGACY_ARGON2_HASH_LENGTH = ARGON2_HASH_LENGTH


def derive_seeded_api_key_secret(user_id: str, seed: str) -> str:
    if not isinstance(seed, str) or seed == "":
        raise InvalidArgumentError("seed must not be empty")
    return hashlib.sha256(f"{user_id}\0{seed}".encode("utf-8")).hexdigest()


class LegacyAPIKeyManager:
    """Manages API keys for multi-tenant authentication (legacy implementation)."""

    def __init__(
        self,
        root_key: str,
        viking_fs: VikingFS,
        api_key_hashing_enabled: bool = False,
    ):
        """Initialize APIKeyManager.

        Args:
            root_key: Global root API key for administrative access.
            viking_fs: VikingFS client for persistent storage of user keys.
            api_key_hashing_enabled: Whether API key Argon2id hashing is enabled.
                Default: false - rely on file-level AES encryption for protection.
        """
        self._root_key = root_key
        self._viking_fs = viking_fs
        self._async_agfs = AsyncAGFSClient(viking_fs.agfs)
        self._api_key_hashing_enabled = api_key_hashing_enabled
        self._accounts: Dict[str, AccountInfo] = {}
        # Prefix index: key_prefix -> list[UserKeyEntry]
        self._prefix_index: Dict[str, list[UserKeyEntry]] = {}
        self._group_lock = asyncio.Lock()
        self._user_group_ids: Dict[tuple[str, str], tuple[str, ...]] = {}

    def _discard_account_state(self, account_id: str) -> None:
        """Remove an account and its key index entries from in-memory state."""
        account = self._accounts.pop(account_id, None)
        self._discard_account_group_index(account_id)
        if account is None:
            return

        for user_id, user_info in account.users.items():
            key_or_hash = user_info.get("key", "")
            if not key_or_hash:
                continue

            key_prefix = user_info.get("key_prefix", "")
            if not key_prefix:
                key_prefix = self._get_key_prefix(key_or_hash)

            if key_prefix not in self._prefix_index:
                continue

            self._prefix_index[key_prefix] = [
                entry
                for entry in self._prefix_index[key_prefix]
                if not (entry.account_id == account_id and entry.user_id == user_id)
            ]
            if not self._prefix_index[key_prefix]:
                del self._prefix_index[key_prefix]

    async def _rollback_create_account(self, account_id: str) -> None:
        """Best-effort rollback for partially persisted account creation."""
        self._discard_account_state(account_id)
        try:
            await self._save_accounts_json()
        except Exception:
            logger.exception("Failed to persist rollback for account %s", account_id)

    async def load(self) -> None:
        """Load accounts and user keys from VikingFS into memory."""
        accounts_data = await self._read_json(ACCOUNTS_PATH)
        if accounts_data is None:
            # First run: create default account
            now = datetime.now(timezone.utc).isoformat()
            accounts_data = {"accounts": {"default": {"created_at": now}}}
            await self._write_json(ACCOUNTS_PATH, accounts_data)

        for account_id, info in accounts_data.get("accounts", {}).items():
            users_path = USERS_PATH_TEMPLATE.format(account_id=account_id)
            users_data = await self._read_json(users_path)
            users = users_data.get("users", {}) if users_data else {}
            groups_path = GROUPS_PATH_TEMPLATE.format(account_id=account_id)
            groups_data = await self._read_json(groups_path)
            groups = groups_data.get("groups", {}) if groups_data else {}

            self._accounts[account_id] = AccountInfo(
                created_at=info.get("created_at", ""),
                users=users,
                groups=groups,
            )
            self._rebuild_account_group_index(account_id)

            for user_id, user_info in users.items():
                key_or_hash = user_info.get("key", "")
                if key_or_hash:
                    # Check if it's a hashed key
                    if key_or_hash.startswith("$argon2"):
                        # Already hashed
                        stored_key = key_or_hash
                        is_hashed = True
                        key_prefix = user_info.get("key_prefix", "")
                    else:
                        # Plaintext key
                        if self._api_key_hashing_enabled:
                            # If API key hashing enabled, migrate to hashed
                            stored_key = self._hash_api_key(key_or_hash)
                            is_hashed = True
                            key_prefix = self._get_key_prefix(key_or_hash)
                            # Update storage
                            user_info["key"] = stored_key
                            user_info["key_prefix"] = key_prefix
                            await self._save_users_json(account_id)
                            logger.info(
                                "Migrated API key for user %s in account %s", user_id, account_id
                            )
                        else:
                            # If API key hashing not enabled, keep as plaintext
                            stored_key = key_or_hash
                            is_hashed = False
                            # For plaintext keys, compute prefix on the fly for indexing
                            key_prefix = self._get_key_prefix(key_or_hash)

                    entry = UserKeyEntry(
                        account_id=account_id,
                        user_id=user_id,
                        role=Role(user_info.get("role", "user")),
                        key_or_hash=stored_key,
                        is_hashed=is_hashed,
                    )

                    # Add to prefix index
                    if key_prefix:
                        if key_prefix not in self._prefix_index:
                            self._prefix_index[key_prefix] = []
                        self._prefix_index[key_prefix].append(entry)

        logger.info(
            "LegacyAPIKeyManager loaded: %d accounts, %d user keys",
            len(self._accounts),
            sum(len(info.users) for info in self._accounts.values()),
        )

    def resolve(self, api_key: str) -> ResolvedIdentity:
        """Resolve an API key to identity. Sequential matching: root key first, then user key index."""
        if not api_key:
            raise UnauthenticatedError("Missing API Key")

        if hmac.compare_digest(api_key, self._root_key):
            return ResolvedIdentity(role=Role.ROOT)

        # Use prefix index to quickly locate candidate keys
        key_prefix = self._get_key_prefix(api_key)
        candidates = self._prefix_index.get(key_prefix, [])

        for entry in candidates:
            if entry.is_hashed:
                # Verify hashed key
                if self._verify_api_key(api_key, entry.key_or_hash):
                    return ResolvedIdentity(
                        role=entry.role,
                        account_id=entry.account_id,
                        user_id=entry.user_id,
                    )
            else:
                # Verify plaintext key
                if hmac.compare_digest(api_key, entry.key_or_hash):
                    return ResolvedIdentity(
                        role=entry.role,
                        account_id=entry.account_id,
                        user_id=entry.user_id,
                    )

        raise UnauthenticatedError("Invalid API Key")

    async def create_account(
        self,
        account_id: str,
        admin_user_id: str,
        seed: Optional[str] = None,
    ) -> str:
        """Create a new account (workspace) with its first admin user.

        Returns the admin user's API key (legacy format).
        """
        # Validate account_id and user_id format
        verr = validate_account_id(account_id)
        if verr:
            raise InvalidArgumentError(verr)
        verr = validate_user_id(admin_user_id)
        if verr:
            raise InvalidArgumentError(verr)

        if account_id in self._accounts:
            raise AlreadyExistsError(account_id, "account")

        now = datetime.now(timezone.utc).isoformat()
        key = (
            derive_seeded_api_key_secret(admin_user_id, seed)
            if seed is not None
            else self._generate_api_key()
        )

        if self._api_key_hashing_enabled:
            stored_key = self._hash_api_key(key)
            is_hashed = True
            key_prefix = self._get_key_prefix(key)
        else:
            stored_key = key
            is_hashed = False
            key_prefix = self._get_key_prefix(key)

        user_info = {
            "role": "admin",
            "key": stored_key,
        }
        if self._api_key_hashing_enabled:
            user_info["key_prefix"] = key_prefix

        self._accounts[account_id] = AccountInfo(
            created_at=now,
            users={admin_user_id: user_info},
            groups={},
        )

        entry = UserKeyEntry(
            account_id=account_id,
            user_id=admin_user_id,
            role=Role.ADMIN,
            key_or_hash=stored_key,
            is_hashed=is_hashed,
        )

        # Add to prefix index
        if key_prefix:
            if key_prefix not in self._prefix_index:
                self._prefix_index[key_prefix] = []
            self._prefix_index[key_prefix].append(entry)

        try:
            await self._save_accounts_json()
            await self._save_users_json(account_id)
            await self._save_groups_json(account_id)
        except Exception:
            await self._rollback_create_account(account_id)
            raise
        return key

    async def delete_account(self, account_id: str) -> None:
        """Delete an account and remove all its user keys from the index."""
        if account_id not in self._accounts:
            raise NotFoundError(account_id, "account")

        self._discard_account_state(account_id)

        await self._save_accounts_json()

    async def register_user(
        self,
        account_id: str,
        user_id: str,
        role: str = "user",
        seed: Optional[str] = None,
    ) -> str:
        """Register a new user in an account. Returns the user's API key (legacy format)."""
        # Validate user_id format
        verr = validate_user_id(user_id)
        if verr:
            raise InvalidArgumentError(verr)

        account = self._accounts.get(account_id)
        if account is None:
            raise NotFoundError(account_id, "account")
        if user_id in account.users:
            raise AlreadyExistsError(user_id, "user")

        key = (
            derive_seeded_api_key_secret(user_id, seed)
            if seed is not None
            else self._generate_api_key()
        )

        if self._api_key_hashing_enabled:
            stored_key = self._hash_api_key(key)
            is_hashed = True
            key_prefix = self._get_key_prefix(key)
        else:
            stored_key = key
            is_hashed = False
            key_prefix = self._get_key_prefix(key)

        user_info = {
            "role": role,
            "key": stored_key,
        }
        if self._api_key_hashing_enabled:
            user_info["key_prefix"] = key_prefix

        account.users[user_id] = user_info

        entry = UserKeyEntry(
            account_id=account_id,
            user_id=user_id,
            role=Role(role),
            key_or_hash=stored_key,
            is_hashed=is_hashed,
        )

        # Add to prefix index
        if key_prefix:
            if key_prefix not in self._prefix_index:
                self._prefix_index[key_prefix] = []
            self._prefix_index[key_prefix].append(entry)

        await self._save_users_json(account_id)
        return key

    async def remove_user(self, account_id: str, user_id: str) -> None:
        """Remove a user from an account."""
        async with self._group_lock:
            account = self._accounts.get(account_id)
            if account is None:
                raise NotFoundError(account_id, "account")
            if user_id not in account.users:
                raise NotFoundError(user_id, "user")

            groups = copy.deepcopy(account.groups)
            changed = False
            for group in groups.values():
                members = group.get("members", [])
                if user_id in members:
                    group["members"] = [member for member in members if member != user_id]
                    changed = True
            if changed:
                await self._replace_groups(account_id, account, groups)

            user_info = account.users.pop(user_id)
            key_or_hash = user_info.get("key", "")

            if key_or_hash:
                key_prefix = user_info.get("key_prefix", "") or self._get_key_prefix(key_or_hash)
                if key_prefix in self._prefix_index:
                    self._prefix_index[key_prefix] = [
                        entry
                        for entry in self._prefix_index[key_prefix]
                        if not (entry.account_id == account_id and entry.user_id == user_id)
                    ]
                    if not self._prefix_index[key_prefix]:
                        del self._prefix_index[key_prefix]

            await self._save_users_json(account_id)

    async def regenerate_key(self, account_id: str, user_id: str, seed: Optional[str] = None) -> str:
        """Regenerate a user's API key. Old key is immediately invalidated."""
        account = self._accounts.get(account_id)
        if account is None:
            raise NotFoundError(account_id, "account")
        if user_id not in account.users:
            raise NotFoundError(user_id, "user")

        old_user_info = account.users[user_id]
        old_key_or_hash = old_user_info.get("key", "")

        # Get old key_prefix - if not in user_info, compute from key
        old_key_prefix = old_user_info.get("key_prefix", "")
        if not old_key_prefix and old_key_or_hash:
            old_key_prefix = self._get_key_prefix(old_key_or_hash)

        # Remove old key from prefix index
        if old_key_prefix in self._prefix_index:
            self._prefix_index[old_key_prefix] = [
                entry
                for entry in self._prefix_index[old_key_prefix]
                if not (entry.account_id == account_id and entry.user_id == user_id)
            ]
            if not self._prefix_index[old_key_prefix]:
                del self._prefix_index[old_key_prefix]

        # Generate new key
        new_key = (
            derive_seeded_api_key_secret(user_id, seed)
            if seed is not None
            else self._generate_api_key()
        )

        if self._api_key_hashing_enabled:
            new_stored_key = self._hash_api_key(new_key)
            new_is_hashed = True
            new_key_prefix = self._get_key_prefix(new_key)
        else:
            new_stored_key = new_key
            new_is_hashed = False
            new_key_prefix = self._get_key_prefix(new_key)

        # Update user info
        account.users[user_id]["key"] = new_stored_key
        if self._api_key_hashing_enabled:
            account.users[user_id]["key_prefix"] = new_key_prefix
        else:
            # Remove key_prefix if API key hashing is disabled
            if "key_prefix" in account.users[user_id]:
                del account.users[user_id]["key_prefix"]

        # Add new key to prefix index
        entry = UserKeyEntry(
            account_id=account_id,
            user_id=user_id,
            role=Role(account.users[user_id]["role"]),
            key_or_hash=new_stored_key,
            is_hashed=new_is_hashed,
        )

        if new_key_prefix:
            if new_key_prefix not in self._prefix_index:
                self._prefix_index[new_key_prefix] = []
            self._prefix_index[new_key_prefix].append(entry)

        await self._save_users_json(account_id)
        return new_key

    async def set_role(self, account_id: str, user_id: str, role: str) -> None:
        """Update a user's role."""
        account = self._accounts.get(account_id)
        if account is None:
            raise NotFoundError(account_id, "account")
        if user_id not in account.users:
            raise NotFoundError(user_id, "user")

        account.users[user_id]["role"] = role

        # Update role in prefix index
        user_info = account.users[user_id]
        key_or_hash = user_info.get("key", "")
        if key_or_hash:
            # Get key_prefix - if not in user_info, compute from key
            key_prefix = user_info.get("key_prefix", "")
            if not key_prefix:
                key_prefix = self._get_key_prefix(key_or_hash)

            if key_prefix in self._prefix_index:
                for entry in self._prefix_index[key_prefix]:
                    if entry.account_id == account_id and entry.user_id == user_id:
                        entry.role = Role(role)
                        break

        await self._save_users_json(account_id)

    def get_accounts(self) -> list:
        """List all accounts."""
        result = []
        for account_id, info in self._accounts.items():
            result.append(
                {
                    "account_id": account_id,
                    "created_at": info.created_at,
                    "user_count": len(info.users),
                }
            )
        return result

    def get_users(
        self,
        account_id: str,
        limit: int = 100,
        name_filter: str | None = None,
        role_filter: str | None = None,
        expose_key: bool = True,
    ) -> list:
        """List all users in an account."""
        account = self._accounts.get(account_id)
        if account is None:
            raise NotFoundError(account_id, "account")

        result = []
        count = 0
        for user_id, user_info in account.users.items():
            user_role = user_info.get("role", "user")

            # Apply name filter if provided
            if name_filter and not fnmatch.fnmatch(user_id, name_filter):
                continue

            # Apply role filter if provided
            if role_filter and user_role != role_filter:
                continue

            if count >= limit:
                break

            user_data = {
                "user_id": user_id,
                "role": user_role,
            }
            if expose_key:
                key = user_info.get("key")
                if key:
                    if key.startswith("$argon2"):
                        # Hashed key - show key_prefix

                        key_prefix = user_info.get("key_prefix")
                        if key_prefix:
                            user_data["key_prefix"] = key_prefix
                    else:
                        # Plaintext key - show full api_key
                        user_data["api_key"] = key
            result.append(user_data)
            count += 1
        return result

    def has_user(self, account_id: str, user_id: str) -> bool:
        """Return True when the account registry contains the given user."""
        account = self._accounts.get(account_id)
        if account is None:
            return False
        return user_id in account.users

    def get_user_role(self, account_id: str, user_id: str) -> Role:
        """Return the role of the given user in the given account.

        Returns Role.USER if the account or user doesn't exist.
        """
        account = self._accounts.get(account_id)
        if account is None:
            return Role.USER
        user = account.users.get(user_id)
        if user is None:
            return Role.USER
        return Role(user.get("role", "user"))

    def get_user_group_ids(self, account_id: str, user_id: str) -> tuple[str, ...]:
        """Return the account-scoped groups currently containing the user."""
        return self._user_group_ids.get((account_id, user_id), ())

    async def create_group(self, account_id: str, name: str) -> dict:
        name = self._normalize_group_name(name)
        async with self._group_lock:
            account = self._require_account(account_id)
            if any(group.get("name") == name for group in account.groups.values()):
                raise AlreadyExistsError(name, "group")
            group_id = f"grp_{uuid.uuid4().hex}"
            groups = copy.deepcopy(account.groups)
            groups[group_id] = {"name": name, "members": []}
            await self._replace_groups(account_id, account, groups)
            return self._group_result(group_id, groups[group_id])

    def get_groups(self, account_id: str) -> list[dict]:
        account = self._require_account(account_id)
        return [
            self._group_result(group_id, group)
            for group_id, group in sorted(
                account.groups.items(), key=lambda item: (str(item[1].get("name", "")), item[0])
            )
        ]

    def get_group_members(self, account_id: str, group_id: str) -> list[str]:
        group = self._require_group(account_id, group_id)
        return sorted(set(group.get("members", [])))

    async def add_group_member(self, account_id: str, group_id: str, user_id: str) -> bool:
        async with self._group_lock:
            account = self._require_account(account_id)
            if user_id not in account.users:
                raise NotFoundError(user_id, "user")
            group = self._require_group(account_id, group_id)
            if user_id in group.get("members", []):
                return False
            groups = copy.deepcopy(account.groups)
            groups[group_id].setdefault("members", []).append(user_id)
            groups[group_id]["members"].sort()
            await self._replace_groups(account_id, account, groups)
            return True

    async def remove_group_member(self, account_id: str, group_id: str, user_id: str) -> bool:
        async with self._group_lock:
            account = self._require_account(account_id)
            group = self._require_group(account_id, group_id)
            if user_id not in group.get("members", []):
                return False
            groups = copy.deepcopy(account.groups)
            groups[group_id]["members"] = [
                member for member in groups[group_id].get("members", []) if member != user_id
            ]
            await self._replace_groups(account_id, account, groups)
            return True

    async def delete_group(self, account_id: str, group_id: str) -> None:
        async with self._group_lock:
            account = self._require_account(account_id)
            group = self._require_group(account_id, group_id)
            if group.get("members"):
                raise FailedPreconditionError("Group must be empty before deletion")
            groups = copy.deepcopy(account.groups)
            del groups[group_id]
            await self._replace_groups(account_id, account, groups)

    def get_user_key_fingerprint(self, account_id: str, user_id: str) -> Optional[str]:
        """Return SHA-256 hex digest of the user's stored API key value, or None.

        The "stored value" is whatever is persisted in ``user_info["key"]``:
        either the plaintext API key (when hashing is disabled) or its
        Argon2id hash (when hashing is enabled). Both are stable per
        key-generation — they are written once on create / regenerate and
        never mutate in place — so the fingerprint is stable as long as the
        key is unchanged, and changes the moment ``regenerate_key`` runs.

        Used by OAuth to bind issued tokens to the API key that authorized
        them: at OTP / authorize time we record this fingerprint; at every
        OAuth bearer auth we recompute and compare. Mismatch (rotation) or
        ``None`` (user removed) fails the request closed.

        Returns None when the account or user does not exist, or when the
        stored value is empty (no fingerprint to bind to).
        """
        account = self._accounts.get(account_id)
        if account is None:
            return None
        user = account.users.get(user_id)
        if user is None:
            return None
        stored = user.get("key", "")
        if not stored:
            return None
        return hashlib.sha256(stored.encode("utf-8")).hexdigest()

    # ---- internal helpers ----

    def _require_account(self, account_id: str) -> AccountInfo:
        account = self._accounts.get(account_id)
        if account is None:
            raise NotFoundError(account_id, "account")
        return account

    def _require_group(self, account_id: str, group_id: str) -> dict:
        account = self._require_account(account_id)
        group = account.groups.get(group_id)
        if group is None:
            raise NotFoundError(group_id, "group")
        return group

    @staticmethod
    def _normalize_group_name(name: str) -> str:
        if not isinstance(name, str):
            raise InvalidArgumentError("group name must be a string")
        normalized = name.strip()
        if not normalized:
            raise InvalidArgumentError("group name must not be empty")
        if len(normalized) > 100:
            raise InvalidArgumentError("group name must not exceed 100 characters")
        return normalized

    @staticmethod
    def _group_result(group_id: str, group: dict) -> dict:
        return {
            "group_id": group_id,
            "name": group.get("name", ""),
            "member_count": len(set(group.get("members", []))),
        }

    def _discard_account_group_index(self, account_id: str) -> None:
        for key in [key for key in self._user_group_ids if key[0] == account_id]:
            del self._user_group_ids[key]

    def _rebuild_account_group_index(self, account_id: str) -> None:
        self._discard_account_group_index(account_id)
        account = self._accounts.get(account_id)
        if account is None:
            return
        group_ids_by_user: Dict[str, list[str]] = {}
        for group_id, group in account.groups.items():
            for user_id in group.get("members", []):
                if user_id in account.users:
                    group_ids_by_user.setdefault(user_id, []).append(group_id)
        for user_id, group_ids in group_ids_by_user.items():
            self._user_group_ids[(account_id, user_id)] = tuple(sorted(set(group_ids)))

    async def _replace_groups(
        self, account_id: str, account: AccountInfo, groups: Dict[str, dict]
    ) -> None:
        await self._write_groups_json(account_id, groups)
        account.groups = groups
        self._rebuild_account_group_index(account_id)

    def _generate_api_key(self) -> str:
        """Generate new API Key (legacy format - hex)."""
        return secrets.token_hex(32)

    def _get_key_prefix(self, api_key: str) -> str:
        """Extract API Key prefix for indexing."""
        if api_key:
            # Take first 8 characters for indexing
            return api_key[:8]
        return ""

    def _hash_api_key(self, api_key: str) -> str:
        """Hash API Key using Argon2id."""
        ph = PasswordHasher(
            time_cost=ARGON2_TIME_COST,
            memory_cost=ARGON2_MEMORY_COST,
            parallelism=ARGON2_PARALLELISM,
            hash_len=ARGON2_HASH_LENGTH,
        )
        return ph.hash(api_key)

    def _verify_api_key(self, api_key: str, hashed_key: str) -> bool:
        """Verify if API Key matches the hash."""
        ph = PasswordHasher()
        try:
            ph.verify(hashed_key, api_key)
            return True
        except VerifyMismatchError:
            return False

    async def _read_json(self, path: str) -> Optional[dict]:
        """Read a JSON file from AGFS with encryption support. Returns None if not found."""
        try:
            content = await self._async_agfs.read(path)
            if isinstance(content, bytes):
                raw = content
            else:
                raw = content.content if hasattr(content, "content") else b""

            text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            return json.loads(text)
        except AGFSNotFoundError:
            return None

    async def _write_json(self, path: str, data: dict) -> None:
        """Write a JSON file to AGFS with encryption support."""
        content = json.dumps(data, ensure_ascii=False, indent=2)
        if isinstance(content, str):
            content = content.encode("utf-8")

        await self._ensure_parent_dirs_async(path)
        await self._async_agfs.write(path, content)

    async def _ensure_parent_dirs_async(self, path: str) -> None:
        """Recursively create all parent directories for a file path."""
        try:
            await self._async_agfs.ensure_parent_dirs(path)
        except AGFSAlreadyExistsError:
            return

    async def _save_accounts_json(self) -> None:
        """Persist the global accounts list."""
        data = {
            "accounts": {
                aid: {"created_at": info.created_at} for aid, info in self._accounts.items()
            }
        }
        await self._write_json(ACCOUNTS_PATH, data)

    async def _save_users_json(self, account_id: str) -> None:
        """Persist a single account's user registry."""
        account = self._accounts.get(account_id)
        if account is None:
            return
        data = {"users": account.users}
        path = USERS_PATH_TEMPLATE.format(account_id=account_id)
        await self._write_json(path, data)

    async def _write_groups_json(self, account_id: str, groups: dict) -> None:
        path = GROUPS_PATH_TEMPLATE.format(account_id=account_id)
        await self._write_json(path, {"groups": groups})

    async def _save_groups_json(self, account_id: str) -> None:
        account = self._accounts.get(account_id)
        if account is not None:
            await self._write_groups_json(account_id, account.groups)
