//! In-process cache provider used by tests and smoke validation.

use super::{CacheError, CacheProvider, CacheResult, ProviderCapabilities};
use async_trait::async_trait;
use bytes::Bytes;
use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, Ordering};
use tokio::sync::RwLock;

/// A thread-safe in-memory implementation of [`CacheProvider`].
pub struct MemoryCacheProvider {
    values: RwLock<HashMap<String, Bytes>>,
    closed: AtomicBool,
}

/// Test-oriented name for the in-process cache provider.
pub type MemoryMockProvider = MemoryCacheProvider;

impl MemoryCacheProvider {
    /// Create an empty provider.
    pub fn new() -> Self {
        Self {
            values: RwLock::new(HashMap::new()),
            closed: AtomicBool::new(false),
        }
    }

    fn ensure_open(&self) -> CacheResult<()> {
        if self.closed.load(Ordering::Acquire) {
            Err(CacheError::Unavailable(
                "memory provider is closed".to_string(),
            ))
        } else {
            Ok(())
        }
    }

    /// Return the current number of stored objects.
    pub async fn len(&self) -> usize {
        self.values.read().await.len()
    }

    /// Return whether the provider currently stores no objects.
    pub async fn is_empty(&self) -> bool {
        self.len().await == 0
    }

    /// Return a snapshot of stored keys for diagnostics and smoke tests.
    pub async fn keys(&self) -> Vec<String> {
        self.values.read().await.keys().cloned().collect()
    }
}

impl Default for MemoryCacheProvider {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl CacheProvider for MemoryCacheProvider {
    fn name(&self) -> &'static str {
        "memory"
    }

    fn capabilities(&self) -> ProviderCapabilities {
        ProviderCapabilities {
            batch_get: true,
            batch_put: true,
            native_ttl: false,
        }
    }

    async fn get(&self, key: &str) -> CacheResult<Option<Bytes>> {
        self.ensure_open()?;
        Ok(self.values.read().await.get(key).cloned())
    }

    async fn put(&self, key: &str, value: Bytes) -> CacheResult<()> {
        self.ensure_open()?;
        let mut values = self.values.write().await;
        self.ensure_open()?;
        values.insert(key.to_string(), value);
        Ok(())
    }

    async fn delete(&self, key: &str) -> CacheResult<()> {
        self.ensure_open()?;
        let mut values = self.values.write().await;
        self.ensure_open()?;
        values.remove(key);
        Ok(())
    }

    async fn batch_get(&self, keys: &[String]) -> CacheResult<Vec<Option<Bytes>>> {
        self.ensure_open()?;
        let values = self.values.read().await;
        Ok(keys.iter().map(|key| values.get(key).cloned()).collect())
    }

    async fn batch_put(&self, entries: Vec<(String, Bytes)>) -> CacheResult<()> {
        self.ensure_open()?;
        let mut values = self.values.write().await;
        self.ensure_open()?;
        values.extend(entries);
        Ok(())
    }

    async fn invalidate(&self, keys: &[String]) -> CacheResult<()> {
        self.ensure_open()?;
        let mut values = self.values.write().await;
        self.ensure_open()?;
        for key in keys {
            values.remove(key);
        }
        Ok(())
    }

    async fn flush(&self) -> CacheResult<()> {
        self.ensure_open()?;
        let mut values = self.values.write().await;
        self.ensure_open()?;
        values.clear();
        Ok(())
    }

    async fn close(&self) -> CacheResult<()> {
        self.closed.store(true, Ordering::Release);
        self.values.write().await.clear();
        Ok(())
    }
}
