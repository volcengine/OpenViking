//! SQLFS - Database-backed File System
//!
//! This module provides a persistent file system implementation backed by
//! SQLite or MySQL/TiDB. Features include:
//!
//! - Persistent storage (survives server restarts)
//! - ACID transactions
//! - LRU cache for directory listings
//! - Multiple database backends
//! - Maximum file size limit (5MB)

pub mod backend;
pub mod cache;

use async_trait::async_trait;
use backend::{create_backend, DatabaseBackend, MAX_FILE_SIZE, MAX_FILE_SIZE_MB};
use cache::ListDirCache;
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::RwLock;

use crate::core::{
    ConfigParameter, Error, FileInfo, FileSystem, PluginConfig, Result, ServicePlugin, WriteFlag,
};

/// SQLFS - Database-backed file system
pub struct SQLFileSystem {
    backend: Arc<RwLock<Box<dyn DatabaseBackend>>>,
    cache: ListDirCache,
}

impl SQLFileSystem {
    /// Create a new SQLFS instance
    ///
    /// # Arguments
    /// * `config` - Plugin configuration containing database connection parameters
    pub fn new(config: &PluginConfig) -> Result<Self> {
        // Create database backend (schema init and optimizations happen inside)
        let backend = create_backend(&config.params)?;

        tracing::info!(
            "SQLFS backend created: {}",
            backend.driver_name(),
        );

        // Create cache from config
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
            .get("cache_ttl_seconds")
            .and_then(|v| v.as_int())
            .unwrap_or(5);

        let cache = ListDirCache::new(cache_max_size, cache_ttl as u64, cache_enabled);

        tracing::info!(
            "SQLFS initialized with backend: {}, cache: {} (max_size: {}, ttl: {}s)",
            backend.driver_name(),
            cache_enabled,
            cache_max_size,
            cache_ttl
        );

        Ok(Self {
            backend: Arc::new(RwLock::new(backend)),
            cache,
        })
    }

    /// Normalize path to ensure consistent format
    fn normalize_path(path: &str) -> String {
        if path.is_empty() || path == "/" {
            return "/".to_string();
        }

        // Ensure starts with /
        let mut result = if path.starts_with('/') {
            path.to_string()
        } else {
            format!("/{}", path)
        };

        // Remove trailing slash (except for root)
        if result.len() > 1 && result.ends_with('/') {
            result.pop();
        }

        // Collapse double slashes
        while result.contains("//") {
            result = result.replace("//", "/");
        }

        result
    }

    /// Get file name from full path
    fn file_name(path: &str) -> String {
        if path == "/" {
            return "/".to_string();
        }

        let normalized = Self::normalize_path(path);
        normalized
            .rsplit('/')
            .next()
            .unwrap_or("")
            .to_string()
    }
}

impl Default for SQLFileSystem {
    fn default() -> Self {
        // Create with default SQLite in-memory database
        let config = PluginConfig {
            name: "sqlfs".to_string(),
            mount_path: "/sqlfs".to_string(),
            params: HashMap::new(),
        };

        Self::new(&config).expect("Failed to create default SQLFS")
    }
}

#[async_trait]
impl FileSystem for SQLFileSystem {
    async fn create(&self, path: &str) -> Result<()> {
        let normalized = Self::normalize_path(path);
        let backend = self.backend.read().await;

        // Check parent directory exists
        let parent = backend.parent_path(&normalized);
        if parent != "/" {
            match backend.is_directory(&parent)? {
                true => {}
                false => {
                    if backend.path_exists(&parent)? {
                        return Err(Error::NotADirectory(parent));
                    }
                    return Err(Error::not_found(&parent));
                }
            }
        }

        // Check if file already exists
        if backend.path_exists(&normalized)? {
            return Err(Error::already_exists(&normalized));
        }

        // Create empty file
        backend.create_file(&normalized, 0o644, &[])?;

        // Invalidate parent cache
        self.cache.invalidate_parent(&normalized).await;

        Ok(())
    }

    async fn mkdir(&self, path: &str, mode: u32) -> Result<()> {
        let normalized = Self::normalize_path(path);
        let backend = self.backend.read().await;

        // Check parent directory exists
        let parent = backend.parent_path(&normalized);
        if parent != "/" {
            match backend.is_directory(&parent)? {
                true => {}
                false => {
                    if backend.path_exists(&parent)? {
                        return Err(Error::NotADirectory(parent));
                    }
                    return Err(Error::not_found(&parent));
                }
            }
        }

        // Check if directory already exists
        if backend.path_exists(&normalized)? {
            return Err(Error::already_exists(&normalized));
        }

        // Create directory
        let mode_to_use = if mode == 0 { 0o755 } else { mode };
        backend.create_directory(&normalized, mode_to_use)?;

        // Invalidate parent cache
        self.cache.invalidate_parent(&normalized).await;

        Ok(())
    }

