//! A transparent [`FileSystem`](crate::FileSystem) cache wrapper.

use super::envelope::{CacheEnvelope, CacheObjectKind, GenerationSnapshot};
use super::{
    CacheError, CacheMetrics, CachePolicy, CacheProvider, CacheResult, CacheTraversalMode,
};
use crate::core::filesystem::{
    compile_grep_regex, is_excluded_path, normalize_prefix_path, relative_depth,
    relative_match_file,
};
use crate::core::{
    FileInfo, FileSystem, GrepMatch, GrepResult, MultiWriteWrappedFS, Result, TreeEntry, WriteFlag,
};
use async_trait::async_trait;
use bytes::Bytes;
use futures::stream::{self, StreamExt};
use regex::Regex;
use std::collections::HashMap;
use std::sync::Arc;
use std::time::Instant;
use tokio::sync::{Mutex, RwLock};
use uuid::Uuid;

const GREP_CACHE_FILE_CONCURRENCY: usize = 8;

/// Namespace prepended to every provider key owned by one wrapper.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CacheNamespace {
    value: String,
}

impl CacheNamespace {
    /// Create a namespace from a mount-, account-, or application-specific value.
    pub fn new(value: impl Into<String>) -> Self {
        Self {
            value: value.into(),
        }
    }

    /// Return the namespace text.
    pub fn as_str(&self) -> &str {
        &self.value
    }
}

/// A provider-independent read-through filesystem cache.
///
/// Construction is explicit. RAGFS does not install this wrapper in the
/// default mount path, so existing filesystem behavior remains unchanged.
pub struct CachedFileSystem {
    backend: Box<dyn FileSystem>,
    provider: Arc<dyn CacheProvider>,
    namespace: CacheNamespace,
    policy: CachePolicy,
    metrics: Arc<CacheMetrics>,
    operation_lock: RwLock<()>,
    inflight: Mutex<HashMap<String, Arc<Mutex<()>>>>,
    bypass_scopes: RwLock<Vec<String>>,
    generation_epoch: u64,
    generations: RwLock<HashMap<String, u64>>,
}

impl CachedFileSystem {
    /// Wrap an existing backend with a cache provider.
    pub fn new(
        backend: Box<dyn FileSystem>,
        provider: Arc<dyn CacheProvider>,
        namespace: CacheNamespace,
        policy: CachePolicy,
    ) -> Self {
        Self {
            backend,
            provider,
            namespace,
            policy,
            metrics: Arc::new(CacheMetrics::default()),
            operation_lock: RwLock::new(()),
            inflight: Mutex::new(HashMap::new()),
            bypass_scopes: RwLock::new(Vec::new()),
            generation_epoch: nonzero_random_u64(),
            generations: RwLock::new(HashMap::new()),
        }
    }

    /// Return this wrapper's cache metrics.
    pub fn metrics(&self) -> Arc<CacheMetrics> {
        Arc::clone(&self.metrics)
    }

    /// Return the provider used by this wrapper.
    pub fn provider(&self) -> Arc<dyn CacheProvider> {
        Arc::clone(&self.provider)
    }

    /// Return the wrapped filesystem for mount-stack capability discovery.
    pub(crate) fn inner_fs(&self) -> &dyn FileSystem {
        self.backend.as_ref()
    }

    fn wraps_multiwrite(&self) -> bool {
        let any = self.backend.as_ref() as &dyn std::any::Any;
        any.downcast_ref::<MultiWriteWrappedFS>().is_some()
    }

    /// Invalidate cache objects affected by a write that bypassed this wrapper.
    pub(crate) async fn invalidate_external_write(&self, path: &str) {
        self.invalidate_path_objects(path).await;
        self.invalidate_parent_directory(path).await;
    }

