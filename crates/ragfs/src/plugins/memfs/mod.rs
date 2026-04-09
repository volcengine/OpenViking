//! MemFS - In-memory File System
//!
//! A simple file system that stores all data in memory. All data is lost
//! when the server restarts. This is useful for temporary storage and testing.

use async_trait::async_trait;
use std::collections::HashMap;
use std::sync::Arc;
use std::time::SystemTime;
use tokio::sync::RwLock;

use crate::core::{
    ConfigParameter, Error, FileInfo, FileSystem, PluginConfig, Result, ServicePlugin, WriteFlag,
};

/// File entry in memory
#[derive(Clone)]
struct FileEntry {
    /// File data
    data: Vec<u8>,
    /// File mode/permissions
    mode: u32,
    /// Last modification time
    mod_time: SystemTime,
    /// Whether this is a directory
    is_dir: bool,
}

impl FileEntry {
    /// Create a new file entry
    fn new_file(mode: u32) -> Self {
        Self {
            data: Vec::new(),
            mode,
            mod_time: SystemTime::now(),
            is_dir: false,
        }
    }

    /// Create a new directory entry
    fn new_dir(mode: u32) -> Self {
        Self {
            data: Vec::new(),
            mode,
            mod_time: SystemTime::now(),
            is_dir: true,
        }
    }

    /// Update modification time
    fn touch(&mut self) {
        self.mod_time = SystemTime::now();
    }
}

/// In-memory file system implementation
pub struct MemFileSystem {
    /// Storage for files and directories
    entries: Arc<RwLock<HashMap<String, FileEntry>>>,
}

impl MemFileSystem {
    /// Create a new MemFileSystem
    pub fn new() -> Self {
        let mut entries = HashMap::new();

        // Create root directory
        entries.insert(
            "/".to_string(),
            FileEntry::new_dir(0o755),
        );

        Self {
            entries: Arc::new(RwLock::new(entries)),
        }
    }

    /// Normalize path (ensure it starts with /)
    fn normalize_path(path: &str) -> String {
        if path.is_empty() || path == "/" {
            return "/".to_string();
        }

        let mut normalized = path.to_string();
        if !normalized.starts_with('/') {
            normalized.insert(0, '/');
        }

        // Remove trailing slash (except for root)
        if normalized.len() > 1 && normalized.ends_with('/') {
            normalized.pop();
        }

        normalized
    }

    /// Get parent directory path
    fn parent_path(path: &str) -> Option<String> {
        if path == "/" {
            return None;
        }

        let normalized = Self::normalize_path(path);
        let parts: Vec<&str> = normalized.split('/').collect();

        if parts.len() <= 2 {
            return Some("/".to_string());
        }

        Some(parts[..parts.len() - 1].join("/"))
    }

    /// Get file name from path
    fn file_name(path: &str) -> String {
        if path == "/" {
            return "/".to_string();
        }

        let normalized = Self::normalize_path(path);
        normalized
            .split('/')
            .last()
            .unwrap_or("")
            .to_string()
    }

    /// List entries in a directory
    fn list_entries(&self, entries: &HashMap<String, FileEntry>, dir_path: &str) -> Vec<String> {
        let normalized_dir = Self::normalize_path(dir_path);
        let prefix = if normalized_dir == "/" {
            "/".to_string()
        } else {
            format!("{}/", normalized_dir)
        };

        entries
            .keys()
            .filter(|path| {
                if *path == &normalized_dir {
                    return false;
                }

                if !path.starts_with(&prefix) {
                    return false;
                }

                // Only direct children (no nested paths)
                let relative = &path[prefix.len()..];
                !relative.contains('/')
            })
            .cloned()
            .collect()
    }
}

impl Default for MemFileSystem {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl FileSystem for MemFileSystem {
    async fn create(&self, path: &str) -> Result<()> {
        let normalized = Self::normalize_path(path);
        let mut entries = self.entries.write().await;

        // Check if already exists
        if entries.contains_key(&normalized) {
            return Err(Error::already_exists(&normalized));
        }

        // Check parent directory exists
        if let Some(parent) = Self::parent_path(&normalized) {
            match entries.get(&parent) {
                Some(entry) if entry.is_dir => {}
                Some(_) => return Err(Error::NotADirectory(parent)),
                None => return Err(Error::not_found(&parent)),
            }
        }

        // Create file
        entries.insert(normalized, FileEntry::new_file(0o644));
        Ok(())
    }