    async fn remove(&self, path: &str) -> Result<()> {
        let normalized = Self::normalize_path(path);

        if normalized == "/" {
            return Err(Error::invalid_operation("cannot remove root directory"));
        }

        let backend = self.backend.read().await;

        // Check if exists
        if !backend.path_exists(&normalized)? {
            return Err(Error::not_found(&normalized));
        }

        // Check if it's a directory
        if backend.is_directory(&normalized)? {
            // Check if directory is empty
            let pattern = format!("{}/%", normalized);
            let child_count = backend.count_by_pattern(&pattern)?;
            if child_count > 0 {
                return Err(Error::DirectoryNotEmpty(normalized));
            }
        }

        // Delete entry
        backend.delete_entry(&normalized)?;

        // Invalidate caches
        self.cache.invalidate_parent(&normalized).await;
        self.cache.invalidate(&normalized).await;

        Ok(())
    }

    async fn remove_all(&self, path: &str) -> Result<()> {
        let normalized = Self::normalize_path(path);
        let backend = self.backend.read().await;

        const BATCH_SIZE: usize = 1000;

        if normalized == "/" {
            // Delete all children except root
            loop {
                let deleted = backend.delete_entries_by_pattern("/%", Some("/"))?;
                if deleted == 0 || deleted < BATCH_SIZE {
                    break;
                }
            }
            self.cache.invalidate_prefix("/").await;
            return Ok(());
        }

        // Delete path and all children
        loop {
            let pattern = format!("{}/%", normalized);
            let deleted = backend.delete_entries_by_pattern(&pattern, None)?;
            if deleted == 0 || deleted < BATCH_SIZE {
                break;
            }
        }

        // Delete the entry itself
        backend.delete_entry(&normalized)?;

        // Invalidate caches
        self.cache.invalidate_parent(&normalized).await;
        self.cache.invalidate_prefix(&normalized).await;

        Ok(())
    }

    async fn read(&self, path: &str, offset: u64, size: u64) -> Result<Vec<u8>> {
        let normalized = Self::normalize_path(path);
        let backend = self.backend.read().await;

        match backend.read_file(&normalized)? {
            Some((is_dir, data)) => {
                if is_dir {
                    return Err(Error::IsADirectory(normalized));
                }

                // Apply offset and size
                let data_len = data.len();
                let offset = offset as usize;

                if offset >= data_len {
                    return Ok(Vec::new());
                }

                let end = if size == 0 {
                    data_len
                } else {
                    std::cmp::min(offset + size as usize, data_len)
                };

                Ok(data[offset..end].to_vec())
            }
            None => Err(Error::not_found(&normalized)),
        }
    }

    async fn write(&self, path: &str, data: &[u8], offset: u64, flags: WriteFlag) -> Result<u64> {
        let normalized = Self::normalize_path(path);

        // Check file size limit
        if data.len() > MAX_FILE_SIZE {
            return Err(Error::invalid_operation(format!(
                "file size exceeds maximum limit of {}MB (got {} bytes)",
                MAX_FILE_SIZE_MB,
                data.len()
            )));
        }

        // SQLFS doesn't support offset writes (like object store)
        if offset > 0 {
            return Err(Error::invalid_operation(
                "SQLFS does not support offset writes",
            ));
        }

        let backend = self.backend.read().await;

        let exists = backend.path_exists(&normalized)?;

        if exists {
            // Check if it's a directory
            if backend.is_directory(&normalized)? {
                return Err(Error::IsADirectory(normalized));
            }

            // Update existing file
            backend.update_file(&normalized, data)?;
        } else {
            // Create new file
            if !matches!(flags, WriteFlag::Create) {
                return Err(Error::not_found(&normalized));
            }

            // Check parent exists
            let parent = backend.parent_path(&normalized);
            if parent != "/" {
                if !backend.is_directory(&parent)? {
                    return Err(Error::not_found(&parent));
                }
            }

            backend.create_file(&normalized, 0o644, data)?;

            // Invalidate parent cache
            self.cache.invalidate_parent(&normalized).await;
        }

        Ok(data.len() as u64)
    }

