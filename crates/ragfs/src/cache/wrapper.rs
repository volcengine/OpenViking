//! A transparent [`FileSystem`](crate::FileSystem) cache wrapper.

use super::envelope::QueryCacheEnvelope;
use super::CacheMetrics;
use crate::cache_runtime::{
    CacheError, CacheResult, CacheRuntime, ScriptDefinition, ScriptRequest, ScriptValue,
};
use crate::core::filesystem::{apply_read_dir_options, normalize_prefix_path};
use crate::core::internal_names::is_hidden_internal_name;
use crate::core::{
    FileInfo, FileSystem, GlobPage, GrepOptions, GrepResult, ListSortBy, Result, SortOrder,
    TreeEntry, WriteFlag,
};
use async_trait::async_trait;
use bytes::Bytes;
use rand::Rng;
use serde::de::DeserializeOwned;
use serde::Serialize;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::future::Future;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tokio::sync::Mutex;
use tracing::warn;
use uuid::Uuid;
use xxhash_rust::xxh3::xxh3_128;

const QUERY_INDEX_GC_TRIGGER_THRESHOLD: u64 = 64;
const UNLINK_MAX_RETRIES: u32 = 3;
const UNLINK_RETRY_BASE_MS: u64 = 10;
const PENDING_EPOCH_TTL_MS: u64 = 300_000;
const BEGIN_FILL_SCRIPT_ID: &str = "ragfs.cache.query.begin_fill.v1";
const FILL_SCRIPT_ID: &str = "ragfs.cache.query.fill_if_epoch.v1";
const DELETE_BUCKET_SCRIPT_ID: &str = "ragfs.cache.query.delete_bucket.v1";
const BEGIN_FILL_SCRIPT: &str = r#"
local epoch = redis.call("HGET", KEYS[1], "__epoch")
local created = 0
local now_ms = tonumber(ARGV[5])
local score
if not epoch then
    epoch = ARGV[2]
    created = 1
    redis.call("HSET", KEYS[1], "__epoch", epoch)
end
local value = redis.call("HGET", KEYS[1], ARGV[1])
local ttl = tonumber(ARGV[3])
if ttl > 0 then
    redis.call("PEXPIRE", KEYS[1], ttl)
    score = now_ms + ttl
elseif created == 1 then
    redis.call("PEXPIRE", KEYS[1], ARGV[4])
    score = now_ms + tonumber(ARGV[4])
else
    local pttl = redis.call("PTTL", KEYS[1])
    if pttl >= 0 then score = now_ms + pttl
    else score = tonumber(ARGV[6]) end
end
redis.call("ZADD", KEYS[2], score, KEYS[1])
if value then return { epoch, 1, value } end
return { epoch, 0 }
"#;
const FILL_SCRIPT: &str = r#"
if redis.call("HGET", KEYS[1], "__epoch") ~= ARGV[1] then return 0 end
redis.call("HSET", KEYS[1], ARGV[2], ARGV[3])
local ttl = tonumber(ARGV[4])
local score
if ttl > 0 then
    redis.call("PEXPIRE", KEYS[1], ttl)
    score = tonumber(ARGV[5]) + ttl
else
    redis.call("PERSIST", KEYS[1])
    score = tonumber(ARGV[6])
