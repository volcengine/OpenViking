use crate::{MooncakeObjectStore, MooncakeReplicateConfig, MooncakeStoreError};
use ragfs::cache::{CacheError, CacheResult};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::{RwLock, Semaphore};

pub(crate) struct MooncakeClient {
    store: RwLock<Option<Arc<dyn MooncakeObjectStore>>>,
    concurrency: Arc<Semaphore>,
    concurrency_limit: u32,
    timeout: Duration,
    closed: AtomicBool,
}

impl MooncakeClient {
    pub(crate) fn new(
        store: Arc<dyn MooncakeObjectStore>,
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
        F: FnOnce(Arc<dyn MooncakeObjectStore>) -> Result<T, MooncakeStoreError> + Send + 'static,
    {
        if self.closed.load(Ordering::Acquire) {
            return Err(CacheError::Unavailable(
                "Mooncake provider is closed".into(),
            ));
        }

        let work = async {
            let permit = Arc::clone(&self.concurrency)
                .acquire_owned()
                .await
                .map_err(|_| CacheError::Unavailable("Mooncake client is closing".into()))?;
            if self.closed.load(Ordering::Acquire) {
                return Err(CacheError::Unavailable(
                    "Mooncake provider is closed".into(),
                ));
            }
            let store = self.store.read().await.clone().ok_or_else(|| {
                CacheError::Unavailable("Mooncake store has been released".into())
            })?;
            tokio::task::spawn_blocking(move || {
                let _permit = permit;
                call(store)
            })
            .await
            .map_err(|error| {
                CacheError::Internal(format!(
                    "Mooncake {operation} blocking task failed: {error}"
                ))
            })?
            .map_err(map_store_error)
        };

        tokio::time::timeout(self.timeout, work)
            .await
            .map_err(|_| {
                CacheError::Timeout(format!(
                    "Mooncake {operation} exceeded {} ms",
                    self.timeout.as_millis()
                ))
            })?
    }

    pub(crate) async fn health_check(&self) -> CacheResult<()> {
        self.execute("health_check", |store| store.health_check())
            .await
    }

    pub(crate) async fn is_exist(&self, key: &str) -> CacheResult<bool> {
        let key = key.to_owned();
        self.execute("is_exist", move |store| store.is_exist(&key))
            .await
    }

    pub(crate) async fn get(&self, key: &str) -> CacheResult<Vec<u8>> {
        let key = key.to_owned();
        self.execute("get", move |store| store.get(&key)).await
    }

    pub(crate) async fn put(
        &self,
        key: &str,
        value: &[u8],
        replicate: &MooncakeReplicateConfig,
    ) -> CacheResult<()> {
        let key = key.to_owned();
        let value = value.to_vec();
        let replicate = replicate.clone();
        self.execute("put", move |store| store.put(&key, &value, &replicate))
            .await
    }

    pub(crate) async fn remove(&self, key: &str) -> CacheResult<()> {
        let key = key.to_owned();
        self.execute("remove", move |store| store.remove(&key, true))
            .await
    }

    pub(crate) async fn close(&self) -> CacheResult<()> {
        if self.closed.swap(true, Ordering::AcqRel) {
            return Ok(());
        }
        let permits = Arc::clone(&self.concurrency)
            .acquire_many_owned(self.concurrency_limit)
            .await
            .map_err(|_| CacheError::Unavailable("Mooncake client is closing".into()))?;
        self.store.write().await.take();
        drop(permits);
        Ok(())
    }
}

fn map_store_error(error: MooncakeStoreError) -> CacheError {
    match error {
        MooncakeStoreError::NotFound => {
            CacheError::Unavailable("Mooncake object disappeared".into())
        }
        MooncakeStoreError::Unavailable(message) => CacheError::Unavailable(message),
        MooncakeStoreError::InvalidArgument(message) => CacheError::InvalidArgument(message),
        MooncakeStoreError::Internal(message) => CacheError::Internal(message),
    }
}
