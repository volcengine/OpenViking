//! In-memory request-scoped stat state. No filesystem or provider IO occurs here.
#[cfg(test)]
mod tests {
    use super::*;

    fn miss(cache: &Arc<RequestStatCache>, path: &str) -> RequestStatMiss {
        match cache.begin("mount", path) {
            RequestStatLookup::Miss(ticket) => ticket,
            _ => panic!("expected miss"),
        }
    }

    #[test]
    fn registry_capacity_is_atomic_and_release_is_isolated() {
        let registry = Arc::new(RequestCacheRegistry::new(1));
        let handles: Vec<_> = (0..8)
            .map(|i| {
                let registry = registry.clone();
                std::thread::spawn(move || registry.get_or_create("tenant", &i.to_string()))
            })
            .collect();
        assert_eq!(
            handles
                .into_iter()
                .map(|h| h.join().unwrap())
                .filter(Option::is_some)
                .count(),
            1
        );
        let registry = RequestCacheRegistry::new(2);
        let first = registry.get_or_create("a", "id").unwrap();
        assert!(Arc::ptr_eq(
            &first,
            &registry.get_or_create("a", "id").unwrap()
        ));
        let other = registry.get_or_create("b", "id").unwrap();
        assert!(!Arc::ptr_eq(&first, &other));
        assert!(registry.get_or_create("a", "full").is_none());
        assert!(registry.release("a", "id"));
        assert!(!registry.release("a", "id"));
        assert!(registry.get_or_create("a", "new").is_some());
        assert!(registry.get_or_create("a", "").is_none());
    }

    #[test]
    fn epoch_rejects_concurrent_old_fills_and_unrelated_writes_preserve_it() {
        let cache = Arc::new(RequestStatCache::default());
        let first = miss(&cache, "/dir/file");
        let second = miss(&cache, "/dir/file");
        assert!(!cache.invalidate("mount", &["/other"], &[]));
        assert!(!cache.invalidate("other-mount", &[], &["/"]));
        assert_eq!(cache.state.read().unwrap().epoch, 0);
        assert!(cache.invalidate("mount", &[], &["/dir"]));
        let value = Err(Error::NotFound("/dir/file".into()));
        assert!(!first.complete(&value));
        assert!(!second.complete(&value));
        assert!(miss(&cache, "/dir/file").complete(&value));
        assert!(matches!(
            cache.begin("mount", "/dir/file"),
            RequestStatLookup::Hit(Err(Error::NotFound(_)))
        ));
        assert!(cache.state.read().unwrap().inflight_paths.is_empty());
    }

    #[test]
    fn unrelated_write_preserves_cached_metadata_and_errors_are_not_cached() {
        let cache = Arc::new(RequestStatCache::default());
        let value = Ok(FileInfo::new_file("/file".into(), 3, 0o644));
        assert!(miss(&cache, "/file").complete(&value));
        assert!(!cache.invalidate("mount", &["/other"], &[]));
        assert!(matches!(
            cache.begin("mount", "/file"),
            RequestStatLookup::Hit(Ok(_))
        ));
        assert!(cache.invalidate("mount", &["/file"], &[]));
        assert!(!miss(&cache, "/file").complete(&Err(Error::internal("backend unavailable"))));
        drop(miss(&cache, "/file"));
    }

    #[tokio::test]
    async fn cancelled_miss_unregisters_without_filling() {
        let cache = Arc::new(RequestStatCache::default());
        let ticket = miss(&cache, "/file");
        let task = tokio::spawn(async move {
            let _ticket = ticket;
            std::future::pending::<()>().await;
        });
        task.abort();
        assert!(task.await.unwrap_err().is_cancelled());
        let state = cache.state.read().unwrap();
        assert!(state.inflight_paths.is_empty());
        assert!(state.entries.is_empty());
    }
}

use crate::core::{Error, FileInfo, Result};
use path_clean::PathClean;
use std::collections::HashMap;
use std::sync::{Arc, RwLock};

/// One registry per binding/client; capacity limits registry-owned caches, not active handles.
#[derive(Debug)]
pub struct RequestCacheRegistry {
    max_active_request_caches: usize,
    caches: RwLock<HashMap<(String, String), Arc<RequestStatCache>>>,
}

impl RequestCacheRegistry {
    /// A zero limit disables request-cache creation.
    pub fn new(max_active_request_caches: usize) -> Self {
        Self {
            max_active_request_caches,
            caches: RwLock::new(HashMap::new()),
        }
    }

    /// Reuse a request cache or create it atomically if capacity permits.
    /// Call only with a request's nonempty, unique cache ID; absent IDs bypass L0.
    pub fn get_or_create(&self, account_id: &str, cache_id: &str) -> Option<Arc<RequestStatCache>> {
        if cache_id.is_empty() {
            return None;
        }
        let key = (account_id.to_owned(), cache_id.to_owned());
        let mut caches = self.caches.write().unwrap();
        if let Some(cache) = caches.get(&key) {
            return Some(cache.clone());
        }
        if caches.len() >= self.max_active_request_caches {
            return None;
        }
        let cache = Arc::new(RequestStatCache::default());
        caches.insert(key, cache.clone());
        Some(cache)
    }

