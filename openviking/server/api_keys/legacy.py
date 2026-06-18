# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Legacy API Key management (original implementation)."""

import asyncio
import fnmatch
import hashlib
import hmac
import json
import secrets
import time
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
    InvalidArgumentError,
    NotFoundError,
    UnauthenticatedError,
)
from openviking_cli.session.user_id import validate_account_id, validate_user_id
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

ACCOUNTS_PATH = "/local/_system/accounts.json"
USERS_PATH_TEMPLATE = "/local/{account_id}/_system/users.json"


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

# In-memory cache TTL. Multi-instance deploys share AGFS state but each instance
# keeps its own dict; without a TTL an account created on instance A would never
# appear on instance B. Cache-miss reload (see resolve_with_refresh) handles the
# urgent case; this constant bounds staleness for already-cached entries.
ACCOUNTS_CACHE_TTL_SECONDS = 30.0

# Bound how often a *known-bad* key can trigger a reload. Without this a stream
# of bogus keys from a misconfigured client would reload AGFS on every request.
UNKNOWN_KEY_RELOAD_COOLDOWN_SECONDS = ACCOUNTS_CACHE_TTL_SECONDS


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
        # Monotonic clock at last successful AGFS load. ``None`` means "never
        # loaded" — resolve_with_refresh treats this as expired so the first
        # cache-miss for an unknown key always triggers a reload.
        self._loaded_at: Optional[float] = None
        # Lazily created in resolve_with_refresh (constructor may run outside
        # an event loop, e.g. test fixtures). Dedupes concurrent reloads so a
        # burst of misses for a freshly-created key only hits AGFS once.
        self._reload_lock: Optional[asyncio.Lock] = None
        # Negative cache: monotonic timestamp of the most recent forced reload
        # triggered by *this* unknown key. Bounds the reload rate when a client
        # repeatedly presents a key that genuinely doesn't exist.
        self._unknown_key_reload_at: Dict[str, float] = {}

    def _discard_account_state(self, account_id: str) -> None:
        """Remove an account and its key index entries from in-memory state."""
        account = self._accounts.pop(account_id, None)
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
        """Load accounts and user keys from VikingFS into memory.

        Safe to call repeatedly — clears existing in-memory state first so a
        reload picks up writes made by peer instances against shared AGFS.
        """
        load_start = time.monotonic()
        # Reset in-memory state so peer-instance deletions are also reflected.
        self._accounts.clear()
        self._prefix_index.clear()

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

            self._accounts[account_id] = AccountInfo(
                created_at=info.get("created_at", ""),
                users=users,
            )

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
        elapsed = time.monotonic() - load_start
        if elapsed > 1.0:
            logger.warning(
                "LegacyAPIKeyManager.load() took %.2fs (>1s) — consider scoping"
                " reloads if storage layer adds per-key reads",
                elapsed,
            )
        self._loaded_at = time.monotonic()
        # A successful reload makes any prior negative-cache entries obsolete.
        self._unknown_key_reload_at.clear()

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

    def _cache_is_stale(self) -> bool:
        """Whether the in-memory cache is older than the TTL."""
        if self._loaded_at is None:
            return True
        return (time.monotonic() - self._loaded_at) > ACCOUNTS_CACHE_TTL_SECONDS

    def _should_skip_unknown_key_reload(self, api_key: str) -> bool:
        """Whether this unknown key has already triggered a recent reload."""
        last = self._unknown_key_reload_at.get(api_key)
        if last is None:
            return False
        return (time.monotonic() - last) < UNKNOWN_KEY_RELOAD_COOLDOWN_SECONDS

    def invalidate_cache(self) -> None:
        """Clear the unknown-key cooldown after a local write.

        Local writes mutate ``self._accounts`` synchronously so the new state
        is already visible to ``resolve``. The only stale piece is the
        negative cache: a key looked up *before* it was minted would have
        been recorded as unknown and would otherwise sit in cooldown until
        the TTL elapsed. Clearing the cooldown lets the just-minted key
        resolve on the very next request.
        """
        self._unknown_key_reload_at.clear()

    async def _reload_locked(self, force: bool = False) -> None:
        """Re-read AGFS state under the dedup lock.

        ``force=True`` semantics: at least one reload after the call site
        observed a stale cache. Concurrent forced reloads dedupe — once any
        caller's reload completes inside the lock, later callers see a fresh
        ``_loaded_at`` and skip.
        """
        if self._reload_lock is None:
            self._reload_lock = asyncio.Lock()
        # Snapshot the load timestamp *before* acquiring the lock so we can
        # tell whether a peer caller already refreshed by the time we get in.
        observed = self._loaded_at
        async with self._reload_lock:
            if force:
                if self._loaded_at is not None and self._loaded_at != observed:
                    # Another caller refreshed while we were queued. Done.
                    return
            else:
                if not self._cache_is_stale():
                    return
            logger.info("api-key cache reload from AGFS (force=%s)", force)
            await self.load()

    async def resolve_with_refresh(self, api_key: str) -> ResolvedIdentity:
        """Like ``resolve`` but reloads from AGFS on cache miss / TTL expiry.

        Two-pronged freshness for multi-instance deploys:
          1. Cache-miss reload — an unknown ``api_key`` triggers one forced
             reload before declaring 401, so accounts created on a peer
             instance become visible on the next request.
          2. TTL refresh — entries older than ``ACCOUNTS_CACHE_TTL_SECONDS``
             are refreshed before the lookup, bounding staleness for entries
             we already think we know.

        Concurrent callers dedupe through ``_reload_lock`` so a thundering
        herd of 401s after a fresh account creation only hits AGFS once.
        """
        if not api_key:
            raise UnauthenticatedError("Missing API Key")

        # Periodic refresh — picks up rotations / removals on peer instances.
        if self._cache_is_stale():
            await self._reload_locked(force=False)

        try:
            return self.resolve(api_key)
        except UnauthenticatedError:
            pass

        # Cache miss. Avoid hammering AGFS for keys we just confirmed missing
        # *and for which a reload has already been performed*. If a peer
        # caller is mid-reload, our _reload_locked call below will piggy-back
        # on that reload rather than starting a new one.
        if self._should_skip_unknown_key_reload(api_key):
            raise UnauthenticatedError("Invalid API Key")

        observed_loaded_at = self._loaded_at
        await self._reload_locked(force=True)
        # Only stamp the negative cache *after* the reload resolved the
        # question (key truly absent). Stamping before would make peer
        # callers in the same burst short-circuit their own reload attempt
        # without ever seeing the post-reload state.
        try:
            return self.resolve(api_key)
        except UnauthenticatedError:
            # Only count this as a confirmed miss if our caller (or a peer)
            # actually reloaded — otherwise a concurrent caller's stamp could
            # cause us to mark a key as missing without re-checking AGFS.
            if self._loaded_at != observed_loaded_at:
                self._unknown_key_reload_at[api_key] = time.monotonic()
            raise

    async def create_account(
        self,
        account_id: str,
        admin_user_id: str,
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
        key = self._generate_api_key()

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
        except Exception:
            await self._rollback_create_account(account_id)
            raise
        self.invalidate_cache()
        return key

    async def delete_account(self, account_id: str) -> None:
        """Delete an account and remove all its user keys from the index."""
        if account_id not in self._accounts:
            raise NotFoundError(account_id, "account")

        self._discard_account_state(account_id)

        await self._save_accounts_json()
        self.invalidate_cache()

    async def register_user(self, account_id: str, user_id: str, role: str = "user") -> str:
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

        key = self._generate_api_key()

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
        self.invalidate_cache()
        return key

    async def remove_user(self, account_id: str, user_id: str) -> None:
        """Remove a user from an account."""
        account = self._accounts.get(account_id)
        if account is None:
            raise NotFoundError(account_id, "account")
        if user_id not in account.users:
            raise NotFoundError(user_id, "user")

        user_info = account.users.pop(user_id)
        key_or_hash = user_info.get("key", "")

        if key_or_hash:
            # Get key_prefix - if not in user_info, compute from key
            key_prefix = user_info.get("key_prefix", "")
            if not key_prefix:
                key_prefix = self._get_key_prefix(key_or_hash)

            # Remove from prefix index
            if key_prefix in self._prefix_index:
                self._prefix_index[key_prefix] = [
                    entry
                    for entry in self._prefix_index[key_prefix]
                    if not (entry.account_id == account_id and entry.user_id == user_id)
                ]
                # Remove prefix if index is empty
                if not self._prefix_index[key_prefix]:
                    del self._prefix_index[key_prefix]

        await self._save_users_json(account_id)
        self.invalidate_cache()

    async def regenerate_key(self, account_id: str, user_id: str) -> str:
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
        new_key = self._generate_api_key()

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
        self.invalidate_cache()
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
        self.invalidate_cache()

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
