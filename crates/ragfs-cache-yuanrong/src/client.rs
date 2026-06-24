use crate::{YuanrongKvStore, YuanrongStoreError};
use ragfs::cache::{CacheError, CacheResult};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::{RwLock, Semaphore};

pub(crate) struct YuanrongClient {
    store: RwLock<Option<Arc<dyn YuanrongKvStore>>>,
    concurrency: Arc<Semaphore>,
    concurrency_limit: u32,
    timeout: Duration,
    closed: AtomicBool,
}

impl YuanrongClient {
    pub(crate) fn new(
        store: Arc<dyn YuanrongKvStore>,
        concurrency_limit: usize,
        timeout: Duration,
    ) -> Self {
        Self {
            store: RwLock::new(Some(store)),
            concurrency: Arc::new(Semaphore::new(concurrency_limit)),
            concurrency_limit: concurrency_limit as u32,
            timeout,
            closed: AtomicBool::new(false),
        }
    }

    async fn execute<T, F>(&self, operation: &'static str, call: F) -> CacheResult<T>
    where
        T: Send + 'static,
        F: FnOnce(Arc<dyn YuanrongKvStore>) -> Result<T, YuanrongStoreError> + Send + 'static,
    {
        if self.closed.load(Ordering::Acquire) {
            return Err(CacheError::Unavailable(
                "Yuanrong provider is closed".into(),
            ));
        }

        let work = async {
            let permit = Arc::clone(&self.concurrency)
                .acquire_owned()
                .await
                .map_err(|_| CacheError::Unavailable("Yuanrong client is closing".into()))?;
            if self.closed.load(Ordering::Acquire) {
                return Err(CacheError::Unavailable(
                    "Yuanrong provider is closed".into(),
                ));
            }
            let store = self.store.read().await.clone().ok_or_else(|| {
                CacheError::Unavailable("Yuanrong KV client has been released".into())
            })?;
            tokio::task::spawn_blocking(move || {
                let _permit = permit;
                call(store)
            })
            .await
            .map_err(|error| {
                CacheError::Internal(format!(
                    "Yuanrong {operation} blocking task failed: {error}"
                ))
            })?
            .map_err(map_store_error)
        };

        tokio::time::timeout(self.timeout, work)
            .await
            .map_err(|_| {
                CacheError::Timeout(format!(
                    "Yuanrong {operation} exceeded {} ms",
                    self.timeout.as_millis()
                ))
            })?
    }

    pub(crate) async fn health_check(&self) -> CacheResult<()> {
        self.execute("health_check", |store| store.health_check())
            .await
    }

    pub(crate) async fn get(&self, key: &str) -> CacheResult<Option<Vec<u8>>> {
        let key = key.to_owned();
        self.execute("get", move |store| store.get(&key)).await
    }

    pub(crate) async fn set(&self, key: &str, value: &[u8]) -> CacheResult<()> {
        let key = key.to_owned();
        let value = value.to_vec();
        self.execute("set", move |store| store.set(&key, &value))
            .await
    }

    pub(crate) async fn delete(&self, key: &str) -> CacheResult<()> {
        let key = key.to_owned();
        self.execute("delete", move |store| store.delete(&key))
            .await
    }

    pub(crate) async fn exists(&self, key: &str) -> CacheResult<bool> {
        let key = key.to_owned();
        self.execute("exists", move |store| store.exists(&key))
            .await
    }

    pub(crate) async fn batch_get(&self, keys: &[String]) -> CacheResult<Vec<Option<Vec<u8>>>> {
        let keys = keys.to_vec();
        self.execute("batch_get", move |store| store.batch_get(&keys))
            .await
    }

    pub(crate) async fn batch_set(&self, entries: Vec<(String, Vec<u8>)>) -> CacheResult<()> {
        self.execute("batch_set", move |store| store.batch_set(&entries))
            .await
    }

    pub(crate) async fn batch_delete(&self, keys: &[String]) -> CacheResult<()> {
        let keys = keys.to_vec();
        self.execute("batch_delete", move |store| store.batch_delete(&keys))
            .await
    }

    pub(crate) async fn close(&self) -> CacheResult<()> {
        if self.closed.swap(true, Ordering::AcqRel) {
            return Ok(());
        }
        let permits = Arc::clone(&self.concurrency)
            .acquire_many_owned(self.concurrency_limit)
            .await
            .map_err(|_| CacheError::Unavailable("Yuanrong client is closing".into()))?;
        let store = self.store.write().await.take();
        let result = if let Some(store) = store {
            tokio::time::timeout(
                self.timeout,
                tokio::task::spawn_blocking(move || store.shutdown()),
            )
            .await
            .map_err(|_| {
                CacheError::Timeout(format!(
                    "Yuanrong shutdown exceeded {} ms",
                    self.timeout.as_millis()
                ))
            })?
            .map_err(|error| {
                CacheError::Internal(format!("Yuanrong shutdown task failed: {error}"))
            })?
            .map_err(map_store_error)
        } else {
            Ok(())
        };
        drop(permits);
        result
    }
}

fn map_store_error(error: YuanrongStoreError) -> CacheError {
    match error {
        YuanrongStoreError::Unavailable(message) => CacheError::Unavailable(message),
        YuanrongStoreError::Timeout(message) => CacheError::Timeout(message),
        YuanrongStoreError::InvalidArgument(message) => CacheError::InvalidArgument(message),
        YuanrongStoreError::Internal(message) => CacheError::Internal(message),
    }
}
