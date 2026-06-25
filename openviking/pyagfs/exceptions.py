"""Exception classes for pyagfs"""


class AGFSClientError(Exception):
    """Base exception for AGFS client errors"""

    pass


class AGFSConnectionError(AGFSClientError):
    """Connection related errors"""

    pass


class AGFSTimeoutError(AGFSClientError):
    """Timeout errors"""

    pass


class AGFSHTTPError(AGFSClientError):
    """HTTP related errors"""

    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class AGFSNotSupportedError(AGFSClientError):
    """Operation not supported by the server or filesystem (HTTP 501)"""

    pass


class AGFSNotFoundError(AGFSClientError):
    """File or directory not found"""

    pass


class AGFSAlreadyExistsError(AGFSClientError):
    """File or directory already exists"""

    pass


class AGFSFileExistsError(AGFSAlreadyExistsError):
    """File already exists (alias for AGFSAlreadyExistsError)"""

    pass


class AGFSPermissionDeniedError(AGFSClientError):
    """Permission denied"""

    pass


class AGFSInvalidPathError(AGFSClientError):
    """Invalid path"""

    pass


class AGFSNotADirectoryError(AGFSClientError):
    """Not a directory"""

    pass


class AGFSIsADirectoryError(AGFSClientError):
    """Is a directory (when file operation expected)"""

    pass


class AGFSDirectoryNotEmptyError(AGFSClientError):
    """Directory not empty"""

    pass


class AGFSInvalidOperationError(AGFSClientError):
    """Invalid operation"""

    pass


class AGFSIoError(AGFSClientError):
    """I/O error"""

    pass


class AGFSConfigError(AGFSClientError):
    """Configuration error"""

    pass


class AGFSMountPointNotFoundError(AGFSClientError):
    """Mount point not found"""

    pass


class AGFSMountPointExistsError(AGFSClientError):
    """Mount point already exists"""

    pass


class AGFSSerializationError(AGFSClientError):
    """Serialization error"""

    pass


class AGFSNetworkError(AGFSClientError):
    """Network error"""

    pass


class AGFSInternalError(AGFSClientError):
    """Internal error"""

    pass


class AGFSPluginError(AGFSClientError):
    """Plugin error"""

    pass


class GitConcurrentCommitError(AGFSClientError):
    """Raised when a git ref CAS update lost the race against another writer.

    The branch ref moved between the read-parent step and the cas_update step.
    Callers should refresh and retry, or surface the conflict to the user.
    """

    pass


class GitRestoreWritebackPartialError(AGFSClientError):
    """Raised when ``git_restore`` advanced the branch ref to the new commit
    but at least one per-path VFS write or delete failed afterwards.

    The branch ref already points at ``new_commit_oid`` — the operation cannot
    be rolled back. Callers must:

    1. Still trigger reindex for ``written_paths`` / ``deleted_paths`` (those
       did reach the VFS, so the vector index would otherwise stay stale).
    2. Surface ``failed_writes`` / ``failed_deletes`` so the operator can
       retry or repair the affected paths.

    Native code constructs this with ``(message, payload_dict)``; callers may
    also build it from Python with no payload (defaults are zero-counts /
    empty lists). ``task_id`` is filled in by ``VikingFS.restore`` once the
    background reindex has been scheduled.
    """

    def __init__(self, message, payload=None):
        super().__init__(message)
        payload = payload or {}
        self.new_commit_oid = payload.get("new_commit_oid")
        self.source_commit = payload.get("source_commit")
        self.parent_commit = payload.get("parent_commit")
        self.written = payload.get("written", 0)
        self.deleted = payload.get("deleted", 0)
        self.unchanged = payload.get("unchanged", 0)
        self.written_paths = list(payload.get("written_paths") or [])
        self.deleted_paths = list(payload.get("deleted_paths") or [])
        # Each entry is a (path, error-message) pair; tuples in Rust marshal
        # to Python as tuples, but accept lists here too for tolerance.
        self.failed_writes = [tuple(p) for p in (payload.get("failed_writes") or [])]
        self.failed_deletes = [tuple(p) for p in (payload.get("failed_deletes") or [])]
        # Filled by ``VikingFS.restore`` after it schedules the reindex task.
        self.task_id = None

    def to_dict(self):
        return {
            "new_commit_oid": self.new_commit_oid,
            "source_commit": self.source_commit,
            "parent_commit": self.parent_commit,
            "written": self.written,
            "deleted": self.deleted,
            "unchanged": self.unchanged,
            "written_paths": self.written_paths,
            "deleted_paths": self.deleted_paths,
            "failed_writes": [list(p) for p in self.failed_writes],
            "failed_deletes": [list(p) for p in self.failed_deletes],
            "task_id": self.task_id,
        }
