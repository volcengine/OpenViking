//! Core module for RAGFS
//!
//! This module contains the fundamental abstractions and types used throughout RAGFS:
//! - Error types and Result alias
//! - FileSystem trait for filesystem implementations
//! - ServicePlugin trait for plugin system
//! - MountableFS for routing operations to mounted plugins
//! - Core data types (FileInfo, ConfigParameter, etc.)

pub mod builder;
pub mod context;
pub mod encryption_wrapper;
pub mod errors;
pub mod filesystem;
pub mod mountable;
pub mod plugin;
pub mod stats;
pub mod stats_wrapper;
pub mod types;

// Re-export commonly used types
pub use builder::{
    build_default_stack, register_builtin_plugins, EncryptionConfig, RagfsConfig, RagfsStack,
};
pub use context::{FsContext, FsContextInner, FsContextView, FS_CTX};
pub use encryption_wrapper::EncryptionWrappedFS;
pub use errors::{Error, Result};
pub use filesystem::FileSystem;
pub use mountable::MountableFS;
pub use plugin::{HealthStatus, PluginRegistry, ServicePlugin};
pub use stats::{FilesystemStats, FsOperation, OperationStats, OperationTimer, StatsCollector};
pub use stats_wrapper::StatsWrappedFS;
pub use types::{
    ConfigParameter, ConfigValue, FileInfo, GrepMatch, GrepResult, PluginConfig, TreeEntry,
    WriteFlag,
};
