use bytes::Bytes;
use ragfs::cache::{CacheError, CacheProvider};
use ragfs_cache_yuanrong::{YuanrongConfig, YuanrongKvStore, YuanrongProvider, YuanrongStoreError};
use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

#[derive(Default)]
struct FakeKvStore {
    values: Mutex<HashMap<String, Vec<u8>>>,
    healthy: AtomicBool,
    available: AtomicBool,
    delay_ms: AtomicUsize,
    active: AtomicUsize,
    max_active: AtomicUsize,
    batch_get_calls: AtomicUsize,
    batch_set_calls: AtomicUsize,
    batch_delete_calls: AtomicUsize,
    shutdown_calls: AtomicUsize,
}

impl FakeKvStore {
    fn available() -> Self {
        Self {
            healthy: AtomicBool::new(true),
            available: AtomicBool::new(true),
            ..Self::default()
        }
    }

    fn enter(&self) -> Result<ActiveGuard<'_>, YuanrongStoreError> {
        if !self.available.load(Ordering::SeqCst) {
            return Err(YuanrongStoreError::Unavailable("worker unavailable".into()));
        }
        let active = self.active.fetch_add(1, Ordering::SeqCst) + 1;
        self.max_active.fetch_max(active, Ordering::SeqCst);
        let delay = self.delay_ms.load(Ordering::SeqCst);
        if delay > 0 {
            std::thread::sleep(Duration::from_millis(delay as u64));
        }
        Ok(ActiveGuard { store: self })
    }
}

struct ActiveGuard<'a> {
    store: &'a FakeKvStore,
}

impl Drop for ActiveGuard<'_> {
    fn drop(&mut self) {
        self.store.active.fetch_sub(1, Ordering::SeqCst);
    }
}

impl YuanrongKvStore for FakeKvStore {
    fn health_check(&self) -> Result<(), YuanrongStoreError> {
        let _guard = self.enter()?;
        if self.healthy.load(Ordering::SeqCst) {
            Ok(())
        } else {
            Err(YuanrongStoreError::Unavailable("unhealthy worker".into()))
        }
    }

    fn get(&self, key: &str) -> Result<Option<Vec<u8>>, YuanrongStoreError> {
        let _guard = self.enter()?;
        Ok(self.values.lock().unwrap().get(key).cloned())
    }

    fn set(&self, key: &str, value: &[u8]) -> Result<(), YuanrongStoreError> {
        let _guard = self.enter()?;
        self.values
            .lock()
            .unwrap()
            .insert(key.to_owned(), value.to_vec());
        Ok(())
    }

    fn delete(&self, key: &str) -> Result<(), YuanrongStoreError> {
        let _guard = self.enter()?;
        self.values.lock().unwrap().remove(key);
        Ok(())
    }

    fn exists(&self, key: &str) -> Result<bool, YuanrongStoreError> {
        let _guard = self.enter()?;
        Ok(self.values.lock().unwrap().contains_key(key))
    }

    fn batch_get(&self, keys: &[String]) -> Result<Vec<Option<Vec<u8>>>, YuanrongStoreError> {
        let _guard = self.enter()?;
        self.batch_get_calls.fetch_add(1, Ordering::SeqCst);
        let values = self.values.lock().unwrap();
        Ok(keys.iter().map(|key| values.get(key).cloned()).collect())
    }

    fn batch_set(&self, entries: &[(String, Vec<u8>)]) -> Result<(), YuanrongStoreError> {
        let _guard = self.enter()?;
        self.batch_set_calls.fetch_add(1, Ordering::SeqCst);
        self.values.lock().unwrap().extend(entries.iter().cloned());
        Ok(())
    }

    fn batch_delete(&self, keys: &[String]) -> Result<(), YuanrongStoreError> {
        let _guard = self.enter()?;
        self.batch_delete_calls.fetch_add(1, Ordering::SeqCst);
        let mut values = self.values.lock().unwrap();
        for key in keys {
            values.remove(key);
        }
        Ok(())
    }

    fn shutdown(&self) -> Result<(), YuanrongStoreError> {
        self.shutdown_calls.fetch_add(1, Ordering::SeqCst);
        Ok(())
    }
}

fn config() -> YuanrongConfig {
    YuanrongConfig {
        host: "127.0.0.1".into(),
        port: 9088,
        connect_timeout_ms: 1_000,
        request_timeout_ms: 100,
        sdk_concurrency: 2,
    }
}

async fn provider(store: Arc<FakeKvStore>) -> YuanrongProvider {
    YuanrongProvider::from_store(config(), store).await.unwrap()
}

