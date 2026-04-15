# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Storage-layer exceptions."""


class VikingDBException(Exception):
    """Base exception for vector-store operations."""


class StorageException(VikingDBException):
    """Legacy alias for VikingDBException for backward compatibility."""


class CollectionNotFoundError(StorageException):
    """Raised when a collection does not exist."""


class RecordNotFoundError(StorageException):
    """Raised when a record does not exist."""


class DuplicateKeyError(StorageException):
    """Raised when trying to insert a duplicate key."""


class ConnectionError(StorageException):
    """Raised when storage connection fails."""


class SchemaError(StorageException):
    """Raised when schema validation fails."""


class LockError(VikingDBException):
    """Raised when a lock operation fails."""


class LockAcquisitionError(LockError):
    """Raised when lock acquisition fails."""


class ResourceBusyError(LockError):
    """Raised when a resource is locked by an ongoing operation (e.g. semantic processing)."""
