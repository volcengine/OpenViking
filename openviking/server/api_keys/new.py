# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""New format API Key implementation with directly decodable identity.

This implementation delegates legacy format support to LegacyAPIKeyManager
to avoid code duplication, while adding new format key generation.
"""

import base64
import hmac
import secrets
from typing import Optional, Tuple

from openviking.server.api_keys.legacy import (
    LegacyAPIKeyManager,
)
from openviking.server.api_keys.models import AccountInfo, UserKeyEntry
from openviking.server.identity import AccountNamespacePolicy, ResolvedIdentity, Role
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import InvalidArgumentError, UnauthenticatedError
from openviking_cli.session.user_id import validate_account_id, validate_user_id
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


def _encode_segment(data: str) -> str:
    """Encode a string segment using URL-safe base64 without padding."""
    encoded = base64.urlsafe_b64encode(data.encode("utf-8"))
    return encoded.decode("utf-8").rstrip("=")


def _decode_segment(encoded: str) -> str:
    """Decode a URL-safe base64 segment, adding padding if needed."""
    padding_needed = 4 - (len(encoded) % 4)
    if padding_needed != 4:
        encoded += "=" * padding_needed
    decoded = base64.urlsafe_b64decode(encoded.encode("utf-8"))
    return decoded.decode("utf-8")


def is_new_format_key(api_key: str) -> bool:
    """Check if the API key is in the new format (three base64 segments separated by dots)."""
    if not api_key:
        return False
    parts = api_key.split(".")
    return len(parts) == 3


def parse_api_key(api_key: str) -> Tuple[str, str, str]:
    """Parse a new format API key into (account_id, user_id, secret)."""
    if not is_new_format_key(api_key):
        raise ValueError("Not a new format API key")
    parts = api_key.split(".")
    account_id = _decode_segment(parts[0])
    user_id = _decode_segment(parts[1])
    secret = _decode_segment(parts[2])
    return account_id, user_id, secret


def generate_api_key(account_id: str, user_id: str) -> str:
    """Generate a new format API key."""
    account_segment = _encode_segment(account_id)
    user_segment = _encode_segment(user_id)
    # Generate a 32-byte random secret and encode it
    secret = secrets.token_hex(32)
    secret_segment = _encode_segment(secret)
    return f"{account_segment}.{user_segment}.{secret_segment}"


class NewAPIKeyManager:
    """Manages API keys for multi-tenant authentication with new key format.

    New key format: base64url(account_id).base64url(user_id).base64url(secret)
    - Can directly decode account_id and user_id without prefix lookup
    - Still uses Argon2id for secure secret verification
    - Delegates legacy functionality to LegacyAPIKeyManager to avoid code duplication
    """

    def __init__(
        self,
        root_key: str,
        viking_fs: VikingFS,
        encryption_enabled: bool = False,
    ):
        """Initialize NewAPIKeyManager.

        Args:
            root_key: Global root API key for administrative access.
            viking_fs: VikingFS client for persistent storage of user keys.
            encryption_enabled: Whether API key hashing is enabled.
        """
        # Delegate to legacy manager for all core functionality
        self._legacy = LegacyAPIKeyManager(root_key, viking_fs, encryption_enabled)

    async def load(self) -> None:
        """Load accounts and user keys from VikingFS into memory."""
        await self._legacy.load()
        logger.info(
            "NewAPIKeyManager loaded: %d accounts, %d user keys",
            len(self._legacy.get_accounts()),
            sum(len(info.users) for info in self._legacy._accounts.values()),
        )

    def resolve(self, api_key: str) -> ResolvedIdentity:
        """Resolve an API key to identity.

        First checks for root key.
        Then checks if it's a new format key (fast decode path).
        Then falls back to legacy prefix index lookup.
        """
        if not api_key:
            raise UnauthenticatedError("Missing API Key")

        # Check root key first - use legacy's root key
        if hmac.compare_digest(api_key, self._legacy._root_key):
            return ResolvedIdentity(role=Role.ROOT)

        # Fast path for new format keys - decode identity directly from key
        if is_new_format_key(api_key):
            try:
                account_id, user_id, _ = parse_api_key(api_key)

                # Verify the user exists in the account using legacy's data
                account = self._legacy._accounts.get(account_id)
                if account and user_id in account.users:
                    user_info = account.users[user_id]
                    stored_key_or_hash = user_info.get("key", "")

                    # Verify the secret part matches using legacy's verify method
                    if stored_key_or_hash:
                        if user_info.get("key_prefix", "").startswith(
                            "$argon2"
                        ) or stored_key_or_hash.startswith("$argon2"):
                            # Hashed key
                            if self._legacy._verify_api_key(api_key, stored_key_or_hash):
                                return ResolvedIdentity(
                                    role=Role(user_info.get("role", "user")),
                                    account_id=account_id,
                                    user_id=user_id,
                                    namespace_policy=self.get_account_policy(account_id),
                                )
                        else:
                            # Plaintext key
                            if hmac.compare_digest(api_key, stored_key_or_hash):
                                return ResolvedIdentity(
                                    role=Role(user_info.get("role", "user")),
                                    account_id=account_id,
                                    user_id=user_id,
                                    namespace_policy=self.get_account_policy(account_id),
                                )
            except Exception:
                # If parsing fails or verification fails, fall through to try legacy path
                pass

        # Fall back to legacy resolver for legacy keys
        return self._legacy.resolve(api_key)

    async def create_account(
        self,
        account_id: str,
        admin_user_id: str,
        *,
        namespace_policy: Optional[AccountNamespacePolicy] = None,
    ) -> str:
        """Create a new account (workspace) with its first admin user.

        Returns the admin user's API key in new format.
        """
        # Validate account_id and user_id format
        verr = validate_account_id(account_id)
        if verr:
            raise InvalidArgumentError(verr)
        verr = validate_user_id(admin_user_id)
        if verr:
            raise InvalidArgumentError(verr)

        # Generate new format key
        key = generate_api_key(account_id, admin_user_id)
        policy = namespace_policy or AccountNamespacePolicy()

        # Use legacy to create account but with our new key
        # We temporarily replace the generate method, call, then restore
        # Instead, we implement the create_account with new key format

        # Check existence first
        if account_id in self._legacy._accounts:
            from openviking_cli.exceptions import AlreadyExistsError

            raise AlreadyExistsError(account_id, "account")

        # Copy the legacy implementation but use new key format
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()

        if self._legacy._encryption_enabled:
            stored_key = self._legacy._hash_api_key(key)
            is_hashed = True
            key_prefix = self._legacy._get_key_prefix(key)
        else:
            stored_key = key
            is_hashed = False
            key_prefix = self._legacy._get_key_prefix(key)

        user_info = {
            "role": "admin",
            "key": stored_key,
        }
        if self._legacy._encryption_enabled:
            user_info["key_prefix"] = key_prefix

        # Add to legacy's data structures
        self._legacy._accounts[account_id] = AccountInfo(
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

        if key_prefix:
            if key_prefix not in self._legacy._prefix_index:
                self._legacy._prefix_index[key_prefix] = []
            self._legacy._prefix_index[key_prefix].append(entry)

        await self._legacy._save_accounts_json()
        await self._legacy._save_users_json(account_id)
        await self._legacy._save_settings_json(account_id)
        return key

    async def delete_account(self, account_id: str) -> None:
        """Delete an account and remove all its user keys from the index."""
        await self._legacy.delete_account(account_id)

    async def register_user(self, account_id: str, user_id: str, role: str = "user") -> str:
        """Register a new user in an account. Returns the user's API key in new format."""
        # Validate user_id format
        verr = validate_user_id(user_id)
        if verr:
            raise InvalidArgumentError(verr)

        # Check account exists first
        account = self._legacy._accounts.get(account_id)
        if account is None:
            from openviking_cli.exceptions import NotFoundError

            raise NotFoundError(account_id, "account")
        if user_id in account.users:
            from openviking_cli.exceptions import AlreadyExistsError

            raise AlreadyExistsError(user_id, "user")

        # Generate new format key
        key = generate_api_key(account_id, user_id)

        if self._legacy._encryption_enabled:
            stored_key = self._legacy._hash_api_key(key)
            is_hashed = True
            key_prefix = self._legacy._get_key_prefix(key)
        else:
            stored_key = key
            is_hashed = False
            key_prefix = self._legacy._get_key_prefix(key)

        user_info = {
            "role": role,
            "key": stored_key,
        }
        if self._legacy._encryption_enabled:
            user_info["key_prefix"] = key_prefix

        account.users[user_id] = user_info

        entry = UserKeyEntry(
            account_id=account_id,
            user_id=user_id,
            role=Role(role),
            key_or_hash=stored_key,
            is_hashed=is_hashed,
        )

        if key_prefix:
            if key_prefix not in self._legacy._prefix_index:
                self._legacy._prefix_index[key_prefix] = []
            self._legacy._prefix_index[key_prefix].append(entry)

        await self._legacy._save_users_json(account_id)
        return key

    async def remove_user(self, account_id: str, user_id: str) -> None:
        """Remove a user from an account."""
        await self._legacy.remove_user(account_id, user_id)

    async def regenerate_key(self, account_id: str, user_id: str) -> str:
        """Regenerate a user's API key. Old key is immediately invalidated.

        Generates new format key regardless of original format.
        """
        # Check account and user exist
        account = self._legacy._accounts.get(account_id)
        if account is None:
            from openviking_cli.exceptions import NotFoundError

            raise NotFoundError(account_id, "account")
        if user_id not in account.users:
            from openviking_cli.exceptions import NotFoundError

            raise NotFoundError(user_id, "user")

        old_user_info = account.users[user_id]
        old_key_or_hash = old_user_info.get("key", "")

        # Get old key_prefix
        old_key_prefix = old_user_info.get("key_prefix", "")
        if not old_key_prefix and old_key_or_hash:
            old_key_prefix = self._legacy._get_key_prefix(old_key_or_hash)

        # Remove old key from prefix index
        if old_key_prefix in self._legacy._prefix_index:
            self._legacy._prefix_index[old_key_prefix] = [
                entry
                for entry in self._legacy._prefix_index[old_key_prefix]
                if not (entry.account_id == account_id and entry.user_id == user_id)
            ]
            if not self._legacy._prefix_index[old_key_prefix]:
                del self._legacy._prefix_index[old_key_prefix]

        # Generate new key in new format
        new_key = generate_api_key(account_id, user_id)

        if self._legacy._encryption_enabled:
            new_stored_key = self._legacy._hash_api_key(new_key)
            new_is_hashed = True
            new_key_prefix = self._legacy._get_key_prefix(new_key)
        else:
            new_stored_key = new_key
            new_is_hashed = False
            new_key_prefix = self._legacy._get_key_prefix(new_key)

        # Update user info
        account.users[user_id]["key"] = new_stored_key
        if self._legacy._encryption_enabled:
            account.users[user_id]["key_prefix"] = new_key_prefix
        else:
            if "key_prefix" in account.users[user_id]:
                del account.users[user_id]["key_prefix"]

        entry = UserKeyEntry(
            account_id=account_id,
            user_id=user_id,
            role=Role(account.users[user_id]["role"]),
            key_or_hash=new_stored_key,
            is_hashed=new_is_hashed,
        )

        if new_key_prefix:
            if new_key_prefix not in self._legacy._prefix_index:
                self._legacy._prefix_index[new_key_prefix] = []
            self._legacy._prefix_index[new_key_prefix].append(entry)

        await self._legacy._save_users_json(account_id)
        return new_key

    async def set_role(self, account_id: str, user_id: str, role: str) -> None:
        """Update a user's role."""
        await self._legacy.set_role(account_id, user_id, role)

    def get_accounts(self) -> list:
        """List all accounts."""
        return self._legacy.get_accounts()

    def get_account_policy(self, account_id: Optional[str]) -> AccountNamespacePolicy:
        return self._legacy.get_account_policy(account_id)

    def get_users(
        self,
        account_id: str,
        limit: int = 100,
        name_filter: str | None = None,
        role_filter: str | None = None,
        expose_key: bool = True,
    ) -> list:
        """List all users in an account."""
        return self._legacy.get_users(
            account_id,
            limit=limit,
            name_filter=name_filter,
            role_filter=role_filter,
            expose_key=expose_key,
        )

    def has_user(self, account_id: str, user_id: str) -> bool:
        """Return True when the account registry contains the given user."""
        return self._legacy.has_user(account_id, user_id)

    def get_user_role(self, account_id: str, user_id: str) -> Role:
        """Return the role of the given user in the given account.

        Returns Role.USER if the account or user doesn't exist.
        """
        return self._legacy.get_user_role(account_id, user_id)

    # ---- Property proxies for backward compatibility with tests ----

    @property
    def _accounts(self):
        return self._legacy._accounts

    @property
    def _prefix_index(self):
        return self._legacy._prefix_index

    @property
    def _root_key(self):
        return self._legacy._root_key

    @property
    def _encryption_enabled(self):
        return self._legacy._encryption_enabled

    # ---- Helper method proxies for backward compatibility ----

    def _get_key_prefix(self, api_key: str) -> str:
        return self._legacy._get_key_prefix(api_key)

    def _hash_api_key(self, api_key: str) -> str:
        return self._legacy._hash_api_key(api_key)

    def _verify_api_key(self, api_key: str, hashed_key: str) -> bool:
        return self._legacy._verify_api_key(api_key, hashed_key)

    async def _read_json(self, path: str) -> Optional[dict]:
        return await self._legacy._read_json(path)

    async def _write_json(self, path: str, data: dict) -> None:
        return await self._legacy._write_json(path, data)

    def _ensure_parent_dirs(self, path: str) -> None:
        return self._legacy._ensure_parent_dirs(path)

    async def _save_accounts_json(self) -> None:
        return await self._legacy._save_accounts_json()

    async def _save_users_json(self, account_id: str) -> None:
        return await self._legacy._save_users_json(account_id)

    async def _save_settings_json(
        self,
        account_id: str,
        *,
        settings_data: Optional[dict] = None,
    ) -> None:
        return await self._legacy._save_settings_json(
            account_id,
            settings_data=settings_data,
        )