    async fn tree_directory_via_cache(
        &self,
        path: &str,
        show_hidden: bool,
        node_limit: Option<usize>,
        level_limit: Option<usize>,
    ) -> Result<Vec<TreeEntry>> {
        enum TreeTask {
            VisitDir(String),
            Emit(TreeEntry),
        }

        let base_path = normalize_prefix_path(path);
        let mut result = Vec::new();
        let mut stack = vec![TreeTask::VisitDir(base_path.clone())];

        while let Some(task) = stack.pop() {
            if node_limit.is_some_and(|limit| result.len() >= limit) {
                break;
            }

            match task {
                TreeTask::Emit(entry) => result.push(entry),
                TreeTask::VisitDir(current_path) => {
                    if let Some(limit) = level_limit {
                        let current_rel = relative_match_file(&base_path, &current_path);
                        if relative_depth(&current_rel) >= limit {
                            continue;
                        }
                    }

                    let entries = self.read_dir(&current_path).await?;
                    for entry in entries.into_iter().rev() {
                        let is_hidden_file = !entry.is_dir && entry.name.starts_with('.');
                        if is_hidden_file && !show_hidden {
                            continue;
                        }

                        let entry_path = if current_path == "/" {
                            format!("/{}", entry.name)
                        } else {
                            format!("{}/{}", current_path, entry.name)
                        };
                        let rel_path = relative_match_file(&base_path, &entry_path);
                        let is_dir = entry.is_dir;
                        let tree_entry = TreeEntry {
                            path: entry_path.clone(),
                            rel_path,
                            info: entry,
                            extra: HashMap::new(),
                        };

                        if is_dir {
                            stack.push(TreeTask::VisitDir(entry_path));
                        }
                        stack.push(TreeTask::Emit(tree_entry));
                    }
                }
            }
        }

        Ok(result)
    }

    async fn grep_via_cache(
        &self,
        path: &str,
        pattern: &str,
        recursive: bool,
        case_insensitive: bool,
        node_limit: Option<usize>,
        exclude_path: Option<&str>,
        level_limit: Option<usize>,
    ) -> Result<GrepResult> {
        enum GrepTask {
            Visit { path: String, is_dir: Option<bool> },
        }

        let re = compile_grep_regex(pattern, case_insensitive)?;
        let base_path = normalize_prefix_path(path);
        let normalized_exclude = exclude_path.map(normalize_prefix_path);
        let mut result = GrepResult::new();
        let generation_cache = Mutex::new(HashMap::new());
        let mut file_batch = Vec::new();
        let mut stack = vec![GrepTask::Visit {
            path: base_path.clone(),
            is_dir: None,
        }];

        while let Some(GrepTask::Visit {
            path: current_path,
            is_dir,
        }) = stack.pop()
        {
            if node_limit.is_some_and(|limit| result.count >= limit) {
                break;
            }

            if let Some(exclude) = normalized_exclude.as_deref() {
                if is_excluded_path(&current_path, exclude) {
                    continue;
                }
            }

            let is_dir = match is_dir {
                Some(is_dir) => is_dir,
                None => self.stat(&current_path).await?.is_dir,
            };
            if is_dir {
                self.flush_grep_file_batch(
                    &mut file_batch,
                    &base_path,
                    &re,
                    node_limit,
                    &mut result,
                    &generation_cache,
                )
                .await?;
                if node_limit.is_some_and(|limit| result.count >= limit) {
                    break;
                }

                if !recursive && current_path != base_path {
                    continue;
                }

                if let Some(limit) = level_limit {
                    let rel = relative_match_file(&base_path, &current_path);
                    if relative_depth(&rel) >= limit {
                        continue;
                    }
                }

                let entries = self
                    .read_dir_with_generation_cache(&current_path, &generation_cache)
                    .await?;
                for entry in entries.into_iter().rev() {
                    let entry_path = if current_path == "/" {
                        format!("/{}", entry.name)
                    } else {
                        format!("{}/{}", current_path, entry.name)
                    };
                    stack.push(GrepTask::Visit {
                        path: entry_path,
                        is_dir: Some(entry.is_dir),
                    });
                }
            } else {
                if let Some(limit) = level_limit {
                    let rel = relative_match_file(&base_path, &current_path);
                    if relative_depth(&rel) > limit {
                        continue;
                    }
                }

                file_batch.push(current_path);
                if file_batch.len() >= GREP_CACHE_FILE_CONCURRENCY {
                    self.flush_grep_file_batch(
                        &mut file_batch,
                        &base_path,
                        &re,
                        node_limit,
                        &mut result,
                        &generation_cache,
                    )
                    .await?;
                }
            }
        }

        self.flush_grep_file_batch(
            &mut file_batch,
            &base_path,
            &re,
            node_limit,
            &mut result,
            &generation_cache,
        )
        .await?;

        Ok(result)
    }

