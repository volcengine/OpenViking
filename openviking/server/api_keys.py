# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""API Key management for OpenViking multi-tenant HTTP Server."""

import hmac
import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from openviking.server.identity import AccountNamespacePolicy, ResolvedIdentity, Role
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import (
    AlreadyExistsError,
    NotFoundError,
    UnauthenticatedError,
)
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

ACCOUNTS_PATH = "/local/_system/accounts.json"
USERS_PATH_TEMPLATE = "/local/{account_id}/_system/users.json"
SETTINGS_PATH_TEMPLATE = "/local/{account_id}/_system/setting.json"


# Argon2id parameters
ARGON2_TIME_COST = 3
ARGON2_MEMORY_COST = 65536
ARGON2_PARALLELISM = 2
ARGON2_HASH_LENGTH = 32


@dataclass
class UserKeyEntry:
    """In-memory index entry for a user key."""

    account_id: str
    user_id: str
    role: Role
    key_or_hash: str
    is_hashed: bool


@dataclass
class AccountInfo:
    """In-memory account info."""

    created_at: str
    users: Dict[str, dict] = field(default_factory=dict)
    namespace_policy: AccountNamespacePolicy = field(default_factory=AccountNamespacePolicy)


class APIKeyManager:
    """Manages API keys for multi-tenant authentication.

    Two-level storage:
    - /_system/accounts.json: global workspace list
    - /{account_id}/_system/users.json: per-account user registry

    In-memory index for fast key lookup.
    Uses Argon2id for secure API key hashing.
    """

    def __init__(
        self,
        root_key: str,
        viking_fs: VikingFS,
        encryption_enabled: bool = False,
    ):
        """Initialize APIKeyManager.

        Args:
            root_key: Global root API key for administrative access.
            viking_fs: VikingFS client for persistent storage of user keys.
            encryption_enabled: Whether API key hashing is enabled.
        """
        self._root_key = root_key
        self._viking_fs = viking_fs
        self._encryption_enabled = encryption_enabled
        self._accounts: Dict[str, AccountInfo] = {}
        # Prefix index: key_prefix -> list[UserKeyEntry]
        self._prefix_index: Dict[str, list[UserKeyEntry]] = {}

    async def load(self) -> None:
        """Load accounts and user keys from VikingFS into memory."""
        accounts_data = await self._read_json(ACCOUNTS_PATH)
        fresh_workspace = accounts_data is None
        if accounts_data is None:
            # First run: create default account
            now = datetime.now(timezone.utc).isoformat()
            accounts_data = {"accounts": {"default": {"created_at": now}}}
            await self._write_json(ACCOUNTS_PATH, accounts_data)

        for account_id, info in accounts_data.get("accounts", {}).items():
            users_path = USERS_PATH_TEMPLATE.format(account_id=account_id)
            users_data = await self._read_json(users_path)
            users = users_data.get("users", {}) if users_data else {}
            settings_path = SETTINGS_PATH_TEMPLATE.format(account_id=account_id)
            settings_data = await self._read_json(settings_path)
            namespace_policy, should_persist_settings, inferred_from_legacy = (
                self._resolve_namespace_policy(
                    settings_data,
                    allow_legacy_inference=not fresh_workspace,
                )
            )

            self._accounts[account_id] = AccountInfo(
                created_at=info.get("created_at", ""),
                users=users,
                namespace_policy=namespace_policy,
            )
            if should_persist_settings:
                await self._save_settings_json(account_id, settings_data=settings_data)
                if inferred_from_legacy:
                    logger.info(
                        "Inferred namespace policy for legacy account %s using the historical "
                        "default user-shared/agent-shared layout",
                        account_id,
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
                        if self._encryption_enabled:
                            # If encryption enabled, migrate to hashed
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
                            # If encryption not enabled, keep as plaintext
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
            "APIKeyManager loaded: %d accounts, %d user keys",
            len(self._accounts),
            sum(len(info.users) for info in self._accounts.values()),
        )

    def _resolve_namespace_policy(
        self,
        settings_data: Optional[dict],
        *,
        allow_legacy_inference: bool,
    ) -> tuple[AccountNamespacePolicy, bool, bool]:
        """Resolve persisted namespace policy, with one-time inference for legacy accounts."""
        namespace_data = settings_data.get("namespace") if isinstance(settings_data, dict) else None
        if isinstance(namespace_data, dict):
            return AccountNamespacePolicy.from_dict(namespace_data), False, False

        if allow_legacy_inference:
            return self._infer_legacy_namespace_policy(), True, True
        return AccountNamespacePolicy(), True, False

    def _infer_legacy_namespace_policy(self) -> AccountNamespacePolicy:
        """Map pre-policy accounts to the compatibility default namespace policy."""
        return AccountNamespacePolicy(
            isolate_user_scope_by_agent=False,
            isolate_agent_scope_by_user=False,
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
                        namespace_policy=self.get_account_policy(entry.account_id),
                    )
            else:
                # Verify plaintext key
                if hmac.compare_digest(api_key, entry.key_or_hash):
                    return ResolvedIdentity(
                        role=entry.role,
                        account_id=entry.account_id,
                        user_id=entry.user_id,
                        namespace_policy=self.get_account_policy(entry.account_id),
                    )

        raise UnauthenticatedError("Invalid API Key")

    async def create_account(
        self,
        account_id: str,
        admin_user_id: str,
        *,
        namespace_policy: Optional[AccountNamespacePolicy] = None,
    ) -> str:
        """Create a new account (workspace) with its first admin user.

        Returns the admin user's API key.
        """
        if account_id in self._accounts:
            raise AlreadyExistsError(account_id, "account")

        now = datetime.now(timezone.utc).isoformat()
        key = self._generate_api_key()
        policy = namespace_policy or AccountNamespacePolicy()

        if self._encryption_enabled:
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
        if self._encryption_enabled:
            user_info["key_prefix"] = key_prefix

        self._accounts[account_id] = AccountInfo(
            created_at=now,
            users={admin_user_id: user_info},
            namespace_policy=policy,
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

        await self._save_accounts_json()
        await self._save_users_json(account_id)
        await self._save_settings_json(account_id)
        return key

    async def delete_account(self, account_id: str) -> None:
        """Delete an account and remove all its user keys from the index.

        Note: AGFS data and VectorDB cleanup is the caller's responsibility.
        """
        if account_id not in self._accounts:
            raise NotFoundError(account_id, "account")

        account = self._accounts.pop(account_id)
        # Remove all keys for this account from prefix index
        for user_info in account.users.values():
            key_or_hash = user_info.get("key", "")
            if key_or_hash:
                # Get key_prefix - if not in user_info, compute from key
                key_prefix = user_info.get("key_prefix", "")
                if not key_prefix:
                    key_prefix = self._get_key_prefix(key_or_hash)

                if key_prefix in self._prefix_index:
                    self._prefix_index[key_prefix] = [
                        entry
                        for entry in self._prefix_index[key_prefix]
                        if entry.account_id != account_id
                    ]
                    # Remove prefix if index is empty
                    if not self._prefix_index[key_prefix]:
                        del self._prefix_index[key_prefix]

        await self._save_accounts_json()

    async def register_user(self, account_id: str, user_id: str, role: str = "user") -> str:
        """Register a new user in an account. Returns the user's API key."""
        account = self._accounts.get(account_id)
        if account is None:
            raise NotFoundError(account_id, "account")
        if user_id in account.users:
            raise AlreadyExistsError(user_id, "user")

        key = self._generate_api_key()

        if self._encryption_enabled:
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
        if self._encryption_enabled:
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

        if self._encryption_enabled:
            new_stored_key = self._hash_api_key(new_key)
            new_is_hashed = True
            new_key_prefix = self._get_key_prefix(new_key)
        else:
            new_stored_key = new_key
            new_is_hashed = False
            new_key_prefix = self._get_key_prefix(new_key)

        # Update user info
        account.users[user_id]["key"] = new_stored_key
        if self._encryption_enabled:
            account.users[user_id]["key_prefix"] = new_key_prefix
        else:
            # Remove key_prefix if encryption is disabled
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
                    **info.namespace_policy.to_dict(),
                }
            )
        return result

    def get_account_policy(self, account_id: Optional[str]) -> AccountNamespacePolicy:
        if not account_id:
            return AccountNamespacePolicy()
        account = self._accounts.get(account_id)
        if account is None:
            return AccountNamespacePolicy()
        return account.namespace_policy

    def get_users(self, account_id: str) -> list:
        """List all users in an account."""
        account = self._accounts.get(account_id)
        if account is None:
            raise NotFoundError(account_id, "account")

        result = []
        for user_id, user_info in account.users.items():
            result.append(
                {
                    "user_id": user_id,
                    "role": user_info.get("role", "user"),
                }
            )
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

    # ---- internal helpers ----

    def _generate_api_key(self) -> str:
        """Generate new API Key."""
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
            # Read file directly using AGFS
            content = self._viking_fs.agfs.read(path)
            if isinstance(content, bytes):
                raw = content
            else:
                raw = content.content if hasattr(content, "content") else b""

            # Decrypt content if encryption is enabled
            # Extract account ID from path
            parts = path.split("/")
            account_id = parts[2] if len(parts) >= 3 else "default"
            raw = await self._viking_fs.decrypt_bytes(account_id, raw)

            text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            return json.loads(text)
        except Exception:
            return None

    async def _write_json(self, path: str, data: dict) -> None:
        """Write a JSON file to AGFS with encryption support."""
        content = json.dumps(data, ensure_ascii=False, indent=2)
        if isinstance(content, str):
            content = content.encode("utf-8")

        # Encrypt content if encryption is enabled
        # Extract account ID from path
        parts = path.split("/")
        account_id = parts[2] if len(parts) >= 3 else "default"
        content = await self._viking_fs.encrypt_bytes(account_id, content)

        # Ensure parent directories exist
        self._ensure_parent_dirs(path)
        # Write file directly using AGFS
        self._viking_fs.agfs.write(path, content)

    def _ensure_parent_dirs(self, path: str) -> None:
        """Recursively create all parent directories for a file path."""
        # Handle direct AGFS paths
        if path.startswith("/local/"):
            # Extract path parts from /local/{account_id}/_system/...
            parts = path.lstrip("/").split("/")
            if parts:
                # Create directory for each parent path
                for i in range(1, len(parts)):
                    parent = "/" + "/".join(parts[:i])
                    try:
                        self._viking_fs.agfs.mkdir(parent)
                    except Exception:
                        pass

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

    async def _save_settings_json(
        self,
        account_id: str,
        *,
        settings_data: Optional[dict] = None,
    ) -> None:
        """Persist account namespace settings."""
        account = self._accounts.get(account_id)
        if account is None:
            return
        path = SETTINGS_PATH_TEMPLATE.format(account_id=account_id)
        merged_settings = dict(settings_data) if isinstance(settings_data, dict) else {}
        merged_settings["namespace"] = account.namespace_policy.to_dict()
        await self._write_json(path, merged_settings)
