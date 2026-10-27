//! Optional provider-independent caching for RAGFS filesystems.
//!
//! This module is intentionally not installed by [`crate::MountableFS`] by
//! default. Callers opt in by wrapping an existing [`crate::FileSystem`] with
//! [`CachedFileSystem`].

mod envelope;
mod metrics;
mod wrapper;

pub use metrics::{CacheMetrics, CacheMetricsSnapshot};
pub use wrapper::CachedFileSystem;