    async fn flush_grep_file_batch(
        &self,
        file_batch: &mut Vec<String>,
        base_path: &str,
        re: &Regex,
        node_limit: Option<usize>,
        result: &mut GrepResult,
        generation_cache: &Mutex<HashMap<String, u64>>,
    ) -> Result<()> {
        if file_batch.is_empty() || node_limit.is_some_and(|limit| result.count >= limit) {
            file_batch.clear();
            return Ok(());
        }

        let remaining_limit = node_limit
            .map(|limit| limit.saturating_sub(result.count))
            .unwrap_or(usize::MAX);
        let files = std::mem::take(file_batch);
        let mut indexed = stream::iter(files.into_iter().enumerate())
            .map(|(index, path)| async move {
                let matches = self
                    .grep_cached_file(&path, base_path, re, remaining_limit, generation_cache)
                    .await;
                (index, matches)
            })
            .buffer_unordered(GREP_CACHE_FILE_CONCURRENCY)
            .collect::<Vec<_>>()
            .await;

        indexed.sort_by_key(|(index, _)| *index);
        for (_, matches) in indexed {
            for item in matches? {
                if node_limit.is_some_and(|limit| result.count >= limit) {
                    return Ok(());
                }
                result.matches.push(item);
                result.count += 1;
            }
        }

        Ok(())
    }

    async fn grep_cached_file(
        &self,
        path: &str,
        base_path: &str,
        re: &Regex,
        remaining_limit: usize,
        generation_cache: &Mutex<HashMap<String, u64>>,
    ) -> Result<Vec<GrepMatch>> {
        let content = self
            .read_with_generation_cache(path, 0, 0, generation_cache)
            .await?;
        let content_str = String::from_utf8_lossy(&content);
        let rel_file = relative_match_file(base_path, path);
        let mut matches = Vec::new();

        for (line_num, line) in content_str.lines().enumerate() {
            if matches.len() >= remaining_limit {
                break;
            }
            if re.is_match(line) {
                matches.push(GrepMatch {
                    file: rel_file.clone(),
                    line: (line_num + 1) as u64,
                    content: line.to_string(),
                });
            }
        }

        Ok(matches)
    }

    async fn cache_get(&self, key: &str) -> CacheResult<Option<Bytes>> {
        let started = Instant::now();
        let result = self.provider.get(key).await;
        self.metrics.get(started.elapsed());
        result
    }

    async fn cache_batch_get(&self, keys: &[String]) -> CacheResult<Vec<Option<Bytes>>> {
        if keys.is_empty() {
            return Ok(Vec::new());
        }

        if keys.len() == 1 || !self.provider.capabilities().batch_get {
            let mut values = Vec::with_capacity(keys.len());
            for key in keys {
                values.push(self.cache_get(key).await?);
            }
            return Ok(values);
        }

        let started = Instant::now();
        let result = self.provider.batch_get(keys).await;
        self.metrics.get(started.elapsed());
        let values = result?;
        if values.len() != keys.len() {
            return Err(CacheError::InvalidData(format!(
                "batch_get returned {} values for {} keys",
                values.len(),
                keys.len()
            )));
        }
        Ok(values)
    }

    async fn cache_put(&self, key: &str, value: Bytes, affected_path: &str) -> bool {
        let started = Instant::now();
        let result = self.provider.put(key, value).await;
        self.metrics.put(started.elapsed());
        match result {
            Ok(()) => true,
            Err(_) => {
                self.metrics.error();
                self.mark_bypass(affected_path).await;
                false
            }
        }
    }

    async fn cache_delete(&self, key: &str, affected_path: &str) {
        let started = Instant::now();
        let result = self.provider.delete(key).await;
        self.metrics.delete(started.elapsed());
        match result {
            Ok(()) => self.metrics.invalidation(),
            Err(_) => {
                self.metrics.error();
                self.mark_bypass(affected_path).await;
            }
        }
    }

    async fn mark_bypass(&self, path: &str) {
        let normalized = normalize_path(path);
        let mut scopes = self.bypass_scopes.write().await;
        if scopes
            .iter()
            .any(|scope| is_same_or_descendant(&normalized, scope))
        {
            return;
        }
        scopes.retain(|scope| !is_same_or_descendant(scope, &normalized));
        scopes.push(normalized);
    }

    async fn is_runtime_bypassed(&self, path: &str) -> bool {
        let normalized = normalize_path(path);
        self.bypass_scopes
            .read()
            .await
            .iter()
            .any(|scope| is_same_or_descendant(&normalized, scope))
    }

    async fn current_generation(&self, key: &str) -> CacheResult<u64> {
        let values = self.current_generations(&[key.to_string()]).await?;
        Ok(values[0])
    }

