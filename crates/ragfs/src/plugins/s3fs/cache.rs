//! Dual-layer cache for S3FS
//!
//! Provides two caches:
//! - **ListDirCache**: Caches directory listing results (default TTL: 30s)
//! - **StatCache**: Caches file/directory metadata (default TTL: 60s, 5x capacity)
//!
//! Both caches use LRU eviction with TTL-based expiry.

use crate::core::types::FileInfo;
use lru::LruCache;
use std::num::NonZeroUsize;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::sync::RwLock;

/// Default total byte budget for cached S3 object bodies.
pub const DEFAULT_OBJECT_CACHE_MAX_SIZE_BYTES: usize = 512 * 1024 * 1024;
/// Default per-object byte budget for cached S3 object bodies.
pub const DEFAULT_OBJECT_CACHE_MAX_FILE_SIZE_BYTES: usize = 8 * 1024 * 1024;

/// Cache entry with timestamp for TTL
#[derive(Clone)]
struct CacheEntry<T: Clone> {
    value: T,
    timestamp: Instant,
}

/// Inner cache state (generic)
struct CacheInner<T: Clone> {
    cache: LruCache<String, CacheEntry<T>>,
    ttl: Duration,
    enabled: bool,
}

/// Generic TTL-LRU cache
struct TtlLruCache<T: Clone> {
    inner: Arc<RwLock<CacheInner<T>>>,
}

impl<T: Clone> TtlLruCache<T> {
    fn new(max_size: usize, ttl: Duration, enabled: bool) -> Self {
        let max_size = if max_size == 0 { 1000 } else { max_size };
        Self {
            inner: Arc::new(RwLock::new(CacheInner {
                cache: LruCache::new(NonZeroUsize::new(max_size).unwrap()),
                ttl,
                enabled,
            })),
        }
    }

    async fn get(&self, key: &str) -> Option<T> {
        let mut inner = self.inner.write().await;
        if !inner.enabled {
            return None;
        }

        let ttl = inner.ttl;
        let result = inner.cache.get(key).and_then(|entry| {
            if Instant::now().duration_since(entry.timestamp) > ttl {
                None
            } else {
                Some(entry.value.clone())
            }
        });

        match result {
            Some(value) => {
                if let Some(entry) = inner.cache.get_mut(key) {
                    entry.timestamp = Instant::now();
                }
                Some(value)
            }
            None => {
                inner.cache.pop(key);
                None
            }
        }
    }

    async fn put(&self, key: String, value: T) {
        let mut inner = self.inner.write().await;
        if !inner.enabled {
            return;
        }
        inner.cache.put(
            key,
            CacheEntry {
                value,
                timestamp: Instant::now(),
            },
        );
    }

    async fn invalidate(&self, key: &str) {
        let mut inner = self.inner.write().await;
        inner.cache.pop(key);
    }

    async fn invalidate_prefix(&self, prefix: &str) {
        let mut inner = self.inner.write().await;
        if !inner.enabled {
            return;
        }

        let normalized_prefix = if prefix == "/" {
            "/"
        } else {
            prefix.trim_end_matches('/')
        };
        let child_prefix = if normalized_prefix == "/" {
            "/".to_string()
        } else {
            format!("{normalized_prefix}/")
        };
        let to_remove: Vec<String> = inner
            .cache
            .iter()
            .filter(|(k, _)| *k == normalized_prefix || k.starts_with(&child_prefix))
            .map(|(k, _)| k.clone())
            .collect();

        for key in to_remove {
            inner.cache.pop(&key);
        }
    }

    async fn invalidate_parent(&self, path: &str) {
        if path == "/" {
            self.invalidate("/").await;
            return;
        }

        let trimmed = path.trim_end_matches('/');
        if let Some(pos) = trimmed.rfind('/') {
            let parent = if pos == 0 {
                "/".to_string()
            } else {
                trimmed[..pos].to_string()
            };
            self.invalidate(&parent).await;
        }
    }
}

/// Directory listing cache
pub struct S3ListDirCache {
    cache: TtlLruCache<Vec<FileInfo>>,
}

impl S3ListDirCache {
    /// Create a new directory listing cache
    pub fn new(max_size: usize, ttl_seconds: u64, enabled: bool) -> Self {
        Self {
            cache: TtlLruCache::new(
                max_size,
                Duration::from_secs(if ttl_seconds == 0 { 30 } else { ttl_seconds }),
                enabled,
            ),
        }
    }

    /// Get cached listing
    pub async fn get(&self, path: &str) -> Option<Vec<FileInfo>> {
        self.cache.get(path).await
    }

    /// Store listing
    pub async fn put(&self, path: String, files: Vec<FileInfo>) {
        self.cache.put(path, files).await;
    }

