//! Provider-independent cache eligibility rules.

const DEFAULT_MAX_CACHED_DIR_ENTRIES: usize = 4096;

/// Cache admission decision for a filesystem object.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CacheDecision {
    /// Do not access the cache for this object.
    Bypass,
    /// Cache the object with normal priority.
    Cache,
    /// Prefer this high-value object when a provider supports admission priority.
    Prefer,
}

impl CacheDecision {
    fn should_cache(self) -> bool {
        self != Self::Bypass
    }
}

/// Strategy used by [`super::CachedFileSystem`] for recursive traversal APIs.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CacheTraversalMode {
    /// Delegate traversal APIs to the wrapped backend.
    Backend,
    /// Traverse through `CachedFileSystem` so directory and file caches can be reused.
    CachedTraversal,
}

/// Backwards-compatible alias for the earlier tree-only traversal name.
pub type CacheTreeMode = CacheTraversalMode;

/// Rules used by [`super::CachedFileSystem`] before accessing the cache.
#[derive(Debug, Clone)]
pub struct CachePolicy {
    max_file_size: usize,
    max_cached_dir_entries: usize,
    traversal_mode: CacheTraversalMode,
    bypass_prefixes: Vec<String>,
}

impl CachePolicy {
    /// Create a policy with the supplied maximum full-file cache size.
    pub fn new(max_file_size: usize) -> Self {
        Self {
            max_file_size,
            max_cached_dir_entries: DEFAULT_MAX_CACHED_DIR_ENTRIES,
            traversal_mode: CacheTraversalMode::Backend,
            bypass_prefixes: Vec::new(),
        }
    }

    /// Add a path prefix that always bypasses the cache.
    pub fn with_bypass_prefix(mut self, prefix: impl Into<String>) -> Self {
        self.bypass_prefixes.push(normalize_path(&prefix.into()));
        self
    }

    /// Set the traversal strategy for recursive APIs such as tree and grep.
    pub fn with_traversal_mode(mut self, mode: CacheTraversalMode) -> Self {
        self.traversal_mode = mode;
        self
    }

    /// Set the tree traversal strategy.
    pub fn with_tree_mode(self, mode: CacheTreeMode) -> Self {
        self.with_traversal_mode(mode)
    }

    /// Return the maximum cacheable file size.
    pub fn max_file_size(&self) -> usize {
        self.max_file_size
    }

    /// Return the maximum cacheable raw directory entry count.
    pub fn max_cached_dir_entries(&self) -> usize {
        self.max_cached_dir_entries
    }

    /// Return the traversal strategy for recursive APIs such as tree and grep.
    pub fn traversal_mode(&self) -> CacheTraversalMode {
        self.traversal_mode
    }

    /// Return the tree traversal strategy.
    pub fn tree_mode(&self) -> CacheTreeMode {
        self.traversal_mode()
    }

    /// Return the admission decision for a full-file object.
    pub fn file_decision(&self, path: &str, size: usize) -> CacheDecision {
        if size > self.max_file_size || !self.cache_path(path) {
            return CacheDecision::Bypass;
        }

        match normalize_path(path).rsplit('/').next().unwrap_or("") {
            ".abstract.md" | ".overview.md" => CacheDecision::Prefer,
            _ => CacheDecision::Cache,
        }
    }

    /// Return the admission decision for raw directory entries.
    pub fn directory_decision(&self, path: &str) -> CacheDecision {
        if self.cache_path(path) {
            CacheDecision::Prefer
        } else {
            CacheDecision::Bypass
        }
    }

    /// Return whether a full-file object is eligible for caching.
    pub fn cache_file(&self, path: &str, size: usize) -> bool {
        self.file_decision(path, size).should_cache()
    }

    /// Return whether raw directory entries are eligible for caching.
    pub fn cache_directory(&self, path: &str) -> bool {
        self.directory_decision(path).should_cache()
    }

    /// Return whether raw directory entries are small enough to cache.
    pub fn cache_directory_entries(&self, path: &str, entry_count: usize) -> bool {
        self.cache_directory(path) && entry_count <= self.max_cached_dir_entries
    }

    fn cache_path(&self, path: &str) -> bool {
        let normalized = normalize_path(path);
        if self
            .bypass_prefixes
            .iter()
            .any(|prefix| is_same_or_descendant(&normalized, prefix))
        {
            return false;
        }

        let name = normalized.rsplit('/').next().unwrap_or("");
        if name == ".path.ovlock"
            || name.ends_with(".lock")
            || name.ends_with(".lck")
            || matches!(
                name,
                "enqueue"
                    | "dequeue"
                    | "peek"
                    | "ack"
                    | "heartbeat"
                    | "lease"
                    | "cursor"
                    | "offset"
                    | "pid"
            )
        {
            return false;
        }

        true
    }
}

impl Default for CachePolicy {
    fn default() -> Self {
        Self::new(1024 * 1024)
    }
}

fn normalize_path(path: &str) -> String {
    if path.is_empty() || path == "/" {
        "/".to_string()
    } else {
        format!("/{}", path.trim_matches('/'))
    }
}

fn is_same_or_descendant(path: &str, prefix: &str) -> bool {
    path == prefix
        || prefix == "/"
        || path
            .strip_prefix(prefix)
            .is_some_and(|suffix| suffix.starts_with('/'))
}
