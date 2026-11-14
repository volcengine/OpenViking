//! PathLock subsystem — native file-system-level locking protocol.
//!
//! This module provides exact-path and tree-path locks backed by lock files,
//! process memory, SQLite, PostgreSQL, or Redis.
//!
//! # Architecture
//!
//! - [`PathLockManager`] is the single source of truth for lock semantics.
//! - [`PathLockProvider`] is the storage abstraction.
//! - [`PathLockWrappedFS`] is a convenience wrapper that auto-acquires locks
//!   for simple file operations.
//! - [`LockPathResolver`] computes lock-file paths.
//! - [`LockTokenCodec`] encodes/decodes lock tokens.

pub mod codec;
#[cfg(feature = "cache")]
mod cache_provder;
pub mod manager;
pub mod metrics;
mod path;
#[cfg(feature = "postgres")]
pub mod postgres_provider;
pub mod provider;
pub mod resolver;
pub mod sqlite_provider;
pub mod types;
pub mod wrapper;

pub use codec::LockTokenCodec;
#[cfg(feature = "cache")]
pub use cache_provder::RedisPathLockProvider;
pub use manager::{AutoPathLockAction, PathLockConfig, PathLockManager};
pub use metrics::LockMetrics;
#[cfg(feature = "postgres")]
pub use postgres_provider::PostgresPathLockProvider;
pub use provider::{
    AcquisitionChange, AtomicAcquisition, AtomicAcquisitionResult, FilesystemPathLockProvider,
    MemoryPathLockProvider, PathLockHandleMode, PathLockProvider,
};
pub use resolver::LockPathResolver;
pub use sqlite_provider::SQLitePathLockProvider;
pub use types::{
    BorrowedPathLockLease, LockToken, OwnedPathLockLease, PathLockConflict, PathLockHandoffRef,
    PathLockKind, PathLockLease, PathLockObserveSnapshot, PathLockRequest, PathLockResult,
};
pub use wrapper::PathLockWrappedFS;
