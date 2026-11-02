//! Data models for the PathLock subsystem.

use serde::{Deserialize, Serialize};

use super::path::normalize_logical_path;

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
    /// Independent holders. Empty only for a legacy v1 token.
    pub holders: Vec<LockHolder>,
}

/// One independently renewable reference in a logical owner group.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LockHolder {
    /// Unique reference identity, also used as the local lease reference.
    pub id: String,
    /// Coverage requested by this reference.
    pub kind: PathLockKind,
    /// Last confirmed renewal in Unix nanoseconds.
    pub time_ns: u128,
}

impl LockToken {
    /// Construct a v2 token with one reference.
    pub fn held(owner: &str, holder: &str, kind: PathLockKind, time_ns: u128) -> Self {
        Self {
            owner_id: owner.into(),
            time_ns,
            lock_type: kind,
            holders: vec![LockHolder {
                id: holder.into(),
                kind,
                time_ns,
            }],
        }
    }

    /// Legacy tokens are one reference; v2 coverage derives only from live holders.
    pub fn live_kind(&self, stale_before: u128) -> Option<PathLockKind> {
        if self.holders.is_empty() {
            return (self.time_ns >= stale_before).then_some(self.lock_type);
        }
        self.holders
            .iter()
            .filter(|h| h.time_ns >= stale_before)
            .map(|h| h.kind)
            .max_by_key(|k| usize::from(*k == PathLockKind::Tree))
    }

    /// Rebuild the summary and canonical holder order after mutation.
    pub fn recompute(&mut self) {
        self.time_ns = self.holders.iter().map(|h| h.time_ns).max().unwrap_or(0);
        self.lock_type = self
            .holders
            .iter()
            .map(|h| h.kind)
            .max_by_key(|k| usize::from(*k == PathLockKind::Tree))
            .unwrap_or(PathLockKind::Exact);
        self.holders.sort_by(|a, b| a.id.cmp(&b.id));
    }

    /// Add a reference, optionally replacing the handoff source. Legacy ownership
    /// remains protected for its original TTL; old writers must be stopped.
    pub fn add_holder(
        &mut self,
        id: &str,
        kind: PathLockKind,
        now: u128,
        stale_before: u128,
        source: Option<&str>,
    ) {
        if self.holders.is_empty() && self.time_ns >= stale_before {
            self.holders.push(LockHolder {
                id: "legacy".into(),
                kind: self.lock_type,
                time_ns: self.time_ns,
            });
        }
        self.holders
            .retain(|h| h.time_ns >= stale_before && Some(h.id.as_str()) != source);
        self.holders.push(LockHolder {
            id: id.into(),
            kind,
            time_ns: now,
        });
        self.recompute();
    }
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
    /// Storage holder being transferred. Absent in historic payloads.
    #[serde(default)]
    pub holder_id: Option<String>,
    /// Original lease reference, used to locate the same-process registry entry.
    /// Optional only for backward compatibility with historic queue payloads.
    #[serde(default)]
    pub lease_ref: Option<String>,
    /// Provider handle representation; absent in historic queue payloads.
    #[serde(default)]
    pub handle_mode: Option<super::provider::PathLockHandleMode>,
    /// Owner identity (preserved across handoff).
    pub owner_id: String,
    /// Lock-file paths held.
    pub lock_paths: Vec<String>,
    /// Original path coverage granted by the handed-off lease.
    #[serde(default)]
    pub covered_paths: Vec<PathLockRequest>,
}

/// A single lock request for batch acquire.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
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
    let (Ok(held_path), Ok(request_path)) = (
        normalize_logical_path(&held.path),
        normalize_logical_path(&request.path),
    ) else {
        return false;
    };
    match (held.kind, request.kind) {
        (PathLockKind::Exact, PathLockKind::Exact) => held_path == request_path,
        (PathLockKind::Tree, _) => path_is_self_or_descendant(request_path, held_path),
        (PathLockKind::Exact, PathLockKind::Tree) => false,
    }
}

/// Return true when a normalized `path` is equal to or under `ancestor`.
fn path_is_self_or_descendant(path: &str, ancestor: &str) -> bool {
    path == ancestor
        || ancestor == "/"
        || path
            .strip_prefix(ancestor)
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
#[derive(Debug, Clone, Copy, PartialEq, Eq, thiserror::Error)]
pub enum HandoffRecoveryReason {
    /// The destination uses a different handle representation.
    #[error("provider_changed")]
    ProviderChanged,
    /// Historic logical handles do not describe their lock kinds.
    #[error("coverage_missing")]
    CoverageMissing,
    /// The original token no longer grants ownership.
    #[error("token_unavailable")]
    TokenUnavailable,
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

    /// Lock file exists but contains no token data.
    #[error("empty lock token at '{lock_path}'")]
    EmptyToken {
        /// Lock path containing the empty token.
        lock_path: String,
    },

    /// Invalid lock request supplied by a caller.
    #[error("invalid lock request: {0}")]
    InvalidRequest(String),

    /// Handoff/adopt failure.
    #[error("handoff/adopt failed: {0}")]
    HandoffFailed(String),

    /// Durable work must acquire fresh coverage before continuing.
    #[error("handoff recovery required: {reason}")]
    HandoffRecoveryRequired {
        /// Structured reason; unrelated failures must not trigger recovery.
        reason: HandoffRecoveryReason,
    },

    /// Internal error.
    #[error("lock internal error: {0}")]
    Internal(String),
}

/// Result type alias for PathLock operations.
pub type PathLockResult<T> = std::result::Result<T, PathLockError>;