end
redis.call("ZADD", KEYS[2], score, KEYS[1])
return 1
"#;
const DELETE_BUCKET_SCRIPT: &str = r#"
if #ARGV > 0 then
    if ARGV[1] ~= "cleanup_expired" then
        return redis.error_reply("unsupported delete bucket mode")
    end
    local members = redis.call("ZRANGEBYSCORE", KEYS[1], ARGV[2], ARGV[3], "LIMIT", 0, ARGV[4])
    if #members == 0 then return 0 end
    local expired = {}
    for _, member in ipairs(members) do
        if redis.call("EXISTS", member) == 0 then
            expired[#expired + 1] = member
        end
    end
    if #expired == 0 then return 0 end
    return redis.call("ZREM", KEYS[1], unpack(expired))
end
redis.call("UNLINK", KEYS[1])
redis.call("ZREM", KEYS[2], KEYS[1])
return 1
"#;
const SCRIPT_DEFINITIONS: &[ScriptDefinition] = &[
    ScriptDefinition {
        id: BEGIN_FILL_SCRIPT_ID,
        redis_lua: BEGIN_FILL_SCRIPT,
    },
    ScriptDefinition {
        id: FILL_SCRIPT_ID,
        redis_lua: FILL_SCRIPT,
    },
    ScriptDefinition {
        id: DELETE_BUCKET_SCRIPT_ID,
        redis_lua: DELETE_BUCKET_SCRIPT,
    },
];

/// A provider-independent read-through filesystem cache.
///
/// Construction is explicit. RAGFS does not install this wrapper in the
/// default mount path, so existing filesystem behavior remains unchanged.
pub struct CachedFileSystem {
    backend: Box<dyn FileSystem>,
    runtime: Arc<CacheRuntime>,
    service_name: String,
    shared_redis_ttl: Option<Duration>,
    metrics: Arc<CacheMetrics>,
    inflight: Mutex<HashMap<String, Arc<Mutex<()>>>>,
    index_gc_changes: Arc<AtomicU64>,
    index_gc_running: Arc<AtomicBool>,
}

impl CachedFileSystem {
    /// Wrap an existing backend with the unified cache runtime.
    pub fn with_runtime(
        backend: Box<dyn FileSystem>,
        runtime: Arc<CacheRuntime>,
        service_name: impl Into<String>,
    ) -> Self {
        Self::build(backend, runtime, service_name.into())
    }

    fn build(
        backend: Box<dyn FileSystem>,
        runtime: Arc<CacheRuntime>,
        service_name: String,
    ) -> Self {
        for definition in SCRIPT_DEFINITIONS {
            runtime
                .register_script(*definition)
                .expect("CacheFS script registration must succeed");
        }
        Self {
            backend,
            runtime,
            service_name,
            shared_redis_ttl: None,
            metrics: Arc::new(CacheMetrics::default()),
            inflight: Mutex::new(HashMap::new()),
            index_gc_changes: Arc::new(AtomicU64::new(0)),
            index_gc_running: Arc::new(AtomicBool::new(false)),
        }
    }

    /// Override the TTL for every query bucket when Redis is shared.
    ///
    /// `None` keeps every query bucket on the provider default TTL. This is a
    /// retention bound for shared deployments, not a per-query priority.
    pub fn with_shared_redis_ttl(mut self, ttl: Option<Duration>) -> Self {
        self.shared_redis_ttl = ttl;
        self
    }

    /// Return this wrapper's cache metrics.
    pub fn metrics(&self) -> Arc<CacheMetrics> {
        Arc::clone(&self.metrics)
    }

    /// Return the unified cache runtime used by this wrapper.
    pub fn runtime(&self) -> Arc<CacheRuntime> {
        Arc::clone(&self.runtime)
    }

    /// Return the wrapped filesystem for mount-stack capability discovery.
    pub(crate) fn inner_fs(&self) -> &dyn FileSystem {
        self.backend.as_ref()
    }

    /// Invalidate cache objects affected by a write that bypassed this wrapper.
    pub(crate) async fn invalidate_external_write(&self, path: &str) {
        self.spawn_path_invalidation(path);
    }

    /// Read one query cache field from a provider HASH bucket.
    async fn cache_hash_get(&self, key: &str, field: &str) -> CacheResult<Option<Bytes>> {
        let started = Instant::now();
        let result = self.runtime.hget(key, field).await;
        self.metrics.get(started.elapsed());
        match result {
            Ok(value) => Ok(value),
            Err(error) => {
                self.metrics.error();
                Err(error)
            }
        }
    }

    /// Capture the bucket epoch, index the bucket, and recheck one missed field atomically.
    async fn begin_query_fill(
        &self,
        key: &str,
        field: &str,
        ttl_ms: u64,
    ) -> CacheResult<(Bytes, Option<Bytes>)> {
        let started = Instant::now();
        let result = self
            .runtime
            .execute_script(ScriptRequest {
                script_id: BEGIN_FILL_SCRIPT_ID.to_string(),
                keys: vec![key.to_string(), self.query_index_key()],
                args: vec![
                    Bytes::copy_from_slice(field.as_bytes()),
                    Bytes::from(Uuid::new_v4().to_string()),
                    Bytes::from(ttl_ms.to_string()),
                    Bytes::from(PENDING_EPOCH_TTL_MS.to_string()),
                    Bytes::from(current_time_ms().to_string()),
                    Bytes::from(u64::MAX.to_string()),
                ],
            })
            .await
            .and_then(|result| result.decode());
        self.metrics.get(started.elapsed());
        match result {
            Ok(ScriptValue::Array(values)) => match values.as_slice() {
                [ScriptValue::Bytes(epoch), ScriptValue::Integer(0)] => {
                    Ok((Bytes::copy_from_slice(epoch), None))
                }
                [ScriptValue::Bytes(epoch), ScriptValue::Integer(1), ScriptValue::Bytes(value)] => {
                    Ok((
                        Bytes::copy_from_slice(epoch),
                        Some(Bytes::copy_from_slice(value)),
                    ))
                }
                _ => Err(CacheError::InvalidData(
                    "CacheFS begin-fill script returned invalid data".to_string(),
                )),
            },
            Ok(value) => Err(CacheError::InvalidData(format!(
                "CacheFS begin-fill script returned {value:?}"
            ))),
            Err(error) => Err(error),
        }
        .inspect_err(|_| self.metrics.error())
    }

    /// Store one query field and final index score only when its epoch is still current.
    async fn cache_hash_put(
        &self,
        key: &str,
        epoch: Bytes,
        field: &str,
        value: Bytes,
        ttl_ms: u64,
    ) -> bool {
        let started = Instant::now();
        let result = self
            .runtime
            .execute_script(ScriptRequest {
                script_id: FILL_SCRIPT_ID.to_string(),
                keys: vec![key.to_string(), self.query_index_key()],
                args: vec![
                    epoch,
                    Bytes::copy_from_slice(field.as_bytes()),
                    value,
                    Bytes::from(ttl_ms.to_string()),
                    Bytes::from(current_time_ms().to_string()),
                    Bytes::from(u64::MAX.to_string()),
                ],
            })
            .await
            .and_then(|result| result.decode());
        self.metrics.put(started.elapsed());
        match result {
            Ok(ScriptValue::Integer(1)) => return true,
            Ok(ScriptValue::Integer(0)) => return false,
            Ok(_) | Err(_) => {
                self.metrics.error();
                return false;
            }
        }
    }

    /// Return the configured cache TTL in milliseconds, or zero when disabled.
    fn default_ttl_ms(&self) -> u64 {
        self.runtime
            .default_ttl()
            .map(|ttl| ttl.as_millis().min(u128::from(u64::MAX)) as u64)
            .unwrap_or(0)
    }

    /// Return the TTL for query buckets in a shared Redis deployment.
    ///
    /// The override applies uniformly because query buckets have no stable
    /// file-body priority in the query-driven cache model.
    fn query_ttl_ms(&self) -> u64 {
        let default_ttl = self.default_ttl_ms();
        self.shared_redis_ttl
            .map(|ttl| ttl.as_millis().min(u128::from(u64::MAX)) as u64)
            .unwrap_or(default_ttl)
    }

    /// Delete query cache buckets and remove them from the provider index atomically.
    async fn delete_query_buckets(
        runtime: Arc<CacheRuntime>,
        metrics: Arc<CacheMetrics>,
        index_key: String,
        mut keys: Vec<String>,
        affected_path: String,
    ) {
        if keys.is_empty() {
            return;
        }
        keys.sort();
        keys.dedup();

        let started = Instant::now();
        let mut deleted_count = 0_u64;
        for (index, key) in keys.iter().enumerate() {
            for attempt in 0..=UNLINK_MAX_RETRIES {
                let result = runtime
                    .execute_script(ScriptRequest {
                        script_id: DELETE_BUCKET_SCRIPT_ID.to_string(),
                        keys: vec![key.clone(), index_key.clone()],
                        args: Vec::new(),
                    })
                    .await;
                match result {
                    Ok(_) => {
                        deleted_count += 1;
                        break;
                    }
                    Err(_) if attempt < UNLINK_MAX_RETRIES => {
                        metrics.error();
                        let delay_ms = (UNLINK_RETRY_BASE_MS << attempt)
                            + rand::thread_rng().gen_range(0..=UNLINK_RETRY_BASE_MS);
                        tokio::time::sleep(Duration::from_millis(delay_ms)).await;
                    }
                    Err(error) => {
                        metrics.error();
                        warn!(
                            path = %affected_path,
                            bucket_index = index,
                            retry_count = UNLINK_MAX_RETRIES,
                            error = %error,
                            "query cache invalidation delete failed"
                        );
                        break;
                    }
                }
            }
        }
        metrics.delete(started.elapsed());
        if deleted_count > 0 {
            metrics.invalidation();
        }
    }

    /// Return a cached query payload when the stored request matches.
    async fn probe_query<T>(&self, bucket_key: &str, query_hash: &str, request: &Value) -> Option<T>
    where
        T: DeserializeOwned,
    {
        let value = match self.cache_hash_get(bucket_key, query_hash).await {
            Ok(value) => value?,
            Err(_) => return None,
        };
        self.decode_query(&value, request)
    }

    /// Decode one query envelope and require an exact request match.
    fn decode_query<T>(&self, value: &[u8], request: &Value) -> Option<T>
    where
        T: DeserializeOwned,
    {
        let envelope = match QueryCacheEnvelope::<T>::decode(&value) {
            Ok(envelope) if envelope.request_matches(request) => envelope,
            Ok(_) => return None,
            Err(_) => {
                self.metrics.error();
                return None;
            }
        };

        Some(envelope.into_payload())
    }

    /// Store one query payload in its HASH bucket.
    async fn fill_query<T>(
        &self,
        bucket_key: &str,
        epoch: Bytes,
        query_hash: &str,
        request: Value,
        payload: &T,
        ttl_ms: u64,
    ) where
        T: Serialize,
    {
        match QueryCacheEnvelope::new(request, payload).encode() {
            Ok(value) => {
                self.cache_hash_put(bucket_key, epoch, query_hash, value, ttl_ms)
                    .await;
            }
            Err(_) => self.metrics.error(),
        }
    }

    /// Load one query through the HASH cache, coalescing concurrent misses.
    async fn get_or_fill_query<T, F, Fut>(
        &self,
        op: &str,
        path: &str,
        request: Value,
        query_hash: String,
        load: F,
    ) -> Result<T>
    where
        T: Serialize + DeserializeOwned,
        F: FnOnce() -> Fut,
        Fut: Future<Output = Result<T>>,
    {
        let normalized = normalize_path(path);
        let bucket_key = self.query_bucket_key(&normalized, op);
        let ttl_ms = self.query_ttl_ms();
        if let Some(payload) = self.probe_query(&bucket_key, &query_hash, &request).await {
            self.metrics.query_hit();
            return Ok(payload);
        }
        self.metrics.query_miss();

        let inflight_key = format!("{bucket_key}:{query_hash}");
        let (inflight, _) = self.acquire_inflight(&inflight_key).await;
        let inflight_guard = inflight.lock().await;

        let epoch = match self
            .begin_query_fill(&bucket_key, &query_hash, ttl_ms)
            .await
        {
            Ok((epoch, Some(value))) => match self.decode_query::<T>(&value, &request) {
                Some(payload) => {
                    drop(inflight_guard);
                    self.release_inflight(&inflight_key, &inflight).await;
                    return Ok(payload);
                }
                None => Some(epoch),
            },
            Ok((epoch, None)) => Some(epoch),
            Err(_) => None,
        };
        let result = load().await;
        if let Ok(value) = &result {
            self.metrics.backend_fallback();
            if let Some(epoch) = epoch {
                self.fill_query(&bucket_key, epoch, &query_hash, request, value, ttl_ms)
                    .await;
            }
        }
        drop(inflight_guard);
        self.release_inflight(&inflight_key, &inflight).await;
        result
    }

    async fn acquire_inflight(&self, key: &str) -> (Arc<Mutex<()>>, bool) {
        let mut inflight = self.inflight.lock().await;
        if let Some(lock) = inflight.get(key) {
            (Arc::clone(lock), false)
        } else {
            let lock = Arc::new(Mutex::new(()));
            inflight.insert(key.to_string(), Arc::clone(&lock));
            (lock, true)
        }
    }

    async fn release_inflight(&self, key: &str, lock: &Arc<Mutex<()>>) {
        let mut inflight = self.inflight.lock().await;
        if inflight
            .get(key)
            .is_some_and(|current| Arc::ptr_eq(current, lock) && Arc::strong_count(lock) == 2)
        {
            inflight.remove(key);
        }
    }

    /// Schedule asynchronous invalidation for one changed path.
    fn spawn_path_invalidation(&self, path: &str) {
        if is_encryption_temp_path(path) {
            return;
        }
        self.note_query_index_change();
        let mut keys = Vec::new();
        self.collect_path_change_buckets(path, &mut keys);
        self.spawn_bucket_invalidation(keys, path);
    }

    /// Schedule asynchronous invalidation for one changed path and its descendants.
    fn spawn_descendant_invalidation(&self, path: &str) {
        if is_encryption_temp_path(path) {
            return;
        }
        self.note_query_index_change();
        let path = normalize_path(path);
        let runtime = Arc::clone(&self.runtime);
        let metrics = Arc::clone(&self.metrics);
        let service_name = self.service_name.clone();
        let index_key = self.query_index_key();
        tokio::spawn(async move {
            let mut keys = Self::indexed_descendant_query_buckets(
                Arc::clone(&runtime),
                Arc::clone(&metrics),
                &index_key,
                &service_name,
                &path,
            )
            .await;
            Self::collect_path_change_buckets_for(&service_name, &path, &mut keys);
            Self::delete_query_buckets(runtime, metrics, index_key, keys, path).await;
        });
    }

    /// Record one write-driven invalidation and trigger bounded index GC when needed.
    fn note_query_index_change(&self) {
        let changes = self.index_gc_changes.fetch_add(1, Ordering::Relaxed) + 1;
        if changes < QUERY_INDEX_GC_TRIGGER_THRESHOLD {
            return;
        }
        if self.index_gc_running.swap(true, Ordering::AcqRel) {
            return;
        }
        self.index_gc_changes.store(0, Ordering::Relaxed);

        let runtime = Arc::clone(&self.runtime);
        let metrics = Arc::clone(&self.metrics);
        let index_key = self.query_index_key();
        let running = Arc::clone(&self.index_gc_running);
        tokio::spawn(async move {
            Self::cleanup_expired_query_index_members(
                runtime,
                metrics,
                index_key,
                QUERY_INDEX_GC_TRIGGER_THRESHOLD << 1,
            )
            .await;
            running.store(false, Ordering::Release);
        });
    }

    /// Remove a bounded batch of expired query index members.
    async fn cleanup_expired_query_index_members(
        runtime: Arc<CacheRuntime>,
        metrics: Arc<CacheMetrics>,
        index_key: String,
        limit: u64,
    ) {
        let limit = u32::try_from(limit).unwrap_or(u32::MAX);
        let max_score = current_time_ms() as f64;
        match runtime
            .execute_script(ScriptRequest {
                script_id: DELETE_BUCKET_SCRIPT_ID.to_string(),
                keys: vec![index_key.clone()],
                args: vec![
                    Bytes::from_static(b"cleanup_expired"),
                    Bytes::from(f64::NEG_INFINITY.to_string()),
                    Bytes::from(max_score.to_string()),
                    Bytes::from(limit.to_string()),
                ],
            })
            .await
        {
            Ok(_) => {}
            Err(error) => {
                metrics.error();
                warn!(
                    index_key = %index_key,
                    limit = limit,
                    error = %error,
                    "query cache index gc cleanup failed"
                );
            }
        }
    }

    /// Schedule asynchronous invalidation for already calculated bucket keys.
    fn spawn_bucket_invalidation(&self, keys: Vec<String>, affected_path: &str) {
        let runtime = Arc::clone(&self.runtime);
        let metrics = Arc::clone(&self.metrics);
        let index_key = self.query_index_key();
        let affected_path = normalize_path(affected_path);
        tokio::spawn(async move {
            Self::delete_query_buckets(runtime, metrics, index_key, keys, affected_path).await;
        });
    }

    /// Return indexed query buckets below one path by scanning the global index.
    async fn indexed_descendant_query_buckets(
        runtime: Arc<CacheRuntime>,
        metrics: Arc<CacheMetrics>,
        index_key: &str,
        service_name: &str,
        path: &str,
    ) -> Vec<String> {
        let pattern = query_descendant_pattern(service_name, path);
        match runtime.zscan_match(index_key, &pattern, 1_000).await {
            Ok(keys) => keys,
            Err(error) => {
                metrics.error();
                warn!(
                    path = %path,
                    pattern = %pattern,
                    error = %error,
                    "query cache descendant scan failed"
                );
                Vec::new()
            }
        }
    }

    /// Collect exact and ancestor query buckets affected by a path change.
    fn collect_path_change_buckets(&self, path: &str, keys: &mut Vec<String>) {
        Self::collect_path_change_buckets_for(&self.service_name, path, keys);
    }

    /// Collect exact and ancestor query buckets for the provided service name.
    fn collect_path_change_buckets_for(service_name: &str, path: &str, keys: &mut Vec<String>) {
        let normalized = normalize_path(path);
        for op in [
            "stat",
            "read_dir",
            "tree_directory",
            "glob_directory",
            "grep",
        ] {
            keys.push(query_bucket_key(service_name, &normalized, op));
        }

        let parent = parent_path(&normalized);
        keys.push(query_bucket_key(service_name, &parent, "read_dir"));
        for scope in ancestor_scopes(&parent) {
            for op in ["tree_directory", "glob_directory", "grep", "stat"] {
                keys.push(query_bucket_key(service_name, &scope, op));
            }
        }
    }

    fn query_bucket_key(&self, path: &str, op: &str) -> String {
        query_bucket_key(&self.service_name, path, op)
    }

    fn query_index_key(&self) -> String {
        format!("{{{}}}:ov:cacheidx:v1", self.service_name)
    }
}

#[async_trait]
impl FileSystem for CachedFileSystem {
    async fn create(&self, path: &str) -> Result<()> {
        self.backend.create(path).await?;
        self.spawn_path_invalidation(path);
        Ok(())
    }

    async fn mkdir(&self, path: &str, mode: u32) -> Result<()> {
        self.backend.mkdir(path, mode).await?;
        self.spawn_path_invalidation(path);
        Ok(())
    }

    async fn remove(&self, path: &str) -> Result<()> {
        self.backend.remove(path).await?;
        self.spawn_path_invalidation(path);
        Ok(())
    }

    async fn remove_all(&self, path: &str) -> Result<()> {
        let result = self.backend.remove_all(path).await;
        // Recursive deletion is not transactional: a backend can remove part of
        // the descendant range before reporting an error. Invalidate conservatively so
        // provider-backed caches cannot continue serving deleted objects.
        self.spawn_descendant_invalidation(path);
        result
    }

    async fn read(&self, path: &str, offset: u64, size: u64) -> Result<Vec<u8>> {
        self.backend.read(path, offset, size).await
    }

    async fn write(&self, path: &str, data: &[u8], offset: u64, flags: WriteFlag) -> Result<u64> {
        let written = self.backend.write(path, data, offset, flags).await?;
        self.spawn_path_invalidation(path);
        Ok(written)
    }

    async fn read_dir(
        &self,
        path: &str,
        offset: Option<usize>,
        limit: Option<usize>,
        sort_by: Option<ListSortBy>,
        sort_order: Option<SortOrder>,
    ) -> Result<Vec<FileInfo>> {
        let normalized = normalize_path(path);
        if is_internal_cache_path(&normalized) {
            return self
                .backend
                .read_dir(path, offset, limit, sort_by, sort_order)
                .await;
        }

        let (request, query_hash) = match canonical_query_request(json!({
            "path": normalized.as_str(),
            "offset": offset,
            "limit": limit,
            "sort_by": sort_by,
            "sort_order": sort_order,
        })) {
            Ok(request) => request,
            Err(_) => {
                self.metrics.error();
                return self
                    .backend
                    .read_dir(path, offset, limit, sort_by, sort_order)
                    .await;
            }
        };
        self.get_or_fill_query("read_dir", &normalized, request, query_hash, || async {
            let mut entries = self
                .backend
                .read_dir(path, None, None, sort_by, sort_order)
                .await?;
            entries.retain(|entry| !is_hidden_internal_name(&entry.name));
            Ok(apply_read_dir_options(
                entries, offset, limit, sort_by, sort_order,
            ))
        })
        .await
    }

    async fn read_internal_dir(&self, path: &str) -> Result<Vec<FileInfo>> {
        self.backend.read_internal_dir(path).await
    }

    async fn stat(&self, path: &str) -> Result<FileInfo> {
        let normalized = normalize_path(path);
        if is_internal_cache_path(&normalized) {
            return self.backend.stat(path).await;
        }

        let (request, query_hash) = match canonical_query_request(json!({
            "path": normalized.as_str(),
        })) {
            Ok(request) => request,
            Err(_) => {
                self.metrics.error();
                return self.backend.stat(path).await;
            }
        };
        self.get_or_fill_query("stat", &normalized, request, query_hash, || async {
            self.backend.stat(path).await
        })
        .await
    }

    async fn rename(&self, old_path: &str, new_path: &str) -> Result<()> {
        self.backend.rename(old_path, new_path).await?;
        self.spawn_descendant_invalidation(old_path);
        self.spawn_descendant_invalidation(new_path);
        Ok(())
    }

    async fn replace(&self, src_path: &str, dst_path: &str) -> Result<()> {
        self.backend.replace(src_path, dst_path).await?;
        self.spawn_path_invalidation(src_path);
        self.spawn_path_invalidation(dst_path);
        Ok(())
    }

    async fn chmod(&self, path: &str, mode: u32) -> Result<()> {
        self.backend.chmod(path, mode).await?;
        self.spawn_path_invalidation(path);
        Ok(())
    }

    async fn truncate(&self, path: &str, size: u64) -> Result<()> {
        self.backend.truncate(path, size).await?;
        self.spawn_path_invalidation(path);
        Ok(())
    }

    async fn exists(&self, path: &str) -> bool {
        self.backend.exists(path).await
    }

    async fn grep(
        &self,
        path: &str,
        pattern: &str,
        options: GrepOptions<'_>,
    ) -> Result<GrepResult> {
        let normalized = normalize_path(path);
        if is_internal_cache_path(&normalized) {
            return self.backend.grep(path, pattern, options).await;
        }

        let (request, query_hash) = match canonical_query_request(json!({
            "path": normalized.as_str(),
            "pattern": pattern,
            "recursive": options.recursive,
            "case_insensitive": options.case_insensitive,
            "node_limit": options.node_limit,
            "exclude_path": options.exclude_path.map(normalize_prefix_path),
            "level_limit": options.level_limit,
            "before_context": options.before_context,
            "after_context": options.after_context,
        })) {
            Ok(request) => request,
            Err(_) => {
                self.metrics.error();
                return self.backend.grep(path, pattern, options).await;
            }
        };
        self.get_or_fill_query("grep", &normalized, request, query_hash, || async {
            let mut result = self.backend.grep(path, pattern, options).await?;
            result
                .matches
                .retain(|entry| !is_internal_entry_path(&entry.file));
            result.count = result.matches.len();
            Ok(result)
        })
        .await
    }

    async fn tree_directory(
        &self,
        path: &str,
        show_hidden: bool,
        node_limit: Option<usize>,
        level_limit: Option<usize>,
        offset: Option<usize>,
        sort_by: Option<ListSortBy>,
        sort_order: Option<SortOrder>,
        directories_only: bool,
    ) -> Result<Vec<TreeEntry>> {
        let normalized = normalize_path(path);
        if is_internal_cache_path(&normalized) {
            return self
                .backend
                .tree_directory(
                    path,
                    show_hidden,
                    node_limit,
                    level_limit,
                    offset,
                    sort_by,
                    sort_order,
                    directories_only,
                )
                .await;
        }

        let (request, query_hash) = match canonical_query_request(json!({
            "path": normalized.as_str(),
            "show_hidden": show_hidden,
            "node_limit": node_limit,
            "level_limit": level_limit,
            "offset": offset,
            "sort_by": sort_by,
            "sort_order": sort_order,
            "directories_only": directories_only,
        })) {
            Ok(request) => request,
            Err(_) => {
                self.metrics.error();
                return self
                    .backend
                    .tree_directory(
                        path,
                        show_hidden,
                        node_limit,
                        level_limit,
                        offset,
                        sort_by,
                        sort_order,
                        directories_only,
                    )
                    .await;
            }
        };
        self.get_or_fill_query(
            "tree_directory",
            &normalized,
            request,
            query_hash,
            || async {
                let mut entries = self
                    .backend
                    .tree_directory(
                        path,
                        show_hidden,
                        node_limit,
                        level_limit,
                        offset,
                        sort_by,
                        sort_order,
                        directories_only,
                    )
                    .await?;
                entries.retain(|entry| {
                    !is_hidden_internal_name(&entry.info.name)
                        && !is_internal_entry_path(&entry.path)
                });
                Ok(entries)
            },
        )
        .await
    }

    async fn glob_directory(
        &self,
        path: &str,
        pattern: &str,
        show_hidden: bool,
        page_size: Option<usize>,
        level_limit: Option<usize>,
        continuation_token: Option<String>,
    ) -> Result<GlobPage> {
        let normalized = normalize_path(path);
        if is_internal_cache_path(&normalized) {
            return self
                .backend
                .glob_directory(
                    path,
                    pattern,
                    show_hidden,
                    page_size,
                    level_limit,
                    continuation_token,
                )
                .await;
        }

        let (request, query_hash) = match canonical_query_request(json!({
            "path": normalized.as_str(),
            "pattern": pattern,
            "show_hidden": show_hidden,
            "page_size": page_size,
            "level_limit": level_limit,
            "continuation_token": continuation_token.as_deref(),
        })) {
            Ok(request) => request,
            Err(_) => {
                self.metrics.error();
                return self
                    .backend
                    .glob_directory(
                        path,
                        pattern,
                        show_hidden,
                        page_size,
                        level_limit,
                        continuation_token,
                    )
                    .await;
            }
        };
        self.get_or_fill_query(
            "glob_directory",
            &normalized,
            request,
            query_hash,
            || async {
                let mut page = self
                    .backend
                    .glob_directory(
                        path,
                        pattern,
                        show_hidden,
                        page_size,
                        level_limit,
                        continuation_token,
                    )
                    .await?;
                page.entries.retain(|entry| {
                    !is_hidden_internal_name(&entry.name) && !is_internal_entry_path(&entry.path)
                });
                Ok(page)
            },
        )
        .await
    }
}

/// Build a stable JSON request value and its field hash.
fn canonical_query_request(request: Value) -> CacheResult<(Value, String)> {
    let canonical =
        serde_json::to_vec(&request).map_err(|error| CacheError::InvalidData(error.to_string()))?;
    let hash = format!("{:032x}", xxh3_128(&canonical));
    Ok((request, hash))
}

/// Return the current Unix timestamp in milliseconds, saturating at u64::MAX.
fn current_time_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .min(u128::from(u64::MAX)) as u64
}

