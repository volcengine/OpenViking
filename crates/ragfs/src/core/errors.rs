//! Error types for RAGFS
//!
//! This module defines all error types used throughout the RAGFS system.
//! We use `thiserror` for structured error definitions to ensure type safety
//! and clear error messages.

use std::io;
use serde_json;

/// Result type alias for RAGFS operations
pub type Result<T> = std::result::Result<T, Error>;

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