    async fn read_dir(&self, path: &str) -> Result<Vec<FileInfo>> {
        let normalized = Self::normalize_path(path);

        // Try cache first
        if let Some(files) = self.cache.get(&normalized).await {
            return Ok(files);
        }

        let backend = self.backend.read().await;

        // Check if directory exists
        if !backend.path_exists(&normalized)? {
            return Err(Error::not_found(&normalized));
        }

        if !backend.is_directory(&normalized)? {
            return Err(Error::NotADirectory(normalized));
        }

        // List directory
        let entries = backend.list_directory(&normalized)?;

        // Convert to FileInfo
        let mut files = Vec::new();
        for entry in entries {
            files.push(FileInfo {
                name: Self::file_name(&entry.path),
                size: entry.size as u64,
                mode: entry.mode,
                mod_time: std::time::UNIX_EPOCH
                    .checked_add(std::time::Duration::from_secs(entry.mod_time as u64))
                    .unwrap_or(std::time::UNIX_EPOCH),
                is_dir: entry.is_dir,
            });
        }

        // Cache the result
        self.cache.put(normalized.clone(), files.clone()).await;

        Ok(files)
    }

    async fn stat(&self, path: &str) -> Result<FileInfo> {
        let normalized = Self::normalize_path(path);
        let backend = self.backend.read().await;

        match backend.get_metadata(&normalized)? {
            Some(meta) => Ok(FileInfo {
                name: Self::file_name(&normalized),
                size: meta.size as u64,
                mode: meta.mode,
                mod_time: std::time::UNIX_EPOCH
                    .checked_add(std::time::Duration::from_secs(meta.mod_time as u64))
                    .unwrap_or(std::time::UNIX_EPOCH),
                is_dir: meta.is_dir,
            }),
            None => Err(Error::not_found(&normalized)),
        }
    }

    async fn rename(&self, old_path: &str, new_path: &str) -> Result<()> {
        let old_normalized = Self::normalize_path(old_path);
        let new_normalized = Self::normalize_path(new_path);

        if old_normalized == "/" || new_normalized == "/" {
            return Err(Error::invalid_operation("cannot rename root directory"));
        }

        let backend = self.backend.read().await;

        // Check old path exists
        if !backend.path_exists(&old_normalized)? {
            return Err(Error::not_found(&old_normalized));
        }

        // Check new path doesn't exist
        if backend.path_exists(&new_normalized)? {
            return Err(Error::already_exists(&new_normalized));
        }

        // Check new parent exists
        let new_parent = backend.parent_path(&new_normalized);
        if new_parent != "/" {
            if !backend.is_directory(&new_parent)? {
                return Err(Error::not_found(&new_parent));
            }
        }

        // Rename entry
        backend.rename_path(&old_normalized, &new_normalized)?;

        // If it's a directory, rename children
        if backend.is_directory(&new_normalized)? {
            backend.rename_children(&old_normalized, &new_normalized)?;
        }

        // Invalidate caches
        self.cache.invalidate_parent(&old_normalized).await;
        self.cache.invalidate_parent(&new_normalized).await;
        self.cache.invalidate(&old_normalized).await;
        self.cache.invalidate_prefix(&old_normalized).await;

        Ok(())
    }

    async fn chmod(&self, path: &str, mode: u32) -> Result<()> {
        let normalized = Self::normalize_path(path);
        let backend = self.backend.read().await;

        if !backend.path_exists(&normalized)? {
            return Err(Error::not_found(&normalized));
        }

        backend.update_mode(&normalized, mode)?;
        Ok(())
    }

    async fn truncate(&self, path: &str, size: u64) -> Result<()> {
        let normalized = Self::normalize_path(path);
        let backend = self.backend.read().await;

        match backend.read_file(&normalized)? {
            Some((is_dir, mut data)) => {
                if is_dir {
                    return Err(Error::IsADirectory(normalized));
                }

                data.resize(size as usize, 0);
                backend.update_file(&normalized, &data)?;
                Ok(())
            }
            None => Err(Error::not_found(&normalized)),
        }
    }
}

/// SQLFS Plugin
pub struct SQLFSPlugin {
    config_params: Vec<ConfigParameter>,
}

