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
use std::sync::Arc;
use std::time::SystemTime;

use cache::{S3ListDirCache, S3StatCache};
use client::S3Client;

use crate::core::{
    ConfigParameter, Error, FileInfo, FileSystem, PluginConfig, Result, ServicePlugin, WriteFlag,
};

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
        path.rsplit('/')
            .next()
            .unwrap_or("")
            .to_string()
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
        self.dir_cache
            .put(normalized.clone(), files.clone())
            .await;

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
                self.client
                    .copy_object(&old_dir_key, &new_dir_key)
                    .await?;
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
}
