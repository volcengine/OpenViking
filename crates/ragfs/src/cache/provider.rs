//! Provider-independent cache primitives.

use async_trait::async_trait;
use bytes::Bytes;

/// Result type returned by cache providers.
pub type CacheResult<T> = std::result::Result<T, CacheError>;

/// Errors shared by all cache providers.
#[derive(Debug, thiserror::Error)]
pub enum CacheError {
    /// The provider is unavailable or has been closed.
    #[error("cache provider unavailable: {0}")]
    Unavailable(String),

    /// A provider operation timed out.
    #[error("cache provider operation timed out: {0}")]
    Timeout(String),

    /// A cache value could not be decoded or validated.
    #[error("invalid cache data: {0}")]
    InvalidData(String),

    /// A provider rejected an argument.
    #[error("invalid cache argument: {0}")]
    InvalidArgument(String),

    /// An unspecified provider failure occurred.
    #[error("cache provider internal error: {0}")]
    Internal(String),
}

/// Optional features exposed by a provider.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct ProviderCapabilities {
    /// Whether batch reads have a native implementation.
    pub batch_get: bool,
    /// Whether batch writes have a native implementation.
    pub batch_put: bool,
    /// Whether the provider supports native expiration.
    pub native_ttl: bool,
}

/// Minimal contract implemented by local and remote cache providers.
#[async_trait]
pub trait CacheProvider: Send + Sync {
    /// Return a stable provider name for diagnostics.
    fn name(&self) -> &'static str;

    /// Return optional provider capabilities.
    fn capabilities(&self) -> ProviderCapabilities {
        ProviderCapabilities::default()
    }

    /// Read one cache object.
    async fn get(&self, key: &str) -> CacheResult<Option<Bytes>>;

    /// Write one cache object.
    async fn put(&self, key: &str, value: Bytes) -> CacheResult<()>;

    /// Delete one cache object. Missing keys are treated as success.
    async fn delete(&self, key: &str) -> CacheResult<()>;

    /// Check whether one cache object exists.
    async fn exists(&self, key: &str) -> CacheResult<bool> {
        Ok(self.get(key).await?.is_some())
    }

    /// Read multiple objects while preserving input order.
    async fn batch_get(&self, keys: &[String]) -> CacheResult<Vec<Option<Bytes>>> {
        let mut values = Vec::with_capacity(keys.len());
        for key in keys {
            values.push(self.get(key).await?);
        }
        Ok(values)
    }

    /// Write multiple objects.
    async fn batch_put(&self, entries: Vec<(String, Bytes)>) -> CacheResult<()> {
        for (key, value) in entries {
            self.put(&key, value).await?;
        }
        Ok(())
    }

    /// Invalidate a known set of cache keys.
    async fn invalidate(&self, keys: &[String]) -> CacheResult<()> {
        for key in keys {
            self.delete(key).await?;
        }
        Ok(())
    }

    /// Remove all objects owned by this provider instance.
    async fn flush(&self) -> CacheResult<()> {
        Ok(())
    }

    /// Release provider resources.
    async fn close(&self) -> CacheResult<()> {
        Ok(())
    }
}
