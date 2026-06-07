//! Multi-backend configuration, factory, metadata, and admin support.

/// Multi-backend administrative APIs.
pub mod admin;
/// Multi-backend config validation and normalization helpers.
pub mod config;
/// Multi-backend runtime assembly from validated config.
pub mod factory;
/// Metadata state management shared by multi-backend runtime paths.
pub mod meta;
/// Shared build-time types for multi-backend assembly.
pub mod types;

pub use meta::{FsContextResolver, MetaStateStore};
