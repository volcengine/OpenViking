//! Unified provider-independent cache runtime.

mod api;
mod dynamic;
mod error;
mod executor;
#[cfg(any(test, feature = "test-utils"))]
mod memory;
mod provider;
mod redis;
mod script;

pub use api::{
    Expiration, ListDirection, ListInsertPosition, ListInsertRequest, ListMoveRequest,
    SetCondition, SetOptions, SetResult,
};
pub use dynamic::DynamicProviderConfig;
pub use error::{CacheError, CacheResult};
#[cfg(any(test, feature = "test-utils"))]
pub use memory::MemoryMockProvider;
pub use redis::{RedisDeploymentMode, RedisProviderConfig};

use bytes::Bytes;
use executor::RuntimeExecutor;
use provider::CacheProvider;
pub(crate) use script::{ScriptDefinition, ScriptRegistry, ScriptValue};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

/// Request for one named provider-side atomic program.
#[derive(Debug, Clone)]
pub struct ScriptRequest {
    /// Stable program identifier.
    pub script_id: String,
    /// Fully-qualified keys used by the program.
    pub keys: Vec<String>,
    /// Opaque program arguments.
    pub args: Vec<Bytes>,
}

/// Opaque provider-side program result.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ScriptResult {
    /// Encoded result payload interpreted by the business module.
    pub payload: Bytes,
}

/// One cache runtime bound to one provider instance.
pub struct CacheRuntime {
    provider: Arc<dyn CacheProvider>,
    scripts: Arc<ScriptRegistry>,
    executor: Arc<RuntimeExecutor>,
    closed: AtomicBool,
}

impl CacheRuntime {
    #[cfg(any(test, feature = "test-utils"))]
    pub(crate) fn from_provider(provider: Arc<dyn CacheProvider>) -> Arc<Self> {
        Self::from_provider_with_scripts(provider, Arc::new(ScriptRegistry::default()))
    }

    fn from_provider_with_scripts(
        provider: Arc<dyn CacheProvider>,
        scripts: Arc<ScriptRegistry>,
    ) -> Arc<Self> {
        let executor =
            Arc::new(RuntimeExecutor::new().expect("CacheRuntime executor must initialize"));
        Self::from_provider_with_parts(provider, scripts, executor)
    }

    fn from_provider_with_parts(
        provider: Arc<dyn CacheProvider>,
        scripts: Arc<ScriptRegistry>,
        executor: Arc<RuntimeExecutor>,
    ) -> Arc<Self> {
        Arc::new(Self {
            provider,
            scripts,
            executor,
            closed: AtomicBool::new(false),
        })
    }

    /// Build an in-process runtime for tests and smoke validation.
    #[cfg(any(test, feature = "test-utils"))]
    pub fn memory() -> Arc<Self> {
        Self::memory_with_provider(Arc::new(MemoryMockProvider::new()))
    }

    /// Build a Runtime around one controllable in-memory provider.
    #[cfg(any(test, feature = "test-utils"))]
    pub fn memory_with_provider(provider: Arc<MemoryMockProvider>) -> Arc<Self> {
        Self::from_provider(provider)
    }

    /// Connect the built-in Redis provider and create one Runtime.
    pub async fn redis(config: RedisProviderConfig) -> CacheResult<Arc<Self>> {
        let scripts = Arc::new(ScriptRegistry::default());
        let provider = redis::RedisProvider::connect(config, Arc::clone(&scripts)).await?;
        Ok(Self::from_provider_with_scripts(
            Arc::new(provider),
            scripts,
        ))
    }

    /// Connect Redis on the dedicated RuntimeExecutor for synchronous callers.
    pub fn connect_sync(config: RedisProviderConfig) -> CacheResult<Arc<Self>> {
        if tokio::runtime::Handle::try_current().is_ok() {
            return Err(CacheError::InvalidExecutionContext);
        }
        let executor = Arc::new(RuntimeExecutor::new()?);
        let scripts = Arc::new(ScriptRegistry::default());
        let provider_scripts = Arc::clone(&scripts);
        let provider = executor.run(async move {
            redis::RedisProvider::connect(config, provider_scripts)
                .await
                .map(|provider| Arc::new(provider) as Arc<dyn CacheProvider>)
        })?;
        Ok(Self::from_provider_with_parts(provider, scripts, executor))
    }

    /// Load one provider through the versioned dynamic C ABI.
    pub async fn dynamic(_config: DynamicProviderConfig) -> CacheResult<Arc<Self>> {
        Err(CacheError::UnsupportedProvider(
            "DynamicProvider is planned for a later release".into(),
        ))
    }

    pub(crate) fn register_script(&self, definition: ScriptDefinition) -> CacheResult<()> {
        self.scripts.register(definition)
    }

