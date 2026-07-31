use bytes::Bytes;
use ragfs::cache::{CacheError, CacheProvider};
use ragfs_cache_mooncake::{
    MooncakeConfig, MooncakeObjectStore, MooncakeProvider, MooncakeReplicateConfig,
    MooncakeStoreError,
};
use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

#[derive(Default)]
struct FakeStore {
    values: Mutex<HashMap<String, Vec<u8>>>,
    remove_force_flags: Mutex<Vec<bool>>,
    healthy: AtomicBool,
    fail_get: AtomicBool,
    remove_during_get: AtomicBool,
    delay_ms: AtomicUsize,
    active: AtomicUsize,
    max_active: AtomicUsize,
}

impl FakeStore {
    fn healthy() -> Self {
        Self {
            healthy: AtomicBool::new(true),
            ..Self::default()
        }
    }

    fn enter(&self) -> ActiveGuard<'_> {
        let active = self.active.fetch_add(1, Ordering::SeqCst) + 1;
        self.max_active.fetch_max(active, Ordering::SeqCst);
        let delay = self.delay_ms.load(Ordering::SeqCst);
        if delay > 0 {
            std::thread::sleep(Duration::from_millis(delay as u64));
        }
        ActiveGuard { store: self }
    }
}

struct ActiveGuard<'a> {
    store: &'a FakeStore,
}

impl Drop for ActiveGuard<'_> {
    fn drop(&mut self) {
        self.store.active.fetch_sub(1, Ordering::SeqCst);
    }
}

impl MooncakeObjectStore for FakeStore {
    fn health_check(&self) -> Result<(), MooncakeStoreError> {
        if self.healthy.load(Ordering::SeqCst) {
            Ok(())
        } else {
            Err(MooncakeStoreError::Unavailable("not healthy".into()))
        }
    }

    fn is_exist(&self, key: &str) -> Result<bool, MooncakeStoreError> {
        let _guard = self.enter();
        Ok(self.values.lock().unwrap().contains_key(key))
    }

    fn get(&self, key: &str) -> Result<Vec<u8>, MooncakeStoreError> {
        let _guard = self.enter();
        if self.remove_during_get.load(Ordering::SeqCst) {
            self.values.lock().unwrap().remove(key);
            return Err(MooncakeStoreError::NotFound);
        }
        if self.fail_get.load(Ordering::SeqCst) {
            return Err(MooncakeStoreError::Internal("get failed".into()));
        }
        self.values
            .lock()
            .unwrap()
            .get(key)
            .cloned()
            .ok_or(MooncakeStoreError::NotFound)
    }

    fn put(
        &self,
        key: &str,
        value: &[u8],
        _replicate: &MooncakeReplicateConfig,
    ) -> Result<(), MooncakeStoreError> {
        let _guard = self.enter();
        self.values
            .lock()
            .unwrap()
            .insert(key.to_string(), value.to_vec());
        Ok(())
    }

    fn remove(&self, key: &str, force: bool) -> Result<(), MooncakeStoreError> {
        let _guard = self.enter();
        self.remove_force_flags.lock().unwrap().push(force);
        self.values.lock().unwrap().remove(key);
        Ok(())
    }
}

fn config() -> MooncakeConfig {
    MooncakeConfig {
        local_hostname: "127.0.0.1".into(),
        metadata_server: "http://127.0.0.1:8080/metadata".into(),
        master_server_addr: "127.0.0.1:50051".into(),
        protocol: "tcp".into(),
        device_name: String::new(),
        global_segment_size: 512 << 20,
        local_buffer_size: 128 << 20,
        replica_num: 2,
        sdk_concurrency: 2,
        operation_timeout_ms: 100,
    }
}

async fn provider(store: Arc<FakeStore>) -> MooncakeProvider {
    MooncakeProvider::from_store(config(), store).await.unwrap()
}

#[tokio::test]
async fn initialization_requires_valid_config_and_health_check() {
    let mut invalid = config();
    invalid.sdk_concurrency = 0;
    let error = MooncakeProvider::from_store(invalid, Arc::new(FakeStore::healthy()))
        .await
        .unwrap_err();
    assert!(matches!(error, CacheError::InvalidArgument(_)));

    let mut invalid_protocol = config();
    invalid_protocol.protocol = "unknown".into();
    let error = MooncakeProvider::from_store(invalid_protocol, Arc::new(FakeStore::healthy()))
        .await
        .unwrap_err();
    assert!(matches!(error, CacheError::InvalidArgument(_)));

    let error = MooncakeProvider::from_store(config(), Arc::new(FakeStore::default()))
        .await
        .unwrap_err();
    assert!(matches!(error, CacheError::Unavailable(_)));
}

#[tokio::test]
async fn get_distinguishes_hit_miss_and_internal_error() {
    let store = Arc::new(FakeStore::healthy());
    store
        .values
        .lock()
        .unwrap()
        .insert("hit".into(), b"value".to_vec());
    let provider = provider(store.clone()).await;

    assert_eq!(
        provider.get("hit").await.unwrap(),
        Some(Bytes::from_static(b"value"))
    );
    assert_eq!(provider.get("missing").await.unwrap(), None);

    store.fail_get.store(true, Ordering::SeqCst);
    let error = provider.get("hit").await.unwrap_err();
    assert!(matches!(error, CacheError::Internal(_)));
}

#[tokio::test]
async fn object_removed_between_exist_and_get_is_a_miss() {
    let store = Arc::new(FakeStore::healthy());
    store
        .values
        .lock()
        .unwrap()
        .insert("racy".into(), b"value".to_vec());
    store.remove_during_get.store(true, Ordering::SeqCst);
    let provider = provider(store).await;

    assert_eq!(provider.get("racy").await.unwrap(), None);
}

