//! PathLock subsystem — native file-system-level locking protocol.
//!
//! This module provides exact-path and tree-path locks backed by lock files
//! (`.path.ovlock` / `.exact.ovlock.*`) on the underlying filesystem.
//!
//! # Architecture
//!
//! - [`PathLockManager`] is the single source of truth for lock semantics.
//! - [`PathLockProvider`] is the storage abstraction (memory or filesystem).
//! - [`PathLockWrappedFS`] is a convenience wrapper that auto-acquires locks
//!   for simple file operations.
//! - [`LockPathResolver`] computes lock-file paths.
//! - [`LockTokenCodec`] encodes/decodes lock tokens.

pub mod codec;
pub mod manager;
pub mod metrics;
pub mod provider;
pub mod resolver;
pub mod types;
pub mod wrapper;

pub use codec::LockTokenCodec;
pub use manager::{AutoPathLockAction, PathLockConfig, PathLockManager};
pub use metrics::LockMetrics;
pub use provider::{FilesystemPathLockProvider, MemoryPathLockProvider, PathLockProvider};
pub use resolver::LockPathResolver;
pub use types::{
    BorrowedPathLockLease, LockToken, OwnedPathLockLease, PathLockConflict, PathLockHandoffRef,
    PathLockKind, PathLockLease, PathLockObserveSnapshot, PathLockRequest, PathLockResult,
};
pub use wrapper::PathLockWrappedFS;
