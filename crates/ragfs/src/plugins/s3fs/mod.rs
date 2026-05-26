//! S3FS - S3-backed File System
//!
//! A file system backed by Amazon S3 or S3-compatible object storage.
//! Supports AWS S3, MinIO, LocalStack, ByteDance TOS, and other
//! S3-compatible services.
//!
//! ## Features
//!
//! - Full POSIX-like file system operations over S3
//! - Directory simulation via prefix/delimiter listing + marker objects
//! - Dual-layer caching (directory listings + stat metadata)
//! - Range-based reads for partial file access
//! - Configurable directory marker modes
//! - Support for custom S3 endpoints

pub mod cache;
pub mod client;

use async_trait::async_trait;
use std::sync::{Arc, Mutex};
use std::time::SystemTime;

use cache::{S3ListDirCache, S3StatCache};
use client::S3Client;
use futures::stream::{self, StreamExt};
use regex::Regex;
use std::sync::atomic::{AtomicUsize, Ordering};

use crate::core::{
    ConfigParameter, Error, FileInfo, FileSystem, GrepMatch, GrepResult, PluginConfig, Result,
    ServicePlugin, WriteFlag,
};

/// Check whether `path` is under `exclude_path` (including itself).
fn s3_is_excluded_path(path: &str, exclude_path: &str) -> bool {
    if exclude_path == "/" {
        return true;
    }

    path == exclude_path
        || path
            .strip_prefix(exclude_path)
            .is_some_and(|suffix| suffix.starts_with('/'))
}

/// Convert an absolute/plugin path to a query-root-relative grep match path.
fn s3_relative_match_file(query_root: &str, path: &str) -> String {
    let base = if query_root == "/" {
        "/".to_string()
    } else {
        query_root.trim_end_matches('/').to_string()
    };

    if path == base {
        return ".".to_string();
    }

    if base == "/" {
        return path.trim_start_matches('/').to_string();
    }

    match path.strip_prefix(&base) {
        Some(rest) => {
            let rel = rest.trim_start_matches('/');
            if rel.is_empty() {
                ".".to_string()
            } else {
                rel.to_string()
            }
        }
        None => path.trim_start_matches('/').to_string(),
    }
}

/// Compute depth from a query-root-relative path.
fn s3_relative_depth(rel: &str) -> usize {
    if rel.is_empty() || rel == "." {
        0
    } else {
        rel.split('/').filter(|p| !p.is_empty()).count()
    }
}

/// Abstract trait for reading chunks from a file during grep.
/// Extracted to allow unit testing chunk-boundary logic with mock providers.
#[async_trait]
trait ChunkReader: Send {
    async fn read_chunk(&mut self, offset: u64, size: u64) -> Result<Vec<u8>>;
}

/// S3-backed chunk reader — delegates to `get_object_range`.
struct S3ChunkReader<'a> {
    client: &'a S3Client,
    key: &'a str,
}

#[async_trait]
impl ChunkReader for S3ChunkReader<'_> {
    async fn read_chunk(&mut self, offset: u64, size: u64) -> Result<Vec<u8>> {
        self.client.get_object_range(self.key, offset, size).await
    }
}

/// Core streaming grep logic: reads chunks via a [`ChunkReader`],
/// handles line-boundary stitching across chunk edges, and returns
/// matches up to `remaining_limit`.
async fn grep_stream(
    rel_file: &str,
    file_size: u64,
    re: &Regex,
    remaining_limit: usize,
    chunk_size: u64,
    max_partial_size: usize,
    reader: &mut dyn ChunkReader,
) -> Result<Vec<GrepMatch>> {
    let mut offset: u64 = 0;
    let mut partial = String::new();
    let mut line_no: u64 = 0;
    let mut matches = Vec::with_capacity(remaining_limit.min(64));

    loop {
        if matches.len() >= remaining_limit || offset >= file_size {
            break;
        }

        let chunk = reader.read_chunk(offset, chunk_size).await?;

        let is_last = chunk.is_empty()
            || chunk.len() < chunk_size as usize
            || offset + chunk_size >= file_size;

        let chunk_str = String::from_utf8_lossy(&chunk);

        let complete_end = if is_last {
            chunk_str.len()
        } else {
            chunk_str.rfind('\n').map(|p| p + 1).unwrap_or(0)
        };

        if complete_end == 0 && !is_last {
            partial.push_str(&chunk_str);
            if partial.len() > max_partial_size {
                return Ok(matches);
            }
            offset += chunk_size;
            continue;
        }

        let (text, remainder) = if partial.is_empty() {
            if complete_end == chunk_str.len() {
                (chunk_str.into_owned(), String::new())
            } else {
                (
                    chunk_str[..complete_end].to_string(),
                    chunk_str[complete_end..].to_string(),
                )
            }
        } else {
            let merged = format!("{}{}", partial, &chunk_str[..complete_end]);
            partial.clear();
            (merged, chunk_str[complete_end..].to_string())
        };

        for line in text.lines() {
            if matches.len() >= remaining_limit {
                break;
            }
            line_no += 1;
            if re.is_match(line) {
                matches.push(GrepMatch {
                    file: rel_file.to_string(),
                    line: line_no,
                    content: line.to_string(),
                });
            }
        }

        partial.push_str(&remainder);

        if is_last {
            break;
        }
        offset += chunk_size;
    }

    if !partial.is_empty() && matches.len() < remaining_limit {
        line_no += 1;
        if re.is_match(&partial) {
            matches.push(GrepMatch {
                file: rel_file.to_string(),
                line: line_no,
                content: partial,
            });
        }
    }

    Ok(matches)
}