    async fn current_generations(&self, keys: &[String]) -> CacheResult<Vec<u64>> {
        let provider_values = self.cache_batch_get(keys).await?;
        let local_generations = self.generations.read().await;
        let mut values = Vec::with_capacity(keys.len());
        let mut missing = Vec::new();

        for (key, provider_value) in keys.iter().zip(provider_values) {
            let value = match provider_value {
                None => {
                    let value = local_generations
                        .get(key)
                        .copied()
                        .unwrap_or(self.generation_epoch);
                    missing.push((key.clone(), value));
                    value
                }
                Some(value) if value.len() == std::mem::size_of::<u64>() => {
                    let mut bytes = [0_u8; 8];
                    bytes.copy_from_slice(&value);
                    u64::from_be_bytes(bytes)
                }
                Some(_) => {
                    return Err(CacheError::InvalidData(format!(
                        "generation key {key} has invalid length"
                    )))
                }
            };
            values.push(value);
        }
        drop(local_generations);

        {
            let mut local_generations = self.generations.write().await;
            for (key, value) in keys.iter().zip(values.iter()) {
                local_generations.insert(key.clone(), *value);
            }
        }

        self.put_missing_generations(missing).await;

        Ok(values)
    }

    async fn current_generations_memoized(
        &self,
        keys: &[String],
        generation_cache: &Mutex<HashMap<String, u64>>,
    ) -> CacheResult<Vec<u64>> {
        let mut values = vec![None; keys.len()];
        let mut missing_keys = Vec::new();
        let mut missing_positions = Vec::new();

        {
            let generation_cache = generation_cache.lock().await;
            for (index, key) in keys.iter().enumerate() {
                if let Some(value) = generation_cache.get(key) {
                    values[index] = Some(*value);
                } else {
                    missing_positions.push(index);
                    missing_keys.push(key.clone());
                }
            }
        }

        let missing_values = self.current_generations(&missing_keys).await?;
        if !missing_values.is_empty() {
            let mut generation_cache = generation_cache.lock().await;
            for (index, value) in missing_positions.into_iter().zip(missing_values) {
                generation_cache.insert(keys[index].clone(), value);
                values[index] = Some(value);
            }
        }

        values
            .into_iter()
            .map(|value| {
                value.ok_or_else(|| CacheError::Internal("missing generation value".to_string()))
            })
            .collect()
    }

    async fn put_missing_generations(&self, missing: Vec<(String, u64)>) {
        if missing.is_empty() {
            return;
        }

        if missing.len() > 1 && self.provider.capabilities().batch_put {
            let entries = missing
                .into_iter()
                .map(|(key, value)| (key, Bytes::copy_from_slice(&value.to_be_bytes())))
                .collect();
            let started = Instant::now();
            if self.provider.batch_put(entries).await.is_err() {
                self.metrics.error();
            }
            self.metrics.put(started.elapsed());
            return;
        }

        for (key, value) in missing {
            let started = Instant::now();
            if self
                .provider
                .put(&key, Bytes::copy_from_slice(&value.to_be_bytes()))
                .await
                .is_err()
            {
                self.metrics.error();
            }
            self.metrics.put(started.elapsed());
        }
    }

    async fn generation_snapshots(&self, path: &str) -> CacheResult<Vec<GenerationSnapshot>> {
        let keys = ancestor_scopes(path)
            .into_iter()
            .map(|scope| self.generation_key(&scope))
            .collect::<Vec<_>>();
        let values = self.current_generations(&keys).await?;
        Ok(keys
            .into_iter()
            .zip(values)
            .map(|(key, value)| GenerationSnapshot { key, value })
            .collect())
    }

    async fn generations_match_with_cache(
        &self,
        envelope: &CacheEnvelope,
        generation_cache: Option<&Mutex<HashMap<String, u64>>>,
    ) -> CacheResult<bool> {
        let keys = envelope
            .generations()
            .iter()
            .map(|snapshot| snapshot.key.clone())
            .collect::<Vec<_>>();
        let values = match generation_cache {
            Some(generation_cache) => {
                self.current_generations_memoized(&keys, generation_cache)
                    .await?
            }
            None => self.current_generations(&keys).await?,
        };

        for (snapshot, value) in envelope.generations().iter().zip(values) {
            if value != snapshot.value {
                return Ok(false);
            }
        }
        Ok(true)
    }