/// Build one query HASH bucket key from service, path, and operation.
fn query_bucket_key(service_name: &str, path: &str, op: &str) -> String {
    let normalized = normalize_path(path);
    format!(
        "{{{}}}:ov:cache:v1:{}:{}",
        service_name,
        encode_path_key(&normalized),
        op
    )
}

/// Build the Redis glob pattern for descendant query buckets under one path.
fn query_descendant_pattern(service_name: &str, path: &str) -> String {
    let normalized = normalize_path(path);
    if normalized == "/" {
        return format!("{{{}}}:ov:cache:v1:*", service_name);
    }
    format!(
        "{{{}}}:ov:cache:v1:{}/*:*",
        service_name,
        encode_path_key(&normalized)
    )
}

/// Percent-encode a normalized path for use in provider bucket keys.
fn encode_path_key(path: &str) -> String {
    let mut encoded = String::with_capacity(path.len());
    for ch in path.chars() {
        match ch {
            ':' => encoded.push_str("%3A"),
            '%' => encoded.push_str("%25"),
            '*' => encoded.push_str("%2A"),
            '?' => encoded.push_str("%3F"),
            '[' => encoded.push_str("%5B"),
            ']' => encoded.push_str("%5D"),
            _ => encoded.push(ch),
        }
    }
    encoded
}

