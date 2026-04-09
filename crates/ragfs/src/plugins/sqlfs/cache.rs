//! LRU cache for directory listings
//!
//! This module provides an LRU (Least Recently Used) cache with TTL
//! for directory listings in SQLFS. This significantly improves performance
//! for operations like shell tab completion and repeated directory listings.

use crate::core::types::FileInfo;
use lru::LruCache;
use std::num::NonZeroUsize;
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::sync::RwLock;

/// Cache entry with timestamp for TTL
#[derive(Debug, Clone)]
struct CacheEntry {
    files: Vec<FileInfo>,
    timestamp: Instant,
}

/// LRU cache for directory listings
///
/// This cache provides:
/// - LRU eviction when max capacity is reached
/// - TTL (time-to-live) for each entry
/// - Thread-safe access for concurrent operations
/// - Cache hit/miss statistics
pub struct ListDirCache {
    inner: Arc<RwLock<CacheInner>>,
}

/// Inner cache state
struct CacheInner {
    cache: LruCache<String, CacheEntry>,
    ttl: Duration,
    enabled: bool,
    hit_count: u64,
    miss_count: u64,
}

impl ListDirCache {
    /// Create a new directory listing cache
    ///
    /// # Arguments
    /// * `max_size` - Maximum number of entries to cache (default: 1000)
    /// * `ttl_seconds` - Time-to-live in seconds (default: 5)
    /// * `enabled` - Whether caching is enabled (default: true)
    pub fn new(max_size: usize, ttl_seconds: u64, enabled: bool) -> Self {
        let max_size = if max_size == 0 { 1000 } else { max_size };
        let ttl = if ttl_seconds == 0 {
            Duration::from_secs(5)
        } else {
            Duration::from_secs(ttl_seconds)
        };

        Self {
            inner: Arc::new(RwLock::new(CacheInner {
                cache: LruCache::new(NonZeroUsize::new(max_size).unwrap()),
                ttl,
                enabled,
                hit_count: 0,
                miss_count: 0,
            })),
        }
    }

    /// Get cached directory listing
    ///
    /// Returns None if:
    /// - Cache is disabled
    /// - Path is not in cache
    /// - Entry has expired (TTL)
    pub async fn get(&self, path: &str) -> Option<Vec<FileInfo>> {
        let mut inner = self.inner.write().await;

        if !inner.enabled {
            return None;
        }

        let ttl = inner.ttl;

        // Check if entry exists and is still valid
        let result = inner.cache.get(path).and_then(|entry| {
            if Instant::now().duration_since(entry.timestamp) > ttl {
                None // expired
            } else {
                Some(entry.files.clone())
            }
        });

        match result {
            Some(files) => {
                // Refresh the entry's timestamp
                if let Some(entry) = inner.cache.get_mut(path) {
                    entry.timestamp = Instant::now();
                }
                inner.hit_count += 1;
                Some(files)
            }
            None => {
                // Remove expired entry if it exists
                inner.cache.pop(path);
                inner.miss_count += 1;
                None
            }
        }
    }

    /// Put a directory listing into the cache
    pub async fn put(&self, path: String, files: Vec<FileInfo>) {
        let mut inner = self.inner.write().await;

        if !inner.enabled {
            return;
        }

        let entry = CacheEntry {
            files,
            timestamp: Instant::now(),
        };

        inner.cache.put(path, entry);
    }

    /// Invalidate a specific path from the cache
    pub async fn invalidate(&self, path: &str) {
        let mut inner = self.inner.write().await;

        if !inner.enabled {
            return;
        }

        inner.cache.pop(path);
    }

    /// Invalidate all paths with a given prefix
    ///
    /// This is used when a directory or its children are modified.
    pub async fn invalidate_prefix(&self, prefix: &str) {
        let mut inner = self.inner.write().await;

        if !inner.enabled {
            return;
        }

        // Collect keys to invalidate
        let to_invalidate: Vec<String> = inner
            .cache
            .iter()
            .filter(|(path, _)| {
                *path == prefix || is_descendant(path, prefix)
            })
            .map(|(path, _)| path.clone())
            .collect();

        // Remove all invalidated paths
        for path in to_invalidate {
            inner.cache.pop(&path);
        }
    }

    /// Invalidate the parent directory of a given path
    ///
    /// This is called when a file/directory is created, deleted, or renamed.
    pub async fn invalidate_parent(&self, path: &str) {
        let parent = parent_path(path);
        self.invalidate(&parent).await;
    }

    /// Clear all entries from the cache
    pub async fn clear(&self) {
        let mut inner = self.inner.write().await;

        if !inner.enabled {
            return;
        }

        inner.cache.clear();
    }