    async fn bump_generation(&self, path: &str) {
        let key = self.generation_key(path);
        let current = match self.current_generation(&key).await {
            Ok(value) => value,
            Err(_) => {
                self.metrics.error();
                self.mark_bypass(path).await;
                return;
            }
        };
        let next = current.wrapping_add(1);
        self.generations.write().await.insert(key.clone(), next);
        if self
            .cache_put(&key, Bytes::copy_from_slice(&next.to_be_bytes()), path)
            .await
        {
            self.metrics.invalidation();
        }
    }

    async fn probe_file(&self, key: &str, path: &str, record_hit: bool) -> Option<Vec<u8>> {
        self.probe_file_with_generation_cache(key, path, record_hit, None)
            .await
    }

    async fn probe_file_with_generation_cache(
        &self,
        key: &str,
        path: &str,
        record_hit: bool,
        generation_cache: Option<&Mutex<HashMap<String, u64>>>,
    ) -> Option<Vec<u8>> {
        let value = match self.cache_get(key).await {
            Ok(value) => value?,
            Err(_) => {
                self.metrics.error();
                self.mark_bypass(path).await;
                return None;
            }
        };

        let envelope = match CacheEnvelope::decode(&value) {
            Ok(envelope) if envelope.matches(CacheObjectKind::File, path) => envelope,
            _ => {
                self.metrics.error();
                self.cache_delete(key, path).await;
                return None;
            }
        };

        match self
            .generations_match_with_cache(&envelope, generation_cache)
            .await
        {
            Ok(true) => match envelope.into_file() {
                Ok(data) => {
                    if record_hit {
                        self.metrics.file_hit(data.len());
                    }
                    Some(data)
                }
                Err(_) => {
                    self.metrics.error();
                    self.cache_delete(key, path).await;
                    None
                }
            },
            Ok(false) => {
                self.cache_delete(key, path).await;
                None
            }
            Err(_) => {
                self.metrics.error();
                self.mark_bypass(path).await;
                None
            }
        }
    }

    async fn probe_directory(
        &self,
        key: &str,
        path: &str,
        record_hit: bool,
    ) -> Option<Vec<FileInfo>> {
        self.probe_directory_with_generation_cache(key, path, record_hit, None)
            .await
    }