/// S3-backed file system
pub struct S3FileSystem {
    client: Arc<S3Client>,
    dir_cache: S3ListDirCache,
    stat_cache: S3StatCache,
}

impl S3FileSystem {
    /// Create a new S3FileSystem
    pub async fn new(config: &PluginConfig) -> Result<Self> {
        let client = S3Client::new(&config.params).await?;

        let cache_enabled = config
            .params
            .get("cache_enabled")
            .and_then(|v| v.as_bool())
            .unwrap_or(true);

        let cache_max_size = config
            .params
            .get("cache_max_size")
            .and_then(|v| v.as_int())
            .unwrap_or(1000) as usize;

        let cache_ttl = config
            .params
            .get("cache_ttl")
            .and_then(|v| v.as_int())
            .unwrap_or(30) as u64;

        let stat_cache_ttl = config
            .params
            .get("stat_cache_ttl")
            .and_then(|v| v.as_int())
            .unwrap_or(60) as u64;

        let dir_cache = S3ListDirCache::new(cache_max_size, cache_ttl, cache_enabled);
        let stat_cache = S3StatCache::new(cache_max_size, stat_cache_ttl, cache_enabled);

        tracing::info!(
            "S3FS initialized: bucket={}, cache={}",
            client.bucket(),
            cache_enabled
        );

        Ok(Self {
            client: Arc::new(client),
            dir_cache,
            stat_cache,
        })
    }

    /// Normalize path to consistent format
    fn normalize_path(path: &str) -> String {
        if path.is_empty() || path == "/" {
            return "/".to_string();
        }

        let mut result = if path.starts_with('/') {
            path.to_string()
        } else {
            format!("/{}", path)
        };

        if result.len() > 1 && result.ends_with('/') {
            result.pop();
        }

        while result.contains("//") {
            result = result.replace("//", "/");
        }

        result
    }

    /// Get file name from path
    fn file_name(path: &str) -> String {
        if path == "/" {
            return "/".to_string();
        }
        path.rsplit('/').next().unwrap_or("").to_string()
    }

    /// Chunk size for streaming file reads during grep (64 KiB).
    const GREP_CHUNK_SIZE: u64 = 65536;

    /// Maximum allowed accumulated partial-line buffer to prevent OOM
    /// when processing files with extremely long lines (16 MiB).
    const GREP_MAX_PARTIAL_SIZE: usize = 16_777_216;

    /// Default concurrency window for parallel grep.
    fn default_grep_concurrency() -> usize {
        std::thread::available_parallelism()
            .map(|n| (n.get() * 2).clamp(16, 100))
            .unwrap_or(16)
    }

    /// Stream a single file in fixed-size chunks, match lines against `re`,
    /// and return matches up to `remaining_limit`.
    async fn grep_one_file(
        &self,
        base_path: &str,
        path: &str,
        file_size: u64,
        re: &Regex,
        remaining_limit: usize,
    ) -> Result<Vec<GrepMatch>> {
        let normalized = Self::normalize_path(path);
        let key = self.client.build_key(&normalized);
        let rel_file = s3_relative_match_file(base_path, path);
        let mut reader = S3ChunkReader {
            client: &self.client,
            key: &key,
        };
        grep_stream(
            &rel_file,
            file_size,
            re,
            remaining_limit,
            Self::GREP_CHUNK_SIZE,
            Self::GREP_MAX_PARTIAL_SIZE,
            &mut reader,
        )
        .await
    }

    /// Parallel chunked grep over a pre-collected list of files with
    /// sliding-window concurrency and lock-free early-termination.
    async fn grep_files_concurrent(
        &self,
        base_path: &str,
        files: Vec<(String, u64)>,
        re: &Regex,
        node_limit: Option<usize>,
    ) -> Result<GrepResult> {
        let limit = node_limit.unwrap_or(usize::MAX);
        let result = Arc::new(Mutex::new(GrepResult::new()));
        let matched_count = Arc::new(AtomicUsize::new(0));
        let buf_size = Self::default_grep_concurrency().min(files.len());

        let mut stream = stream::iter(files)
            .map(|(path, file_size)| {
                let result = Arc::clone(&result);
                let matched_count = Arc::clone(&matched_count);
                async move {
                    let done = matched_count.load(Ordering::Acquire);
                    if done >= limit {
                        return Ok(());
                    }

                    let remaining = limit.saturating_sub(done);
                    let matches = self
                        .grep_one_file(base_path, &path, file_size, re, remaining)
                        .await?;

                    let mut r = result.lock().unwrap();
                    for m in matches {
                        if r.count >= limit {
                            break;
                        }
                        r.matches.push(m);
                        r.count += 1;
                    }
                    matched_count.store(r.count, Ordering::Release);

                    Ok::<_, Error>(())
                }
            })
            .buffer_unordered(buf_size);

        while let Some(item) = stream.next().await {
            item?;
        }

        let final_result = {
            let mut guard = result.lock().unwrap();
            std::mem::take(&mut *guard)
        };

        Ok(final_result)
    }
}

#[async_trait]
impl FileSystem for S3FileSystem {
    async fn create(&self, path: &str) -> Result<()> {
        let normalized = Self::normalize_path(path);
        let key = self.client.build_key(&normalized);

        // Check if already exists
        if self.client.head_object(&key).await?.is_some() {
            return Err(Error::already_exists(&normalized));
        }

        // Create empty file
        self.client.put_object(&key, Vec::new()).await?;

        // Invalidate caches
        self.dir_cache.invalidate_parent(&normalized).await;
        self.stat_cache.invalidate(&normalized).await;

        Ok(())
    }