    async fn mkdir(&self, path: &str, mode: u32) -> Result<()> {
        let normalized = Self::normalize_path(path);
        let mut entries = self.entries.write().await;

        // Check if already exists
        if entries.contains_key(&normalized) {
            return Err(Error::already_exists(&normalized));
        }

        // Check parent directory exists
        if let Some(parent) = Self::parent_path(&normalized) {
            match entries.get(&parent) {
                Some(entry) if entry.is_dir => {}
                Some(_) => return Err(Error::NotADirectory(parent)),
                None => return Err(Error::not_found(&parent)),
            }
        }

        // Create directory
        entries.insert(normalized, FileEntry::new_dir(mode));
        Ok(())
    }

    async fn remove(&self, path: &str) -> Result<()> {
        let normalized = Self::normalize_path(path);
        let mut entries = self.entries.write().await;

        // Check if exists
        match entries.get(&normalized) {
            Some(entry) if entry.is_dir => {
                return Err(Error::IsADirectory(normalized));
            }
            Some(_) => {}
            None => return Err(Error::not_found(&normalized)),
        }

        // Remove file
        entries.remove(&normalized);
        Ok(())
    }

    async fn remove_all(&self, path: &str) -> Result<()> {
        let normalized = Self::normalize_path(path);
        let mut entries = self.entries.write().await;

        // Check if exists
        if !entries.contains_key(&normalized) {
            return Err(Error::not_found(&normalized));
        }

        // Remove entry and all children
        let to_remove: Vec<String> = entries
            .keys()
            .filter(|p| *p == &normalized || p.starts_with(&format!("{}/", normalized)))
            .cloned()
            .collect();

        for path in to_remove {
            entries.remove(&path);
        }

        Ok(())
    }

    async fn read(&self, path: &str, offset: u64, size: u64) -> Result<Vec<u8>> {
        let normalized = Self::normalize_path(path);
        let entries = self.entries.read().await;

        match entries.get(&normalized) {
            Some(entry) if entry.is_dir => Err(Error::IsADirectory(normalized)),
            Some(entry) => {
                let offset = offset as usize;
                let data_len = entry.data.len();

                if offset >= data_len {
                    return Ok(Vec::new());
                }

                let end = if size == 0 {
                    data_len
                } else {
                    std::cmp::min(offset + size as usize, data_len)
                };

                Ok(entry.data[offset..end].to_vec())
            }
            None => Err(Error::not_found(&normalized)),
        }
    }

    async fn write(&self, path: &str, data: &[u8], offset: u64, flags: WriteFlag) -> Result<u64> {
        let normalized = Self::normalize_path(path);
        let mut entries = self.entries.write().await;

        match entries.get_mut(&normalized) {
            Some(entry) if entry.is_dir => Err(Error::IsADirectory(normalized)),
            Some(entry) => {
                entry.touch();

                match flags {
                    WriteFlag::Create | WriteFlag::Truncate => {
                        entry.data = data.to_vec();
                    }
                    WriteFlag::Append => {
                        entry.data.extend_from_slice(data);
                    }
                    WriteFlag::None => {
                        let offset = offset as usize;
                        let end = offset + data.len();

                        // Extend if necessary
                        if end > entry.data.len() {
                            entry.data.resize(end, 0);
                        }

                        entry.data[offset..end].copy_from_slice(data);
                    }
                }

                Ok(data.len() as u64)
            }
            None => {
                // Create file if Create flag is set
                if matches!(flags, WriteFlag::Create) {
                    // Check parent exists
                    if let Some(parent) = Self::parent_path(&normalized) {
                        match entries.get(&parent) {
                            Some(entry) if entry.is_dir => {}
                            Some(_) => return Err(Error::NotADirectory(parent)),
                            None => return Err(Error::not_found(&parent)),
                        }
                    }

                    let mut entry = FileEntry::new_file(0o644);
                    entry.data = data.to_vec();
                    entries.insert(normalized, entry);
                    Ok(data.len() as u64)
                } else {
                    Err(Error::not_found(&normalized))
                }
            }
        }
    }