#[tokio::test]
async fn initialization_validates_config_and_health() {
    let mut invalid = config();
    invalid.host.clear();
    let error = YuanrongProvider::from_store(invalid, Arc::new(FakeKvStore::available()))
        .await
        .unwrap_err();
    assert!(matches!(error, CacheError::InvalidArgument(_)));

    let unhealthy = Arc::new(FakeKvStore::available());
    unhealthy.healthy.store(false, Ordering::SeqCst);
    let error = YuanrongProvider::from_store(config(), unhealthy)
        .await
        .unwrap_err();
    assert!(matches!(error, CacheError::Unavailable(_)));
}

#[tokio::test]
async fn hit_miss_write_delete_and_exists_map_to_kv_operations() {
    let store = Arc::new(FakeKvStore::available());
    store
        .values
        .lock()
        .unwrap()
        .insert("hit".into(), b"value".to_vec());
    let provider = provider(store).await;

    assert_eq!(
        provider.get("hit").await.unwrap(),
        Some(Bytes::from_static(b"value"))
    );
    assert_eq!(provider.get("missing").await.unwrap(), None);
    provider
        .put("written", Bytes::from_static(b"new"))
        .await
        .unwrap();
    assert!(provider.exists("written").await.unwrap());
    assert_eq!(
        provider.get("written").await.unwrap(),
        Some(Bytes::from_static(b"new"))
    );
    provider.delete("written").await.unwrap();
    provider.delete("written").await.unwrap();
    assert_eq!(provider.get("written").await.unwrap(), None);
}

#[tokio::test]
async fn batch_operations_use_native_kv_batch_calls_and_preserve_order() {
    let store = Arc::new(FakeKvStore::available());
    let provider = provider(store.clone()).await;

    provider
        .batch_put(vec![
            ("one".into(), Bytes::from_static(b"1")),
            ("two".into(), Bytes::from_static(b"2")),
        ])
        .await
        .unwrap();
    assert_eq!(
        provider
            .batch_get(&["two".into(), "missing".into(), "one".into()])
            .await
            .unwrap(),
        vec![
            Some(Bytes::from_static(b"2")),
            None,
            Some(Bytes::from_static(b"1"))
        ]
    );
    provider
        .invalidate(&["one".into(), "two".into()])
        .await
        .unwrap();

    assert_eq!(store.batch_set_calls.load(Ordering::SeqCst), 1);
    assert_eq!(store.batch_get_calls.load(Ordering::SeqCst), 1);
    assert_eq!(store.batch_delete_calls.load(Ordering::SeqCst), 1);
    assert!(provider.capabilities().batch_get);
    assert!(provider.capabilities().batch_put);
}

#[tokio::test]
async fn synchronous_calls_are_bounded_and_timeout_is_not_a_miss() {
    let store = Arc::new(FakeKvStore::available());
    store
        .values
        .lock()
        .unwrap()
        .insert("slow".into(), b"value".to_vec());
    store.delay_ms.store(30, Ordering::SeqCst);
    let mut bounded = config();
    bounded.request_timeout_ms = 500;
    let provider = Arc::new(
        YuanrongProvider::from_store(bounded, store.clone())
            .await
            .unwrap(),
    );

    let tasks = (0..8)
        .map(|_| {
            let provider = provider.clone();
            tokio::spawn(async move { provider.get("slow").await.unwrap() })
        })
        .collect::<Vec<_>>();
    for task in tasks {
        assert_eq!(task.await.unwrap(), Some(Bytes::from_static(b"value")));
    }
    assert!(store.max_active.load(Ordering::SeqCst) <= 2);

    let timeout_store = Arc::new(FakeKvStore::available());
    let mut timeout_config = config();
    timeout_config.request_timeout_ms = 10;
    let provider = YuanrongProvider::from_store(timeout_config, timeout_store.clone())
        .await
        .unwrap();
    timeout_store.delay_ms.store(80, Ordering::SeqCst);
    let error = provider.get("slow").await.unwrap_err();
    assert!(matches!(error, CacheError::Timeout(_)));
}

#[tokio::test]
async fn close_shuts_down_store_and_rejects_new_operations() {
    let store = Arc::new(FakeKvStore::available());
    let provider = provider(store.clone()).await;

    provider.close().await.unwrap();
    assert_eq!(store.shutdown_calls.load(Ordering::SeqCst), 1);
    let error = provider.get("key").await.unwrap_err();
    assert!(matches!(error, CacheError::Unavailable(_)));
}

#[cfg(not(feature = "yuanrong-native"))]
#[tokio::test]
async fn connect_without_native_feature_returns_startup_error() {
    let error = YuanrongProvider::connect(config()).await.unwrap_err();
    assert!(matches!(
        error,
        CacheError::Unavailable(message) if message.contains("yuanrong-native")
    ));
}