    async fn mkdir(&self, path: &str, _mode: u32) -> Result<()> {
        let normalized = Self::normalize_path(path);

        // Check if already exists
        if self.client.directory_exists(&normalized).await? {
            return Err(Error::already_exists(&normalized));
        }

        // Create directory marker
        self.client.create_directory_marker(&normalized).await?;

        // Invalidate caches
        self.dir_cache.invalidate_parent(&normalized).await;
        self.stat_cache.invalidate(&normalized).await;

        Ok(())
    }

    async fn remove(&self, path: &str) -> Result<()> {
        let normalized = Self::normalize_path(path);

        if normalized == "/" {
            return Err(Error::invalid_operation("cannot remove root directory"));
        }

        let key = self.client.build_key(&normalized);

        // Check if it's a file
        if let Some(meta) = self.client.head_object(&key).await? {
            if !meta.is_dir_marker {
                // Delete file
                self.client.delete_object(&key).await?;
                self.dir_cache.invalidate_parent(&normalized).await;
                self.stat_cache.invalidate(&normalized).await;
                return Ok(());
            }
        }

        // Check if it's a directory
        if self.client.directory_exists(&normalized).await? {
            // Check if directory is empty
            let dir_prefix = format!("{}/", self.client.build_key(&normalized));
            let listing = self.client.list_objects(&dir_prefix, Some("/")).await?;

            if !listing.files.is_empty() || !listing.directories.is_empty() {
                return Err(Error::DirectoryNotEmpty(normalized));
            }

            // Delete directory marker
            let dir_key = format!("{}/", self.client.build_key(&normalized));
            self.client.delete_object(&dir_key).await?;

            self.dir_cache.invalidate_parent(&normalized).await;
            self.dir_cache.invalidate(&normalized).await;
            self.stat_cache.invalidate(&normalized).await;
            return Ok(());
        }

        Err(Error::not_found(&normalized))
    }

    async fn remove_all(&self, path: &str) -> Result<()> {
        let normalized = Self::normalize_path(path);

        if normalized == "/" {
            // Delete everything under prefix
            self.client.delete_directory("").await?;
            self.dir_cache.invalidate_prefix("/").await;
            self.stat_cache.invalidate_prefix("/").await;
            return Ok(());
        }

        // Delete the file itself (if it exists as a file)
        let key = self.client.build_key(&normalized);
        let _ = self.client.delete_object(&key).await;

        // Delete directory and all children
        self.client.delete_directory(&normalized).await?;

        self.dir_cache.invalidate_parent(&normalized).await;
        self.dir_cache.invalidate_prefix(&normalized).await;
        self.stat_cache.invalidate_prefix(&normalized).await;

        Ok(())
    }

    async fn read(&self, path: &str, offset: u64, size: u64) -> Result<Vec<u8>> {
        let normalized = Self::normalize_path(path);
        let key = self.client.build_key(&normalized);

        // Check if it's a directory
        if key.ends_with('/') || self.client.directory_exists(&normalized).await? {
            // Try to read as file first
            if self.client.head_object(&key).await?.is_none() {
                return Err(Error::IsADirectory(normalized));
            }
        }

        if offset == 0 && size == 0 {
            // Full read
            self.client.get_object(&key).await
        } else {
            // Range read
            self.client.get_object_range(&key, offset, size).await
        }
    }

    async fn write(&self, path: &str, data: &[u8], _offset: u64, _flags: WriteFlag) -> Result<u64> {
        let normalized = Self::normalize_path(path);
        let key = self.client.build_key(&normalized);

        // S3 always replaces the full object
        self.client.put_object(&key, data.to_vec()).await?;

        // Invalidate caches
        self.dir_cache.invalidate_parent(&normalized).await;
        self.stat_cache.invalidate(&normalized).await;

        Ok(data.len() as u64)
    }

    async fn read_dir(&self, path: &str) -> Result<Vec<FileInfo>> {
        let normalized = Self::normalize_path(path);

        // Check cache
        if let Some(files) = self.dir_cache.get(&normalized).await {
            return Ok(files);
        }

        // Build prefix for listing
        let prefix = if normalized == "/" {
            if self.client.build_key("").is_empty() {
                String::new()
            } else {
                self.client.build_key("")
            }
        } else {
            format!("{}/", self.client.build_key(&normalized))
        };

        let listing = self.client.list_objects(&prefix, Some("/")).await?;

        let mut files = Vec::new();

        // Add files
        for obj in &listing.files {
            let rel_path = self.client.strip_prefix(&obj.key);
            let name = rel_path.rsplit('/').next().unwrap_or(&rel_path);

            if name.is_empty() {
                continue;
            }

            files.push(FileInfo {
                name: name.to_string(),
                size: obj.size as u64,
                mode: 0o644,
                mod_time: obj.last_modified,
                is_dir: false,
            });
        }

        // Add directories
        for dir_key in &listing.directories {
            let rel_path = self.client.strip_prefix(dir_key);
            let name = rel_path.rsplit('/').next().unwrap_or(&rel_path);

            if name.is_empty() {
                continue;
            }

            files.push(FileInfo {
                name: name.to_string(),
                size: 0,
                mode: 0o755,
                mod_time: SystemTime::now(),
                is_dir: true,
            });
        }

        // Sort by name
        files.sort_by(|a, b| a.name.cmp(&b.name));

        // Cache
        self.dir_cache.put(normalized.clone(), files.clone()).await;

        Ok(files)
    }

