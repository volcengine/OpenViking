//! Error types for RAGFS
//!
//! This module defines all error types used throughout the RAGFS system.
//! We use `thiserror` for structured error definitions to ensure type safety
//! and clear error messages.

use serde_json;
use std::io;

/// Result type alias for RAGFS operations
pub type Result<T> = std::result::Result<T, Error>;

/// Structured failure detail for one backup target in synchronous multi-write fanout.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SyncWriteFailureDetail {
    /// Logical backup backend name.
    pub backend: String,
    /// Stable error kind for programmatic inspection.
    pub kind: String,
    /// Human-readable error message including the original error display text.
    pub message: String,
}

/// Main error type for RAGFS operations
#[derive(Debug, thiserror::Error)]
pub enum Error {
    /// File or directory not found
    #[error("not found: {0}")]
    NotFound(String),

    /// File or directory already exists
    #[error("already exists: {0}")]
    AlreadyExists(String),

    /// Permission denied
    #[error("permission denied: {0}")]
    PermissionDenied(String),

    /// Invalid path
    #[error("invalid path: {0}")]
    InvalidPath(String),

    /// Not a directory
    #[error("not a directory: {0}")]
    NotADirectory(String),

    /// Is a directory (when file operation expected)
    #[error("is a directory: {0}")]
    IsADirectory(String),

    /// Directory not empty
    #[error("directory not empty: {0}")]
    DirectoryNotEmpty(String),

    /// Invalid operation
    #[error("invalid operation: {0}")]
    InvalidOperation(String),

    /// I/O error
    #[error("I/O error: {0}")]
    Io(#[from] io::Error),

    /// Plugin error
    #[error("plugin error: {0}")]
    Plugin(String),

    /// Configuration error
    #[error("configuration error: {0}")]
    Config(String),

    /// Mount point not found
    #[error("mount point not found: {0}")]
    MountPointNotFound(String),

    /// Mount point already exists
    #[error("mount point already exists: {0}")]
    MountPointExists(String),

    /// Serialization error
    #[error("serialization error: {0}")]
    Serialization(String),

    /// Network error
    #[error("network error: {0}")]
    Network(String),

    /// Timeout error
    #[error("operation timed out: {0}")]
    Timeout(String),

    /// Multi-write synchronous fanout failed to reach the required acknowledgement quorum
    #[error(
        "sync write quorum failed: {succeeded}/{attempted} backups succeeded (required {required}); failures: {failures:?}"
    )]
    SyncWriteQuorum {
        /// Number of backup targets that completed successfully.
        succeeded: usize,
        /// Minimum number of backup acknowledgements required for success.
        required: usize,
        /// Total number of backup targets that were attempted.
        attempted: usize,
        /// Structured failure details for each unsuccessful backup target.
        failures: Vec<SyncWriteFailureDetail>,
    },

    /// Required filesystem context is missing from the current task
    #[error("filesystem context missing: {0}")]
    ContextMissing(String),

    /// Internal error
    #[error("internal error: {0}")]
    Internal(String),
}

impl From<serde_json::Error> for Error {
    fn from(err: serde_json::Error) -> Self {
        Self::Serialization(err.to_string())
    }
}

impl Error {
    /// Create a NotFound error
    pub fn not_found(path: impl Into<String>) -> Self {
        Self::NotFound(path.into())
    }

    /// Create an AlreadyExists error
    pub fn already_exists(path: impl Into<String>) -> Self {
        Self::AlreadyExists(path.into())
    }

    /// Create a PermissionDenied error
    pub fn permission_denied(path: impl Into<String>) -> Self {
        Self::PermissionDenied(path.into())
    }

    /// Create an InvalidPath error
    pub fn invalid_path(path: impl Into<String>) -> Self {
        Self::InvalidPath(path.into())
    }

    /// Create a Plugin error
    pub fn plugin(msg: impl Into<String>) -> Self {
        Self::Plugin(msg.into())
    }

    /// Create a Config error
    pub fn config(msg: impl Into<String>) -> Self {
        Self::Config(msg.into())
    }

    /// Create an Internal error
    pub fn internal(msg: impl Into<String>) -> Self {
        Self::Internal(msg.into())
    }

    /// Create a ContextMissing error
    pub fn context_missing(msg: impl Into<String>) -> Self {
        Self::ContextMissing(msg.into())
    }

    /// Create a Timeout error
    pub fn timeout(msg: impl Into<String>) -> Self {
        Self::Timeout(msg.into())
    }

    /// Return a stable error kind name for structured reporting.
    pub fn kind_name(&self) -> &'static str {
        match self {
            Self::NotFound(_) => "not_found",
            Self::AlreadyExists(_) => "already_exists",
            Self::PermissionDenied(_) => "permission_denied",
            Self::InvalidPath(_) => "invalid_path",
            Self::NotADirectory(_) => "not_a_directory",
            Self::IsADirectory(_) => "is_a_directory",
            Self::DirectoryNotEmpty(_) => "directory_not_empty",
            Self::InvalidOperation(_) => "invalid_operation",
            Self::Io(_) => "io",
            Self::Plugin(_) => "plugin",
            Self::Config(_) => "config",
            Self::MountPointNotFound(_) => "mount_point_not_found",
            Self::MountPointExists(_) => "mount_point_exists",
            Self::Serialization(_) => "serialization",
            Self::Network(_) => "network",
            Self::Timeout(_) => "timeout",
            Self::SyncWriteQuorum { .. } => "sync_write_quorum",
            Self::ContextMissing(_) => "context_missing",
            Self::Internal(_) => "internal",
        }
    }

    /// Create an InvalidOperation error
    pub fn invalid_operation(msg: impl Into<String>) -> Self {
        Self::InvalidOperation(msg.into())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_error_creation() {
        let err = Error::not_found("/test/path");
        assert!(matches!(err, Error::NotFound(_)));
        assert_eq!(err.to_string(), "not found: /test/path");
    }

    #[test]
    fn test_error_display() {
        let err = Error::permission_denied("/protected");
        assert_eq!(err.to_string(), "permission denied: /protected");
    }
}
