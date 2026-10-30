# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Asynchronous account and API key persistence contract."""

from abc import ABC, abstractmethod

from typing_extensions import deprecated

from openviking.server.account_stores.models import (
    AccountSummary,
    DeletionRecord,
    GroupSummary,
    UsersPage,
    UserSummary,
)


class AccountStore(ABC):
    """Nominal contract implemented by every complete account store provider."""

    @abstractmethod
    async def load(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    # Account
    @abstractmethod
    async def get_account(self, account_id: str) -> AccountSummary | None: ...

    @abstractmethod
    async def create_account(
        self,
        account_id: str,
        admin_user_id: str,
    ) -> None: ...

    @abstractmethod
    async def create_account_with_api_key(
        self,
        account_id: str,
        admin_user_id: str,
        api_key: str,
    ) -> None: ...

    @abstractmethod
    async def delete_account(self, account_id: str) -> None: ...

    @abstractmethod
    async def list_accounts(
        self,
        name_filter: str | None = None,
        limit: int | None = None,
        page: int = 1,
        query_filter: str | None = None,
    ) -> list[AccountSummary]: ...

    # User
    @abstractmethod
    async def get_user(self, account_id: str, user_id: str) -> UserSummary | None: ...

    async def get_user_membership(
        self, account_id: str, user_id: str
    ) -> UserSummary | None:
        """Point-read one user for the admin membership probe.

        Mirrors ``get_account``: stores whose in-memory state can lag a peer
        process (FileStore with the watcher off) override this to refresh from
        the persisted registry first. Query-per-call stores (MySQL) are always
        fresh and keep the default delegation.
        """
        return await self.get_user(account_id, user_id)

    @deprecated(
        "get_user_for_management is a FileStore compatibility path and will be removed.",
        category=None,
    )
    async def get_user_for_management(
        self, account_id: str, user_id: str
    ) -> UserSummary | None:
        """Return one user for an explicit management read."""
        return await self.get_user(account_id, user_id)

    @abstractmethod
    async def create_user(self, account_id: str, user_id: str, role: str) -> None: ...

    @abstractmethod
    async def create_user_with_api_key(
        self,
        account_id: str,
        user_id: str,
        role: str,
        api_key: str,
    ) -> None: ...

    @abstractmethod
    async def list_users_page(
        self,
        account_id: str,
        limit: int | None = 100,
        name_filter: str | None = None,
        role_filter: str | None = None,
        page: int = 1,
        query_filter: str | None = None,
        fresh: bool = False,
    ) -> UsersPage: ...

    @abstractmethod
    async def set_role(self, account_id: str, user_id: str, role: str) -> None: ...

    # Group
    @abstractmethod
    async def create_group(self, account_id: str, group_id: str) -> GroupSummary: ...

    @abstractmethod
    async def get_groups(self, account_id: str) -> list[GroupSummary]: ...

    @abstractmethod
    async def get_group_members(self, account_id: str, group_id: str) -> list[str]: ...

    @abstractmethod
    async def get_user_group_ids(self, account_id: str, user_id: str) -> tuple[str, ...]: ...

    @abstractmethod
    async def add_group_member(self, account_id: str, group_id: str, user_id: str) -> bool: ...

    @abstractmethod
    async def remove_group_member(self, account_id: str, group_id: str, user_id: str) -> bool: ...

    @abstractmethod
    async def begin_group_deletion(self, account_id: str, group_id: str) -> None: ...

    @abstractmethod
    async def finish_group_deletion(self, account_id: str, group_id: str) -> bool: ...

    @abstractmethod
    async def iter_deleting_groups(self) -> list[tuple[str, str]]: ...

    # Provisioning
    @abstractmethod
    async def ensure_trusted_identities(
        self, identities: dict[str, set[str]]
    ) -> dict[str, int]: ...

    # Deletion
    @abstractmethod
    async def begin_deletion(
        self,
        account_id: str,
        user_id: str | None,
        *,
        task_id: str,
        owner_account_id: str,
        owner_user_id: str,
    ) -> tuple[DeletionRecord, bool]: ...

    @abstractmethod
    async def replace_deletion_task(
        self,
        account_id: str,
        user_id: str | None,
        *,
        expected_task_id: str,
        task_id: str,
        owner_account_id: str,
        owner_user_id: str,
    ) -> DeletionRecord: ...

    @abstractmethod
    async def finish_deletion(self, account_id: str, user_id: str | None, task_id: str) -> bool: ...

    @abstractmethod
    async def get_deletion(
        self, account_id: str, user_id: str | None = None
    ) -> DeletionRecord | None: ...

    @abstractmethod
    async def iter_deletions(
        self,
    ) -> list[tuple[str, str | None, DeletionRecord]]: ...

    # Authentication
    @abstractmethod
    async def verify_api_key(
        self,
        api_key: str,
        *,
        account_id_hint: str | None = None,
        user_id_hint: str | None = None,
    ) -> tuple[str, str] | None: ...

    # Credentials
    @abstractmethod
    async def replace_active_user_api_key(
        self,
        account_id: str,
        user_id: str,
        api_key: str,
        *,
        expected_api_key: str | None = None,
    ) -> None:
        """Replace the key, atomically checking the active key when supplied.

        A revoked or superseded expected key must raise UnauthenticatedError.
        """
        ...

    @abstractmethod
    async def revoke_active_api_keys(self, account_id: str, user_id: str | None = None) -> None: ...

    @abstractmethod
    async def get_user_key_fingerprint(self, account_id: str, user_id: str) -> str | None: ...