    async fn stat(&self, path: &str) -> Result<FileInfo> {
        let normalized = Self::normalize_path(path);

        // Root always exists
        if normalized == "/" {
            return Ok(FileInfo {
                name: "/".to_string(),
                size: 0,
                mode: 0o755,
                mod_time: SystemTime::now(),
                is_dir: true,
            });
        }

        // Check stat cache
        if let Some(cached) = self.stat_cache.get(&normalized).await {
            return cached.ok_or_else(|| Error::not_found(&normalized));
        }

        let key = self.client.build_key(&normalized);

        // Check if it's a file
        if let Some(meta) = self.client.head_object(&key).await? {
            if !meta.is_dir_marker {
                let info = FileInfo {
                    name: Self::file_name(&normalized),
                    size: meta.size as u64,
                    mode: 0o644,
                    mod_time: meta.last_modified,
                    is_dir: false,
                };
                self.stat_cache
                    .put(normalized.clone(), Some(info.clone()))
                    .await;
                return Ok(info);
            }
        }

        // Check if it's a directory
        if self.client.directory_exists(&normalized).await? {
            let info = FileInfo {
                name: Self::file_name(&normalized),
                size: 0,
                mode: 0o755,
                mod_time: SystemTime::now(),
                is_dir: true,
            };
            self.stat_cache
                .put(normalized.clone(), Some(info.clone()))
                .await;
            return Ok(info);
        }

        // Not found
        self.stat_cache.put(normalized.clone(), None).await;
        Err(Error::not_found(&normalized))
    }

    async fn rename(&self, old_path: &str, new_path: &str) -> Result<()> {
        let old_normalized = Self::normalize_path(old_path);
        let new_normalized = Self::normalize_path(new_path);

        if old_normalized == "/" || new_normalized == "/" {
            return Err(Error::invalid_operation("cannot rename root directory"));
        }

        let old_key = self.client.build_key(&old_normalized);

        // Check if old path exists as a file
        if let Some(meta) = self.client.head_object(&old_key).await? {
            if !meta.is_dir_marker {
                // File rename: copy + delete
                let new_key = self.client.build_key(&new_normalized);
                self.client.copy_object(&old_key, &new_key).await?;
                self.client.delete_object(&old_key).await?;

                self.dir_cache.invalidate_parent(&old_normalized).await;
                self.dir_cache.invalidate_parent(&new_normalized).await;
                self.stat_cache.invalidate(&old_normalized).await;
                self.stat_cache.invalidate(&new_normalized).await;

                return Ok(());
            }
        }

        // Directory rename: copy all children + delete originals
        if self.client.directory_exists(&old_normalized).await? {
            let old_prefix = format!("{}/", self.client.build_key(&old_normalized));
            let new_prefix_base = self.client.build_key(&new_normalized);

            // List all objects under old prefix
            let listing = self.client.list_objects(&old_prefix, None).await?;

            // Copy directory marker
            let old_dir_key = format!("{}/", self.client.build_key(&old_normalized));
            let new_dir_key = format!("{}/", new_prefix_base);

            if self.client.head_object(&old_dir_key).await?.is_some() {
                self.client.copy_object(&old_dir_key, &new_dir_key).await?;
            }

            // Copy all children
            for obj in &listing.files {
                let relative = obj.key.strip_prefix(&old_prefix).unwrap_or(&obj.key);
                let new_key = format!("{}/{}", new_prefix_base, relative);
                self.client.copy_object(&obj.key, &new_key).await?;
            }

            // Delete old directory
            self.client.delete_directory(&old_normalized).await?;

            // Also delete the old directory marker
            let _ = self.client.delete_object(&old_dir_key).await;

            // Invalidate caches
            self.dir_cache.invalidate_prefix(&old_normalized).await;
            self.dir_cache.invalidate_parent(&old_normalized).await;
            self.dir_cache.invalidate_parent(&new_normalized).await;
            self.stat_cache.invalidate_prefix(&old_normalized).await;
            self.stat_cache.invalidate_prefix(&new_normalized).await;

            return Ok(());
        }

        Err(Error::not_found(&old_normalized))
    }

    async fn chmod(&self, _path: &str, _mode: u32) -> Result<()> {
        // S3 doesn't support Unix permissions - no-op
        Ok(())
    }

    async fn ensure_parent_dirs(&self, _path: &str, _mode: u32) -> Result<()> {
        // S3 doesn't require directories to exist - no-op
        Ok(())
    }

