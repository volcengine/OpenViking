//! RAGFS - Rust implementation of AGFS (Aggregated File System)
//!
//! RAGFS provides a unified filesystem abstraction that allows multiple
//! filesystem implementations (plugins) to be mounted at different paths.
//! It is consumed in-process through the Rust binding (`ragfs-python`).
//!
//! # Architecture
//!
//! - **Core**: Fundamental traits and types (FileSystem, ServicePlugin, etc.)
//! - **Plugins**: Filesystem implementations (MemFS, KVFS, QueueFS, etc.)
//!
//! # Example
//!
//! ```rust,no_run
//! use ragfs::core::{PluginRegistry, FileSystem};
//!
//! #[tokio::main]
//! async fn main() -> ragfs::core::Result<()> {
//!     // Create a plugin registry
//!     let mut registry = PluginRegistry::new();
//!
//!     // Register plugins
//!     // registry.register(MemFSPlugin);
//!
//!     Ok(())
//! }
//! ```

#![warn(missing_docs)]
#![warn(clippy::all)]

pub mod core;
pub mod crypto;
pub mod plugins;

// Re-export core types for convenience
pub use core::{
    ConfigParameter, ConfigValue, Error, FileInfo, FileSystem, FilesystemStats, FsOperation,
    HealthStatus, MountableFS, OperationStats, OperationTimer, PluginConfig, PluginRegistry,
    Result, ServicePlugin, StatsCollector, StatsWrappedFS, WriteFlag,
};

/// Version of RAGFS
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

/// Name of the package
pub const NAME: &str = env!("CARGO_PKG_NAME");

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_version() {
        assert!(!VERSION.is_empty());
        assert_eq!(NAME, "ragfs");
    }
}