    async fn probe_directory_with_generation_cache(
        &self,
        key: &str,
        path: &str,
        record_hit: bool,
        generation_cache: Option<&Mutex<HashMap<String, u64>>>,
    ) -> Option<Vec<FileInfo>> {
        let value = match self.cache_get(key).await {
            Ok(value) => value?,
            Err(_) => {
                self.metrics.error();
                self.mark_bypass(path).await;
                return None;
            }
        };

        let envelope = match CacheEnvelope::decode(&value) {
            Ok(envelope) if envelope.matches(CacheObjectKind::Directory, path) => envelope,
            _ => {
                self.metrics.error();
                self.cache_delete(key, path).await;
                return None;
            }
        };

        match self
            .generations_match_with_cache(&envelope, generation_cache)
            .await
        {
            Ok(true) => match envelope.into_directory() {
                Ok(entries) => {
                    if !self.policy.cache_directory_entries(path, entries.len()) {
                        self.cache_delete(key, path).await;
                        return None;
                    }
                    if record_hit {
                        self.metrics.read_dir_hit();
                    }
                    Some(entries)
                }
                Err(_) => {
                    self.metrics.error();
                    self.cache_delete(key, path).await;
                    None
                }
            },
            Ok(false) => {
                self.cache_delete(key, path).await;
                None
            }
            Err(_) => {
                self.metrics.error();
                self.mark_bypass(path).await;
                None
            }
        }
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

    async fn read_with_generation_cache(
        &self,
        path: &str,
        offset: u64,
        size: u64,
        generation_cache: &Mutex<HashMap<String, u64>>,
    ) -> Result<Vec<u8>> {
        if offset != 0
            || size != 0
            || !self.policy.cache_file(path, 0)
            || self.is_runtime_bypassed(path).await
        {
            self.metrics.policy_bypass();
            return self.backend.read(path, offset, size).await;
        }

        let _operation_guard = self.operation_lock.read().await;
        if self.is_runtime_bypassed(path).await {
            self.metrics.policy_bypass();
            return self.backend.read(path, offset, size).await;
        }

        let normalized = normalize_path(path);
        let key = self.file_key(&normalized);
        if let Some(data) = self
            .probe_file_with_generation_cache(&key, &normalized, true, Some(generation_cache))
            .await
        {
            return Ok(data);
        }
        self.metrics.file_miss();

        let (inflight, leader) = self.acquire_inflight(&key).await;
        if leader {
            self.metrics.inflight_leader();
        } else {
            self.metrics.inflight_follower();
        }
        let inflight_guard = inflight.lock().await;

        if !leader {
            if let Some(data) = self
                .probe_file_with_generation_cache(&key, &normalized, false, Some(generation_cache))
                .await
            {
                self.metrics.inflight_backend_saved();
                drop(inflight_guard);
                self.release_inflight(&key, &inflight).await;
                return Ok(data);
            }
        }

        let data = self.backend.read(path, 0, 0).await;
        if let Ok(value) = &data {
            self.metrics.backend_fallback(value.len());
            self.fill_file(&key, &normalized, value).await;
        }
        drop(inflight_guard);
        self.release_inflight(&key, &inflight).await;
        data
    }

    async fn read_dir_with_generation_cache(
        &self,
        path: &str,
        generation_cache: &Mutex<HashMap<String, u64>>,
    ) -> Result<Vec<FileInfo>> {
        if !self.policy.cache_directory(path) || self.is_runtime_bypassed(path).await {
            self.metrics.policy_bypass();
            return self.backend.read_dir(path).await;
        }

        let _operation_guard = self.operation_lock.read().await;
        if self.is_runtime_bypassed(path).await {
            self.metrics.policy_bypass();
            return self.backend.read_dir(path).await;
        }

        let normalized = normalize_path(path);
        let key = self.directory_key(&normalized);
        if let Some(entries) = self
            .probe_directory_with_generation_cache(&key, &normalized, true, Some(generation_cache))
            .await
        {
            return Ok(entries);
        }
        self.metrics.read_dir_miss();

        let (inflight, leader) = self.acquire_inflight(&key).await;
        if leader {
            self.metrics.inflight_leader();
        } else {
            self.metrics.inflight_follower();
        }
        let inflight_guard = inflight.lock().await;

        if !leader {
            if let Some(entries) = self
                .probe_directory_with_generation_cache(
                    &key,
                    &normalized,
                    false,
                    Some(generation_cache),
                )
                .await
            {
                self.metrics.inflight_backend_saved();
                drop(inflight_guard);
                self.release_inflight(&key, &inflight).await;
                return Ok(entries);
            }
        }

        let entries = self.backend.read_dir(path).await;
        if let Ok(value) = &entries {
            self.metrics.backend_fallback(0);
            self.fill_directory(&key, &normalized, value).await;
        }
        drop(inflight_guard);
        self.release_inflight(&key, &inflight).await;
        entries
    }

    async fn fill_file(&self, key: &str, path: &str, data: &[u8]) {
        if !self.policy.cache_file(path, data.len()) {
            return;
        }
        let generations = match self.generation_snapshots(path).await {
            Ok(generations) => generations,
            Err(_) => {
                self.metrics.error();
                return;
            }
        };
        match CacheEnvelope::file(path.to_string(), data.to_vec(), generations).encode() {
            Ok(value) => {
                self.cache_put(key, value, path).await;
            }
            Err(_) => self.metrics.error(),
        }
    }

    async fn fill_directory(&self, key: &str, path: &str, entries: &[FileInfo]) {
        if !self.policy.cache_directory_entries(path, entries.len()) {
            return;
        }
        let generations = match self.generation_snapshots(path).await {
            Ok(generations) => generations,
            Err(_) => {
                self.metrics.error();
                return;
            }
        };
        match CacheEnvelope::directory(path.to_string(), entries.to_vec(), generations).encode() {
            Ok(value) => {
                self.cache_put(key, value, path).await;
            }
            Err(_) => self.metrics.error(),
        }
    }

    async fn invalidate_path_objects(&self, path: &str) {
        self.cache_delete(&self.file_key(path), path).await;
        self.cache_delete(&self.directory_key(path), path).await;
    }

    async fn invalidate_parent_directory(&self, path: &str) {
        let parent = parent_path(path);
        self.cache_delete(&self.directory_key(&parent), &parent)
            .await;
    }

    fn file_key(&self, path: &str) -> String {
        self.object_key("file", path)
    }

    fn directory_key(&self, path: &str) -> String {
        self.object_key("dir", path)
    }

    fn generation_key(&self, path: &str) -> String {
        self.object_key("subtree", path)
    }

    fn object_key(&self, kind: &str, path: &str) -> String {
        let normalized = normalize_path(path);
        format!(
            "ragfs:v1:{}:{}:{:016x}",
            self.namespace.as_str(),
            kind,
            stable_hash(normalized.as_bytes())
        )
    }
}

#[async_trait]
impl FileSystem for CachedFileSystem {
    async fn create(&self, path: &str) -> Result<()> {
        let _guard = self.operation_lock.write().await;
        self.backend.create(path).await?;
        self.invalidate_path_objects(path).await;
        self.invalidate_parent_directory(path).await;
        Ok(())
    }