    async fn truncate(&self, path: &str, size: u64) -> Result<()> {
        let normalized = Self::normalize_path(path);
        let key = self.client.build_key(&normalized);

        // Read current data
        let mut data = self.client.get_object(&key).await?;

        // Truncate
        data.resize(size as usize, 0);

        // Write back
        self.client.put_object(&key, data).await?;

        self.stat_cache.invalidate(&normalized).await;

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
        let normalized = Self::normalize_path(path);
        let normalized_exclude = exclude_path.map(|p| Self::normalize_path(p));

        let info = self.stat(&normalized).await?;

        let re = if case_insensitive {
            Regex::new(&format!("(?i){}", pattern))
        } else {
            Regex::new(pattern)
        }
        .map_err(|e| Error::invalid_operation(format!("Invalid regex: {}", e)))?;

        if !info.is_dir {
            if let Some(ref excl) = normalized_exclude {
                if s3_is_excluded_path(&normalized, excl) {
                    return Ok(GrepResult::new());
                }
            }
            if let Some(limit) = level_limit {
                let rel = s3_relative_match_file(&normalized, &normalized);
                if s3_relative_depth(&rel) > limit {
                    return Ok(GrepResult::new());
                }
            }
            let files = vec![(normalized.clone(), info.size)];
            return self
                .grep_files_concurrent(&normalized, files, &re, node_limit)
                .await;
        }

        let prefix = if normalized == "/" {
            self.client.build_key("")
        } else {
            format!("{}/", self.client.build_key(&normalized))
        };

        let listing = self.client.list_objects(&prefix, None).await?;

        let mut files: Vec<(String, u64)> = Vec::new();

        for obj in &listing.files {
            if obj.is_dir_marker || obj.key.ends_with('/') {
                continue;
            }

            let fs_path = format!("/{}", self.client.strip_prefix(&obj.key));

            if let Some(ref excl) = normalized_exclude {
                if s3_is_excluded_path(&fs_path, excl) {
                    continue;
                }
            }

            if !recursive || level_limit.is_some() {
                let rel = s3_relative_match_file(&normalized, &fs_path);
                let depth = s3_relative_depth(&rel);

                if !recursive && depth > 1 {
                    continue;
                }
                if let Some(limit) = level_limit {
                    if depth > limit {
                        continue;
                    }
                }
            }

            files.push((fs_path, obj.size as u64));
        }

        if files.is_empty() {
            return Ok(GrepResult::new());
        }

        self.grep_files_concurrent(&normalized, files, &re, node_limit)
            .await
    }
}

/// S3FS Plugin
pub struct S3FSPlugin {
    config_params: Vec<ConfigParameter>,
}

impl S3FSPlugin {
    /// Create a new S3FSPlugin
    pub fn new() -> Self {
        Self {
            config_params: vec![
                ConfigParameter::required_string("bucket", "S3 bucket name"),
                ConfigParameter::optional(
                    "region",
                    "string",
                    "us-east-1",
                    "AWS region",
                ),
                ConfigParameter::optional(
                    "endpoint",
                    "string",
                    "",
                    "Custom S3 endpoint (for MinIO, LocalStack, TOS)",
                ),
                ConfigParameter::optional(
                    "access_key_id",
                    "string",
                    "",
                    "AWS access key ID (falls back to AWS_ACCESS_KEY_ID env)",
                ),
                ConfigParameter::optional(
                    "secret_access_key",
                    "string",
                    "",
                    "AWS secret access key (falls back to AWS_SECRET_ACCESS_KEY env)",
                ),
                ConfigParameter::optional(
                    "use_path_style",
                    "bool",
                    "true",
                    "Use path-style addressing (bucket/key vs bucket.host/key)",
                ),
                ConfigParameter::optional(
                    "prefix",
                    "string",
                    "",
                    "Key prefix for namespace isolation (e.g. 'agfs/')",
                ),
                ConfigParameter::optional(
                    "normalize_encoding_chars",
                    "string",
                    "?#%+@",
                    "Characters to escape in S3 object keys as !HH hexadecimal bytes; empty string disables normalization",
                ),
                ConfigParameter::optional(
                    "directory_marker_mode",
                    "string",
                    "empty",
                    "Directory marker mode: none, empty, nonempty",
                ),
                ConfigParameter::optional(
                    "disable_batch_delete",
                    "bool",
                    "false",
                    "Disable batch delete (DeleteObjects) for S3-compatible services like OSS",
                ),
                ConfigParameter::optional(
                    "cache_enabled",
                    "bool",
                    "true",
                    "Enable caching",
                ),
                ConfigParameter::optional(
                    "cache_max_size",
                    "int",
                    "1000",
                    "Maximum cache entries",
                ),
                ConfigParameter::optional(
                    "cache_ttl",
                    "int",
                    "30",
                    "Directory listing cache TTL in seconds",
                ),
                ConfigParameter::optional(
                    "stat_cache_ttl",
                    "int",
                    "60",
                    "Stat cache TTL in seconds",
                ),
            ],
        }
    }
}

impl Default for S3FSPlugin {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl ServicePlugin for S3FSPlugin {
    fn name(&self) -> &str {
        "s3fs"
    }

    fn version(&self) -> &str {
        "0.1.0"
    }

    fn description(&self) -> &str {
        "S3-backed file system (AWS S3, MinIO, LocalStack, TOS)"
    }

    fn readme(&self) -> &str {
        r#"# S3FS - S3-backed File System

A file system backed by Amazon S3 or S3-compatible object storage.

## Features

- Full POSIX-like file system operations over S3
- Supports AWS S3, MinIO, LocalStack, ByteDance TOS
- Directory simulation via prefix/delimiter + marker objects
- Dual-layer caching (directory listings + stat metadata)
- Range-based reads for partial file access
- Configurable directory marker modes
- Optional configurable key normalization for selected characters

## Configuration

### AWS S3
```yaml
plugins:
  s3fs:
    enabled: true
    path: /s3
    config:
      bucket: my-bucket
      region: us-east-1
```

### MinIO (Local Testing)
```yaml
plugins:
  s3fs:
    enabled: true
    path: /s3
    config:
      bucket: test-bucket
      endpoint: http://localhost:9000
      access_key_id: minioadmin
      secret_access_key: minioadmin
      use_path_style: true
```

### ByteDance TOS
```yaml
plugins:
  s3fs:
    enabled: true
    path: /s3
    config:
      bucket: my-tos-bucket
      region: cn-beijing
      endpoint: https://tos-cn-beijing.volces.com
      use_path_style: false
      directory_marker_mode: nonempty
      normalize_encoding_chars: "?#%+@"
```

### Alibaba Cloud OSS
```yaml
plugins:
  s3fs:
    enabled: true
    path: /s3
    config:
      bucket: my-oss-bucket
      region: cn-beijing
      endpoint: http://s3.oss-cn-beijing.aliyuncs.com
      disable_batch_delete: true
```

## Directory Marker Modes

- `empty` (default): Zero-byte marker objects for directories
- `nonempty`: Single-byte marker (for TOS and services that reject zero-byte objects)
- `none`: No markers, pure prefix-based directory detection

## Key Normalization

- `normalize_encoding_chars: "?#%+@"` (default): escape only `?`, `#`, `%`, `+`, and `@` as `!HH`
- `normalize_encoding_chars: ""`: keep original path segments in object keys
- Characters not listed in `normalize_encoding_chars`, including Chinese and other Unicode characters, remain unchanged

## Notes

- S3 does not support partial/offset writes (always full object replacement)
- chmod is a no-op (S3 has no Unix permissions)
- Rename is implemented as copy + delete
"#
    }

