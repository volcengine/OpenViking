# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Data structures returned by account storage operations."""

from threading import Lock
from time import monotonic
from typing import TypedDict

from openviking.core.global_policy import GlobalPolicyError, validate_global_policy_path
from openviking.core.identifiers import validate_user_id
from openviking.storage.internal_names import is_storage_internal_name
from openviking_cli.exceptions import InvalidArgumentError

MAX_FROZEN_FILES = 10


class EmptyFrozenFileCache:
    """Bounded empty snapshots; expiry starts before the authoritative read."""

    def __init__(self, ttl_seconds: float = 60.0):
        self.ttl_seconds = ttl_seconds
        self._deadlines: dict[str, float] = {}
        self.revision = 0
        self._lock = Lock()

    def contains(self, account_id: str) -> bool:
        return self._deadlines.get(account_id, 0.0) > monotonic()

    def remember(self, account_id: str, started_at: float, revision: int) -> None:
        with self._lock:
            if revision != self.revision:
                return
            if account_id not in self._deadlines and len(self._deadlines) >= 1024:
                self._deadlines.pop(next(iter(self._deadlines)))
            self._deadlines[account_id] = started_at + self.ttl_seconds

    def invalidate(self, account_id: str) -> None:
        with self._lock:
            self.revision += 1
            self._deadlines.pop(account_id, None)

    def clear(self) -> None:
        with self._lock:
            self.revision += 1
            self._deadlines.clear()


def frozen_file_uri(user_id: str, path: str) -> str:
    """Validate one literal user-relative file path without inspecting user data."""
    if error := validate_user_id(user_id):
        raise InvalidArgumentError(error)
    try:
        validate_global_policy_path(path)
        if any(len(part.encode("utf-8")) > 255 for part in path.split("/")):
            raise InvalidArgumentError("Frozen file path components must not exceed 255 bytes")
        if any(is_storage_internal_name(part.casefold()) for part in path.split("/")):
            raise InvalidArgumentError("Frozen files cannot target storage-internal paths")
    except GlobalPolicyError as exc:
        raise InvalidArgumentError(str(exc)) from exc
    return f"viking://user/{user_id}/{path}"


def validate_frozen_files(value: object) -> tuple[str, ...]:
    """Reject corrupt authorization metadata instead of treating it as empty."""
    if not isinstance(value, (list, tuple)) or len(value) > MAX_FROZEN_FILES:
        raise InvalidArgumentError(
            f"Account frozen files must contain at most {MAX_FROZEN_FILES} paths"
        )
    for uri in value:
        if not isinstance(uri, str) or not uri.startswith("viking://user/"):
            raise InvalidArgumentError("Account frozen files require canonical user file URIs")
        user_id, separator, path = uri[len("viking://user/") :].partition("/")
        if not separator or frozen_file_uri(user_id, path) != uri:
            raise InvalidArgumentError("Account frozen files require canonical user file URIs")
    return tuple(sorted(set(value)))


def update_frozen_files(value: object, uri: str, frozen: bool) -> tuple[str, ...]:
    """Apply an idempotent change to a persisted snapshot under its storage guard."""
    paths = set(validate_frozen_files(value))
    if frozen:
        paths.add(uri)
    else:
        paths.discard(uri)
    return validate_frozen_files(tuple(paths))


class DeletionRecord(TypedDict, total=False):
    task_id: str
    owner_account_id: str
    owner_user_id: str


class AccountSummary(TypedDict, total=False):
    account_id: str
    created_at: str
    user_count: int
    status: str
    task_id: str


class UserSummary(TypedDict, total=False):
    user_id: str
    role: str
    api_key: str
    key_prefix: str


class UsersPage(TypedDict):
    users: list[UserSummary]
    total: int
    account_total: int
    manager_count: int
    key_count: int


class GroupSummary(TypedDict):
    group_id: str
    member_count: int


class AuthSnapshot(TypedDict):
    """Verified account identity and authorization state from one storage read."""

    account_id: str
    user_id: str
    role: str
    group_ids: tuple[str, ...]