    /// Invalidate a specific path
    pub async fn invalidate(&self, path: &str) {
        self.cache.invalidate(path).await;
    }

    /// Invalidate all entries with a prefix
    pub async fn invalidate_prefix(&self, prefix: &str) {
        self.cache.invalidate_prefix(prefix).await;
    }

    /// Invalidate the parent of a path
    pub async fn invalidate_parent(&self, path: &str) {
        self.cache.invalidate_parent(path).await;
    }
}

/// File metadata (stat) cache
pub struct S3StatCache {
    cache: TtlLruCache<Option<FileInfo>>,
}

/// Full-object cache for small S3 reads.  The entry count follows the S3FS
/// cache configuration while configurable byte limits bound process memory.
pub struct S3ObjectCache {
    inner: Arc<RwLock<ObjectCacheInner>>,
    generation: AtomicU64,
    ttl: Duration,
    enabled: bool,
    max_entries: usize,
    max_file_bytes: usize,
    max_total_bytes: usize,
}

struct ObjectCacheInner {
    cache: LruCache<String, CacheEntry<Vec<u8>>>,
    bytes: usize,
}

impl S3ObjectCache {
    /// Create a bounded cache for complete object reads.
    pub fn new(
        max_entries: usize,
        ttl_seconds: u64,
        enabled: bool,
        max_file_bytes: usize,
        max_total_bytes: usize,
    ) -> Self {
        let max_entries = if max_entries == 0 {
            10_000
        } else {
            max_entries
        };
        Self {
            inner: Arc::new(RwLock::new(ObjectCacheInner {
                cache: LruCache::new(NonZeroUsize::new(max_entries).unwrap()),
                bytes: 0,
            })),
            generation: AtomicU64::new(0),
            ttl: Duration::from_secs(if ttl_seconds == 0 { 600 } else { ttl_seconds }),
            enabled,
            max_entries,
            max_file_bytes,
            max_total_bytes,
        }
    }

    /// Return a cached complete object when it has not expired.
    pub async fn get(&self, key: &str) -> Option<Vec<u8>> {
        if !self.enabled {
            return None;
        }
        let mut inner = self.inner.write().await;
        let now = Instant::now();
        let expired = inner
            .cache
            .peek(key)
            .is_some_and(|entry| now.duration_since(entry.timestamp) > self.ttl);
        if expired {
            if let Some(entry) = inner.cache.pop(key) {
                inner.bytes = inner.bytes.saturating_sub(entry.value.len());
            }
            return None;
        }
        let entry = inner.cache.get_mut(key)?;
        entry.timestamp = now;
        Some(entry.value.clone())
    }

    /// Store a complete object when it fits within configured safety budgets.
    pub async fn put(&self, key: String, value: Vec<u8>) {
        if !self.enabled || value.len() > self.max_file_bytes {
            return;
        }
        let mut inner = self.inner.write().await;
        Self::put_locked(
            &mut inner,
            self.max_entries,
            self.max_total_bytes,
            key,
            value,
        );
    }

    /// Capture the cache generation before a backend read starts.
    pub fn generation(&self) -> u64 {
        self.generation.load(Ordering::SeqCst)
    }

    /// Store a backend read only when no write-side invalidation occurred.
    pub async fn put_if_current(&self, generation: u64, key: String, value: Vec<u8>) {
        if !self.enabled || value.len() > self.max_file_bytes {
            return;
        }
        let mut inner = self.inner.write().await;
        if self.generation() == generation {
            Self::put_locked(
                &mut inner,
                self.max_entries,
                self.max_total_bytes,
                key,
                value,
            );
        }
    }

    fn put_locked(
        inner: &mut ObjectCacheInner,
        max_entries: usize,
        max_total_bytes: usize,
        key: String,
        value: Vec<u8>,
    ) {
        let value_len = value.len();
        if let Some(previous) = inner.cache.pop(&key) {
            inner.bytes = inner.bytes.saturating_sub(previous.value.len());
        }
        while (inner.bytes + value_len > max_total_bytes || inner.cache.len() >= max_entries)
            && !inner.cache.is_empty()
        {
            if let Some((_key, entry)) = inner.cache.pop_lru() {
                inner.bytes = inner.bytes.saturating_sub(entry.value.len());
            }
        }
        if inner.bytes + value_len <= max_total_bytes {
            inner.bytes += value_len;
            inner.cache.put(
                key,
                CacheEntry {
                    value,
                    timestamp: Instant::now(),
                },
            );
        }
    }