    /// Read one value.
    pub async fn get(&self, key: &str) -> CacheResult<Option<Bytes>> {
        self.ensure_open()?;
        self.provider.get(key).await
    }

    /// Store one value using Redis SET semantics.
    pub async fn set(
        &self,
        key: &str,
        value: Bytes,
        options: SetOptions,
    ) -> CacheResult<SetResult> {
        self.ensure_open()?;
        self.provider.set(key, value, options).await
    }

    /// Delete multiple keys and return the number removed.
    pub async fn del(&self, keys: &[String]) -> CacheResult<u64> {
        self.ensure_open()?;
        self.provider.del(keys).await
    }

    /// Read multiple keys while preserving input order.
    pub async fn mget(&self, keys: &[String]) -> CacheResult<Vec<Option<Bytes>>> {
        self.ensure_open()?;
        self.provider.mget(keys).await
    }

    /// Store multiple values.
    pub async fn mset(&self, entries: Vec<(String, Bytes)>) -> CacheResult<()> {
        self.ensure_open()?;
        self.provider.mset(entries).await
    }

    /// Increment one integer value by one.
    pub async fn incr(&self, key: &str) -> CacheResult<i64> {
        self.incr_by(key, 1).await
    }

    /// Increment one integer value by `delta`.
    pub async fn incr_by(&self, key: &str, delta: i64) -> CacheResult<i64> {
        self.ensure_open()?;
        self.provider.incr_by(key, delta).await
    }

    /// Decrement one integer value by one.
    pub async fn decr(&self, key: &str) -> CacheResult<i64> {
        self.incr_by(key, -1).await
    }

    /// Decrement one integer value by `delta`.
    pub async fn decr_by(&self, key: &str, delta: i64) -> CacheResult<i64> {
        let increment = delta.checked_neg().ok_or_else(|| {
            CacheError::InvalidArgument("decrement delta is too large".to_string())
        })?;
        self.incr_by(key, increment).await
    }

    /// Check whether a set contains one member.
    pub async fn sismember(&self, key: &str, member: &[u8]) -> CacheResult<bool> {
        self.ensure_open()?;
        self.provider.sismember(key, member).await
    }

    /// Return all members of one set in unspecified order.
    pub async fn smembers(&self, key: &str) -> CacheResult<Vec<Bytes>> {
        self.ensure_open()?;
        self.provider.smembers(key).await
    }

    /// Return the cardinality of one set.
    pub async fn scard(&self, key: &str) -> CacheResult<u64> {
        self.ensure_open()?;
        self.provider.scard(key).await
    }

    /// Push values to the head of one list.
    pub async fn lpush(&self, key: &str, values: Vec<Bytes>) -> CacheResult<u64> {
        self.ensure_open()?;
        self.provider.lpush(key, values).await
    }

    /// Push values to the tail of one list.
    pub async fn rpush(&self, key: &str, values: Vec<Bytes>) -> CacheResult<u64> {
        self.ensure_open()?;
        self.provider.rpush(key, values).await
    }

    /// Pop values from the head of one list.
    pub async fn lpop(&self, key: &str, count: Option<u64>) -> CacheResult<Vec<Bytes>> {
        self.ensure_open()?;
        self.provider.lpop(key, count).await
    }

    /// Pop values from the tail of one list.
    pub async fn rpop(&self, key: &str, count: Option<u64>) -> CacheResult<Vec<Bytes>> {
        self.ensure_open()?;
        self.provider.rpop(key, count).await
    }

    /// Return the length of one list.
    pub async fn llen(&self, key: &str) -> CacheResult<u64> {
        self.ensure_open()?;
        self.provider.llen(key).await
    }

    /// Return an inclusive range from one list.
    pub async fn lrange(&self, key: &str, start: i64, stop: i64) -> CacheResult<Vec<Bytes>> {
        self.ensure_open()?;
        self.provider.lrange(key, start, stop).await
    }

    /// Return one list element by index.
    pub async fn lindex(&self, key: &str, index: i64) -> CacheResult<Option<Bytes>> {
        self.ensure_open()?;
        self.provider.lindex(key, index).await
    }

    /// Replace one list element by index.
    pub async fn lset(&self, key: &str, index: i64, value: Bytes) -> CacheResult<()> {
        self.ensure_open()?;
        self.provider.lset(key, index, value).await
    }

    /// Trim one list to an inclusive range.
    pub async fn ltrim(&self, key: &str, start: i64, stop: i64) -> CacheResult<()> {
        self.ensure_open()?;
        self.provider.ltrim(key, start, stop).await
    }

