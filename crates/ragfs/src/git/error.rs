//! Git module error types

use thiserror::Error;

/// Errors from ObjectStore operations
#[derive(Debug, Error)]
pub enum ObjectStoreError {
    /// Object not found
    #[error("object not found: {0}")]
    NotFound(gix_hash::ObjectId),

    /// I/O error
    #[error("i/o error: {0}")]
    Io(#[from] std::io::Error),

    /// Zlib decompression error
    #[error("zlib error: {0}")]
    Zlib(String),

    /// ObjectId mismatch (content integrity check failed)
    #[error("oid mismatch: expected {expected}, got {actual}")]
    OidMismatch {
        expected: gix_hash::ObjectId,
        actual: gix_hash::ObjectId,
    },

    /// Backend-specific error
    #[error("backend error: {0}")]
    Backend(String),
}

/// Errors from RefStore operations
#[derive(Debug, Error)]
pub enum RefStoreError {
    /// Ref not found
    #[error("ref not found: {0}")]
    NotFound(String),

    /// CAS conflict - expected value didn't match actual
    #[error("cas conflict: expected {expected:?}, actual {actual:?}")]
    Conflict {
        expected: Option<gix_hash::ObjectId>,
        actual: Option<gix_hash::ObjectId>,
    },

    /// Invalid ref name (failed validation)
    #[error("invalid ref name: {0}")]
    InvalidName(String),

    /// I/O error
    #[error("i/o error: {0}")]
    Io(#[from] std::io::Error),

    /// Backend-specific error
    #[error("backend error: {0}")]
    Backend(String),
}

/// Top-level Git service error
#[derive(Debug, Error)]
pub enum GitError {
    /// ObjectStore error
    #[error("object store error: {0}")]
    ObjectStore(#[from] ObjectStoreError),

    /// RefStore error
    #[error("ref store error: {0}")]
    RefStore(#[from] RefStoreError),

    /// Path not found in tree
    #[error("path not found in tree: {0}")]
    PathNotFound(String),

    /// Path exists in tree but resolves to a directory (tree), not a blob.
    /// Returned by `show()` when the caller asked for blob bytes at a path
    /// that turned out to be a subdirectory.
    #[error("path is a directory, not a file: {0}")]
    PathIsDirectory(String),

    /// `project_dir` is an empty / malformed path string.
    /// Same validation as `TreeEditor::upsert`: must be non-empty, no leading
    /// or trailing `/`, no empty components, no `.` / `..` / backslash /
    /// control char components.
    #[error("invalid project_dir: {0}")]
    InvalidProjectDir(String),

    /// A user-supplied relative path in `CommitRequest.paths` or
    /// `ShowRequest.path` failed validation. The Rust GitService is a native
    /// boundary (PyO3 bindings, future SDK callers), so it cannot rely on
    /// upstream HTTP / SDK layers to have already normalized away `..` /
    /// `\` / control chars. Rejected before any VFS or object-store I/O.
    #[error("invalid path: {0}")]
    InvalidPath(String),

    /// The requested `project_dir` does not resolve to a subtree in the
    /// referenced commit's tree (either the path is missing entirely or it
    /// resolves to a blob rather than a tree).
    #[error("project_dir {project_dir:?} not found as a subtree in commit {commit}")]
    SubtreeNotFoundInCommit {
        project_dir: String,
        commit: gix_hash::ObjectId,
    },

    /// Invalid account ID
    #[error("invalid account id: {0}")]
    InvalidAccountId(String),

    /// Concurrent commit conflict
    #[error("concurrent commit: ref {ref_name} changed during commit (expected {expected:?}, actual {actual:?})")]
    ConcurrentCommit {
        ref_name: String,
        expected: Option<gix_hash::ObjectId>,
        actual: Option<gix_hash::ObjectId>,
    },

    /// `restore()` advanced the branch ref to the new commit, but at least
    /// one per-path VFS write or delete failed afterwards. The branch ref
    /// already points at `new_commit_oid`; the caller must use the payload
    /// to drive reindex of the paths that did reach the VFS and report the
    /// failures to whoever needs to retry them.
    #[error(
        "restore writeback partial: {writes_failed} write(s) and {deletes_failed} delete(s) failed after ref advanced to {new_commit}",
        writes_failed = .0.failed_writes.len(),
        deletes_failed = .0.failed_deletes.len(),
        new_commit = .0.new_commit_oid,
    )]
    RestoreWritebackPartial(Box<crate::git::types::RestoreWritebackPartial>),

    /// Blob too large
    #[error("blob too large: {size} bytes exceeds limit {limit} bytes")]
    BlobTooLarge { size: u64, limit: u64 },

    /// Too many files in commit
    #[error("too many files: {count} exceeds limit {limit}")]
    TooManyFiles { count: usize, limit: usize },

    /// Feature not enabled
    #[error("git feature not enabled")]
    FeatureDisabled,

    /// Corrupted object
    #[error("corrupted object: {0}")]
    CorruptedObject(String),

    /// No object matched the abbreviated OID prefix
    #[error("no commit found matching OID prefix {prefix}")]
    OidPrefixNotFound { prefix: String },

    /// Multiple objects matched the abbreviated OID prefix
    #[error("ambiguous OID prefix {prefix} matches {count} commits: {candidates}")]
    AmbiguousOid {
        prefix: String,
        count: usize,
        candidates: String,
    },

    /// Other error
    #[error("{0}")]
    Other(String),

    /// Vfs error wrapper
    #[error("vfs: {0}")]
    Vfs(String),
}

impl From<crate::core::errors::Error> for GitError {
    fn from(e: crate::core::errors::Error) -> Self {
        GitError::Vfs(e.to_string())
    }
}