    /// Invalidate one object.
    pub async fn invalidate(&self, key: &str) {
        self.generation.fetch_add(1, Ordering::SeqCst);
        let mut inner = self.inner.write().await;
        if let Some(entry) = inner.cache.pop(key) {
            inner.bytes = inner.bytes.saturating_sub(entry.value.len());
        }
    }

    /// Invalidate an object subtree.
    pub async fn invalidate_prefix(&self, prefix: &str) {
        self.generation.fetch_add(1, Ordering::SeqCst);
        let normalized = if prefix == "/" {
            "/"
        } else {
            prefix.trim_end_matches('/')
        };
        let child_prefix = if normalized == "/" {
            "/".to_string()
        } else {
            format!("{normalized}/")
        };
        let mut inner = self.inner.write().await;
        let keys: Vec<String> = inner
            .cache
            .iter()
            .filter(|(key, _)| *key == normalized || key.starts_with(&child_prefix))
            .map(|(key, _)| key.clone())
            .collect();
        for key in keys {
            if let Some(entry) = inner.cache.pop(&key) {
                inner.bytes = inner.bytes.saturating_sub(entry.value.len());
            }
        }
    }
}

impl S3StatCache {
    /// Create a new stat cache (5x the capacity of dir cache)
    pub fn new(max_size: usize, ttl_seconds: u64, enabled: bool) -> Self {
        let max_size = if max_size == 0 { 5000 } else { max_size * 5 };
        Self {
            cache: TtlLruCache::new(
                max_size,
                Duration::from_secs(if ttl_seconds == 0 { 60 } else { ttl_seconds }),
                enabled,
            ),
        }
    }

    /// Get cached stat result
    pub async fn get(&self, path: &str) -> Option<Option<FileInfo>> {
        self.cache.get(path).await
    }

    /// Store stat result (None means "does not exist")
    pub async fn put(&self, path: String, info: Option<FileInfo>) {
        self.cache.put(path, info).await;
    }

    /// Invalidate a specific path
    pub async fn invalidate(&self, path: &str) {
        self.cache.invalidate(path).await;
    }

    /// Invalidate all entries with a prefix
    pub async fn invalidate_prefix(&self, prefix: &str) {
        self.cache.invalidate_prefix(prefix).await;
    }