impl SQLFSPlugin {
    /// Create a new SQLFSPlugin
    pub fn new() -> Self {
        Self {
            config_params: vec![
                ConfigParameter::optional(
                    "backend",
                    "string",
                    "sqlite",
                    "Database backend (sqlite, mysql, tidb)",
                ),
                ConfigParameter::optional(
                    "db_path",
                    "string",
                    ":memory:",
                    "Database file path (SQLite only)",
                ),
                ConfigParameter::optional(
                    "host",
                    "string",
                    "127.0.0.1",
                    "Database host (MySQL/TiDB)",
                ),
                ConfigParameter::optional("port", "int", "3306", "Database port (MySQL/TiDB)"),
                ConfigParameter::optional(
                    "user",
                    "string",
                    "root",
                    "Database user (MySQL/TiDB)",
                ),
                ConfigParameter::optional(
                    "password",
                    "string",
                    "",
                    "Database password (MySQL/TiDB)",
                ),
                ConfigParameter::optional(
                    "database",
                    "string",
                    "sqlfs",
                    "Database name (MySQL/TiDB)",
                ),
                ConfigParameter::optional(
                    "cache_enabled",
                    "bool",
                    "true",
                    "Enable directory listing cache",
                ),
                ConfigParameter::optional(
                    "cache_max_size",
                    "int",
                    "1000",
                    "Maximum cache entries",
                ),
                ConfigParameter::optional(
                    "cache_ttl_seconds",
                    "int",
                    "5",
                    "Cache TTL in seconds",
                ),
            ],
        }
    }
}

impl Default for SQLFSPlugin {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl ServicePlugin for SQLFSPlugin {
    fn name(&self) -> &str {
        "sqlfs"
    }

    fn version(&self) -> &str {
        "0.1.0"
    }

    fn description(&self) -> &str {
        "Database-backed file system with SQLite and MySQL/TiDB support"
    }

    fn readme(&self) -> &str {
        r#"# SQLFS - Database-backed File System

A persistent file system backed by SQLite or MySQL/TiDB.

## Features

- Persistent storage (survives server restarts)
- Full POSIX-like file system operations
- Multiple database backends (SQLite, MySQL, TiDB)
- ACID transactions
- LRU cache for directory listings
- Maximum file size: 5MB

## Configuration

### SQLite Backend (Local Testing)
```yaml
plugins:
  sqlfs:
    enabled: true
    path: /sqlfs
    config:
      backend: sqlite
      db_path: sqlfs.db
      cache_enabled: true
      cache_max_size: 1000
      cache_ttl_seconds: 5
```

### MySQL/TiDB Backend
```yaml
plugins:
  sqlfs:
    enabled: true
    path: /sqlfs
    config:
      backend: mysql
      host: localhost
      port: 3306
      user: root
      password: password
      database: sqlfs
      cache_enabled: true
```

## Usage

Create a directory:
```
agfs mkdir /sqlfs/mydir
```

Write a file:
```
echo "Hello, World!" | agfs write /sqlfs/mydir/file.txt
```

Read a file:
```
agfs cat /sqlfs/mydir/file.txt
```

List directory:
```
agfs ls /sqlfs/mydir
```

## Notes

- SQLFS does not support offset writes (like object store)
- Maximum file size is 5MB per file
- Use MemFS or StreamFS for larger files
"#
    }

    async fn validate(&self, config: &PluginConfig) -> Result<()> {
        // Validate backend type
        let backend = config
            .params
            .get("backend")
            .and_then(|v| v.as_string())
            .unwrap_or("sqlite");

        let valid_backends = ["sqlite", "sqlite3", "mysql", "tidb"];
        if !valid_backends.contains(&backend) {
            return Err(Error::config(format!(
                "unsupported backend: {} (valid: {})",
                backend,
                valid_backends.join(", ")
            )));
        }

        // Validate cache settings if provided
        if let Some(v) = config.params.get("cache_enabled") {
            v.as_bool()
                .ok_or_else(|| Error::config("cache_enabled must be a boolean"))?;
        }

        if let Some(v) = config.params.get("cache_max_size") {
            v.as_int()
                .ok_or_else(|| Error::config("cache_max_size must be an integer"))?;
        }

        if let Some(v) = config.params.get("cache_ttl_seconds") {
            v.as_int()
                .ok_or_else(|| Error::config("cache_ttl_seconds must be an integer"))?;
        }

        Ok(())
    }

    async fn initialize(&self, config: PluginConfig) -> Result<Box<dyn FileSystem>> {
        let fs = SQLFileSystem::new(&config)?;
        Ok(Box::new(fs))
    }

