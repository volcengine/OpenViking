use crate::client::RedisClient;
use crate::RedisConfig;
use async_trait::async_trait;
use bytes::Bytes;
use ragfs::cache::{CacheError, CacheProvider, CacheResult, ProviderCapabilities};
use std::collections::HashSet;
use std::fmt;
use std::sync::{Arc, Mutex, MutexGuard};

/// Redis implementation of the common RAGFS cache provider contract.
pub struct RedisProvider {
    client: Arc<RedisClient>,
    key_prefix: String,
    ttl_ms: Option<u64>,
    known_keys: Mutex<HashSet<String>>,
}

impl fmt::Debug for RedisProvider {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("RedisProvider")
            .field("key_prefix", &self.key_prefix)
            .field("ttl_ms", &self.ttl_ms)
            .finish_non_exhaustive()
    }
}

impl RedisProvider {
    /// Connect to Redis and validate the provider with PING.
    pub async fn connect(config: RedisConfig) -> CacheResult<Self> {
        config.validate()?;
        let key_prefix = normalized_prefix(&config.key_prefix);
        let ttl_ms = ttl_ms(config.default_ttl_seconds)?;
        let client = Arc::new(RedisClient::connect(&config).await?);
        Ok(Self {
            client,
            key_prefix,
            ttl_ms,
            known_keys: Mutex::new(HashSet::new()),
        })
    }

    /// Check whether the connected Redis service is healthy.
    pub async fn health_check(&self) -> CacheResult<()> {
        self.client.health_check().await
    }

    fn redis_key(&self, key: &str) -> String {
        format!("{}{}", self.key_prefix, key)
    }
}

fn normalized_prefix(prefix: &str) -> String {
    if prefix.ends_with(':') {
        prefix.to_owned()
    } else {
        format!("{prefix}:")
    }
}

fn ttl_ms(ttl_seconds: u64) -> CacheResult<Option<u64>> {
    if ttl_seconds == 0 {
        return Ok(None);
    }
    ttl_seconds
        .checked_mul(1_000)
        .map(Some)
        .ok_or_else(|| CacheError::InvalidArgument("Redis default_ttl_seconds is too large".into()))
}

fn lock_known_keys(
    known_keys: &Mutex<HashSet<String>>,
) -> CacheResult<MutexGuard<'_, HashSet<String>>> {
    known_keys
        .lock()
        .map_err(|_| CacheError::Internal("Redis key tracker is poisoned".into()))
}

#[async_trait]
impl CacheProvider for RedisProvider {
    fn name(&self) -> &'static str {
        "redis"
    }

    fn capabilities(&self) -> ProviderCapabilities {
        ProviderCapabilities {
            batch_get: true,
            batch_put: true,
            native_ttl: self.ttl_ms.is_some(),
        }
    }

    async fn get(&self, key: &str) -> CacheResult<Option<Bytes>> {
        Ok(self.client.get(self.redis_key(key)).await?.map(Bytes::from))
    }

    async fn put(&self, key: &str, value: Bytes) -> CacheResult<()> {
        self.client
            .set(self.redis_key(key), value.to_vec(), self.ttl_ms)
            .await?;
        lock_known_keys(&self.known_keys)?.insert(key.to_owned());
        Ok(())
    }

    async fn delete(&self, key: &str) -> CacheResult<()> {
        self.client.delete(self.redis_key(key)).await?;
        lock_known_keys(&self.known_keys)?.remove(key);
        Ok(())
    }

    async fn exists(&self, key: &str) -> CacheResult<bool> {
        self.client.exists(self.redis_key(key)).await
    }

    async fn batch_get(&self, keys: &[String]) -> CacheResult<Vec<Option<Bytes>>> {
        let redis_keys = keys.iter().map(|key| self.redis_key(key)).collect();
        Ok(self
            .client
            .batch_get(redis_keys)
            .await?
            .into_iter()
            .map(|value| value.map(Bytes::from))
            .collect())
    }

    async fn batch_put(&self, entries: Vec<(String, Bytes)>) -> CacheResult<()> {
        let redis_entries = entries
            .iter()
            .map(|(key, value)| (self.redis_key(key), value.to_vec()))
            .collect();
        self.client.batch_set(redis_entries, self.ttl_ms).await?;
        lock_known_keys(&self.known_keys)?.extend(entries.into_iter().map(|(key, _)| key));
        Ok(())
    }

    async fn invalidate(&self, keys: &[String]) -> CacheResult<()> {
        let redis_keys = keys.iter().map(|key| self.redis_key(key)).collect();
        self.client.batch_delete(redis_keys).await?;
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
        lock_known_keys(&self.known_keys)?.clear();
        self.client.close().await
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::panic::{self, AssertUnwindSafe};

    #[test]
    fn normalizes_key_prefix_once() {
        assert_eq!(normalized_prefix("ragfs-cache"), "ragfs-cache:");
        assert_eq!(normalized_prefix("ragfs-cache:"), "ragfs-cache:");
    }

    #[test]
    fn ttl_zero_disables_native_expiration() {
        assert_eq!(ttl_ms(0).unwrap(), None);
        assert_eq!(ttl_ms(2).unwrap(), Some(2_000));
    }

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
            CacheError::Internal(message) if message == "Redis key tracker is poisoned"
        ));
    }
}
