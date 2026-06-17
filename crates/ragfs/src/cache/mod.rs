//! Optional provider-independent caching for RAGFS filesystems.
//!
//! This module is intentionally not installed by [`crate::MountableFS`] by
//! default. Callers opt in by wrapping an existing [`crate::FileSystem`] with
//! [`CachedFileSystem`].

mod envelope;
mod memory;
mod metrics;
mod policy;
mod provider;
mod wrapper;

pub use memory::{MemoryCacheProvider, MemoryMockProvider};
pub use metrics::{CacheMetrics, CacheMetricsSnapshot};
pub use policy::{CacheDecision, CachePolicy, CacheTraversalMode, CacheTreeMode};
pub use provider::{CacheError, CacheProvider, CacheResult, ProviderCapabilities};
pub use wrapper::{CacheNamespace, CachedFileSystem};