    async fn mkdir(&self, path: &str, mode: u32) -> Result<()> {
        let _guard = self.operation_lock.write().await;
        self.backend.mkdir(path, mode).await?;
        self.bump_generation(path).await;
        self.cache_delete(&self.directory_key(path), path).await;
        self.invalidate_parent_directory(path).await;
        Ok(())
    }

    async fn remove(&self, path: &str) -> Result<()> {
        let _guard = self.operation_lock.write().await;
        self.backend.remove(path).await?;
        self.bump_generation(path).await;
        self.invalidate_path_objects(path).await;
        self.invalidate_parent_directory(path).await;
        Ok(())
    }

    async fn remove_all(&self, path: &str) -> Result<()> {
        let _guard = self.operation_lock.write().await;
        self.backend.remove_all(path).await?;
        self.bump_generation(path).await;
        self.invalidate_path_objects(path).await;
        self.invalidate_parent_directory(path).await;
        Ok(())
    }

    async fn read(&self, path: &str, offset: u64, size: u64) -> Result<Vec<u8>> {
        if offset != 0
            || size != 0
            || !self.policy.cache_file(path, 0)
            || self.is_runtime_bypassed(path).await
        {
            self.metrics.policy_bypass();
            return self.backend.read(path, offset, size).await;
        }

        let _operation_guard = self.operation_lock.read().await;
        if self.is_runtime_bypassed(path).await {
            self.metrics.policy_bypass();
            return self.backend.read(path, offset, size).await;
        }

        let normalized = normalize_path(path);
        let key = self.file_key(&normalized);
        if let Some(data) = self.probe_file(&key, &normalized, true).await {
            return Ok(data);
        }
        self.metrics.file_miss();

        let (inflight, leader) = self.acquire_inflight(&key).await;
        if leader {
            self.metrics.inflight_leader();
        } else {
            self.metrics.inflight_follower();
        }
        let inflight_guard = inflight.lock().await;

        if !leader {
            if let Some(data) = self.probe_file(&key, &normalized, false).await {
                self.metrics.inflight_backend_saved();
                drop(inflight_guard);
                self.release_inflight(&key, &inflight).await;
                return Ok(data);
            }
        }

        let data = self.backend.read(path, 0, 0).await;
        if let Ok(value) = &data {
            self.metrics.backend_fallback(value.len());
            self.fill_file(&key, &normalized, value).await;
        }
        drop(inflight_guard);
        self.release_inflight(&key, &inflight).await;
        data
    }

    async fn write(&self, path: &str, data: &[u8], offset: u64, flags: WriteFlag) -> Result<u64> {
        let _guard = self.operation_lock.write().await;
        let written = self.backend.write(path, data, offset, flags).await?;
        let normalized = normalize_path(path);
        let key = self.file_key(&normalized);
        self.cache_delete(&key, &normalized).await;
        if offset == 0
            && matches!(flags, WriteFlag::Create | WriteFlag::Truncate)
            && self.policy.cache_file(&normalized, data.len())
            && !self.is_runtime_bypassed(&normalized).await
        {
            self.fill_file(&key, &normalized, data).await;
        }
        self.invalidate_parent_directory(path).await;
        Ok(written)
    }

    async fn read_dir(&self, path: &str) -> Result<Vec<FileInfo>> {
        if !self.policy.cache_directory(path) || self.is_runtime_bypassed(path).await {
            self.metrics.policy_bypass();
            return self.backend.read_dir(path).await;
        }

        let _operation_guard = self.operation_lock.read().await;
        if self.is_runtime_bypassed(path).await {
            self.metrics.policy_bypass();
            return self.backend.read_dir(path).await;
        }

        let normalized = normalize_path(path);
        let key = self.directory_key(&normalized);
        if let Some(entries) = self.probe_directory(&key, &normalized, true).await {
            return Ok(entries);
        }
        self.metrics.read_dir_miss();

        let (inflight, leader) = self.acquire_inflight(&key).await;
        if leader {
            self.metrics.inflight_leader();
        } else {
            self.metrics.inflight_follower();
        }
        let inflight_guard = inflight.lock().await;

        if !leader {
            if let Some(entries) = self.probe_directory(&key, &normalized, false).await {
                self.metrics.inflight_backend_saved();
                drop(inflight_guard);
                self.release_inflight(&key, &inflight).await;
                return Ok(entries);
            }
        }

        let entries = self.backend.read_dir(path).await;
        if let Ok(value) = &entries {
            self.metrics.backend_fallback(0);
            self.fill_directory(&key, &normalized, value).await;
        }
        drop(inflight_guard);
        self.release_inflight(&key, &inflight).await;
        entries
    }