    async fn read_dir(&self, path: &str) -> Result<Vec<FileInfo>> {
        let normalized = Self::normalize_path(path);
        let entries = self.entries.read().await;

        // Check if directory exists
        match entries.get(&normalized) {
            Some(entry) if !entry.is_dir => return Err(Error::NotADirectory(normalized)),
            Some(_) => {}
            None => return Err(Error::not_found(&normalized)),
        }

        // List entries
        let children = self.list_entries(&entries, &normalized);
        let mut result = Vec::new();

        for child_path in children {
            if let Some(entry) = entries.get(&child_path) {
                let name = Self::file_name(&child_path);
                result.push(FileInfo {
                    name,
                    size: entry.data.len() as u64,
                    mode: entry.mode,
                    mod_time: entry.mod_time,
                    is_dir: entry.is_dir,
                });
            }
        }

        Ok(result)
    }

    async fn stat(&self, path: &str) -> Result<FileInfo> {
        let normalized = Self::normalize_path(path);
        let entries = self.entries.read().await;

        match entries.get(&normalized) {
            Some(entry) => Ok(FileInfo {
                name: Self::file_name(&normalized),
                size: entry.data.len() as u64,
                mode: entry.mode,
                mod_time: entry.mod_time,
                is_dir: entry.is_dir,
            }),
            None => Err(Error::not_found(&normalized)),
        }
    }

    async fn rename(&self, old_path: &str, new_path: &str) -> Result<()> {
        let old_normalized = Self::normalize_path(old_path);
        let new_normalized = Self::normalize_path(new_path);
        let mut entries = self.entries.write().await;

        // Check old path exists
        let entry = entries
            .get(&old_normalized)
            .ok_or_else(|| Error::not_found(&old_normalized))?
            .clone();

        // Check new path doesn't exist
        if entries.contains_key(&new_normalized) {
            return Err(Error::already_exists(&new_normalized));
        }

        // Check new parent exists
        if let Some(parent) = Self::parent_path(&new_normalized) {
            match entries.get(&parent) {
                Some(e) if e.is_dir => {}
                Some(_) => return Err(Error::NotADirectory(parent)),
                None => return Err(Error::not_found(&parent)),
            }
        }

        // Collect all child entries if renaming a directory
        let old_prefix = if old_normalized == "/" {
            "/".to_string()
        } else {
            format!("{}/", old_normalized)
        };
        let new_prefix = if new_normalized == "/" {
            "/".to_string()
        } else {
            format!("{}/", new_normalized)
        };

        let mut to_move = Vec::new();
        for (path, _) in entries.iter() {
            if path == &old_normalized {
                continue;
            }
            if path.starts_with(&old_prefix) {
                // Check for conflicts with new path
                let new_child_path = format!("{}{}", new_prefix, &path[old_prefix.len()..]);
                if entries.contains_key(&new_child_path) {
                    return Err(Error::already_exists(&new_child_path));
                }
                to_move.push(path.clone());
            }
        }

        // Move the main entry
        entries.remove(&old_normalized);
        entries.insert(new_normalized, entry);

        // Move all child entries
        for old_child_path in to_move {
            let new_child_path = format!("{}{}", new_prefix, &old_child_path[old_prefix.len()..]);
            if let Some(child_entry) = entries.remove(&old_child_path) {
                entries.insert(new_child_path, child_entry);
            }
        }

        Ok(())
    }

    async fn chmod(&self, path: &str, mode: u32) -> Result<()> {
        let normalized = Self::normalize_path(path);
        let mut entries = self.entries.write().await;

        match entries.get_mut(&normalized) {
            Some(entry) => {
                entry.mode = mode;
                entry.touch();
                Ok(())
            }
            None => Err(Error::not_found(&normalized)),
        }
    }

    async fn truncate(&self, path: &str, size: u64) -> Result<()> {
        let normalized = Self::normalize_path(path);
        let mut entries = self.entries.write().await;

        match entries.get_mut(&normalized) {
            Some(entry) if entry.is_dir => Err(Error::IsADirectory(normalized)),
            Some(entry) => {
                entry.data.resize(size as usize, 0);
                entry.touch();
                Ok(())
            }
            None => Err(Error::not_found(&normalized)),
        }
    }
}

/// MemFS plugin
pub struct MemFSPlugin;

#[async_trait]
impl ServicePlugin for MemFSPlugin {
    fn name(&self) -> &str {
        "memfs"
    }

