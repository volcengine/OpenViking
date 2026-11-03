//! Multi-backend configuration, factory, and metadata support.

/// Prefix reconciliation primitives for backup catch-up.
pub mod catch_up;
/// Checkpoint construction, validation, publication, and reading.
pub mod checkpoint;
/// Stable binary codecs for V2 segments and checkpoints.
pub mod codec;
/// Multi-backend config validation and normalization helpers.
pub mod config;
/// Shared constants for V2 multi-write metadata.
pub(crate) mod constants;
/// Multi-backend runtime assembly from validated config.
pub mod factory;
/// Metadata garbage collection.
pub mod gc;
/// Metadata state management shared by multi-backend runtime paths.
pub mod meta;
/// V2 protocol initialization and full-data import.
pub mod migration;
/// V2 multi-write persistence and in-memory models.
pub mod model;
/// V2 multi-write persistence provider interfaces and implementations.
pub mod provider;
/// Logical path mapping and account partition routing.
pub mod router;
/// Foreground event queue and metadata flush worker.
pub mod runtime;
/// Shared build-time types for multi-backend assembly.
pub mod types;

pub use meta::FsContextResolver;