    async fn validate(&self, config: &PluginConfig) -> Result<()> {
        // bucket is required
        if config
            .params
            .get("bucket")
            .and_then(|v| v.as_string())
            .is_none()
        {
            return Err(Error::config("'bucket' is required for S3FS"));
        }

        // Validate directory_marker_mode if provided
        if let Some(mode) = config
            .params
            .get("directory_marker_mode")
            .and_then(|v| v.as_string())
        {
            if !["none", "empty", "nonempty"].contains(&mode) {
                return Err(Error::config(format!(
                    "invalid directory_marker_mode: {} (valid: none, empty, nonempty)",
                    mode
                )));
            }
        }

        if let Some(value) = config.params.get("normalize_encoding_chars") {
            if value.as_string().is_none() {
                return Err(Error::config(
                    "invalid normalize_encoding_chars: expected string",
                ));
            }
        }

        Ok(())
    }

    async fn initialize(&self, config: PluginConfig) -> Result<Box<dyn FileSystem>> {
        let fs = S3FileSystem::new(&config).await?;
        Ok(Box::new(fs))
    }

    fn config_params(&self) -> &[ConfigParameter] {
        &self.config_params
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_normalize_path() {
        assert_eq!(S3FileSystem::normalize_path(""), "/");
        assert_eq!(S3FileSystem::normalize_path("/"), "/");
        assert_eq!(S3FileSystem::normalize_path("/foo"), "/foo");
        assert_eq!(S3FileSystem::normalize_path("/foo/"), "/foo");
        assert_eq!(S3FileSystem::normalize_path("foo"), "/foo");
        assert_eq!(S3FileSystem::normalize_path("/foo//bar"), "/foo/bar");
    }

    #[test]
    fn test_file_name() {
        assert_eq!(S3FileSystem::file_name("/"), "/");
        assert_eq!(S3FileSystem::file_name("/foo.txt"), "foo.txt");
        assert_eq!(S3FileSystem::file_name("/dir/file.txt"), "file.txt");
    }

    #[tokio::test]
    async fn test_plugin_validate() {
        let plugin = S3FSPlugin::new();

        // Missing bucket should fail
        let config = PluginConfig {
            name: "s3fs".to_string(),
            mount_path: "/s3".to_string(),
            params: std::collections::HashMap::new(),
        };
        assert!(plugin.validate(&config).await.is_err());

        // With bucket should pass
        let mut params = std::collections::HashMap::new();
        params.insert(
            "bucket".to_string(),
            crate::core::ConfigValue::String("test-bucket".to_string()),
        );
        let config = PluginConfig {
            name: "s3fs".to_string(),
            mount_path: "/s3".to_string(),
            params,
        };
        assert!(plugin.validate(&config).await.is_ok());
    }

    #[tokio::test]
    async fn test_plugin_validate_marker_mode() {
        let plugin = S3FSPlugin::new();

        // Invalid marker mode
        let mut params = std::collections::HashMap::new();
        params.insert(
            "bucket".to_string(),
            crate::core::ConfigValue::String("test".to_string()),
        );
        params.insert(
            "directory_marker_mode".to_string(),
            crate::core::ConfigValue::String("invalid".to_string()),
        );
        let config = PluginConfig {
            name: "s3fs".to_string(),
            mount_path: "/s3".to_string(),
            params,
        };
        assert!(plugin.validate(&config).await.is_err());

        // Valid marker mode
        let mut params = std::collections::HashMap::new();
        params.insert(
            "bucket".to_string(),
            crate::core::ConfigValue::String("test".to_string()),
        );
        params.insert(
            "directory_marker_mode".to_string(),
            crate::core::ConfigValue::String("nonempty".to_string()),
        );
        let config = PluginConfig {
            name: "s3fs".to_string(),
            mount_path: "/s3".to_string(),
            params,
        };
        assert!(plugin.validate(&config).await.is_ok());
    }

    #[tokio::test]
    async fn test_plugin_validate_normalize_encoding_chars() {
        let plugin = S3FSPlugin::new();

        let mut params = std::collections::HashMap::new();
        params.insert(
            "bucket".to_string(),
            crate::core::ConfigValue::String("test".to_string()),
        );
        params.insert(
            "normalize_encoding_chars".to_string(),
            crate::core::ConfigValue::Bool(true),
        );
        let config = PluginConfig {
            name: "s3fs".to_string(),
            mount_path: "/s3".to_string(),
            params,
        };
        assert!(plugin.validate(&config).await.is_err());

        let mut params = std::collections::HashMap::new();
        params.insert(
            "bucket".to_string(),
            crate::core::ConfigValue::String("test".to_string()),
        );
        params.insert(
            "normalize_encoding_chars".to_string(),
            crate::core::ConfigValue::String("?#%+@".to_string()),
        );
        let config = PluginConfig {
            name: "s3fs".to_string(),
            mount_path: "/s3".to_string(),
            params,
        };
        assert!(plugin.validate(&config).await.is_ok());
    }

    #[test]
    fn test_s3_is_excluded_path() {
        assert!(
            s3_is_excluded_path("/foo", "/"),
            "exclude root excludes everything"
        );
        assert!(s3_is_excluded_path("/a", "/a"), "exclude exact match");
        assert!(s3_is_excluded_path("/a/b", "/a"), "exclude prefix with '/'");
        assert!(s3_is_excluded_path("/a/b/c", "/a"), "exclude nested prefix");

        assert!(
            !s3_is_excluded_path("/ab", "/a"),
            "exclude /a does not exclude /ab"
        );
        assert!(
            !s3_is_excluded_path("/b", "/a"),
            "unrelated path not excluded"
        );
        assert!(s3_is_excluded_path("/a", "/"), "root exclude matches /a");
    }

    #[test]
    fn test_s3_is_excluded_path_root_matches_all() {
        assert!(s3_is_excluded_path("", "/"), "empty string excluded by /");
        assert!(
            s3_is_excluded_path("/anything", "/"),
            "any path excluded by /"
        );
        assert!(
            s3_is_excluded_path("/deep/nested/path", "/"),
            "deep path excluded by /"
        );
    }

    #[test]
    fn test_s3_relative_match_file_same_path() {
        assert_eq!(s3_relative_match_file("/foo", "/foo"), ".");
        assert_eq!(s3_relative_match_file("/", "/"), ".");
    }

    #[test]
    fn test_s3_relative_match_file_root_base() {
        assert_eq!(s3_relative_match_file("/", "/a"), "a");
        assert_eq!(s3_relative_match_file("/", "/a/b"), "a/b");
        assert_eq!(
            s3_relative_match_file("/", "/deep/nested/file"),
            "deep/nested/file"
        );
    }

    #[test]
    fn test_s3_relative_match_file_subdirectory() {
        assert_eq!(s3_relative_match_file("/a", "/a/b"), "b");
        assert_eq!(s3_relative_match_file("/a", "/a/b/c"), "b/c");
        assert_eq!(s3_relative_match_file("/dir", "/dir/file.txt"), "file.txt");
    }

    #[test]
    fn test_s3_relative_match_file_no_prefix_match() {
        assert_eq!(s3_relative_match_file("/a", "/b"), "b");
        assert_eq!(s3_relative_match_file("/x", "/y/z"), "y/z");
    }

    #[test]
    fn test_s3_relative_match_file_trailing_slash_base() {
        assert_eq!(s3_relative_match_file("/foo/", "/foo/bar"), "bar");
        assert_eq!(s3_relative_match_file("/foo/", "/foo"), ".");
    }

    #[test]
    fn test_s3_relative_match_file_empty_remainder() {
        assert_eq!(s3_relative_match_file("/dir", "/dir/"), ".");
    }

    #[test]
    fn test_s3_relative_match_file_leading_slash_in_rest() {
        assert_eq!(s3_relative_match_file("/dir", "/dir//sub"), "sub");
    }

    #[test]
    fn test_s3_relative_depth() {
        assert_eq!(s3_relative_depth("."), 0);
        assert_eq!(s3_relative_depth(""), 0);
        assert_eq!(s3_relative_depth("a"), 1);
        assert_eq!(s3_relative_depth("a/b"), 2);
        assert_eq!(s3_relative_depth("a/b/c"), 3);
        assert_eq!(s3_relative_depth("deep/nested/path/to/file"), 5);
    }

    #[test]
    fn test_s3_relative_depth_filters_empty_segments() {
        assert_eq!(s3_relative_depth("a//b"), 2);
        assert_eq!(s3_relative_depth("///"), 0);
    }

    #[test]
    fn test_default_grep_concurrency() {
        let n = S3FileSystem::default_grep_concurrency();
        assert!(n >= 16, "concurrency should be at least 16, got {n}");
        assert!(n <= 100, "concurrency should be at most 100, got {n}");
    }

    // ── Mock ChunkReader for testing grep_stream chunk-boundary logic ──

    /// A mock chunk reader that serves slices from an in-memory byte buffer.
    /// Returns the range `[offset, offset+size)` clamped to `data.len()`.
    struct MockChunkReader {
        data: Vec<u8>,
    }

    #[async_trait]
    impl ChunkReader for MockChunkReader {
        async fn read_chunk(&mut self, offset: u64, size: u64) -> Result<Vec<u8>> {
            let start = offset as usize;
            if start >= self.data.len() {
                return Ok(Vec::new());
            }
            let end = ((offset + size) as usize).min(self.data.len());
            Ok(self.data[start..end].to_vec())
        }
    }

    fn build_re(pattern: &str) -> Regex {
        Regex::new(pattern).unwrap()
    }

    // ── Case  3: 正则无效 ──

    #[test]
    fn test_grep_invalid_regex() {
        assert!(
            Regex::new("(unclosed").is_err(),
            "unclosed group should fail"
        );
        assert!(
            Regex::new("[invalid").is_err(),
            "unclosed character class should fail"
        );
    }

    // ── Case 17: case_insensitive 正则构建 ──

    #[test]
    fn test_case_insensitive_regex() {
        let re = Regex::new(&format!("(?i){}", "hello")).unwrap();
        assert!(re.is_match("HELLO"), "uppercase match");
        assert!(re.is_match("hello"), "lowercase match");
        assert!(re.is_match("Hello"), "mixed case match");
        assert!(!re.is_match("world"), "non-match");

        let re2 = Regex::new(&format!("(?i){}", "WORLD")).unwrap();
        assert!(re2.is_match("world"), "uppercase pattern matches lowercase");
    }

    // ── Case  9: 文件大小恰好为 CHUNK_SIZE（单 chunk，is_last 靠 offset+size>=file_size） ──

    #[tokio::test]
    async fn test_grep_stream_exact_chunk_single() {
        let data = b"hello\nworld\n";
        let file_size = data.len() as u64;
        let chunk_size = file_size;
        let mut reader = MockChunkReader {
            data: data.to_vec(),
        };
        let re = build_re(".");

        let matches = grep_stream("f", file_size, &re, 100, chunk_size, 1024, &mut reader)
            .await
            .unwrap();

        assert_eq!(matches.len(), 2, "both lines should match '.'");
        assert_eq!(matches[0].line, 1);
        assert_eq!(matches[0].content, "hello");
        assert_eq!(matches[1].line, 2);
        assert_eq!(matches[1].content, "world");
    }

    // ── Case 10: 最后 chunk 恰好等于 CHUNK_SIZE（多 chunk 场景） ──

    #[tokio::test]
    async fn test_grep_stream_last_chunk_exact_size() {
        let data = b"line1\nline2\nline3\nline4\n";
        let file_size = data.len() as u64; // 24
        let chunk_size = 12u64;
        let mut reader = MockChunkReader {
            data: data.to_vec(),
        };
        let re = build_re("line");

        let matches = grep_stream("f", file_size, &re, 100, chunk_size, 1024, &mut reader)
            .await
            .unwrap();

        assert_eq!(matches.len(), 4, "all 4 lines should match");
        assert_eq!(matches[3].content, "line4");
    }

    // ── Case 11: 跨 chunk 边界行的拼接 ──

    #[tokio::test]
    async fn test_grep_stream_cross_chunk_line() {
        let data = b"hel\nlo wo\nrld\n";
        let file_size = data.len() as u64; // 15
        let chunk_size = 8u64;
        let mut reader = MockChunkReader {
            data: data.to_vec(),
        };
        let re = build_re("lo");

        let matches = grep_stream("f", file_size, &re, 100, chunk_size, 1024, &mut reader)
            .await
            .unwrap();

        assert_eq!(matches.len(), 1, "only 'lo wo' contains 'lo'");
        assert_eq!(matches[0].line, 2, "line number should be 2");
        assert_eq!(matches[0].content, "lo wo", "stitched across boundary");
    }

    // ── Case 12: 连续多 chunk 无换行符 ──

    #[tokio::test]
    async fn test_grep_stream_multi_chunk_no_newline() {
        let data = b"abcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*()"; // 46 bytes, no \n
        let file_size = data.len() as u64;
        let chunk_size = 10u64;
        let mut reader = MockChunkReader {
            data: data.to_vec(),
        };
        let re = build_re("xyz");

        let matches = grep_stream("f", file_size, &re, 100, chunk_size, 1024, &mut reader)
            .await
            .unwrap();

        assert_eq!(matches.len(), 1, "one long line should match");
        assert_eq!(matches[0].line, 1);
        assert!(
            matches[0].content.contains("xyz"),
            "line contains xyz: {}",
            matches[0].content
        );
    }

    // ── Case 13: 超长行 > GREP_MAX_PARTIAL_SIZE ──

    #[tokio::test]
    async fn test_grep_stream_line_exceeds_max_partial() {
        let content = "a".repeat(200);
        let data = content.as_bytes();
        let file_size = data.len() as u64; // 200
        let chunk_size = 64u64;
        let max_partial = 100usize;
        let mut reader = MockChunkReader {
            data: data.to_vec(),
        };
        let re = build_re("a");

        let matches = grep_stream(
            "f",
            file_size,
            &re,
            100,
            chunk_size,
            max_partial,
            &mut reader,
        )
        .await
        .unwrap();

        assert!(
            matches.is_empty(),
            "should bail out early when partial exceeds max"
        );
    }

    // ── Case 14: 二进制内容（含无效 UTF-8） ──

    #[tokio::test]
    async fn test_grep_stream_binary_content() {
        let mut data: Vec<u8> = b"hello\nworld\n".to_vec();
        data.extend_from_slice(&[0xff, 0xfe, 0xfd]);
        data.extend_from_slice(b"\nvalid\n");
        let file_size = data.len() as u64;
        let chunk_size = 64u64;
        let mut reader = MockChunkReader { data };
        let re = build_re("world");

        let matches = grep_stream("f", file_size, &re, 100, chunk_size, 1024, &mut reader)
            .await
            .unwrap();

        assert_eq!(
            matches.len(),
            1,
            "should match 'world' despite binary content"
        );
        assert_eq!(matches[0].content, "world");
        assert_eq!(matches[0].line, 2);
    }

    #[tokio::test]
    async fn test_grep_stream_binary_content_valid_line_after() {
        let mut data: Vec<u8> = b"first\n".to_vec();
        data.extend_from_slice(&[0xff, 0xfe, 0xfd, b'\n']);
        data.extend_from_slice(b"last\n");
        let file_size = data.len() as u64;
        let chunk_size = 64u64;
        let mut reader = MockChunkReader { data };
        let re = build_re("last");

        let matches = grep_stream("f", file_size, &re, 100, chunk_size, 1024, &mut reader)
            .await
            .unwrap();

        assert_eq!(matches.len(), 1, "should match 'last' after binary line");
        assert_eq!(matches[0].content, "last");
    }
}