/// Return true when a normalized query path names a runtime-internal file.
fn is_internal_cache_path(path: &str) -> bool {
    let normalized = normalize_path(path);
    is_encryption_temp_path(&normalized)
        || normalized
            .rsplit('/')
            .next()
            .is_some_and(is_hidden_internal_name)
}

/// Return true when a path names the encryption staging directory or one of its descendants.
fn is_encryption_temp_path(path: &str) -> bool {
    let normalized = normalize_path(path);
    normalized.ends_with("/temp/.encrypt_stage") || normalized.contains("/temp/.encrypt_stage/")
}

/// Return true when a result path names a runtime-internal file.
fn is_internal_entry_path(path: &str) -> bool {
    path.rsplit('/').next().is_some_and(is_hidden_internal_name)
}

/// Normalize one query path into the cache key path form.
fn normalize_path(path: &str) -> String {
    if path.is_empty() || path == "/" {
        "/".to_string()
    } else {
        format!("/{}", path.trim_matches('/'))
    }
}

/// Return the normalized parent path for one normalized or unnormalized path.
fn parent_path(path: &str) -> String {
    let normalized = normalize_path(path);
    if normalized == "/" {
        return "/".to_string();
    }
    normalized
        .rsplit_once('/')
        .map(|(parent, _)| {
            if parent.is_empty() {
                "/".to_string()
            } else {
                parent.to_string()
            }
        })
        .unwrap_or_else(|| "/".to_string())
}

/// Return all ancestor scopes from root through the provided path.
fn ancestor_scopes(path: &str) -> Vec<String> {
    let normalized = normalize_path(path);
    if normalized == "/" {
        return vec!["/".to_string()];
    }

    let mut scopes = vec!["/".to_string()];
    let mut current = String::new();
    for component in normalized.trim_start_matches('/').split('/') {
        current.push('/');
        current.push_str(component);
        scopes.push(current.clone());
    }
    scopes
}

#[cfg(test)]
#[path = "tests/test_wrapper.rs"]
mod tests;