    fn version(&self) -> &str {
        "0.1.0"
    }

    fn description(&self) -> &str {
        "In-memory file system for temporary storage"
    }

    fn readme(&self) -> &str {
        r#"# MemFS - In-memory File System

A simple file system that stores all data in memory. All data is lost
when the server restarts.

## Features

- Fast in-memory storage
- Full POSIX-like file operations
- Directory support
- No persistence (data lost on restart)

## Usage

Mount the filesystem:
```bash
curl -X POST http://localhost:8080/api/v1/mount \
  -H "Content-Type: application/json" \
  -d '{"plugin": "memfs", "path": "/memfs"}'
```

Create and write to a file:
```bash
echo "hello world" | curl -X PUT \
  "http://localhost:8080/api/v1/files?path=/memfs/test.txt" \
  --data-binary @-
```

Read the file:
```bash
curl "http://localhost:8080/api/v1/files?path=/memfs/test.txt"
```

## Configuration

MemFS has no configuration parameters.
"#
    }

    async fn validate(&self, _config: &PluginConfig) -> Result<()> {
        // MemFS has no required configuration
        Ok(())
    }

    async fn initialize(&self, _config: PluginConfig) -> Result<Box<dyn FileSystem>> {
        Ok(Box::new(MemFileSystem::new()))
    }

    fn config_params(&self) -> &[ConfigParameter] {
        // No configuration parameters
        &[]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_create_and_read_file() {
        let fs = MemFileSystem::new();

        // Create file
        fs.create("/test.txt").await.unwrap();

        // Write data
        let data = b"hello world";
        fs.write("/test.txt", data, 0, WriteFlag::None)
            .await
            .unwrap();

        // Read data
        let read_data = fs.read("/test.txt", 0, 0).await.unwrap();
        assert_eq!(read_data, data);
    }

    #[tokio::test]
    async fn test_mkdir_and_list() {
        let fs = MemFileSystem::new();

        // Create directory
        fs.mkdir("/testdir", 0o755).await.unwrap();

        // Create files in directory
        fs.create("/testdir/file1.txt").await.unwrap();
        fs.create("/testdir/file2.txt").await.unwrap();

        // List directory
        let entries = fs.read_dir("/testdir").await.unwrap();
        assert_eq!(entries.len(), 2);
    }

    #[tokio::test]
    async fn test_remove_file() {
        let fs = MemFileSystem::new();

        fs.create("/test.txt").await.unwrap();
        fs.remove("/test.txt").await.unwrap();

        // Should not exist
        assert!(fs.stat("/test.txt").await.is_err());
    }

    #[tokio::test]
    async fn test_rename() {
        let fs = MemFileSystem::new();

        fs.create("/old.txt").await.unwrap();
        fs.write("/old.txt", b"data", 0, WriteFlag::None)
            .await
            .unwrap();

        fs.rename("/old.txt", "/new.txt").await.unwrap();

        // Old should not exist
        assert!(fs.stat("/old.txt").await.is_err());

        // New should exist with same data
        let data = fs.read("/new.txt", 0, 0).await.unwrap();
        assert_eq!(data, b"data");
    }

    #[tokio::test]
    async fn test_write_flags() {
        let fs = MemFileSystem::new();

        // Create with data
        fs.write("/test.txt", b"hello", 0, WriteFlag::Create)
            .await
            .unwrap();

        // Append
        fs.write("/test.txt", b" world", 0, WriteFlag::Append)
            .await
            .unwrap();

        let data = fs.read("/test.txt", 0, 0).await.unwrap();
        assert_eq!(data, b"hello world");

        // Truncate
        fs.write("/test.txt", b"new", 0, WriteFlag::Truncate)
            .await
            .unwrap();

        let data = fs.read("/test.txt", 0, 0).await.unwrap();
        assert_eq!(data, b"new");
    }

    #[tokio::test]
    async fn test_plugin() {
        let plugin = MemFSPlugin;
        assert_eq!(plugin.name(), "memfs");

        let config = PluginConfig {
            name: "memfs".to_string(),
            mount_path: "/memfs".to_string(),
            params: HashMap::new(),
        };

        assert!(plugin.validate(&config).await.is_ok());
        assert!(plugin.initialize(config).await.is_ok());
    }
}