    /// Get cache statistics
    pub async fn stats(&self) -> CacheStats {
        let inner = self.inner.read().await;

        CacheStats {
            size: inner.cache.len(),
            hit_count: inner.hit_count,
            miss_count: inner.miss_count,
            enabled: inner.enabled,
        }
    }
}

/// Cache statistics
#[derive(Debug, Clone)]
pub struct CacheStats {
    /// Number of entries in cache
    pub size: usize,

    /// Total cache hits
    pub hit_count: u64,

    /// Total cache misses
    pub miss_count: u64,

    /// Whether cache is enabled
    pub enabled: bool,
}

impl CacheStats {
    /// Calculate hit rate
    pub fn hit_rate(&self) -> f64 {
        let total = self.hit_count + self.miss_count;
        if total == 0 {
            0.0
        } else {
            (self.hit_count as f64) / (total as f64)
        }
    }
}

/// Get parent directory path
fn parent_path(path: &str) -> String {
    if path == "/" {
        return "/".to_string();
    }

    // Remove trailing slash
    let trimmed = path.trim_end_matches('/');
    if trimmed.is_empty() {
        return "/".to_string();
    }

    // Find last slash
    if let Some(pos) = trimmed.rfind('/') {
        if pos == 0 {
            return "/".to_string();
        }
        return trimmed[..pos].to_string();
    }

    "/".to_string()
}

/// Check if a path is a descendant of a parent path
fn is_descendant(path: &str, parent: &str) -> bool {
    // A path is not a descendant of itself
    if path == parent {
        return false;
    }

    // Special case for root: everything is a descendant except root itself
    if parent == "/" {
        return path != "/";
    }

    // Check if path starts with parent + "/"
    if path.len() <= parent.len() {
        return false;
    }

    &path[..parent.len()] == parent && path.as_bytes()[parent.len()] == b'/'
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_cache_basic() {
        let cache = ListDirCache::new(10, 5, true);

        // Put and get
        let files = vec![FileInfo::new_file("test.txt".to_string(), 100, 0o644)];
        cache.put("/test".to_string(), files.clone()).await;

        let retrieved = cache.get("/test").await;
        assert!(retrieved.is_some());
        assert_eq!(retrieved.unwrap().len(), 1);

        // Invalidate
        cache.invalidate("/test").await;
        assert!(cache.get("/test").await.is_none());
    }

    #[tokio::test]
    async fn test_cache_invalidate_prefix() {
        let cache = ListDirCache::new(100, 5, true);

        // Populate cache
        cache.put("/a".to_string(), vec![]).await;
        cache.put("/a/b".to_string(), vec![]).await;
        cache.put("/a/b/c".to_string(), vec![]).await;
        cache.put("/d".to_string(), vec![]).await;

        // Invalidate prefix /a
        cache.invalidate_prefix("/a").await;

        // /a and descendants should be gone
        assert!(cache.get("/a").await.is_none());
        assert!(cache.get("/a/b").await.is_none());
        assert!(cache.get("/a/b/c").await.is_none());

        // /d should still exist
        assert!(cache.get("/d").await.is_some());
    }

    #[tokio::test]
    async fn test_cache_lru() {
        let cache = ListDirCache::new(3, 5, true);

        cache.put("a".to_string(), vec![]).await;
        cache.put("b".to_string(), vec![]).await;
        cache.put("c".to_string(), vec![]).await;

        // Access 'a' to make it most recently used
        cache.get("a").await;

        // Add 'd', should evict 'b' (least recently used)
        cache.put("d".to_string(), vec![]).await;

        assert!(cache.get("a").await.is_some());
        assert!(cache.get("c").await.is_some());
        assert!(cache.get("d").await.is_some());
        assert!(cache.get("b").await.is_none());
    }

    #[test]
    fn test_is_descendant() {
        assert!(!is_descendant("/a", "/a"));
        assert!(is_descendant("/a/b", "/a"));
        assert!(is_descendant("/a/b/c", "/a"));
        assert!(!is_descendant("/ab/c", "/a"));
        assert!(!is_descendant("/b", "/a"));

        // Root special case
        assert!(!is_descendant("/", "/"));
        assert!(is_descendant("/a", "/"));
        assert!(is_descendant("/a/b", "/"));
    }

    #[test]
    fn test_parent_path() {
        assert_eq!(parent_path("/"), "/");
        assert_eq!(parent_path("/file.txt"), "/");
        assert_eq!(parent_path("/dir/"), "/");
        assert_eq!(parent_path("/dir/file.txt"), "/dir");
        assert_eq!(parent_path("/a/b/c/file.txt"), "/a/b/c");
    }
}
