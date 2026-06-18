use crate::client::YuanrongClient;
use crate::{YuanrongConfig, YuanrongKvStore};
use async_trait::async_trait;
use bytes::Bytes;
use ragfs::cache::{CacheError, CacheProvider, CacheResult, ProviderCapabilities};
use std::collections::HashSet;
use std::fmt;
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::Duration;

/// Yuanrong implementation of the common RAGFS cache provider contract.
pub struct YuanrongProvider {
    client: Arc<YuanrongClient>,
    known_keys: Mutex<HashSet<String>>,
}

impl fmt::Debug for YuanrongProvider {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("YuanrongProvider")
            .finish_non_exhaustive()
    }
}

impl YuanrongProvider {
    /// Construct a provider over a Yuanrong-compatible synchronous KV store.
    ///
    /// Construction validates configuration and performs a health check.
    pub async fn from_store(
        config: YuanrongConfig,
        store: Arc<dyn YuanrongKvStore>,
    ) -> CacheResult<Self> {
        config.validate()?;
        let client = Arc::new(YuanrongClient::new(
            store,
            config.sdk_concurrency,
            Duration::from_millis(config.request_timeout_ms),
        ));
        client.health_check().await?;
        Ok(Self {
            client,
            known_keys: Mutex::new(HashSet::new()),
        })
    }

    /// Return a startup error when the native Yuanrong bridge is not compiled.
    #[cfg(not(feature = "yuanrong-native"))]
    pub async fn connect(config: YuanrongConfig) -> CacheResult<Self> {
        config.validate()?;
        Err(CacheError::Unavailable(
            "Yuanrong support requires the yuanrong-native feature".into(),
        ))
    }

    /// Check whether the connected Yuanrong worker is healthy.
    pub async fn health_check(&self) -> CacheResult<()> {
        self.client.health_check().await
    }
}

fn lock_known_keys(
    known_keys: &Mutex<HashSet<String>>,
) -> CacheResult<MutexGuard<'_, HashSet<String>>> {
    known_keys
        .lock()
        .map_err(|_| CacheError::Internal("Yuanrong key tracker is poisoned".into()))
}

#[async_trait]
impl CacheProvider for YuanrongProvider {
    fn name(&self) -> &'static str {
        "yuanrong"
    }

    fn capabilities(&self) -> ProviderCapabilities {
        ProviderCapabilities {
            batch_get: true,
            batch_put: true,
            native_ttl: false,
        }
    }

    async fn get(&self, key: &str) -> CacheResult<Option<Bytes>> {
        Ok(self.client.get(key).await?.map(Bytes::from))
    }

    async fn put(&self, key: &str, value: Bytes) -> CacheResult<()> {
        self.client.set(key, &value).await?;
        lock_known_keys(&self.known_keys)?.insert(key.to_owned());
        Ok(())
    }

    async fn delete(&self, key: &str) -> CacheResult<()> {
        self.client.delete(key).await?;
        lock_known_keys(&self.known_keys)?.remove(key);
        Ok(())
    }

    async fn exists(&self, key: &str) -> CacheResult<bool> {
        self.client.exists(key).await
    }

    async fn batch_get(&self, keys: &[String]) -> CacheResult<Vec<Option<Bytes>>> {
        Ok(self
            .client
            .batch_get(keys)
            .await?
            .into_iter()
            .map(|value| value.map(Bytes::from))
            .collect())
    }

    async fn batch_put(&self, entries: Vec<(String, Bytes)>) -> CacheResult<()> {
        let native_entries = entries
            .iter()
            .map(|(key, value)| (key.clone(), value.to_vec()))
            .collect();
        self.client.batch_set(native_entries).await?;
        lock_known_keys(&self.known_keys)?.extend(entries.into_iter().map(|(key, _)| key));
        Ok(())
    }

    async fn invalidate(&self, keys: &[String]) -> CacheResult<()> {
        self.client.batch_delete(keys).await?;
        let mut known_keys = lock_known_keys(&self.known_keys)?;
        for key in keys {
            known_keys.remove(key);
        }
        Ok(())
    }

    async fn flush(&self) -> CacheResult<()> {
        let keys = lock_known_keys(&self.known_keys)?
            .iter()
            .cloned()
            .collect::<Vec<_>>();
        self.invalidate(&keys).await
    }

    async fn close(&self) -> CacheResult<()> {
        self.client.close().await
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::panic::{self, AssertUnwindSafe};

    #[test]
    fn lock_known_keys_returns_internal_error_when_poisoned() {
        let known_keys = Mutex::new(HashSet::new());

        let _ = panic::catch_unwind(AssertUnwindSafe(|| {
            let _guard = known_keys.lock().unwrap();
            panic!("poison known_keys");
        }));

        let error = lock_known_keys(&known_keys).unwrap_err();

        assert!(matches!(
            error,
            CacheError::Internal(message)
                if message == "Yuanrong key tracker is poisoned"
        ));
    }
}