    /// Remove matching elements from one list.
    pub async fn lrem(&self, key: &str, count: i64, value: Bytes) -> CacheResult<u64> {
        self.ensure_open()?;
        self.provider.lrem(key, count, value).await
    }

    /// Insert one element relative to a pivot.
    pub async fn linsert(&self, request: ListInsertRequest) -> CacheResult<i64> {
        self.ensure_open()?;
        self.provider.linsert(request).await
    }

    /// Atomically move one element between lists.
    pub async fn lmove(&self, request: ListMoveRequest) -> CacheResult<Option<Bytes>> {
        self.ensure_open()?;
        self.provider.lmove(request).await
    }

    /// Execute one registered script.
    pub async fn execute_script(&self, request: ScriptRequest) -> CacheResult<ScriptResult> {
        self.ensure_open()?;
        self.provider.execute_script(request).await
    }

    /// Check whether the provider is healthy.
    pub async fn ping(&self) -> CacheResult<()> {
        self.ensure_open()?;
        self.provider.ping().await
    }

    /// Wrap the current Runtime with synchronous primitive operations.
    pub fn sync_facade(self: &Arc<Self>) -> SyncCacheRuntimeFacade {
        SyncCacheRuntimeFacade {
            runtime: Arc::clone(self),
            executor: Arc::clone(&self.executor),
        }
    }

    /// Close the provider and reject future operations.
    pub async fn close(&self) -> CacheResult<()> {
        if self.closed.swap(true, Ordering::AcqRel) {
            return Ok(());
        }
        self.provider.close().await
    }

    fn ensure_open(&self) -> CacheResult<()> {
        if self.closed.load(Ordering::Acquire) {
            Err(CacheError::Closed)
        } else {
            Ok(())
        }
    }
}

/// Stateless synchronous facade over one CacheRuntime.
pub struct SyncCacheRuntimeFacade {
    runtime: Arc<CacheRuntime>,
    executor: Arc<RuntimeExecutor>,
}

impl SyncCacheRuntimeFacade {
    /// Read one value.
    pub fn get(&self, key: &str) -> CacheResult<Option<Bytes>> {
        let runtime = Arc::clone(&self.runtime);
        let key = key.to_string();
        self.executor.run(async move { runtime.get(&key).await })
    }

    /// Store one value using Redis SET semantics.
    pub fn set(&self, key: &str, value: Bytes, options: SetOptions) -> CacheResult<SetResult> {
        let runtime = Arc::clone(&self.runtime);
        let key = key.to_string();
        self.executor
            .run(async move { runtime.set(&key, value, options).await })
    }

    /// Delete multiple keys and return the number removed.
    pub fn del(&self, keys: &[String]) -> CacheResult<u64> {
        let runtime = Arc::clone(&self.runtime);
        let keys = keys.to_vec();
        self.executor.run(async move { runtime.del(&keys).await })
    }

    /// Read multiple keys while preserving input order.
    pub fn mget(&self, keys: &[String]) -> CacheResult<Vec<Option<Bytes>>> {
        let runtime = Arc::clone(&self.runtime);
        let keys = keys.to_vec();
        self.executor.run(async move { runtime.mget(&keys).await })
    }

    /// Store multiple values.
    pub fn mset(&self, entries: Vec<(String, Bytes)>) -> CacheResult<()> {
        let runtime = Arc::clone(&self.runtime);
        self.executor
            .run(async move { runtime.mset(entries).await })
    }

    /// Increment one integer value by one.
    pub fn incr(&self, key: &str) -> CacheResult<i64> {
        self.incr_by(key, 1)
    }

    /// Increment one integer value by `delta`.
    pub fn incr_by(&self, key: &str, delta: i64) -> CacheResult<i64> {
        let runtime = Arc::clone(&self.runtime);
        let key = key.to_string();
        self.executor
            .run(async move { runtime.incr_by(&key, delta).await })
    }

    /// Decrement one integer value by one.
    pub fn decr(&self, key: &str) -> CacheResult<i64> {
        self.incr_by(key, -1)
    }

    /// Decrement one integer value by `delta`.
    pub fn decr_by(&self, key: &str, delta: i64) -> CacheResult<i64> {
        let increment = delta.checked_neg().ok_or_else(|| {
            CacheError::InvalidArgument("decrement delta is too large".to_string())
        })?;
        self.incr_by(key, increment)
    }

    /// Check whether a set contains one member.
    pub fn sismember(&self, key: &str, member: &[u8]) -> CacheResult<bool> {
        let runtime = Arc::clone(&self.runtime);
        let key = key.to_string();
        let member = member.to_vec();
        self.executor
            .run(async move { runtime.sismember(&key, &member).await })
    }

