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
from datetime import datetime, timezone
from typing import Dict, Optional

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from openviking.pyagfs import AGFSAlreadyExistsError, AGFSNotFoundError, AsyncAGFSClient
from openviking.pyagfs.async_client import fs_ctx_from_agfs_path
from openviking.server.api_keys.models import (
    AccountInfo,
    UserKeyEntry,
    validate_account_user_role,
)
from openviking.server.identity import ResolvedIdentity, Role
from openviking.storage.errors import LockAcquisitionError, ResourceBusyError
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import (
    AlreadyExistsError,
    FailedPreconditionError,
    InvalidArgumentError,
    NotFoundError,
    UnauthenticatedError,
)
from openviking_cli.session.user_id import (
    validate_account_id,
    validate_identifier_part,
    validate_user_id,
)
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


def _paginate(items: list, limit: int | None, page: int) -> list:
    """Slice a list by 1-based ``page`` of ``limit`` items.

    ``limit=None`` returns everything (pagination is opt-in), so callers that
    rely on the full set are unaffected. ``page`` is clamped to a minimum of 1.
    """
    if limit is None:
        return items
    if page < 1:
        page = 1
    start = (page - 1) * limit
    return items[start : start + limit]


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
        self._user_group_ids: Dict[tuple[str, str], tuple[str, ...]] = {}
        self._identity_registry_signatures: dict[str | None, tuple] = {}
        # Serializes reload() so overlapping refreshes can't interleave.
        self._reload_lock = asyncio.Lock()
        # Serializes all mutations made by the unified manager in this process.
        # File locks remain responsible for inter-instance coordination.
        self._mutation_lock = asyncio.Lock()
        self._user_deletion_lock = asyncio.Lock()

    @property
    def mutation_lock(self) -> asyncio.Lock:
        return self._mutation_lock

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

    async def _rollback_create_account(self, account_id: str, *, account_was_created: bool) -> None:
        """Best-effort rollback for partially persisted account creation."""
        self._discard_account_state(account_id)
        if not account_was_created:
            return
        try:
            await self._save_accounts_json(delete_account_ids={account_id})
        except Exception:
            logger.exception("Failed to persist rollback for account %s", account_id)

    async def load(self) -> None:
        """Load keys into memory (writable startup path; migrates plaintext; see reload())."""
        async with self._mutation_lock:
            accounts_data = await self._read_json(ACCOUNTS_PATH)
            if accounts_data is None:
                # First run: create default account
                now = datetime.now(timezone.utc).isoformat()
                accounts_data = {"accounts": {"default": {"created_at": now}}}
                await self._write_json(ACCOUNTS_PATH, accounts_data)

            accounts, prefix_index, user_group_ids = await self._build_state(
                accounts_data, allow_migration=True
            )
            self._accounts = accounts
            self._prefix_index = prefix_index
            self._user_group_ids = user_group_ids
            await self._update_identity_registry_signatures()

            logger.info(
                "LegacyAPIKeyManager loaded: %d accounts, %d user keys",
                len(self._accounts),
                sum(len(info.users) for info in self._accounts.values()),
            )

    async def reload(self) -> None:
        """Read-only refresh: re-read store and atomically swap state (never writes/migrates)."""
        async with self._mutation_lock:
            await self._reload_unlocked()

    async def _reload_unlocked(self) -> None:
        """Reload while holding ``_mutation_lock``."""
        async with self._reload_lock:
            accounts_data = await self._read_json(ACCOUNTS_PATH)
            if accounts_data is None:
                # Store not initialized yet (reader started before writer): keep state.
                return

            accounts, prefix_index, user_group_ids = await self._build_state(
                accounts_data, allow_migration=False
            )
            # Atomic swap: rebind so readers never observe a half-built index.
            self._accounts = accounts
            self._prefix_index = prefix_index
            self._user_group_ids = user_group_ids
            await self._update_identity_registry_signatures()

            logger.debug(
                "LegacyAPIKeyManager reloaded: %d accounts, %d user keys",
                len(self._accounts),
                sum(len(info.users) for info in self._accounts.values()),
            )

    async def refresh_identity_registry_if_changed(
        self, account_id: str | None = None
    ) -> bool:
        """Reload registry state only when the management read target has changed."""
        async with self._mutation_lock:
            scope = account_id
            signature = await self._identity_registry_signature(account_id)
            if self._identity_registry_signatures.get(scope) == signature:
                return False
            await self._reload_unlocked()
            return True

    async def _update_identity_registry_signatures(
        self, account_ids: set[str] | None = None
    ) -> None:
        accounts_signature = await self._stat_signature(ACCOUNTS_PATH)
        target_account_ids = self._accounts if account_ids is None else account_ids
        signatures = {None: (accounts_signature,)}
        for account_id in target_account_ids:
            users_path = USERS_PATH_TEMPLATE.format(account_id=account_id)
            signatures[account_id] = (
                accounts_signature,
                await self._stat_signature(users_path),
            )

        if account_ids is None:
            self._identity_registry_signatures = signatures
        else:
            self._identity_registry_signatures.update(signatures)

    async def _identity_registry_signature(self, account_id: str | None = None) -> tuple:
        accounts_signature = await self._stat_signature(ACCOUNTS_PATH)
        if account_id is not None:
            users_path = USERS_PATH_TEMPLATE.format(account_id=account_id)
            return (accounts_signature, await self._stat_signature(users_path))
        return (accounts_signature,)

    async def _build_state(
        self, accounts_data: dict, *, allow_migration: bool
    ) -> tuple[
        Dict[str, AccountInfo],
        Dict[str, list[UserKeyEntry]],
        Dict[tuple[str, str], tuple[str, ...]],
    ]:
        """Build fresh (accounts, prefix_index) state; migrate plaintext only if allow_migration."""
        accounts: Dict[str, AccountInfo] = {}
        prefix_index: Dict[str, list[UserKeyEntry]] = {}
        user_group_ids: Dict[tuple[str, str], tuple[str, ...]] = {}

        for account_id, info in accounts_data.get("accounts", {}).items():
            users_path = USERS_PATH_TEMPLATE.format(account_id=account_id)
            users_data = await self._read_json(users_path)
            users = users_data.get("users", {}) if users_data else {}
            groups_path = GROUPS_PATH_TEMPLATE.format(account_id=account_id)
            groups_data = await self._read_json(groups_path)
            groups = groups_data.get("groups", {}) if groups_data else {}

            accounts[account_id] = AccountInfo(
                created_at=info.get("created_at", ""),
                users=users,
                groups=groups,
            )
            user_group_ids.update({
                (account_id, user_id): group_ids
                for user_id, group_ids in self._group_memberships(users, groups).items()
            })

            for user_id, user_info in users.items():
                key_or_hash = user_info.get("key", "")
                if not key_or_hash:
                    continue

                if key_or_hash.startswith("$argon2"):
                    # Already hashed
                    stored_key = key_or_hash
                    is_hashed = True
                    key_prefix = user_info.get("key_prefix", "")
                elif self._api_key_hashing_enabled and allow_migration:
                    # Migrate plaintext to hashed and persist.
                    stored_key = self._hash_api_key(key_or_hash)
                    is_hashed = True
                    key_prefix = self._get_key_prefix(key_or_hash)
                    user_info["key"] = stored_key
                    user_info["key_prefix"] = key_prefix
                    await self._save_users_json(account_id, {user_id: user_info})
                    logger.info("Migrated API key for user %s in account %s", user_id, account_id)
                else:
                    # Keep plaintext (hashing off or read-only refresh); prefix on the fly.
                    stored_key = key_or_hash
                    is_hashed = False
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
                    if key_prefix not in prefix_index:
                        prefix_index[key_prefix] = []
                    prefix_index[key_prefix].append(entry)

        return accounts, prefix_index, user_group_ids

    async def compute_store_signature(self) -> tuple:
        """Return a cheap (path, size, modTime) signature over accounts.json + all users.json."""
        signature: list[tuple] = []

        accounts_data = await self._read_json(ACCOUNTS_PATH)
        signature.append(await self._stat_signature(ACCOUNTS_PATH))

        if accounts_data:
            for account_id in accounts_data.get("accounts", {}):
                users_path = USERS_PATH_TEMPLATE.format(account_id=account_id)
                signature.append(await self._stat_signature(users_path))
                groups_path = GROUPS_PATH_TEMPLATE.format(account_id=account_id)
                signature.append(await self._stat_signature(groups_path))

        return tuple(signature)

    async def _stat_signature(self, path: str) -> tuple:
        """Return a (path, size, mod_time) tuple for one file; missing/error yields a sentinel."""
        try:
            # Bypass plugin-local stat caches: on S3 the sliding-TTL stat cache
            # would otherwise pin stale metadata and mask writer-side changes.
            info = await self._async_agfs.stat(path, bypass_cache=True)
        except AGFSNotFoundError:
            return (path, None, None)
        except Exception:
            logger.debug("Failed to stat %s for key-store signature", path, exc_info=True)
            return (path, None, None)

        if not isinstance(info, dict):
            return (path, None, None)

        size = info.get("size")
        mod_time = info.get("modTime", info.get("mod_time", info.get("mtime")))
        return (path, size, mod_time)

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

        account_was_created = False
        try:
            created_account_ids = await self._save_accounts_json(
                updated_account_ids={account_id},
                reject_existing_account_ids={account_id},
            )
            account_was_created = account_id in created_account_ids
            await self._save_users_json(
                account_id,
                {admin_user_id: user_info},
                replace_existing=account_id in created_account_ids,
            )
            await self._write_groups_json(account_id, {})
        except Exception:
            await self._rollback_create_account(account_id, account_was_created=account_was_created)
            raise
        return key

    async def delete_account(self, account_id: str) -> None:
        """Delete an account and remove all its user keys from the index."""
        if account_id not in self._accounts:
            raise NotFoundError(account_id, "account")

        self._discard_account_state(account_id)

        await self._save_accounts_json(delete_account_ids={account_id})

    async def register_user(
        self,
        account_id: str,
        user_id: str,
        role: str = "user",
        seed: Optional[str] = None,
    ) -> str:
        """Register a new user in an account. Returns the user's API key (legacy format)."""
        resolved_role = validate_account_user_role(role)
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
            "role": resolved_role,
            "key": stored_key,
        }
        if self._api_key_hashing_enabled:
            user_info["key_prefix"] = key_prefix

        account.users[user_id] = user_info

        entry = UserKeyEntry(
            account_id=account_id,
            user_id=user_id,
            role=resolved_role,
            key_or_hash=stored_key,
            is_hashed=is_hashed,
        )

        # Add to prefix index
        if key_prefix:
            if key_prefix not in self._prefix_index:
                self._prefix_index[key_prefix] = []
            self._prefix_index[key_prefix].append(entry)

        try:
            await self._save_users_json(
                account_id, {user_id: user_info}, reject_existing_user_ids={user_id}
            )
        except Exception:
            account.users.pop(user_id, None)
            self._remove_key_index_entry(account_id, user_id, user_info)
            raise
        return key

    async def ensure_trusted_identities(self, identities: Dict[str, set[str]]) -> dict[str, int]:
        """Merge trusted identities into the registry without creating API keys."""
        async with self._mutation_lock:
            return await self._ensure_trusted_identities_unlocked(identities)

    async def _ensure_trusted_identities_unlocked(
        self, identities: Dict[str, set[str]]
    ) -> dict[str, int]:
        normalized = {account_id: set(user_ids) for account_id, user_ids in identities.items() if user_ids}
        if not normalized:
            return {"created_accounts": 0, "created_users": 0}

        for account_id, user_ids in normalized.items():
            error = validate_account_id(account_id)
            if error:
                raise InvalidArgumentError(error)
            for user_id in user_ids:
                error = validate_user_id(user_id)
                if error:
                    raise InvalidArgumentError(error)

        created_accounts = 0
        created_users = 0
        now = datetime.now(timezone.utc).isoformat()

        try:
            accounts_lease = await self._async_agfs.pathlock_acquire_exact(
                ACCOUNTS_PATH, timeout_secs=10.0
            )
        except LockAcquisitionError as exc:
            raise ResourceBusyError(
                "Another account operation is in progress. Please retry.",
                uri=ACCOUNTS_PATH,
                conflict_type="account_registry_busy",
            ) from exc
        try:
            accounts_data = await self._read_json(ACCOUNTS_PATH) or {"accounts": {}}
            persisted_accounts = accounts_data.setdefault("accounts", {})
            for account_id in normalized:
                if account_id not in persisted_accounts:
                    persisted_accounts[account_id] = {"created_at": now}
                    created_accounts += 1
            if created_accounts:
                await self._write_json(ACCOUNTS_PATH, accounts_data, lease_ref=accounts_lease)
        finally:
            await self._async_agfs.pathlock_release(accounts_lease)

        for account_id, user_ids in normalized.items():
            path = USERS_PATH_TEMPLATE.format(account_id=account_id)
            try:
                users_lease = await self._async_agfs.pathlock_acquire_exact(path, timeout_secs=10.0)
            except LockAcquisitionError as exc:
                raise ResourceBusyError(
                    "Another user operation is in progress for this account. Please retry.",
                    uri=path,
                    conflict_type="user_registry_busy",
                ) from exc
            try:
                users_data = await self._read_json(path) or {"users": {}}
                persisted_users = users_data.setdefault("users", {})
                new_users = sorted(user_id for user_id in user_ids if user_id not in persisted_users)
                for user_id in new_users:
                    persisted_users[user_id] = {"role": "user"}
                if new_users:
                    await self._write_json(path, users_data, lease_ref=users_lease)
                    created_users += len(new_users)
                    logger.info(
                        "Persisted trusted identities for account %s: %s",
                        account_id,
                        new_users,
                    )

                account = self._accounts.get(account_id)
                if account is None:
                    created_at = (
                        (accounts_data.get("accounts", {}).get(account_id) or {}).get("created_at")
                        or now
                    )
                    account = AccountInfo(created_at=created_at, users={}, groups={})
                    self._accounts[account_id] = account
                for user_id, user_info in persisted_users.items():
                    account.users.setdefault(user_id, dict(user_info))
            finally:
                await self._async_agfs.pathlock_release(users_lease)

        await self._update_identity_registry_signatures(set(normalized))
        return {"created_accounts": created_accounts, "created_users": created_users}

    async def begin_user_deletion(
        self,
        account_id: str,
        user_id: str,
        *,
        task_id: str,
        owner_account_id: str,
        owner_user_id: str,
    ) -> tuple[dict, bool]:
        """Revoke the user key and persist the deletion task fence."""
        async with self._reload_lock, self._user_deletion_lock:
            account = self._accounts.get(account_id)
            if account is None:
                raise NotFoundError(account_id, "account")
            user_info = account.users.get(user_id)
            if user_info is None:
                raise NotFoundError(user_id, "user")

            existing = user_info.get("deletion")
            if isinstance(existing, dict) and existing.get("task_id"):
                return dict(existing), False

            if user_info.get("role") == Role.ADMIN:
                active_admins = sum(
                    info.get("role") == Role.ADMIN and not info.get("deletion")
                    for info in account.users.values()
                )
                if active_admins <= 1:
                    raise FailedPreconditionError("Cannot delete the last active account admin")

            original = dict(user_info)
            deletion = {
                "task_id": task_id,
                "owner_account_id": owner_account_id,
                "owner_user_id": owner_user_id,
            }
            user_info["deletion"] = deletion
            user_info["key"] = ""
            user_info.pop("key_prefix", None)
            try:
                await self._save_users_json(account_id, {user_id: user_info})
            except Exception:
                account.users[user_id] = original
                raise
            self._remove_key_index_entry(account_id, user_id, original)
            return dict(deletion), True

    async def replace_user_deletion_task(
        self,
        account_id: str,
        user_id: str,
        *,
        expected_task_id: str,
        task_id: str,
        owner_account_id: str,
        owner_user_id: str,
    ) -> dict:
        """Replace the task that owns an existing deletion fence."""
        async with self._reload_lock, self._user_deletion_lock:
            account = self._accounts.get(account_id)
            if account is None:
                raise NotFoundError(account_id, "account")
            user_info = account.users.get(user_id)
            if user_info is None:
                raise NotFoundError(user_id, "user")
            current = user_info.get("deletion")
            if not isinstance(current, dict) or current.get("task_id") != expected_task_id:
                return dict(current) if isinstance(current, dict) else {}

            replacement = {
                "task_id": task_id,
                "owner_account_id": owner_account_id,
                "owner_user_id": owner_user_id,
            }
            user_info["deletion"] = replacement
            try:
                await self._save_users_json(account_id, {user_id: user_info})
            except Exception:
                user_info["deletion"] = current
                raise
            return dict(replacement)

    async def finish_user_deletion(self, account_id: str, user_id: str, task_id: str) -> bool:
        """Remove the user only when this task still owns the deletion fence."""
        async with self._reload_lock, self._user_deletion_lock:
            account = self._accounts.get(account_id)
            if account is None:
                return False
            user_info = account.users.get(user_id)
            if user_info is None:
                return False
            deletion = user_info.get("deletion")
            if not isinstance(deletion, dict) or deletion.get("task_id") != task_id:
                return False

            old_groups = copy.deepcopy(account.groups)
            groups = copy.deepcopy(account.groups)
            for group in groups.values():
                members = group.get("members", [])
                if user_id in members:
                    group["members"] = [member for member in members if member != user_id]
            account.users.pop(user_id)
            try:
                await self._save_users_json(account_id, deleted_user_ids={user_id})
                if groups != old_groups:
                    await self._write_groups_json(account_id, groups)
            except Exception:
                account.users[user_id] = user_info
                account.groups = old_groups
                self._rebuild_account_group_index(account_id)
                raise
            if groups != old_groups:
                account.groups = groups
                self._rebuild_account_group_index(account_id)
            return True

    def get_user_deletion(self, account_id: str, user_id: str) -> Optional[dict]:
        account = self._accounts.get(account_id)
        if account is None:
            return None
        user_info = account.users.get(user_id)
        deletion = user_info.get("deletion") if user_info else None
        return dict(deletion) if isinstance(deletion, dict) else None

    def iter_user_deletions(self) -> list[tuple[str, str, dict]]:
        return [
            (account_id, user_id, dict(deletion))
            for account_id, account in self._accounts.items()
            for user_id, user_info in account.users.items()
            if isinstance((deletion := user_info.get("deletion")), dict)
            and deletion.get("task_id")
        ]

    def is_user_deleting(self, account_id: str, user_id: str) -> bool:
        return self.get_user_deletion(account_id, user_id) is not None

    async def regenerate_key(
        self, account_id: str, user_id: str, seed: Optional[str] = None
    ) -> str:
        """Regenerate a user's API key. Old key is immediately invalidated."""
        account = self._accounts.get(account_id)
        if account is None:
            raise NotFoundError(account_id, "account")
        if user_id not in account.users:
            raise NotFoundError(user_id, "user")
        if account.users[user_id].get("deletion"):
            raise FailedPreconditionError("User deletion is in progress")

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

        await self._save_users_json(account_id, {user_id: account.users[user_id]})
        return new_key

    async def set_role(self, account_id: str, user_id: str, role: str) -> None:
        """Update a user's role."""
        resolved_role = validate_account_user_role(role)
        account = self._accounts.get(account_id)
        if account is None:
            raise NotFoundError(account_id, "account")
        if user_id not in account.users:
            raise NotFoundError(user_id, "user")
        if account.users[user_id].get("deletion"):
            raise FailedPreconditionError("User deletion is in progress")

        account.users[user_id]["role"] = resolved_role

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
                        entry.role = resolved_role
                        break

        await self._save_users_json(account_id, {user_id: account.users[user_id]})

    def get_accounts(
        self,
        name_filter: str | None = None,
        limit: int | None = None,
        page: int = 1,
    ) -> list:
        """List accounts in creation (insertion) order.

        ``name_filter`` uses wildcard (``*`` and ``?``) matching. Pagination is
        opt-in: ``limit=None`` returns every matching account so internal
        callers that rely on the full account set are unaffected; when ``limit``
        is set, ``page`` (1-based) selects the slice.
        """
        result = []
        for account_id, info in self._accounts.items():
            # Apply name filter if provided (fnmatch wildcard matching)
            if name_filter and not fnmatch.fnmatch(account_id, name_filter):
                continue

            result.append(
                {
                    "account_id": account_id,
                    "created_at": info.created_at,
                    "user_count": len(info.users),
                }
            )
        return _paginate(result, limit, page)

    def get_users(
        self,
        account_id: str,
        limit: int | None = 100,
        name_filter: str | None = None,
        role_filter: str | None = None,
        expose_key: bool = True,
        page: int = 1,
    ) -> list:
        """List users in an account in creation (insertion) order.

        Pagination is opt-in via ``limit``/``page`` (1-based); ``limit=None``
        returns every matching user.
        """
        account = self._accounts.get(account_id)
        if account is None:
            raise NotFoundError(account_id, "account")

        result = []
        for user_id, user_info in account.users.items():
            if user_info.get("deletion"):
                continue

            user_role = user_info.get("role", "user")

            # Apply name filter if provided
            if name_filter and not fnmatch.fnmatch(user_id, name_filter):
                continue

            # Apply role filter if provided
            if role_filter and user_role != role_filter:
                continue

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
        return _paginate(result, limit, page)

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

    async def create_group(self, account_id: str, group_id: str) -> dict:
        error = validate_identifier_part(group_id, "group_id")
        if error:
            raise InvalidArgumentError(error)
        async with self._reload_lock:
            account = self._require_account(account_id)
            if group_id in account.groups:
                raise AlreadyExistsError(group_id, "group")
            groups = copy.deepcopy(account.groups)
            groups[group_id] = {"members": []}
            await self._replace_groups(account_id, account, groups)
            return self._group_result(group_id, groups[group_id])

    def get_groups(self, account_id: str) -> list[dict]:
        account = self._require_account(account_id)
        return [
            self._group_result(group_id, group)
            for group_id, group in sorted(account.groups.items())
        ]

    def get_group_members(self, account_id: str, group_id: str) -> list[str]:
        group = self._require_group(account_id, group_id)
        return sorted(set(group.get("members", [])))

    async def add_group_member(self, account_id: str, group_id: str, user_id: str) -> bool:
        async with self._reload_lock:
            account = self._require_account(account_id)
            if user_id not in account.users:
                raise NotFoundError(user_id, "user")
            group = self._require_group(account_id, group_id)
            if user_id in group.get("members", []):
                return True
            groups = copy.deepcopy(account.groups)
            groups[group_id].setdefault("members", []).append(user_id)
            groups[group_id]["members"].sort()
            await self._replace_groups(account_id, account, groups)
            return True

    async def remove_group_member(self, account_id: str, group_id: str, user_id: str) -> bool:
        async with self._reload_lock:
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
        async with self._reload_lock:
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
    def _group_result(group_id: str, group: dict) -> dict:
        return {
            "group_id": group_id,
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
        self._user_group_ids.update({
            (account_id, user_id): group_ids
            for user_id, group_ids in self._group_memberships(
                account.users, account.groups
            ).items()
        })

    @staticmethod
    def _group_memberships(
        users: Dict[str, dict], groups: Dict[str, dict]
    ) -> Dict[str, tuple[str, ...]]:
        group_ids_by_user: Dict[str, list[str]] = {}
        for group_id, group in groups.items():
            for user_id in group.get("members", []):
                if user_id in users:
                    group_ids_by_user.setdefault(user_id, []).append(group_id)
        return {
            user_id: tuple(sorted(set(group_ids)))
            for user_id, group_ids in group_ids_by_user.items()
        }

    async def _replace_groups(
        self, account_id: str, account: AccountInfo, groups: Dict[str, dict]
    ) -> None:
        await self._write_groups_json(account_id, groups)
        account.groups = groups
        self._rebuild_account_group_index(account_id)

    def _remove_key_index_entry(self, account_id: str, user_id: str, user_info: dict) -> None:
        key_or_hash = user_info.get("key", "")
        if not key_or_hash:
            return
        key_prefix = user_info.get("key_prefix", "") or self._get_key_prefix(key_or_hash)
        if key_prefix not in self._prefix_index:
            return
        self._prefix_index[key_prefix] = [
            entry
            for entry in self._prefix_index[key_prefix]
            if not (entry.account_id == account_id and entry.user_id == user_id)
        ]
        if not self._prefix_index[key_prefix]:
            del self._prefix_index[key_prefix]

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

    @staticmethod
    def _fs_ctx_with_lease(path: str, lease_ref: object | None) -> Dict[str, str] | None:
        """Build an AGFS fs_ctx that preserves account_id and an optional lease_ref."""
        if lease_ref is None:
            return None
        ref = None
        if isinstance(lease_ref, dict):
            ref = lease_ref.get("lease_ref")
        else:
            ref = getattr(lease_ref, "lease_ref", None) or getattr(lease_ref, "id", None)
        if not isinstance(ref, str) or not ref:
            return None
        fs_ctx = fs_ctx_from_agfs_path(path)
        fs_ctx["lease_ref"] = ref
        return fs_ctx

    async def _write_json(self, path: str, data: dict, lease_ref: object | None = None) -> None:
        """Write a JSON file to AGFS with encryption support."""
        content = json.dumps(data, ensure_ascii=False, indent=2)
        if isinstance(content, str):
            content = content.encode("utf-8")

        await self._ensure_parent_dirs_async(path)
        await self._async_agfs.write(
            path,
            content,
            fs_ctx=self._fs_ctx_with_lease(path, lease_ref),
        )

    async def _ensure_parent_dirs_async(self, path: str) -> None:
        """Recursively create all parent directories for a file path."""
        try:
            await self._async_agfs.ensure_parent_dirs(path)
        except AGFSAlreadyExistsError:
            return

    def _rebuild_prefix_index(self) -> None:
        prefix_index: Dict[str, list[UserKeyEntry]] = {}
        for account_id, account in self._accounts.items():
            for user_id, user_info in account.users.items():
                key_or_hash = user_info.get("key", "")
                if not key_or_hash:
                    continue
                key_prefix = user_info.get("key_prefix", "") or self._get_key_prefix(key_or_hash)
                if not key_prefix:
                    continue
                prefix_index.setdefault(key_prefix, []).append(
                    UserKeyEntry(
                        account_id=account_id,
                        user_id=user_id,
                        role=Role(user_info.get("role", "user")),
                        key_or_hash=key_or_hash,
                        is_hashed=key_or_hash.startswith("$argon2"),
                    )
                )
        self._prefix_index = prefix_index

    async def _save_accounts_json(
        self,
        *,
        updated_account_ids: set[str] | None = None,
        delete_account_ids: set[str] | None = None,
        reject_existing_account_ids: set[str] | None = None,
    ) -> set[str]:
        """Merge local account changes into the latest locked registry snapshot."""
        try:
            lease = await self._async_agfs.pathlock_acquire_exact(ACCOUNTS_PATH, timeout_secs=10.0)
        except LockAcquisitionError as exc:
            raise ResourceBusyError(
                "Another account operation is in progress. Please retry.",
                uri=ACCOUNTS_PATH,
                conflict_type="account_registry_busy",
            ) from exc
        try:
            data = await self._read_json(ACCOUNTS_PATH) or {"accounts": {}}
            accounts = data.setdefault("accounts", {})
            for account_id in reject_existing_account_ids or set():
                if account_id in accounts:
                    raise AlreadyExistsError(account_id, "account")
            created_account_ids = set()
            for account_id in updated_account_ids or set():
                info = self._accounts.get(account_id)
                if info is None:
                    continue
                if account_id not in accounts:
                    created_account_ids.add(account_id)
                accounts[account_id] = {"created_at": info.created_at}
            for account_id in delete_account_ids or set():
                accounts.pop(account_id, None)
            await self._write_json(ACCOUNTS_PATH, data, lease_ref=lease)
            return created_account_ids
        finally:
            await self._async_agfs.pathlock_release(lease)

    async def _save_users_json(
        self,
        account_id: str,
        updated_users: Dict[str, dict] | None = None,
        *,
        deleted_user_ids: set[str] | None = None,
        reject_existing_user_ids: set[str] | None = None,
        replace_existing: bool = False,
    ) -> None:
        """Merge targeted user mutations, or initialize a newly created account."""
        path = USERS_PATH_TEMPLATE.format(account_id=account_id)
        try:
            lease = await self._async_agfs.pathlock_acquire_exact(path, timeout_secs=10.0)
        except LockAcquisitionError as exc:
            raise ResourceBusyError(
                "Another user operation is in progress for this account. Please retry.",
                uri=path,
                conflict_type="user_registry_busy",
            ) from exc
        try:
            if replace_existing:
                users = copy.deepcopy(updated_users or {})
                data = {"users": users}
            else:
                data = await self._read_json(path) or {"users": {}}
                users = data.setdefault("users", {})
            if updated_users is None:
                account = self._accounts.get(account_id)
                if account is None:
                    return
                users = copy.deepcopy(account.users)
                data["users"] = users
            elif not replace_existing:
                for user_id, user_info in updated_users.items():
                    if user_id in (reject_existing_user_ids or set()) and user_id in users:
                        raise AlreadyExistsError(user_id, "user")
                    users[user_id] = copy.deepcopy(user_info)
            for user_id in deleted_user_ids or set():
                users.pop(user_id, None)
            await self._write_json(path, data, lease_ref=lease)

            account = self._accounts.get(account_id)
            if account is not None:
                account.users = users
                self._rebuild_prefix_index()
        finally:
            await self._async_agfs.pathlock_release(lease)

    async def _write_groups_json(self, account_id: str, groups: dict) -> None:
        path = GROUPS_PATH_TEMPLATE.format(account_id=account_id)
        await self._write_json(path, {"groups": groups})
