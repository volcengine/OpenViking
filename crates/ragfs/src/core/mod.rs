//! Core module for RAGFS
//!
//! This module contains the fundamental abstractions and types used throughout RAGFS:
//! - Error types and Result alias
//! - FileSystem trait for filesystem implementations
//! - ServicePlugin trait for plugin system
//! - MountableFS for routing operations to mounted plugins
//! - Core data types (FileInfo, ConfigParameter, etc.)

pub mod errors;
pub mod filesystem;
pub mod mountable;
pub mod plugin;
pub mod types;

// Re-export commonly used types
pub use errors::{Error, Result};
pub use filesystem::FileSystem;
pub use mountable::MountableFS;
pub use plugin::{HealthStatus, PluginRegistry, ServicePlugin};
pub use types::{ConfigParameter, ConfigValue, FileInfo, PluginConfig, WriteFlag};