    /// Return all members of one set in unspecified order.
    pub fn smembers(&self, key: &str) -> CacheResult<Vec<Bytes>> {
        let runtime = Arc::clone(&self.runtime);
        let key = key.to_string();
        self.executor
            .run(async move { runtime.smembers(&key).await })
    }

    /// Return the cardinality of one set.
    pub fn scard(&self, key: &str) -> CacheResult<u64> {
        let runtime = Arc::clone(&self.runtime);
        let key = key.to_string();
        self.executor.run(async move { runtime.scard(&key).await })
    }

    /// Push values to the head of one list.
    pub fn lpush(&self, key: &str, values: Vec<Bytes>) -> CacheResult<u64> {
        let runtime = Arc::clone(&self.runtime);
        let key = key.to_string();
        self.executor
            .run(async move { runtime.lpush(&key, values).await })
    }

    /// Push values to the tail of one list.
    pub fn rpush(&self, key: &str, values: Vec<Bytes>) -> CacheResult<u64> {
        let runtime = Arc::clone(&self.runtime);
        let key = key.to_string();
        self.executor
            .run(async move { runtime.rpush(&key, values).await })
    }

    /// Pop values from the head of one list.
    pub fn lpop(&self, key: &str, count: Option<u64>) -> CacheResult<Vec<Bytes>> {
        let runtime = Arc::clone(&self.runtime);
        let key = key.to_string();
        self.executor
            .run(async move { runtime.lpop(&key, count).await })
    }

    /// Pop values from the tail of one list.
    pub fn rpop(&self, key: &str, count: Option<u64>) -> CacheResult<Vec<Bytes>> {
        let runtime = Arc::clone(&self.runtime);
        let key = key.to_string();
        self.executor
            .run(async move { runtime.rpop(&key, count).await })
    }

    /// Return the length of one list.
    pub fn llen(&self, key: &str) -> CacheResult<u64> {
        let runtime = Arc::clone(&self.runtime);
        let key = key.to_string();
        self.executor.run(async move { runtime.llen(&key).await })
    }

    /// Return an inclusive range from one list.
    pub fn lrange(&self, key: &str, start: i64, stop: i64) -> CacheResult<Vec<Bytes>> {
        let runtime = Arc::clone(&self.runtime);
        let key = key.to_string();
        self.executor
            .run(async move { runtime.lrange(&key, start, stop).await })
    }

    /// Return one list element by index.
    pub fn lindex(&self, key: &str, index: i64) -> CacheResult<Option<Bytes>> {
        let runtime = Arc::clone(&self.runtime);
        let key = key.to_string();
        self.executor
            .run(async move { runtime.lindex(&key, index).await })
    }

    /// Replace one list element by index.
    pub fn lset(&self, key: &str, index: i64, value: Bytes) -> CacheResult<()> {
        let runtime = Arc::clone(&self.runtime);
        let key = key.to_string();
        self.executor
            .run(async move { runtime.lset(&key, index, value).await })
    }

    /// Trim one list to an inclusive range.
    pub fn ltrim(&self, key: &str, start: i64, stop: i64) -> CacheResult<()> {
        let runtime = Arc::clone(&self.runtime);
        let key = key.to_string();
        self.executor
            .run(async move { runtime.ltrim(&key, start, stop).await })
    }

    /// Remove matching elements from one list.
    pub fn lrem(&self, key: &str, count: i64, value: Bytes) -> CacheResult<u64> {
        let runtime = Arc::clone(&self.runtime);
        let key = key.to_string();
        self.executor
            .run(async move { runtime.lrem(&key, count, value).await })
    }

    /// Insert one element relative to a pivot.
    pub fn linsert(&self, request: ListInsertRequest) -> CacheResult<i64> {
        let runtime = Arc::clone(&self.runtime);
        self.executor
            .run(async move { runtime.linsert(request).await })
    }

    /// Atomically move one element between lists.
    pub fn lmove(&self, request: ListMoveRequest) -> CacheResult<Option<Bytes>> {
        let runtime = Arc::clone(&self.runtime);
        self.executor
            .run(async move { runtime.lmove(request).await })
    }

    /// Execute one registered script.
    pub fn execute_script(&self, request: ScriptRequest) -> CacheResult<ScriptResult> {
        let runtime = Arc::clone(&self.runtime);
        self.executor
            .run(async move { runtime.execute_script(request).await })
    }

    /// Check whether the provider is healthy.
    pub fn ping(&self) -> CacheResult<()> {
        let runtime = Arc::clone(&self.runtime);
        self.executor.run(async move { runtime.ping().await })
    }

    /// Close the shared runtime.
    pub fn close(&self) -> CacheResult<()> {
        let runtime = Arc::clone(&self.runtime);
        self.executor.run(async move { runtime.close().await })
    }
}
