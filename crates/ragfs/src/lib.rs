//! RAGFS - Rust implementation of AGFS (Aggregated File System)
//!
//! RAGFS provides a unified filesystem abstraction that allows multiple
//! filesystem implementations (plugins) to be mounted at different paths.
//! It exposes these filesystems through a REST API, making them accessible
//! to AI agents and other clients.
//!
//! # Architecture
//!
//! - **Core**: Fundamental traits and types (FileSystem, ServicePlugin, etc.)
//! - **Plugins**: Filesystem implementations (MemFS, KVFS, QueueFS, etc.)
//! - **Server**: HTTP API server for remote access
//! - **Shell**: Interactive command-line interface
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
pub mod plugins;
pub mod server;

// Re-export core types for convenience
pub use core::{
    ConfigParameter, ConfigValue, Error, FileInfo, FileSystem, HealthStatus, MountableFS,
    PluginConfig, PluginRegistry, Result, ServicePlugin, WriteFlag,
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
