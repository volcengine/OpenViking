use crate::client::MooncakeClient;
use crate::{MooncakeConfig, MooncakeObjectStore, MooncakeReplicateConfig};
use async_trait::async_trait;
use bytes::Bytes;
use futures::stream::{self, StreamExt};
use ragfs::cache::{CacheError, CacheProvider, CacheResult, ProviderCapabilities};
use std::collections::HashSet;
use std::fmt;
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::Duration;

/// Mooncake implementation of the common RAGFS cache provider contract.
pub struct MooncakeProvider {
    client: Arc<MooncakeClient>,
    replicate: MooncakeReplicateConfig,
    batch_concurrency: usize,
    known_keys: Mutex<HashSet<String>>,
}

impl fmt::Debug for MooncakeProvider {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("MooncakeProvider")
            .field("batch_concurrency", &self.batch_concurrency)
            .finish_non_exhaustive()
    }
}

impl MooncakeProvider {
    /// Construct a provider over a Mooncake-compatible object store.
    ///
    /// Construction validates configuration and performs a health check.
    pub async fn from_store(
        config: MooncakeConfig,
        store: Arc<dyn MooncakeObjectStore>,
    ) -> CacheResult<Self> {
        config.validate()?;
        let client = Arc::new(MooncakeClient::new(
            store,
            config.sdk_concurrency,
            Duration::from_millis(config.operation_timeout_ms),
        ));
        client.health_check().await?;
        Ok(Self {
            client,
            replicate: MooncakeReplicateConfig {
                replica_num: config.replica_num,
                ..MooncakeReplicateConfig::default()
            },
            batch_concurrency: config.sdk_concurrency,
            known_keys: Mutex::new(HashSet::new()),
        })
    }

    /// Return a startup error when the official native binding is not compiled.
    #[cfg(not(feature = "mooncake-native"))]
    pub async fn connect(config: MooncakeConfig) -> CacheResult<Self> {
        config.validate()?;
        Err(CacheError::Unavailable(
            "Mooncake support requires the mooncake-native feature".into(),
        ))
    }

    /// Check whether the connected Mooncake services are healthy.
    pub async fn health_check(&self) -> CacheResult<()> {
        self.client.health_check().await
    }

    async fn get_object(&self, key: &str) -> CacheResult<Option<Bytes>> {
        if !self.client.is_exist(key).await? {
            return Ok(None);
        }
        match self.client.get(key).await {
            Ok(value) => Ok(Some(Bytes::from(value))),
            Err(error) => match self.client.is_exist(key).await {
                Ok(false) => Ok(None),
                Ok(true) | Err(_) => Err(error),
            },
        }
    }

    async fn delete_object(&self, key: &str) -> CacheResult<()> {
        if !self.client.is_exist(key).await? {
            lock_known_keys(&self.known_keys)?.remove(key);
            return Ok(());
        }
        match self.client.remove(key).await {
            Ok(()) => {
                lock_known_keys(&self.known_keys)?.remove(key);
                Ok(())
            }
            Err(error) => match self.client.is_exist(key).await {
                Ok(false) => {
                    lock_known_keys(&self.known_keys)?.remove(key);
                    Ok(())
                }
                Ok(true) | Err(_) => Err(error),
            },
        }
    }
}

fn lock_known_keys(
    known_keys: &Mutex<HashSet<String>>,
) -> CacheResult<MutexGuard<'_, HashSet<String>>> {
    known_keys
        .lock()
        .map_err(|_| CacheError::Internal("Mooncake key tracker is poisoned".into()))
}

#[async_trait]
impl CacheProvider for MooncakeProvider {
    fn name(&self) -> &'static str {
        "mooncake"
    }

    fn capabilities(&self) -> ProviderCapabilities {
        ProviderCapabilities {
            batch_get: true,
            batch_put: true,
            native_ttl: false,
        }
    }

    async fn get(&self, key: &str) -> CacheResult<Option<Bytes>> {
        self.get_object(key).await
    }

    async fn put(&self, key: &str, value: Bytes) -> CacheResult<()> {
        self.client.put(key, &value, &self.replicate).await?;
        lock_known_keys(&self.known_keys)?.insert(key.to_owned());
        Ok(())
    }

    async fn delete(&self, key: &str) -> CacheResult<()> {
        self.delete_object(key).await
    }

    async fn exists(&self, key: &str) -> CacheResult<bool> {
        self.client.is_exist(key).await
    }

    async fn batch_get(&self, keys: &[String]) -> CacheResult<Vec<Option<Bytes>>> {
        let mut values = stream::iter(keys.iter().cloned().enumerate())
            .map(|(index, key)| async move { (index, self.get_object(&key).await) })
            .buffer_unordered(self.batch_concurrency)
            .collect::<Vec<_>>()
            .await;
        values.sort_by_key(|(index, _)| *index);
        values
            .into_iter()
            .map(|(_, value)| value)
            .collect::<CacheResult<Vec<_>>>()
    }

    async fn batch_put(&self, entries: Vec<(String, Bytes)>) -> CacheResult<()> {
        let results = stream::iter(entries)
            .map(|(key, value)| async move { self.put(&key, value).await })
            .buffer_unordered(self.batch_concurrency)
            .collect::<Vec<_>>()
            .await;
        results.into_iter().collect()
    }

    async fn invalidate(&self, keys: &[String]) -> CacheResult<()> {
        let results = stream::iter(keys.iter().cloned())
            .map(|key| async move { self.delete_object(&key).await })
            .buffer_unordered(self.batch_concurrency)
            .collect::<Vec<_>>()
            .await;
        results.into_iter().collect()
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
                if message == "Mooncake key tracker is poisoned"
        ));
    }
}
