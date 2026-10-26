# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""File-backed account and API key store over the AGFS registry layout."""

import asyncio
import copy
import fnmatch
import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Dict, Optional

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from typing_extensions import deprecated

from openviking.core.identifiers import validate_identifier_length
from openviking.pyagfs import AGFSAlreadyExistsError, AGFSNotFoundError, AsyncAGFSClient
from openviking.pyagfs.async_client import fs_ctx_from_agfs_path
from openviking.server.account_stores.base import AccountStore
from openviking.server.account_stores.models import (
    AccountSummary,
    DeletionRecord,
    GroupSummary,
    UsersPage,
    UserSummary,
)
from openviking.server.api_keys.models import (
    AccountInfo,
    UserKeyEntry,
    validate_account_user_role,
)
from openviking.server.identity import Role
from openviking.storage.errors import LockAcquisitionError, ResourceBusyError
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import (
    AlreadyExistsError,
    FailedPreconditionError,
    InvalidArgumentError,
    NotFoundError,
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
_MAX_FILE_ACCOUNT_ID_BYTES = 255


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


def validate_file_account_id(account_id: str) -> None:
    """Reject new account IDs that cannot be used as file path components."""
    if len(account_id.encode("utf-8")) > _MAX_FILE_ACCOUNT_ID_BYTES:
        raise InvalidArgumentError(
            f"account_id must not exceed {_MAX_FILE_ACCOUNT_ID_BYTES} UTF-8 bytes."
        )


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


class FileStore(AccountStore):
    """File-backed account and API key store over the legacy AGFS layout."""

    def __init__(
        self,
        viking_fs: VikingFS,
        *,
        params: dict[str, object] | None = None,
        api_key_hashing_enabled: bool = False,
        watch_enabled: bool = False,
        watch_interval_seconds: float = 30.0,
    ):
        params = dict(params or {})
        reserved = {
            "viking_fs",
            "api_key_hashing_enabled",
        }
        if overridden := reserved.intersection(params):
            names = ", ".join(sorted(overridden))
            raise InvalidArgumentError(
                f"account_store.params cannot override framework parameters: {names}"
            )
        configured_watch_enabled = params.pop("watch_enabled", watch_enabled)
        configured_watch_interval = params.pop(
            "watch_interval_seconds", watch_interval_seconds
        )
        if not isinstance(configured_watch_enabled, bool):
            raise InvalidArgumentError("watch_enabled must be a boolean")
        if not isinstance(configured_watch_interval, (int, float)):
            raise InvalidArgumentError("watch_interval_seconds must be a number")
        if params:
            raise InvalidArgumentError(
                f"Unknown file account store parameters: {', '.join(sorted(params))}"
            )

        self._viking_fs = viking_fs
        self._async_agfs = AsyncAGFSClient(viking_fs.agfs)
        self._api_key_hashing_enabled = api_key_hashing_enabled
        self._watch_enabled = configured_watch_enabled
        self._watch_interval_seconds = (
            float(configured_watch_interval)
            if configured_watch_interval > 0
            else 30.0
        )
        self._watch_task: Optional[asyncio.Task] = None
        self._accounts: Dict[str, AccountInfo] = {}
        # Prefix index: key_prefix -> list[UserKeyEntry]
        self._prefix_index: Dict[str, list[UserKeyEntry]] = {}
        self._user_group_ids: Dict[tuple[str, str], tuple[str, ...]] = {}
        # Serializes internal refreshes so overlapping reloads can't interleave.
        self._reload_lock = asyncio.Lock()
        # Serializes all mutations made by the unified manager in this process.
        # File locks remain responsible for inter-instance coordination.
        self._mutation_lock = asyncio.Lock()
        self._deletion_lock = asyncio.Lock()

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
            logger.info(
                "FileStore loaded: %d accounts, %d user keys",
                len(self._accounts),
                sum(len(info.users) for info in self._accounts.values()),
            )
        self._start_watcher()

    def _start_watcher(self) -> None:
        if not self._watch_enabled or self._watch_task is not None:
            return
        self._watch_task = asyncio.create_task(self._watch_store())
        logger.info(
            "FileStore watcher started (interval=%.1fs)",
            self._watch_interval_seconds,
        )

    async def close(self) -> None:
        if self._watch_task is None:
            return
        self._watch_task.cancel()
        try:
            await self._watch_task
        except asyncio.CancelledError:
            pass
        finally:
            self._watch_task = None

    async def _watch_store(self) -> None:
        try:
            last_signature = await self._compute_store_signature()
        except Exception:
            logger.warning(
                "Initial account store signature failed; watcher will retry", exc_info=True
            )
            last_signature = None

        while True:
            await asyncio.sleep(self._watch_interval_seconds)
            try:
                signature = await self._compute_store_signature()
            except Exception:
                logger.debug("Account store signature check failed", exc_info=True)
                continue
            if signature == last_signature:
                continue
            try:
                await self._reload()
                last_signature = signature
                logger.info("Account store change detected; provider cache reloaded")
            except Exception:
                logger.warning("Account store reload failed; will retry", exc_info=True)

    async def _reload(self) -> None:
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
            logger.debug(
                "FileStore reloaded: %d accounts, %d user keys",
                len(self._accounts),
                sum(len(info.users) for info in self._accounts.values()),
            )

    async def _refresh_accounts_from_store_unlocked(self) -> None:
        accounts_data = await self._read_json(ACCOUNTS_PATH)
        if accounts_data is None:
            return
        persisted_accounts = accounts_data.get("accounts", {})

        for account_id in set(self._accounts) - set(persisted_accounts):
            self._discard_account_state(account_id)
        for account_id, info in persisted_accounts.items():
            created_at = info.get("created_at", "")
            account = self._accounts.get(account_id)
            if account is not None and account.created_at != created_at:
                self._discard_account_state(account_id)
                account = None
            if account is None:
                self._accounts[account_id] = AccountInfo(
                    created_at=created_at,
                    groups_loaded=False,
                    deletion=info.get("deletion"),
                )
            else:
                account.created_at = created_at
                account.deletion = info.get("deletion")

    async def _refresh_account_users_from_store_unlocked(self, account_id: str) -> None:
        users_path = USERS_PATH_TEMPLATE.format(account_id=account_id)
        users_data = await self._read_json(users_path)
        account = self._accounts.get(account_id)
        if users_data is None and account is None:
            raise NotFoundError(account_id, "account")

        users = users_data.get("users", {}) if users_data else {}
        prefix_entries: list[tuple[str, UserKeyEntry]] = []
        for user_id, user_info in users.items():
            key_or_hash = user_info.get("key", "")
            if not key_or_hash:
                continue
            key_prefix = user_info.get("key_prefix", "") or self._get_key_prefix(key_or_hash)
            if key_prefix:
                prefix_entries.append(
                    (
                        key_prefix,
                        UserKeyEntry(
                            account_id=account_id,
                            user_id=user_id,
                            role=Role(user_info.get("role", "user")),
                            key_or_hash=key_or_hash,
                            is_hashed=key_or_hash.startswith("$argon2"),
                        ),
                    )
                )

        if account is None:
            account = AccountInfo(created_at="", groups_loaded=False)
            self._accounts[account_id] = account

        for user_id, user_info in account.users.items():
            self._remove_key_index_entry(account_id, user_id, user_info)

        account.users = users
        for key_prefix, entry in prefix_entries:
            self._prefix_index.setdefault(key_prefix, []).append(entry)
        self._rebuild_account_group_index(account_id)

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
                deletion=info.get("deletion"),
            )
            user_group_ids.update(
                {
                    (account_id, user_id): group_ids
                    for user_id, group_ids in self._group_memberships(users, groups).items()
                }
            )

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

    async def _compute_store_signature(self) -> tuple:
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

    async def _create_account_identity(
        self,
        account_id: str,
        admin_user_id: str,
        api_key: str | None = None,
    ) -> None:
        """Create an account and its first admin, optionally with a credential."""
        if error := validate_account_id(account_id):
            raise InvalidArgumentError(error)
        # Only new accounts: existing remote-backed identities remain readable.
        validate_file_account_id(account_id)
        if error := validate_user_id(admin_user_id):
            raise InvalidArgumentError(error)
        if error := validate_identifier_length(admin_user_id, "admin_user_id"):
            raise InvalidArgumentError(error)
        if account_id in self._accounts:
            raise AlreadyExistsError(account_id, "account")

        now = datetime.now(timezone.utc).isoformat()
        user_info = {"role": "admin"}
        if api_key is not None:
            user_info["key"] = (
                self._hash_api_key(api_key)
                if self._api_key_hashing_enabled
                else api_key
            )
            if self._api_key_hashing_enabled:
                user_info["key_prefix"] = self._get_key_prefix(api_key)
        self._accounts[account_id] = AccountInfo(
            created_at=now,
            users={admin_user_id: user_info},
            groups={},
        )
        account_was_created = False
        try:
            created = await self._save_accounts_json(
                updated_account_ids={account_id},
                reject_existing_account_ids={account_id},
            )
            account_was_created = account_id in created
            await self._save_users_json(
                account_id,
                {admin_user_id: user_info},
                replace_existing=account_was_created,
            )
            await self._write_groups_json(account_id, {})
        except Exception:
            await self._rollback_create_account(
                account_id, account_was_created=account_was_created
            )
            raise

    async def _delete_account_identity(self, account_id: str) -> None:
        """Delete an account and remove all its user keys from the index."""
        if account_id not in self._accounts:
            raise NotFoundError(account_id, "account")

        await self._save_accounts_json(delete_account_ids={account_id})
        self._discard_account_state(account_id)

    async def _create_user_identity(
        self,
        account_id: str,
        user_id: str,
        role: str = "user",
        *,
        api_key: str | None = None,
    ) -> None:
        """Create a user, optionally including its initial API key."""
        resolved_role = validate_account_user_role(role)
        if error := validate_user_id(user_id):
            raise InvalidArgumentError(error)
        if error := validate_identifier_length(user_id, "user_id"):
            raise InvalidArgumentError(error)
        self._ensure_account_active(account_id)
        account = self._accounts.get(account_id)
        if account is None:
            raise NotFoundError(account_id, "account")
        if user_id in account.users:
            raise AlreadyExistsError(user_id, "user")

        user_info = {"role": resolved_role}
        if api_key is not None:
            stored_key = (
                self._hash_api_key(api_key)
                if self._api_key_hashing_enabled
                else api_key
            )
            user_info["key"] = stored_key
            if self._api_key_hashing_enabled:
                user_info["key_prefix"] = self._get_key_prefix(api_key)
        account.users[user_id] = user_info
        try:
            await self._save_users_json(
                account_id,
                {user_id: user_info},
                reject_existing_user_ids={user_id},
            )
        except Exception:
            account.users.pop(user_id, None)
            raise

    async def _set_user_key(
        self,
        account_id: str,
        user_id: str,
        api_key: str,
        *,
        rotate: bool,
    ) -> None:
        """Persist one credential in the legacy user record."""
        self._ensure_account_active(account_id)
        account = self._accounts.get(account_id)
        if account is None:
            raise NotFoundError(account_id, "account")
        user_info = account.users.get(user_id)
        if user_info is None:
            raise NotFoundError(user_id, "user")
        if user_info.get("deletion"):
            raise FailedPreconditionError("User deletion is in progress")
        if user_info.get("key") and not rotate:
            raise AlreadyExistsError(user_id, "API key")

        material = (
            self._hash_api_key(api_key)
            if self._api_key_hashing_enabled
            else api_key
        )
        old_user_info = copy.deepcopy(user_info)
        self._remove_key_index_entry(account_id, user_id, old_user_info)
        user_info["key"] = material
        if material.startswith("$argon2"):
            user_info["key_prefix"] = self._get_key_prefix(api_key)
        else:
            user_info.pop("key_prefix", None)
        try:
            await self._save_users_json(account_id, {user_id: user_info})
        except Exception:
            account.users[user_id] = old_user_info
            self._rebuild_prefix_index()
            raise

    async def _revoke_user_keys(
        self, account_id: str, user_id: str | None = None
    ) -> None:
        """Remove matching credentials from legacy user records."""
        account = self._accounts.get(account_id)
        if account is None:
            return
        targets = [user_id] if user_id is not None else list(account.users)
        updates = {}
        originals = {}
        for target in targets:
            user_info = account.users.get(target)
            if user_info is None or not user_info.get("key"):
                continue
            originals[target] = copy.deepcopy(user_info)
            self._remove_key_index_entry(account_id, target, user_info)
            user_info["key"] = ""
            user_info.pop("key_prefix", None)
            updates[target] = user_info
        if updates:
            try:
                await self._save_users_json(account_id, updates)
            except Exception:
                account.users.update(originals)
                self._rebuild_prefix_index()
                raise

    async def ensure_trusted_identities(self, identities: Dict[str, set[str]]) -> dict[str, int]:
        """Merge trusted identities into the registry without creating API keys."""
        async with self._mutation_lock:
            return await self._ensure_trusted_identities_unlocked(identities)

    async def _ensure_trusted_identities_unlocked(
        self, identities: Dict[str, set[str]]
    ) -> dict[str, int]:
        normalized = {
            account_id: set(user_ids)
            for account_id, user_ids in identities.items()
            if user_ids
        }
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
                account = self._accounts.get(account_id)
                if account is None or user_id not in account.users:
                    error = validate_identifier_length(user_id, "user_id")
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
            accounts_data = await self._read_accounts_for_update()
            persisted_accounts = accounts_data.setdefault("accounts", {})
            normalized = {
                account_id: user_ids
                for account_id, user_ids in normalized.items()
                if not (persisted_accounts.get(account_id) or {}).get("deletion")
            }
            for account_id in normalized:
                if account_id not in persisted_accounts:
                    validate_file_account_id(account_id)
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
                new_users = sorted(
                    user_id for user_id in user_ids if user_id not in persisted_users
                )
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
                    account_info = persisted_accounts[account_id]
                    account = AccountInfo(
                        created_at=account_info.get("created_at", now),
                        users={},
                        groups={},
                        groups_loaded=False,
                        deletion=account_info.get("deletion"),
                    )
                    self._accounts[account_id] = account
                for user_id, user_info in persisted_users.items():
                    account.users.setdefault(user_id, dict(user_info))
            finally:
                await self._async_agfs.pathlock_release(users_lease)

        return {"created_accounts": created_accounts, "created_users": created_users}

    async def _begin_deletion(
        self,
        account_id: str,
        user_id: str | None,
        *,
        task_id: str,
        owner_account_id: str,
        owner_user_id: str,
    ) -> tuple[dict, bool]:
        """Revoke an account or user and persist its cleanup task fence."""
        async with self._reload_lock, self._deletion_lock:
            account = self._accounts.get(account_id)
            if account is None:
                raise NotFoundError(account_id, "account")
            if user_id is None:
                if account.deletion is not None:
                    return dict(account.deletion), False
                account.deletion = {
                    "task_id": task_id,
                    "owner_account_id": owner_account_id,
                    "owner_user_id": owner_user_id,
                }
                try:
                    await self._save_accounts_json(updated_account_ids={account_id})
                except BaseException:
                    account.deletion = None
                    raise
                return dict(account.deletion), True
            self._ensure_account_active(account_id)
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

    async def _replace_deletion_task(
        self,
        account_id: str,
        user_id: str | None,
        *,
        expected_task_id: str,
        task_id: str,
        owner_account_id: str,
        owner_user_id: str,
    ) -> dict:
        """Replace the task that owns an existing deletion fence."""
        async with self._reload_lock, self._deletion_lock:
            account = self._accounts.get(account_id)
            if account is None:
                raise NotFoundError(account_id, "account")
            if user_id is None:
                current = account.deletion
                if current is None or current["task_id"] != expected_task_id:
                    return dict(current) if current else {}
                account.deletion = {
                    "task_id": task_id,
                    "owner_account_id": owner_account_id,
                    "owner_user_id": owner_user_id,
                }
                try:
                    await self._save_accounts_json(updated_account_ids={account_id})
                except BaseException:
                    account.deletion = current
                    raise
                return dict(account.deletion)
            self._ensure_account_active(account_id)
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

    async def _finish_deletion(
        self, account_id: str, user_id: str | None, task_id: str
    ) -> bool:
        """Remove the identity only when this task still owns its deletion fence."""
        async with self._reload_lock, self._deletion_lock:
            account = self._accounts.get(account_id)
            if account is None:
                return False
            if user_id is None:
                if account.deletion is None or account.deletion["task_id"] != task_id:
                    return False
                await self._delete_account_identity(account_id)
                return True
            user_info = account.users.get(user_id)
            if user_info is None:
                return False
            deletion = user_info.get("deletion")
            if not isinstance(deletion, dict) or deletion.get("task_id") != task_id:
                return False

            await self._load_account_groups_if_needed(account_id, account)
            old_groups = copy.deepcopy(account.groups)
            groups = copy.deepcopy(account.groups)
            for group in groups.values():
                members = group.get("members", [])
                if user_id in members:
                    group["members"] = [member for member in members if member != user_id]
            self._remove_key_index_entry(account_id, user_id, user_info)
            account.users.pop(user_id)
            try:
                await self._save_users_json(account_id, deleted_user_ids={user_id})
                if groups != old_groups:
                    await self._write_groups_json(account_id, groups)
            except Exception:
                account.users[user_id] = user_info
                account.groups = old_groups
                self._rebuild_prefix_index()
                self._rebuild_account_group_index(account_id)
                raise
            if groups != old_groups:
                account.groups = groups
                self._rebuild_account_group_index(account_id)
            return True

    def _get_deletion(
        self, account_id: str, user_id: str | None = None
    ) -> Optional[dict]:
        account = self._accounts.get(account_id)
        if account is None:
            return None
        if user_id is None:
            return dict(account.deletion) if account.deletion is not None else None
        user_info = account.users.get(user_id)
        deletion = user_info.get("deletion") if user_info else None
        return dict(deletion) if isinstance(deletion, dict) else None

    def _iter_deletions(self) -> list[tuple[str, str | None, dict]]:
        return [
            (account_id, user_id, dict(deletion))
            for account_id, account in self._accounts.items()
            for user_id, user_info in account.users.items()
            if isinstance((deletion := user_info.get("deletion")), dict) and deletion.get("task_id")
        ] + [
            (account_id, None, dict(account.deletion))
            for account_id, account in self._accounts.items()
            if account.deletion is not None
        ]

    async def _set_role(self, account_id: str, user_id: str, role: str) -> None:
        """Update a user's role."""
        resolved_role = validate_account_user_role(role)
        self._ensure_account_active(account_id)
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

    def _get_accounts(
        self,
        name_filter: str | None = None,
        limit: int | None = None,
        page: int = 1,
        query_filter: str | None = None,
    ) -> list:
        """List accounts in creation (insertion) order.

        ``name_filter`` uses wildcard (``*`` and ``?``) matching. ``query_filter``
        is a case-insensitive substring match on the account id, mirroring the
        user listing search. Pagination is opt-in: ``limit=None`` returns every
        matching account so internal callers that rely on the full account set
        are unaffected; when ``limit`` is set, ``page`` (1-based) selects the
        slice.
        """
        result = []
        query = (query_filter or "").strip().casefold()
        for account_id, info in self._accounts.items():
            # Apply name filter if provided (fnmatch wildcard matching)
            if name_filter and not fnmatch.fnmatch(account_id, name_filter):
                continue
            if query and query not in account_id.casefold():
                continue

            result.append(
                {
                    "account_id": account_id,
                    "created_at": info.created_at,
                    "user_count": len(info.users),
                    "status": "deleting" if info.deletion else "active",
                    **({"task_id": info.deletion["task_id"]} if info.deletion else {}),
                }
            )
        return _paginate(result, limit, page)

    def _get_users(
        self,
        account_id: str,
        limit: int | None = 100,
        name_filter: str | None = None,
        role_filter: str | None = None,
        expose_key: bool = True,
        page: int = 1,
        query_filter: str | None = None,
    ) -> list:
        """List users in an account in creation (insertion) order.

        Pagination is opt-in via ``limit``/``page`` (1-based); ``limit=None``
        returns every matching user.
        """
        return self._get_users_page(
            account_id,
            limit=limit,
            name_filter=name_filter,
            role_filter=role_filter,
            expose_key=expose_key,
            page=page,
            query_filter=query_filter,
        )["users"]

    def _get_users_page(
        self,
        account_id: str,
        limit: int | None = 100,
        name_filter: str | None = None,
        role_filter: str | None = None,
        expose_key: bool = True,
        page: int = 1,
        query_filter: str | None = None,
    ) -> dict:
        """Return one page, matching total, and unfiltered account statistics.

        Only materialize credentials for the requested page. Deleting users are
        excluded from both the results and statistics.
        """
        account = self._accounts.get(account_id)
        if account is None:
            raise NotFoundError(account_id, "account")

        result = []
        total = account_total = manager_count = key_count = 0
        start = (max(1, page) - 1) * limit if limit is not None else 0
        query = (query_filter or "").strip().casefold()
        for user_id, user_info in account.users.items():
            if user_info.get("deletion"):
                continue
            user_role = user_info.get("role", "user")
            key = user_info.get("key")
            visible_key = bool(
                expose_key
                and key
                and (not key.startswith("$argon2") or user_info.get("key_prefix"))
            )
            account_total += 1
            manager_count += user_role in {"admin", "root"}
            key_count += visible_key

            if name_filter and not fnmatch.fnmatch(user_id, name_filter):
                continue
            if role_filter and user_role != role_filter:
                continue
            if query and query not in user_id.casefold():
                continue
            total += 1
            if total <= start or (limit is not None and len(result) >= limit):
                continue

            user_data = {"user_id": user_id, "role": user_role}
            if visible_key:
                if key.startswith("$argon2"):
                    user_data["key_prefix"] = user_info["key_prefix"]
                else:
                    user_data["api_key"] = key
            result.append(user_data)
        return {
            "users": result,
            "total": total,
            "account_total": account_total,
            "manager_count": manager_count,
            "key_count": key_count,
        }

    def _get_user_group_ids(self, account_id: str, user_id: str) -> tuple[str, ...]:
        """Return the account-scoped groups currently containing the user."""
        return self._user_group_ids.get((account_id, user_id), ())

    async def _create_group(self, account_id: str, group_id: str) -> dict:
        error = validate_identifier_part(group_id, "group_id")
        if error:
            raise InvalidArgumentError(error)
        async with self._reload_lock:
            account = self._require_account(account_id)
            await self._load_account_groups_if_needed(account_id, account)
            if group_id in account.groups:
                raise AlreadyExistsError(group_id, "group")
            groups = copy.deepcopy(account.groups)
            groups[group_id] = {"members": []}
            await self._replace_groups(account_id, account, groups)
            return self._group_result(group_id, groups[group_id])

    def _get_groups(self, account_id: str) -> list[dict]:
        account = self._require_account(account_id)
        return [
            self._group_result(group_id, group)
            for group_id, group in sorted(account.groups.items())
        ]

    def _get_group_members(self, account_id: str, group_id: str) -> list[str]:
        group = self._require_group(account_id, group_id)
        return sorted(set(group.get("members", [])))

    async def _add_group_member(
        self, account_id: str, group_id: str, user_id: str
    ) -> bool:
        async with self._reload_lock:
            account = self._require_account(account_id)
            await self._load_account_groups_if_needed(account_id, account)
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

    async def _remove_group_member(
        self, account_id: str, group_id: str, user_id: str
    ) -> bool:
        async with self._reload_lock:
            account = self._require_account(account_id)
            await self._load_account_groups_if_needed(account_id, account)
            group = self._require_group(account_id, group_id)
            if user_id not in group.get("members", []):
                return False
            groups = copy.deepcopy(account.groups)
            groups[group_id]["members"] = [
                member for member in groups[group_id].get("members", []) if member != user_id
            ]
            await self._replace_groups(account_id, account, groups)
            return True

    async def _delete_group(self, account_id: str, group_id: str) -> None:
        async with self._reload_lock:
            account = self._require_account(account_id)
            await self._load_account_groups_if_needed(account_id, account)
            self._require_group(account_id, group_id)
            groups = copy.deepcopy(account.groups)
            del groups[group_id]
            await self._replace_groups(account_id, account, groups)

    # ---- internal helpers ----

    def _ensure_account_active(self, account_id: str) -> None:
        deletion = self._get_deletion(account_id)
        if deletion is not None:
            raise FailedPreconditionError(
                "Account deletion is in progress",
                details={"task_id": deletion["task_id"]},
            )

    def _require_account(self, account_id: str) -> AccountInfo:
        self._ensure_account_active(account_id)
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

    async def _ensure_account_groups_loaded(self, account_id: str) -> None:
        """Load one account's groups when its account metadata was discovered alone."""
        async with self._reload_lock:
            account = self._require_account(account_id)
            await self._load_account_groups_if_needed(account_id, account)

    async def _load_account_groups_if_needed(self, account_id: str, account: AccountInfo) -> None:
        if account.groups_loaded:
            return
        groups_path = GROUPS_PATH_TEMPLATE.format(account_id=account_id)
        groups_data = await self._read_json(groups_path)
        account.groups = groups_data.get("groups", {}) if groups_data else {}
        account.groups_loaded = True
        self._rebuild_account_group_index(account_id)

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
        self._user_group_ids.update(
            {
                (account_id, user_id): group_ids
                for user_id, group_ids in self._group_memberships(
                    account.users, account.groups
                ).items()
            }
        )

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
        account.groups_loaded = True
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

    async def _read_accounts_for_update(self) -> dict:
        data = await self._read_json(ACCOUNTS_PATH)
        if data is None:
            raise FailedPreconditionError(
                "Account registry is missing; refusing to overwrite it with account changes."
            )
        return data

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
            data = await self._read_accounts_for_update()
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
                if info.deletion is not None:
                    accounts[account_id]["deletion"] = dict(info.deletion)
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

    def _watcher_running(self) -> bool:
        return self._watch_task is not None and not self._watch_task.done()

    async def _refresh_accounts_for_management_read(self) -> None:
        async with self._mutation_lock:
            await self._refresh_accounts_for_management_read_unlocked()

    async def _refresh_users_for_management_read(self, account_id: str) -> None:
        async with self._mutation_lock:
            await self._refresh_users_for_management_read_unlocked(account_id)

    async def _refresh_accounts_for_management_read_unlocked(self) -> None:
        """Refresh account state while the caller holds ``_mutation_lock``."""
        if not self._watcher_running():
            async with self._reload_lock:
                await self._refresh_accounts_from_store_unlocked()

    async def _refresh_users_for_management_read_unlocked(
        self, account_id: str
    ) -> None:
        """Refresh user state while the caller holds ``_mutation_lock``."""
        if not self._watcher_running():
            async with self._reload_lock:
                await self._refresh_account_users_from_store_unlocked(account_id)

    async def get_account(self, account_id: str) -> AccountSummary | None:
        await self._refresh_accounts_for_management_read()
        return next(
            (
                account
                for account in self._get_accounts()
                if account["account_id"] == account_id
            ),
            None,
        )

    async def get_user(self, account_id: str, user_id: str) -> UserSummary | None:
        account = self._accounts.get(account_id)
        if account is None:
            return None
        user = account.users.get(user_id)
        return (
            UserSummary(user_id=user_id, role=user.get("role", Role.USER))
            if user
            else None
        )

    @deprecated(
        "get_user_for_management is a FileStore compatibility path and will be removed.",
        category=None,
    )
    async def get_user_for_management(
        self, account_id: str, user_id: str
    ) -> UserSummary | None:
        await self._refresh_users_for_management_read(account_id)
        return await self.get_user(account_id, user_id)

    async def create_account_with_api_key(
        self,
        account_id: str,
        admin_user_id: str,
        api_key: str,
    ) -> None:
        async with self._mutation_lock:
            await self._refresh_accounts_for_management_read_unlocked()
            await self._create_account_identity(
                account_id,
                admin_user_id,
                api_key,
            )

    async def create_account(
        self,
        account_id: str,
        admin_user_id: str,
    ) -> None:
        async with self._mutation_lock:
            await self._refresh_accounts_for_management_read_unlocked()
            await self._create_account_identity(account_id, admin_user_id)

    async def create_user(self, account_id: str, user_id: str, role: str) -> None:
        async with self._mutation_lock:
            await self._create_user_identity(account_id, user_id, role)

    async def create_user_with_api_key(
        self,
        account_id: str,
        user_id: str,
        role: str,
        api_key: str,
    ) -> None:
        async with self._mutation_lock:
            await self._create_user_identity(
                account_id,
                user_id,
                role,
                api_key=api_key,
            )

    async def delete_account(self, account_id: str) -> None:
        async with self._mutation_lock:
            await self._refresh_accounts_for_management_read_unlocked()
            await self._delete_account_identity(account_id)

    async def list_accounts(
        self,
        name_filter: str | None = None,
        limit: int | None = None,
        page: int = 1,
        query_filter: str | None = None,
    ) -> list[AccountSummary]:
        await self._refresh_accounts_for_management_read()
        return self._get_accounts(
            name_filter=name_filter,
            limit=limit,
            page=page,
            query_filter=query_filter,
        )

    async def list_users_page(
        self,
        account_id: str,
        limit: int | None = 100,
        name_filter: str | None = None,
        role_filter: str | None = None,
        page: int = 1,
        query_filter: str | None = None,
        fresh: bool = False,
    ) -> UsersPage:
        if fresh:
            async with self._mutation_lock:
                async with self._reload_lock:
                    await self._refresh_account_users_from_store_unlocked(account_id)
        else:
            await self._refresh_users_for_management_read(account_id)
        return self._get_users_page(
            account_id,
            limit=limit,
            name_filter=name_filter,
            role_filter=role_filter,
            expose_key=True,
            page=page,
            query_filter=query_filter,
        )

    async def set_role(self, account_id: str, user_id: str, role: str) -> None:
        async with self._mutation_lock:
            await self._refresh_accounts_for_management_read_unlocked()
            await self._refresh_users_for_management_read_unlocked(account_id)
            await self._set_role(account_id, user_id, role)

    async def begin_deletion(
        self,
        account_id: str,
        user_id: str | None,
        *,
        task_id: str,
        owner_account_id: str,
        owner_user_id: str,
    ) -> tuple[DeletionRecord, bool]:
        async with self._mutation_lock:
            await self._refresh_accounts_for_management_read_unlocked()
            if user_id is not None:
                await self._refresh_users_for_management_read_unlocked(account_id)
            return await self._begin_deletion(
                account_id,
                user_id,
                task_id=task_id,
                owner_account_id=owner_account_id,
                owner_user_id=owner_user_id,
            )

    async def replace_deletion_task(
        self,
        account_id: str,
        user_id: str | None,
        *,
        expected_task_id: str,
        task_id: str,
        owner_account_id: str,
        owner_user_id: str,
    ) -> DeletionRecord:
        async with self._mutation_lock:
            return await self._replace_deletion_task(
                account_id,
                user_id,
                expected_task_id=expected_task_id,
                task_id=task_id,
                owner_account_id=owner_account_id,
                owner_user_id=owner_user_id,
            )

    async def finish_deletion(
        self, account_id: str, user_id: str | None, task_id: str
    ) -> bool:
        async with self._mutation_lock:
            return await self._finish_deletion(account_id, user_id, task_id)

    async def get_deletion(
        self, account_id: str, user_id: str | None = None
    ) -> DeletionRecord | None:
        return self._get_deletion(account_id) or (
            self._get_deletion(account_id, user_id) if user_id is not None else None
        )

    async def iter_deletions(
        self,
    ) -> list[tuple[str, str | None, DeletionRecord]]:
        return self._iter_deletions()

    async def create_group(
        self, account_id: str, group_id: str
    ) -> GroupSummary:
        await self._refresh_accounts_for_management_read()
        return await self._create_group(account_id, group_id)

    async def get_groups(self, account_id: str) -> list[GroupSummary]:
        await self._refresh_accounts_for_management_read()
        await self._ensure_account_groups_loaded(account_id)
        return self._get_groups(account_id)

    async def get_group_members(
        self, account_id: str, group_id: str
    ) -> list[str]:
        await self._refresh_accounts_for_management_read()
        await self._ensure_account_groups_loaded(account_id)
        return self._get_group_members(account_id, group_id)

    async def get_user_group_ids(self, account_id: str, user_id: str) -> tuple[str, ...]:
        account = self._accounts.get(account_id)
        if account is None or user_id not in account.users:
            return ()
        await self._ensure_account_groups_loaded(account_id)
        return self._get_user_group_ids(account_id, user_id)

    async def add_group_member(
        self, account_id: str, group_id: str, user_id: str
    ) -> bool:
        await self._refresh_users_for_management_read(account_id)
        return await self._add_group_member(account_id, group_id, user_id)

    async def remove_group_member(
        self, account_id: str, group_id: str, user_id: str
    ) -> bool:
        await self._refresh_accounts_for_management_read()
        return await self._remove_group_member(account_id, group_id, user_id)

    async def delete_group(self, account_id: str, group_id: str) -> None:
        await self._refresh_accounts_for_management_read()
        await self._delete_group(account_id, group_id)

    async def replace_active_user_api_key(
        self, account_id: str, user_id: str, api_key: str
    ) -> None:
        async with self._mutation_lock:
            await self._set_user_key(
                account_id,
                user_id,
                api_key,
                rotate=True,
            )

    async def verify_api_key(
        self,
        api_key: str,
        *,
        account_id_hint: str | None = None,
        user_id_hint: str | None = None,
    ) -> tuple[str, str] | None:
        for entry in self._prefix_index.get(self._get_key_prefix(api_key), []):
            if account_id_hint is not None and entry.account_id != account_id_hint:
                continue
            if user_id_hint is not None and entry.user_id != user_id_hint:
                continue
            account = self._accounts.get(entry.account_id)
            if account is None or account.deletion is not None:
                continue
            user = account.users.get(entry.user_id)
            if user is None or user.get("deletion"):
                continue
            if entry.is_hashed:
                try:
                    matched = await asyncio.to_thread(
                        PasswordHasher(
                            time_cost=ARGON2_TIME_COST,
                            memory_cost=ARGON2_MEMORY_COST,
                            parallelism=ARGON2_PARALLELISM,
                            hash_len=ARGON2_HASH_LENGTH,
                        ).verify,
                        entry.key_or_hash,
                        api_key,
                    )
                except VerificationError:
                    matched = False
            else:
                matched = hmac.compare_digest(entry.key_or_hash, api_key)
            if matched:
                return entry.account_id, entry.user_id
        return None

    async def revoke_active_api_keys(
        self, account_id: str, user_id: str | None = None
    ) -> None:
        async with self._mutation_lock:
            await self._revoke_user_keys(account_id, user_id)

    async def get_user_key_fingerprint(
        self, account_id: str, user_id: str
    ) -> str | None:
        account = self._accounts.get(account_id)
        if account is None or account.deletion is not None:
            return None
        user = account.users.get(user_id)
        if user is None or user.get("deletion"):
            return None
        material = user.get("key", "")
        return hashlib.sha256(material.encode()).hexdigest() if material else None