    async fn stat(&self, path: &str) -> Result<FileInfo> {
        self.backend.stat(path).await
    }

    async fn rename(&self, old_path: &str, new_path: &str) -> Result<()> {
        let _guard = self.operation_lock.write().await;
        self.backend.rename(old_path, new_path).await?;
        self.bump_generation(old_path).await;
        self.bump_generation(new_path).await;
        self.invalidate_path_objects(old_path).await;
        self.invalidate_path_objects(new_path).await;
        self.invalidate_parent_directory(old_path).await;
        self.invalidate_parent_directory(new_path).await;
        Ok(())
    }

    async fn chmod(&self, path: &str, mode: u32) -> Result<()> {
        let _guard = self.operation_lock.write().await;
        self.backend.chmod(path, mode).await?;
        self.invalidate_path_objects(path).await;
        self.invalidate_parent_directory(path).await;
        Ok(())
    }

    async fn truncate(&self, path: &str, size: u64) -> Result<()> {
        let _guard = self.operation_lock.write().await;
        self.backend.truncate(path, size).await?;
        self.cache_delete(&self.file_key(path), path).await;
        self.invalidate_parent_directory(path).await;
        Ok(())
    }

    async fn exists(&self, path: &str) -> bool {
        self.backend.exists(path).await
    }

    async fn ensure_parent_dirs(&self, path: &str, mode: u32) -> Result<()> {
        let _guard = self.operation_lock.write().await;
        self.backend.ensure_parent_dirs(path, mode).await?;
        for scope in ancestor_scopes(&parent_path(path)) {
            self.cache_delete(&self.directory_key(&scope), &scope).await;
        }
        Ok(())
    }

    async fn grep(
        &self,
        path: &str,
        pattern: &str,
        recursive: bool,
        case_insensitive: bool,
        node_limit: Option<usize>,
        exclude_path: Option<&str>,
        level_limit: Option<usize>,
    ) -> Result<GrepResult> {
        if self.policy.traversal_mode() == CacheTraversalMode::CachedTraversal
            && !self.wraps_multiwrite()
        {
            return self
                .grep_via_cache(
                    path,
                    pattern,
                    recursive,
                    case_insensitive,
                    node_limit,
                    exclude_path,
                    level_limit,
                )
                .await;
        }

        self.backend
            .grep(
                path,
                pattern,
                recursive,
                case_insensitive,
                node_limit,
                exclude_path,
                level_limit,
            )
            .await
    }

    async fn tree_directory(
        &self,
        path: &str,
        show_hidden: bool,
        node_limit: Option<usize>,
        level_limit: Option<usize>,
    ) -> Result<Vec<TreeEntry>> {
        if self.policy.traversal_mode() == CacheTraversalMode::CachedTraversal
            && !self.wraps_multiwrite()
        {
            return self
                .tree_directory_via_cache(path, show_hidden, node_limit, level_limit)
                .await;
        }

        self.backend
            .tree_directory(path, show_hidden, node_limit, level_limit)
            .await
    }
}

fn normalize_path(path: &str) -> String {
    if path.is_empty() || path == "/" {
        "/".to_string()
    } else {
        format!("/{}", path.trim_matches('/'))
    }
}

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

fn is_same_or_descendant(path: &str, scope: &str) -> bool {
    scope == "/"
        || path == scope
        || path
            .strip_prefix(scope)
            .is_some_and(|suffix| suffix.starts_with('/'))
}

fn stable_hash(bytes: &[u8]) -> u64 {
    let mut hash = 0xcbf29ce484222325_u64;
    for byte in bytes {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(0x100000001b3);
    }
    hash
}

fn nonzero_random_u64() -> u64 {
    let value = Uuid::new_v4().as_u128() as u64;
    if value == 0 {
        1
    } else {
        value
    }
}
