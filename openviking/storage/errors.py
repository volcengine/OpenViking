# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Storage-layer exceptions."""


class VikingDBException(Exception):
    """Base exception for vector-store operations."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        error_type: str | None = None,
        retryable: bool = False,
        action: str | None = None,
    ) -> None:
        super().__init__(message)
        # 私有化下游需要结构化判定重试边界；默认值保持其他存储后端原有异常语义。
        self.status_code = status_code
        self.code = code
        self.error_type = error_type
        self.retryable = retryable
        self.action = action


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


class EmbeddingConfigurationError(StorageException):
    """Raised when embedding provider setup is invalid or unavailable."""


class EmbeddingRebuildRequiredError(StorageException):
    """Raised when existing vector data is incompatible with current embedding config."""


class LockError(VikingDBException):
    """Raised when a lock operation fails."""


class LockAcquisitionError(LockError):
    """Raised when lock acquisition fails."""


class SemanticSourceMissingError(FileNotFoundError, VikingDBException):
    """Raised when the semantic source tree is removed before it can be synced.

    A per-message data condition (e.g. a temp staging tree cleaned up after a
    crash), not a model API failure; the message is dropped without tripping
    the API circuit breaker. Subclasses ``FileNotFoundError`` so existing
    callers that treat a vanished source as a missing-file error keep working.
    """

    def __init__(self, message: str) -> None:
        # OSError.__init__ would shadow VikingDBException.__init__ in the MRO;
        # call it explicitly so structured retry-boundary attributes are set.
        VikingDBException.__init__(self, message)


class ResourceBusyError(LockError):
    """Raised when a resource is locked by an ongoing operation."""

    def __init__(
        self,
        message: str,
        *,
        uri: str | None = None,
        conflict_type: str = "path_busy",
        retryable: bool = True,
    ):
        super().__init__(message)
        self.uri = uri
        self.conflict_type = conflict_type
        self.retryable = retryable
