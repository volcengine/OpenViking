# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""API Key management for OpenViking multi-tenant HTTP Server."""

import hmac
import json
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from openviking.server.identity import (
    DEFAULT_PERMISSION_PROFILE_ID,
    AccountNamespacePolicy,
    EffectivePermissions,
    NO_DATA_ACCESS_PERMISSION_PROFILE_ID,
    PermissionProfile,
    ResolvedIdentity,
    Role,
    get_builtin_permission_profiles,
)
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import (
    AlreadyExistsError,
    FailedPreconditionError,
    InvalidArgumentError,
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
    permission_profiles: Dict[str, PermissionProfile] = field(default_factory=dict)


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
            permission_profiles = self._resolve_permission_profiles(settings_data)

            self._accounts[account_id] = AccountInfo(
                created_at=info.get("created_at", ""),
                users=users,
                namespace_policy=namespace_policy,
                permission_profiles=permission_profiles,
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

    def _resolve_permission_profiles(self, settings_data: Optional[dict]) -> Dict[str, PermissionProfile]:
        """Load custom account-level permission profiles from persisted settings."""
        acl_data = settings_data.get("acl") if isinstance(settings_data, dict) else None
        raw_profiles = acl_data.get("permission_profiles") if isinstance(acl_data, dict) else None
        if not isinstance(raw_profiles, dict):
            return {}

        profiles: Dict[str, PermissionProfile] = {}
        builtin_ids = set(get_builtin_permission_profiles())
        for profile_id, profile_data in raw_profiles.items():
            if not isinstance(profile_id, str) or not profile_id:
                continue
            if profile_id in builtin_ids:
                logger.warning(
                    "Ignoring custom permission profile %s because it collides with a built-in id",
                    profile_id,
                )
                continue
            profiles[profile_id] = PermissionProfile.from_dict(profile_id, profile_data)
        return profiles

    def resolve(self, api_key: str) -> ResolvedIdentity:
        """Resolve an API key to identity. Sequential matching: root key first, then user key index."""
        if not api_key:
            raise UnauthenticatedError("Missing API Key")

        if hmac.compare_digest(api_key, self._root_key):
            return ResolvedIdentity(
                role=Role.ROOT,
                effective_permissions=EffectivePermissions.full_access(),
            )

        # Use prefix index to quickly locate candidate keys
        key_prefix = self._get_key_prefix(api_key)
        candidates = self._prefix_index.get(key_prefix, [])

        for entry in candidates:
            if entry.is_hashed:
                # Verify hashed key
                if self._verify_api_key(api_key, entry.key_or_hash):
                    permission_profile, permissions = self._resolve_effective_permissions(
                        entry.role,
                        entry.account_id,
                        entry.user_id,
                    )
                    return ResolvedIdentity(
                        role=entry.role,
                        account_id=entry.account_id,
                        user_id=entry.user_id,
                        namespace_policy=self.get_account_policy(entry.account_id),
                        permission_profile=permission_profile,
                        effective_permissions=permissions,
                    )
            else:
                # Verify plaintext key
                if hmac.compare_digest(api_key, entry.key_or_hash):
                    permission_profile, permissions = self._resolve_effective_permissions(
                        entry.role,
                        entry.account_id,
                        entry.user_id,
                    )
                    return ResolvedIdentity(
                        role=entry.role,
                        account_id=entry.account_id,
                        user_id=entry.user_id,
                        namespace_policy=self.get_account_policy(entry.account_id),
                        permission_profile=permission_profile,
                        effective_permissions=permissions,
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
            "permission_profile": DEFAULT_PERMISSION_PROFILE_ID,
        }
        if self._encryption_enabled:
            user_info["key_prefix"] = key_prefix

        self._accounts[account_id] = AccountInfo(
            created_at=now,
            users={admin_user_id: user_info},
            namespace_policy=policy,
            permission_profiles={},
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

    async def register_user(
        self,
        account_id: str,
        user_id: str,
        role: str = "user",
        permission_profile: str = DEFAULT_PERMISSION_PROFILE_ID,
    ) -> str:
        """Register a new user in an account. Returns the user's API key."""
        account = self._accounts.get(account_id)
        if account is None:
            raise NotFoundError(account_id, "account")
        if user_id in account.users:
            raise AlreadyExistsError(user_id, "user")
        self.get_permission_profile(account_id, permission_profile)

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
            "permission_profile": permission_profile,
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
                    "custom_permission_profile_count": len(info.permission_profiles),
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
                    "permission_profile": user_info.get(
                        "permission_profile",
                        DEFAULT_PERMISSION_PROFILE_ID,
                    ),
                }
            )
        return result

    def get_permission_profiles(self, account_id: str) -> list[dict]:
        """List built-in and account custom permission profiles."""
        account = self._accounts.get(account_id)
        if account is None:
            raise NotFoundError(account_id, "account")

        profiles = list(get_builtin_permission_profiles().values()) + list(
            account.permission_profiles.values()
        )
        profiles.sort(key=lambda profile: (not profile.builtin, profile.profile_id))
        return [profile.to_dict() for profile in profiles]

    def get_permission_profile(self, account_id: str, profile_id: str) -> PermissionProfile:
        """Resolve a built-in or account custom permission profile."""
        if profile_id in get_builtin_permission_profiles():
            return get_builtin_permission_profiles()[profile_id]

        account = self._accounts.get(account_id)
        if account is None:
            raise NotFoundError(account_id, "account")

        profile = account.permission_profiles.get(profile_id)
        if profile is None:
            raise NotFoundError(profile_id, "permission profile")
        return profile

    async def upsert_permission_profile(
        self,
        account_id: str,
        profile_id: str,
        *,
        permissions: EffectivePermissions,
    ) -> PermissionProfile:
        """Create or update a custom account permission profile."""
        account = self._accounts.get(account_id)
        if account is None:
            raise NotFoundError(account_id, "account")

        normalized_profile_id = self._normalize_custom_permission_profile_id(profile_id)
        profile = PermissionProfile(
            profile_id=normalized_profile_id,
            permissions=permissions,
            builtin=False,
        )
        account.permission_profiles[normalized_profile_id] = profile
        await self._save_settings_json(account_id)
        return profile

    async def delete_permission_profile(self, account_id: str, profile_id: str) -> None:
        """Delete a custom permission profile after verifying no user still references it."""
        account = self._accounts.get(account_id)
        if account is None:
            raise NotFoundError(account_id, "account")

        normalized_profile_id = self._normalize_custom_permission_profile_id(profile_id)
        if normalized_profile_id not in account.permission_profiles:
            raise NotFoundError(normalized_profile_id, "permission profile")

        in_use_by = [
            user_id
            for user_id, user_info in account.users.items()
            if user_info.get("permission_profile", DEFAULT_PERMISSION_PROFILE_ID)
            == normalized_profile_id
        ]
        if in_use_by:
            raise FailedPreconditionError(
                f"Permission profile is still assigned to users: {', '.join(sorted(in_use_by))}",
                details={
                    "profile_id": normalized_profile_id,
                    "user_ids": sorted(in_use_by),
                },
            )

        del account.permission_profiles[normalized_profile_id]
        await self._save_settings_json(account_id)

    async def set_user_permission_profile(
        self,
        account_id: str,
        user_id: str,
        profile_id: str,
    ) -> None:
        """Assign a permission profile to a user."""
        account = self._accounts.get(account_id)
        if account is None:
            raise NotFoundError(account_id, "account")
        if user_id not in account.users:
            raise NotFoundError(user_id, "user")

        self.get_permission_profile(account_id, profile_id)
        account.users[user_id]["permission_profile"] = profile_id
        await self._save_users_json(account_id)

    def has_user(self, account_id: str, user_id: str) -> bool:
        """Return True when the account registry contains the given user."""
        account = self._accounts.get(account_id)
        if account is None:
            return False
        return user_id in account.users

    def _resolve_effective_permissions(
        self,
        role: Role,
        account_id: str,
        user_id: str,
    ) -> tuple[Optional[str], EffectivePermissions]:
        """Resolve effective permissions for the authenticated identity."""
        if role in {Role.ROOT, Role.ADMIN}:
            account = self._accounts.get(account_id)
            user_info = account.users.get(user_id, {}) if account is not None else {}
            return user_info.get("permission_profile"), EffectivePermissions.full_access()
        return self._resolve_user_permissions(account_id, user_id)

    def _resolve_user_permissions(
        self,
        account_id: str,
        user_id: str,
    ) -> tuple[Optional[str], EffectivePermissions]:
        """Resolve the stored profile assignment into effective permissions."""
        account = self._accounts.get(account_id)
        if account is None:
            return None, EffectivePermissions.no_access()

        user_info = account.users.get(user_id, {})
        profile_id = user_info.get("permission_profile") or DEFAULT_PERMISSION_PROFILE_ID
        try:
            profile = self.get_permission_profile(account_id, profile_id)
        except NotFoundError:
            logger.warning(
                "User %s in account %s references missing permission profile %s; "
                "falling back to no_data_access",
                user_id,
                account_id,
                profile_id,
            )
            fallback = self.get_permission_profile(account_id, NO_DATA_ACCESS_PERMISSION_PROFILE_ID)
            return profile_id, fallback.permissions
        return profile_id, profile.permissions

    def _normalize_custom_permission_profile_id(self, profile_id: str) -> str:
        """Validate custom profile ids and block collisions with built-ins."""
        normalized = profile_id.strip() if isinstance(profile_id, str) else ""
        if not normalized:
            raise InvalidArgumentError("permission profile id must be a non-empty string.")
        if normalized in get_builtin_permission_profiles():
            raise InvalidArgumentError(
                f"permission profile id '{normalized}' is reserved by a built-in profile."
            )
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", normalized):
            raise InvalidArgumentError(
                "permission profile id must match [a-z][a-z0-9_-]{0,63}."
            )
        return normalized

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
        existing_acl = merged_settings.get("acl")
        acl_settings = dict(existing_acl) if isinstance(existing_acl, dict) else {}
        acl_settings["permission_profiles"] = {
            profile_id: profile.permissions.to_dict()
            for profile_id, profile in sorted(account.permission_profiles.items())
        }
        merged_settings["acl"] = acl_settings
        await self._write_json(path, merged_settings)
