//! Data models for the PathLock subsystem.

use serde::{Deserialize, Serialize};

/// Lock kind: exact (single path) or tree (path + descendants).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum PathLockKind {
    /// Exact-path lock.
    Exact,
    /// Tree lock covering the path and all descendants.
    Tree,
}

impl PathLockKind {
    /// Single-character encoding: `E` for Exact, `T` for Tree.
    pub fn to_code(self) -> char {
        match self {
            PathLockKind::Exact => 'E',
            PathLockKind::Tree => 'T',
        }
    }

    /// Decode from a single character.
    pub fn from_code(c: char) -> Option<Self> {
        match c {
            'E' => Some(PathLockKind::Exact),
            'T' => Some(PathLockKind::Tree),
            _ => None,
        }
    }
}

/// Structured lock token stored in lock files.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LockToken {
    /// Owner identity (UUIDv4-style).
    pub owner_id: String,
    /// Nanosecond timestamp of last write/refresh.
    pub time_ns: u128,
    /// Lock type.
    pub lock_type: PathLockKind,
}

/// Unified lease record (Rust-internal, exposed to Python as an opaque reference).
#[derive(Debug, Clone)]
pub struct PathLockLease {
    /// Opaque lease reference used by callers; not equal to owner_id.
    pub lease_ref: String,
    /// Owner identity.
    pub owner_id: String,
    /// Lock-file paths held by this lease.
    pub lock_paths: Vec<String>,
    /// Original path coverage granted by the lease.
    pub covered_paths: Vec<PathLockRequest>,
}

/// Owned lease — caller controls refresh/release/handoff lifecycle.
#[derive(Debug, Clone)]
pub struct OwnedPathLockLease {
    /// Underlying lease data.
    pub lease: PathLockLease,
    /// Opaque capability required for lifecycle operations.
    pub ownership_ref: String,
}

/// Borrowed lease — read-only proof that a lock is held, no lifecycle control.
#[derive(Debug, Clone)]
pub struct BorrowedPathLockLease {
    /// Underlying lease data.
    pub lease: PathLockLease,
}

/// Serializable handoff payload for cross-queue / cross-worker lock transfer.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PathLockHandoffRef {
    /// Owner identity (preserved across handoff).
    pub owner_id: String,
    /// Lock-file paths held.
    pub lock_paths: Vec<String>,
    /// Original path coverage granted by the handed-off lease.
    #[serde(default)]
    pub covered_paths: Vec<PathLockRequest>,
}

/// A single lock request for batch acquire.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PathLockRequest {
    /// Target path.
    pub path: String,
    /// Lock kind.
    pub kind: PathLockKind,
}

impl PathLockLease {
    /// Return true when this lease covers a requested operation.
    pub fn covers(&self, request: &PathLockRequest) -> bool {
        self.covered_paths
            .iter()
            .any(|held| request_is_covered_by(held, request))
    }
}

/// Return true when a held request covers another requested operation.
fn request_is_covered_by(held: &PathLockRequest, request: &PathLockRequest) -> bool {
    match (held.kind, request.kind) {
        (PathLockKind::Exact, PathLockKind::Exact) => normalize_path(&held.path) == normalize_path(&request.path),
        (PathLockKind::Tree, _) => path_is_self_or_descendant(&request.path, &held.path),
        (PathLockKind::Exact, PathLockKind::Tree) => false,
    }
}

/// Normalize a path for simple lock coverage checks.
fn normalize_path(path: &str) -> String {
    let trimmed = path.trim_end_matches('/');
    if trimmed.is_empty() {
        "/".to_string()
    } else {
        trimmed.to_string()
    }
}

/// Return true when `path` is equal to or under `ancestor`.
fn path_is_self_or_descendant(path: &str, ancestor: &str) -> bool {
    let path = normalize_path(path);
    let ancestor = normalize_path(ancestor);
    path == ancestor
        || ancestor == "/"
        || path
            .strip_prefix(&ancestor)
            .is_some_and(|suffix| suffix.starts_with('/'))
}

/// Snapshot of current lock state for observability.
#[derive(Debug, Clone)]
pub struct PathLockObserveSnapshot {
    /// Number of active (held) locks.
    pub active_locks: usize,
    /// Number of waiting lock requests.
    pub waiting_locks: usize,
    /// Number of stale locks removed.
    pub stale_locks_removed: usize,
    /// Current conflicts.
    pub conflicts: Vec<PathLockConflict>,
}

/// Description of a lock conflict.
#[derive(Debug, Clone)]
pub struct PathLockConflict {
    /// Lock path where conflict occurred.
    pub lock_path: String,
    /// Owner of the conflicting lock.
    pub conflicting_owner: String,
    /// Kind of the conflicting lock.
    pub conflicting_kind: PathLockKind,
}

/// PathLock-specific error type.
#[derive(Debug, thiserror::Error)]
pub enum PathLockError {
    /// Lock conflict with another owner.
    #[error("lock conflict on '{lock_path}': owned by '{owner}'")]
    Conflict {
        /// Lock path where conflict occurred.
        lock_path: String,
        /// Owner of the conflicting lock.
        owner: String,
        /// Kind of the conflicting lock from the original read snapshot.
        kind: PathLockKind,
    },

    /// Timeout waiting for lock acquisition.
    #[error("lock acquire timed out after {elapsed_ms}ms")]
    Timeout {
        /// Elapsed time in milliseconds.
        elapsed_ms: u64,
    },

    /// Lock token mutation is temporarily blocked and should be retried.
    #[error("lock busy at '{lock_path}' during {operation}")]
    Busy {
        /// Lock path whose token mutation is temporarily blocked.
        lock_path: String,
        /// Mutation operation being attempted.
        operation: String,
    },

    /// I/O error during lock operation.
    #[error("lock I/O error: {0}")]
    Io(String),

    /// Invalid lock token format.
    #[error("invalid lock token: {0}")]
    InvalidToken(String),

    /// Invalid lock request supplied by a caller.
    #[error("invalid lock request: {0}")]
    InvalidRequest(String),

    /// Handoff/adopt failure.
    #[error("handoff/adopt failed: {0}")]
    HandoffFailed(String),

    /// Internal error.
    #[error("lock internal error: {0}")]
    Internal(String),
}

/// Result type alias for PathLock operations.
pub type PathLockResult<T> = std::result::Result<T, PathLockError>;