#[tokio::test]
async fn put_delete_and_batch_operations_preserve_object_semantics() {
    let store = Arc::new(FakeStore::healthy());
    let provider = provider(store.clone()).await;

    provider.put("one", Bytes::from_static(b"1")).await.unwrap();
    provider
        .batch_put(vec![
            ("two".into(), Bytes::from_static(b"2")),
            ("three".into(), Bytes::from_static(b"3")),
        ])
        .await
        .unwrap();
    assert_eq!(
        provider
            .batch_get(&["three".into(), "missing".into(), "one".into()])
            .await
            .unwrap(),
        vec![
            Some(Bytes::from_static(b"3")),
            None,
            Some(Bytes::from_static(b"1"))
        ]
    );

    provider.delete("one").await.unwrap();
    provider.delete("one").await.unwrap();
    assert_eq!(provider.get("one").await.unwrap(), None);

    provider.invalidate(&["two".into()]).await.unwrap();
    assert_eq!(provider.get("two").await.unwrap(), None);
    provider.flush().await.unwrap();
    assert_eq!(provider.get("three").await.unwrap(), None);
    assert!(
        store
            .remove_force_flags
            .lock()
            .unwrap()
            .iter()
            .all(|force| *force),
        "cache invalidation must override active Mooncake leases"
    );

    assert!(provider.capabilities().batch_get);
    assert!(provider.capabilities().batch_put);
    assert!(!provider.capabilities().native_ttl);
}

#[tokio::test]
async fn blocking_calls_are_bounded_and_time_out_without_becoming_misses() {
    let store = Arc::new(FakeStore::healthy());
    store
        .values
        .lock()
        .unwrap()
        .insert("slow".into(), b"value".to_vec());
    store.delay_ms.store(40, Ordering::SeqCst);
    let mut bounded_config = config();
    bounded_config.operation_timeout_ms = 500;
    let provider = Arc::new(
        MooncakeProvider::from_store(bounded_config, store.clone())
            .await
            .unwrap(),
    );

    let mut tasks = Vec::new();
    for _ in 0..8 {
        let provider = provider.clone();
        tasks.push(tokio::spawn(
            async move { provider.get("slow").await.unwrap() },
        ));
    }
    for task in tasks {
        assert_eq!(task.await.unwrap(), Some(Bytes::from_static(b"value")));
    }
    assert!(store.max_active.load(Ordering::SeqCst) <= 2);

    let timeout_store = Arc::new(FakeStore::healthy());
    timeout_store.delay_ms.store(80, Ordering::SeqCst);
    let mut timeout_config = config();
    timeout_config.operation_timeout_ms = 10;
    let timeout_provider = MooncakeProvider::from_store(timeout_config, timeout_store)
        .await
        .unwrap();
    let error = timeout_provider.get("slow").await.unwrap_err();
    assert!(matches!(error, CacheError::Timeout(_)));
}

#[tokio::test]
async fn close_rejects_new_operations() {
    let provider = provider(Arc::new(FakeStore::healthy())).await;
    provider.close().await.unwrap();

    let error = provider.get("key").await.unwrap_err();
    assert!(matches!(error, CacheError::Unavailable(_)));
}

struct SlowDropStore {
    started: Arc<AtomicBool>,
    dropped: Arc<AtomicBool>,
}

impl Drop for SlowDropStore {
    fn drop(&mut self) {
        self.dropped.store(true, Ordering::SeqCst);
    }
}

impl MooncakeObjectStore for SlowDropStore {
    fn health_check(&self) -> Result<(), MooncakeStoreError> {
        Ok(())
    }

    fn is_exist(&self, _key: &str) -> Result<bool, MooncakeStoreError> {
        self.started.store(true, Ordering::SeqCst);
        std::thread::sleep(Duration::from_millis(80));
        Ok(false)
    }

    fn get(&self, _key: &str) -> Result<Vec<u8>, MooncakeStoreError> {
        unreachable!("missing object must not be read")
    }

    fn put(
        &self,
        _key: &str,
        _value: &[u8],
        _replicate: &MooncakeReplicateConfig,
    ) -> Result<(), MooncakeStoreError> {
        Ok(())
    }

    fn remove(&self, _key: &str, _force: bool) -> Result<(), MooncakeStoreError> {
        Ok(())
    }
}

#[tokio::test]
async fn close_waits_for_inflight_calls_before_releasing_store() {
    let started = Arc::new(AtomicBool::new(false));
    let dropped = Arc::new(AtomicBool::new(false));
    let store = Arc::new(SlowDropStore {
        started: started.clone(),
        dropped: dropped.clone(),
    });
    let provider = Arc::new(
        MooncakeProvider::from_store(config(), store.clone())
            .await
            .unwrap(),
    );
    drop(store);

    let reader = {
        let provider = provider.clone();
        tokio::spawn(async move { provider.get("slow").await })
    };
    while !started.load(Ordering::SeqCst) {
        tokio::task::yield_now().await;
    }

    let close_started = Instant::now();
    provider.close().await.unwrap();
    assert!(close_started.elapsed() >= Duration::from_millis(50));
    assert_eq!(reader.await.unwrap().unwrap(), None);
    assert!(dropped.load(Ordering::SeqCst));
}

#[cfg(not(feature = "mooncake-native"))]
#[tokio::test]
async fn connect_without_native_feature_returns_startup_error() {
    let error = MooncakeProvider::connect(config()).await.unwrap_err();
    assert!(matches!(
        error,
        CacheError::Unavailable(message) if message.contains("mooncake-native")
    ));
}