    /// Invalidate the parent of a path
    pub async fn invalidate_parent(&self, path: &str) {
        self.cache.invalidate_parent(path).await;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_list_dir_cache_basic() {
        let cache = S3ListDirCache::new(10, 5, true);

        // Miss
        assert!(cache.get("/test").await.is_none());

        // Put and hit
        let files = vec![FileInfo {
            name: "file.txt".to_string(),
            size: 100,
            mode: 0o644,
            mod_time: std::time::SystemTime::now(),
            is_dir: false,
        }];

        cache.put("/test".to_string(), files.clone()).await;
        let result = cache.get("/test").await;
        assert!(result.is_some());
        assert_eq!(result.unwrap().len(), 1);
    }

    #[tokio::test]
    async fn test_stat_cache_basic() {
        let cache = S3StatCache::new(10, 5, true);

        // Miss
        assert!(cache.get("/test").await.is_none());

        // Put file info
        let info = FileInfo {
            name: "file.txt".to_string(),
            size: 100,
            mode: 0o644,
            mod_time: std::time::SystemTime::now(),
            is_dir: false,
        };

        cache.put("/test".to_string(), Some(info)).await;
        let result = cache.get("/test").await;
        assert!(result.is_some());
        assert!(result.unwrap().is_some());
    }

    #[tokio::test]
    async fn test_stat_cache_negative() {
        let cache = S3StatCache::new(10, 5, true);

        // Cache a "not found" result
        cache.put("/missing".to_string(), None).await;
        let result = cache.get("/missing").await;
        assert!(result.is_some()); // entry exists
        assert!(result.unwrap().is_none()); // but value is None
    }

    #[tokio::test]
    async fn test_cache_invalidation() {
        let cache = S3ListDirCache::new(10, 60, true);

        cache.put("/a".to_string(), vec![]).await;
        cache.put("/a/b".to_string(), vec![]).await;
        cache.put("/c".to_string(), vec![]).await;

        // Invalidate prefix /a
        cache.invalidate_prefix("/a").await;

        assert!(cache.get("/a").await.is_none());
        assert!(cache.get("/a/b").await.is_none());
        assert!(cache.get("/c").await.is_some()); // unaffected
    }

    #[tokio::test]
    async fn test_cache_disabled() {
        let cache = S3ListDirCache::new(10, 5, false);

        cache.put("/test".to_string(), vec![]).await;
        assert!(cache.get("/test").await.is_none());
    }

    #[tokio::test]
    async fn test_root_prefix_invalidation_clears_descendants() {
        let cache = S3ListDirCache::new(10, 60, true);
        cache.put("/".to_string(), vec![]).await;
        cache.put("/a".to_string(), vec![]).await;
        cache.put("/a/b".to_string(), vec![]).await;

        cache.invalidate_prefix("/").await;

        assert!(cache.get("/").await.is_none());
        assert!(cache.get("/a").await.is_none());
        assert!(cache.get("/a/b").await.is_none());
    }

    #[tokio::test]
    async fn test_object_cache_returns_full_object_and_invalidates_prefix() {
        let cache = S3ObjectCache::new(
            10,
            60,
            true,
            DEFAULT_OBJECT_CACHE_MAX_FILE_SIZE_BYTES,
            DEFAULT_OBJECT_CACHE_MAX_SIZE_BYTES,
        );
        cache
            .put("/parent/file.txt".to_string(), b"content".to_vec())
            .await;

        assert_eq!(
            cache.get("/parent/file.txt").await,
            Some(b"content".to_vec())
        );

        cache.invalidate_prefix("/parent").await;
        assert_eq!(cache.get("/parent/file.txt").await, None);
    }

    #[tokio::test]
    async fn test_object_cache_respects_capacity_and_lru_recency() {
        let cache = S3ObjectCache::new(
            2,
            60,
            true,
            DEFAULT_OBJECT_CACHE_MAX_FILE_SIZE_BYTES,
            DEFAULT_OBJECT_CACHE_MAX_SIZE_BYTES,
        );
        cache.put("/one".to_string(), b"one".to_vec()).await;
        cache.put("/two".to_string(), b"two".to_vec()).await;

        assert_eq!(cache.get("/one").await, Some(b"one".to_vec()));

        cache.put("/three".to_string(), b"three".to_vec()).await;

        assert_eq!(cache.get("/one").await, Some(b"one".to_vec()));
        assert_eq!(cache.get("/two").await, None);
        assert_eq!(cache.get("/three").await, Some(b"three".to_vec()));
    }

    #[tokio::test]
    async fn test_object_cache_disabled_or_too_large_never_hits() {
        let disabled = S3ObjectCache::new(
            10,
            60,
            false,
            DEFAULT_OBJECT_CACHE_MAX_FILE_SIZE_BYTES,
            DEFAULT_OBJECT_CACHE_MAX_SIZE_BYTES,
        );
        disabled
            .put("/disabled".to_string(), b"content".to_vec())
            .await;
        assert_eq!(disabled.get("/disabled").await, None);

        let cache = S3ObjectCache::new(
            10,
            60,
            true,
            DEFAULT_OBJECT_CACHE_MAX_FILE_SIZE_BYTES,
            DEFAULT_OBJECT_CACHE_MAX_SIZE_BYTES,
        );
        cache
            .put(
                "/too-large".to_string(),
                vec![0; DEFAULT_OBJECT_CACHE_MAX_FILE_SIZE_BYTES + 1],
            )
            .await;
        assert_eq!(cache.get("/too-large").await, None);
    }

    #[tokio::test]
    async fn test_object_cache_respects_configured_byte_budgets() {
        let cache = S3ObjectCache::new(10, 60, true, 3, 5);

        cache.put("/too-large".to_string(), vec![0; 4]).await;
        assert_eq!(cache.get("/too-large").await, None);

        cache.put("/first".to_string(), vec![0; 3]).await;
        cache.put("/second".to_string(), vec![1; 3]).await;

        assert_eq!(cache.get("/first").await, None);
        assert_eq!(cache.get("/second").await, Some(vec![1; 3]));
    }

    #[tokio::test]
    async fn test_object_cache_does_not_refill_after_invalidation() {
        let cache = S3ObjectCache::new(
            10,
            60,
            true,
            DEFAULT_OBJECT_CACHE_MAX_FILE_SIZE_BYTES,
            DEFAULT_OBJECT_CACHE_MAX_SIZE_BYTES,
        );
        let generation = cache.generation();

        cache.invalidate("/file.txt").await;
        cache
            .put_if_current(generation, "/file.txt".to_string(), b"stale".to_vec())
            .await;

        assert_eq!(cache.get("/file.txt").await, None);
    }

    #[tokio::test]
    async fn test_prefix_invalidation_normalizes_trailing_slash() {
        let cache = S3ListDirCache::new(10, 60, true);
        cache.put("/a".to_string(), vec![]).await;
        cache.put("/a/b".to_string(), vec![]).await;
        cache.put("/ab".to_string(), vec![]).await;

        cache.invalidate_prefix("/a/").await;

        assert!(cache.get("/a").await.is_none());
        assert!(cache.get("/a/b").await.is_none());
        assert!(cache.get("/ab").await.is_some());
    }
}