    fn config_params(&self) -> &[ConfigParameter] {
        &self.config_params
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_sqlfs_basic() {
        let config = PluginConfig {
            name: "sqlfs".to_string(),
            mount_path: "/sqlfs".to_string(),
            params: std::collections::HashMap::new(),
        };

        let plugin = SQLFSPlugin::new();
        assert!(plugin.validate(&config).await.is_ok());

        let fs = plugin.initialize(config).await.unwrap();

        // Create and write
        fs.write("/test.txt", b"hello", 0, WriteFlag::Create)
            .await
            .unwrap();

        // Read
        let data = fs.read("/test.txt", 0, 0).await.unwrap();
        assert_eq!(data, b"hello");

        // Stat
        let info = fs.stat("/test.txt").await.unwrap();
        assert_eq!(info.size, 5);
        assert!(!info.is_dir);
    }

    #[tokio::test]
    async fn test_sqlfs_directories() {
        let fs = SQLFileSystem::default();

        // Create directory
        fs.mkdir("/testdir", 0o755).await.unwrap();

        // Create file in directory
        fs.write("/testdir/file.txt", b"data", 0, WriteFlag::Create)
            .await
            .unwrap();

        // List directory
        let entries = fs.read_dir("/testdir").await.unwrap();
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].name, "file.txt");

        // Cannot remove non-empty directory
        assert!(fs.remove("/testdir").await.is_err());

        // Can remove with remove_all
        fs.remove_all("/testdir").await.unwrap();
        assert!(fs.stat("/testdir").await.is_err());
    }

    #[tokio::test]
    async fn test_sqlfs_rename() {
        let fs = SQLFileSystem::default();

        fs.write("/old.txt", b"data", 0, WriteFlag::Create)
            .await
            .unwrap();

        fs.rename("/old.txt", "/new.txt").await.unwrap();

        assert!(fs.stat("/old.txt").await.is_err());
        let data = fs.read("/new.txt", 0, 0).await.unwrap();
        assert_eq!(data, b"data");
    }

    #[tokio::test]
    async fn test_sqlfs_truncate() {
        let fs = SQLFileSystem::default();

        fs.write("/trunc.txt", b"hello world", 0, WriteFlag::Create)
            .await
            .unwrap();

        fs.truncate("/trunc.txt", 5).await.unwrap();

        let data = fs.read("/trunc.txt", 0, 0).await.unwrap();
        assert_eq!(data, b"hello");
    }

    #[tokio::test]
    async fn test_sqlfs_file_size_limit() {
        let fs = SQLFileSystem::default();

        // Create data larger than MAX_FILE_SIZE
        let big_data = vec![0u8; MAX_FILE_SIZE + 1];

        let result = fs.write("/big.txt", &big_data, 0, WriteFlag::Create).await;
        assert!(result.is_err());
    }

    #[tokio::test]
    async fn test_sqlfs_offset_write_rejected() {
        let fs = SQLFileSystem::default();

        let result = fs.write("/test.txt", b"data", 10, WriteFlag::Create).await;
        assert!(result.is_err());
    }

    #[tokio::test]
    async fn test_sqlfs_nested_directories() {
        let fs = SQLFileSystem::default();

        fs.mkdir("/a", 0o755).await.unwrap();
        fs.mkdir("/a/b", 0o755).await.unwrap();
        fs.write("/a/b/file.txt", b"nested", 0, WriteFlag::Create)
            .await
            .unwrap();

        // List /a should only show /a/b
        let entries = fs.read_dir("/a").await.unwrap();
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].name, "b");
        assert!(entries[0].is_dir);

        // Read nested file
        let data = fs.read("/a/b/file.txt", 0, 0).await.unwrap();
        assert_eq!(data, b"nested");
    }

    #[tokio::test]
    async fn test_sqlfs_read_with_offset_and_size() {
        let fs = SQLFileSystem::default();

        fs.write("/range.txt", b"hello world", 0, WriteFlag::Create)
            .await
            .unwrap();

        // Read with offset
        let data = fs.read("/range.txt", 6, 0).await.unwrap();
        assert_eq!(data, b"world");

        // Read with offset and size
        let data = fs.read("/range.txt", 0, 5).await.unwrap();
        assert_eq!(data, b"hello");

        // Read beyond end
        let data = fs.read("/range.txt", 100, 0).await.unwrap();
        assert!(data.is_empty());
    }

    #[tokio::test]
    async fn test_sqlfs_chmod() {
        let fs = SQLFileSystem::default();

        fs.write("/perm.txt", b"data", 0, WriteFlag::Create)
            .await
            .unwrap();

        fs.chmod("/perm.txt", 0o600).await.unwrap();

        let info = fs.stat("/perm.txt").await.unwrap();
        assert_eq!(info.mode, 0o600);
    }
}