    /// Idempotently remove only the registry reference. Existing context handles stay usable.
    /// Released IDs must not be reused: no unbounded tombstone history is retained.
    pub fn release(&self, account_id: &str, cache_id: &str) -> bool {
        self.caches
            .write()
            .unwrap()
            .remove(&(account_id.to_owned(), cache_id.to_owned()))
            .is_some()
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
struct StatKey {
    namespace: String,
    path: String,
}

impl StatKey {
    fn new(namespace: &str, path: &str) -> Self {
        Self {
            namespace: namespace.to_owned(),
            path: normalize_path(path),
        }
    }
}

fn normalize_path(path: &str) -> String {
    std::path::Path::new("/")
        .join(path)
        .clean()
        .to_string_lossy()
        .into_owned()
}

#[derive(Debug, Clone)]
enum StatValue {
    FileInfo(FileInfo),
    NotFound(String),
}

impl StatValue {
    fn result(&self) -> Result<FileInfo> {
        match self {
            Self::FileInfo(info) => Ok(info.clone()),
            Self::NotFound(path) => Err(Error::NotFound(path.clone())),
        }
    }
}

#[derive(Debug, Default)]
struct StatState {
    epoch: u64,
    entries: HashMap<StatKey, (u64, StatValue)>,
    inflight_paths: HashMap<StatKey, usize>,
}

impl StatState {
    fn hit(&self, key: &StatKey) -> Option<Result<FileInfo>> {
        self.entries
            .get(key)
            .filter(|(epoch, _)| *epoch == self.epoch)
            .map(|(_, value)| value.result())
    }

    fn unregister(&mut self, key: &StatKey) {
        if let Some(count) = self.inflight_paths.get_mut(key) {
            *count -= 1;
            if *count == 0 {
                self.inflight_paths.remove(key);
            }
        }
    }
}

/// Stat results shared by the operations of one request, across mount namespaces.
#[derive(Debug, Default)]
pub struct RequestStatCache {
    state: RwLock<StatState>,
}

/// A cached stat result or a cancellation-safe registration for a backend read.
#[derive(Debug)]
pub enum RequestStatLookup {
    /// File metadata or an explicit NotFound recorded in the current epoch.
    Hit(Result<FileInfo>),
    /// An inflight registration to complete after backend IO, or drop on cancellation.
    Miss(RequestStatMiss),
}

impl RequestStatCache {
    /// Read a current hit, or register a miss before the caller starts backend IO.
    /// Concurrent misses are counted independently, not coalesced.
    pub fn begin(self: &Arc<Self>, namespace: &str, path: &str) -> RequestStatLookup {
        let key = StatKey::new(namespace, path);
        if let Some(value) = self.state.read().unwrap().hit(&key) {
            return RequestStatLookup::Hit(value);
        }
        let mut state = self.state.write().unwrap();
        // Recheck under the same lock that registers the miss and observes the epoch.
        if let Some(value) = state.hit(&key) {
            return RequestStatLookup::Hit(value);
        }
        *state.inflight_paths.entry(key.clone()).or_default() += 1;
        RequestStatLookup::Miss(RequestStatMiss {
            cache: self.clone(),
            key,
            observed_epoch: state.epoch,
            registered: true,
        })
    }

    /// Advance the single global epoch only if exact/subtree scopes intersect a
    /// current entry or inflight path in this namespace. Old entries remain allocated.
    /// Callers supply operation-specific paths, including parents where applicable.
    pub fn invalidate(
        &self,
        namespace: &str,
        exact_paths: &[&str],
        subtree_paths: &[&str],
    ) -> bool {
        let exact: Vec<_> = exact_paths
            .iter()
            .map(|p| StatKey::new(namespace, p))
            .collect();
        let subtrees: Vec<_> = subtree_paths.iter().map(|p| normalize_path(p)).collect();
        let mut state = self.state.write().unwrap();
        let exact_hit = exact.iter().any(|key| {
            state
                .entries
                .get(key)
                .is_some_and(|(epoch, _)| *epoch == state.epoch)
                || state.inflight_paths.contains_key(key)
        });
        let in_subtree = |key: &StatKey| {
            key.namespace == namespace
                && subtrees.iter().any(|root| {
                    root == "/"
                        || key.path == *root
                        || key
                            .path
                            .strip_prefix(root.as_str())
                            .is_some_and(|tail| tail.starts_with('/'))
                })
        };
        let subtree_hit = !subtrees.is_empty()
            && (state
                .entries
                .iter()
                .any(|(key, (epoch, _))| *epoch == state.epoch && in_subtree(key))
                || state.inflight_paths.keys().any(in_subtree));
        if exact_hit || subtree_hit {
            state.epoch = state
                .epoch
                .checked_add(1)
                .expect("request stat epoch exhausted");
            true
        } else {
            false
        }
    }
}

/// Owns an inflight registration without holding a lock across backend IO.
/// Dropping it (including future cancellation) unregisters without caching a result.
#[derive(Debug)]
#[must_use = "complete the backend result or drop the ticket to unregister the miss"]
pub struct RequestStatMiss {
    cache: Arc<RequestStatCache>,
    key: StatKey,
    observed_epoch: u64,
    registered: bool,
}

impl RequestStatMiss {
    /// Unregister and cache only FileInfo/NotFound if the observed epoch is still current.
    /// Returns whether the result was stored; the caller returns its backend result regardless.
    pub fn complete(mut self, result: &Result<FileInfo>) -> bool {
        let value = match result {
            Ok(info) => Some(StatValue::FileInfo(info.clone())),
            Err(Error::NotFound(path)) => Some(StatValue::NotFound(path.clone())),
            Err(_) => None,
        };
        let mut state = self.cache.state.write().unwrap();
        state.unregister(&self.key);
        self.registered = false;
        if state.epoch == self.observed_epoch {
            if let Some(value) = value {
                state
                    .entries
                    .insert(self.key.clone(), (self.observed_epoch, value));
                return true;
            }
        }
        false
    }
}

impl Drop for RequestStatMiss {
    fn drop(&mut self) {
        if self.registered {
            self.cache
                .state
                .write()
                .unwrap_or_else(|poisoned| poisoned.into_inner())
                .unregister(&self.key);
        }
    }
}
