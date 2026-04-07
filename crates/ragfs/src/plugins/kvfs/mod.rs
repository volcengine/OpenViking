//! KVFS - Key-Value File System
//!
//! A file system that treats files as key-value pairs. Each file's path
//! becomes a key, and the file content becomes the value. This is useful
//! for simple key-value storage scenarios.

use async_trait::async_trait;
use std::collections::HashMap;
use std::sync::Arc;
use std::time::SystemTime;
use tokio::sync::RwLock;

use crate::core::{
    ConfigParameter, Error, FileInfo, FileSystem, PluginConfig, Result, ServicePlugin, WriteFlag,
};

/// Key-value entry
#[derive(Clone)]
struct KVEntry {
    /// Value (file content)
    value: Vec<u8>,
    /// Last modification time
    mod_time: SystemTime,
}

impl KVEntry {
    fn new(value: Vec<u8>) -> Self {
        Self {
            value,
            mod_time: SystemTime::now(),
        }
    }

    fn touch(&mut self) {
        self.mod_time = SystemTime::now();
    }
}

/// Key-Value file system implementation
pub struct KVFileSystem {
    /// Storage for key-value pairs
    store: Arc<RwLock<HashMap<String, KVEntry>>>,
}

impl KVFileSystem {
    /// Create a new KVFileSystem
    pub fn new() -> Self {
        Self {
            store: Arc::new(RwLock::new(HashMap::new())),
        }
    }

    /// Normalize path to key (remove leading /)
    fn path_to_key(path: &str) -> String {
        let normalized = if path.starts_with('/') {
            &path[1..]
        } else {
            path
        };

        if normalized.is_empty() {
            "/".to_string()
        } else {
            normalized.to_string()
        }
    }

    /// Get parent directory path
    fn parent_key(key: &str) -> Option<String> {
        if key == "/" || !key.contains('/') {
            return Some("/".to_string());
        }

        let parts: Vec<&str> = key.split('/').collect();
        if parts.len() <= 1 {
            return Some("/".to_string());
        }

        Some(parts[..parts.len() - 1].join("/"))
    }

    /// List all keys with a given prefix
    fn list_keys_with_prefix(&self, store: &HashMap<String, KVEntry>, prefix: &str) -> Vec<String> {
        let search_prefix = if prefix == "/" {
            ""
        } else {
            prefix
        };

        store
            .keys()
            .filter(|k| {
                if search_prefix.is_empty() {
                    // Root: only keys without '/'
                    !k.contains('/')
                } else {
                    // Keys that start with prefix/ and have no further /
                    k.starts_with(&format!("{}/", search_prefix))
                        && !k[search_prefix.len() + 1..].contains('/')
                }
            })
            .cloned()
            .collect()
    }
}

impl Default for KVFileSystem {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl FileSystem for KVFileSystem {
    async fn create(&self, path: &str) -> Result<()> {
        let key = Self::path_to_key(path);
        let mut store = self.store.write().await;

        if store.contains_key(&key) {
            return Err(Error::already_exists(path));
        }

        store.insert(key, KVEntry::new(Vec::new()));
        Ok(())
    }

    async fn mkdir(&self, path: &str, _mode: u32) -> Result<()> {
        // KVFS doesn't have real directories, but we accept mkdir for compatibility
        // We just create an empty entry to mark the "directory"
        let key = Self::path_to_key(path);
        let mut store = self.store.write().await;

        if store.contains_key(&key) {
            return Err(Error::already_exists(path));
        }

        // Mark as directory by using empty value
        store.insert(key, KVEntry::new(Vec::new()));
        Ok(())
    }

    async fn remove(&self, path: &str) -> Result<()> {
        let key = Self::path_to_key(path);
        let mut store = self.store.write().await;

        if store.remove(&key).is_none() {
            return Err(Error::not_found(path));
        }

        Ok(())
    }

    async fn remove_all(&self, path: &str) -> Result<()> {
        let key = Self::path_to_key(path);
        let mut store = self.store.write().await;

        // Remove the key itself
        if !store.contains_key(&key) {
            return Err(Error::not_found(path));
        }

        // Remove all keys with this prefix
        let prefix = if key == "/" { "" } else { &key };
        let to_remove: Vec<String> = store
            .keys()
            .filter(|k| *k == &key || k.starts_with(&format!("{}/", prefix)))
            .cloned()
            .collect();

        for k in to_remove {
            store.remove(&k);
        }

        Ok(())
    }

    async fn read(&self, path: &str, offset: u64, size: u64) -> Result<Vec<u8>> {
        let key = Self::path_to_key(path);
        let store = self.store.read().await;

        match store.get(&key) {
            Some(entry) => {
                let offset = offset as usize;
                let data_len = entry.value.len();

                if offset >= data_len {
                    return Ok(Vec::new());
                }

                let end = if size == 0 {
                    data_len
                } else {
                    std::cmp::min(offset + size as usize, data_len)
                };

                Ok(entry.value[offset..end].to_vec())
            }
            None => Err(Error::not_found(path)),
        }
    }

    async fn write(&self, path: &str, data: &[u8], offset: u64, flags: WriteFlag) -> Result<u64> {
        let key = Self::path_to_key(path);
        let mut store = self.store.write().await;

        match store.get_mut(&key) {
            Some(entry) => {
                entry.touch();

                match flags {
                    WriteFlag::Create | WriteFlag::Truncate => {
                        entry.value = data.to_vec();
                    }
                    WriteFlag::Append => {
                        entry.value.extend_from_slice(data);
                    }
                    WriteFlag::None => {
                        let offset = offset as usize;
                        let end = offset + data.len();

                        if end > entry.value.len() {
                            entry.value.resize(end, 0);
                        }

                        entry.value[offset..end].copy_from_slice(data);
                    }
                }

                Ok(data.len() as u64)
            }
            None => {
                if matches!(flags, WriteFlag::Create) {
                    store.insert(key, KVEntry::new(data.to_vec()));
                    Ok(data.len() as u64)
                } else {
                    Err(Error::not_found(path))
                }
            }
        }
    }

    async fn read_dir(&self, path: &str) -> Result<Vec<FileInfo>> {
        let key = Self::path_to_key(path);
        let store = self.store.read().await;

        // Check if the directory exists (or root)
        if key != "/" && !store.contains_key(&key) {
            return Err(Error::not_found(path));
        }

        let keys = self.list_keys_with_prefix(&store, &key);
        let mut result = Vec::new();

        for k in keys {
            if let Some(entry) = store.get(&k) {
                let name = k.split('/').last().unwrap_or(&k).to_string();
                result.push(FileInfo {
                    name,
                    size: entry.value.len() as u64,
                    mode: 0o644,
                    mod_time: entry.mod_time,
                    is_dir: false,
                });
            }
        }

        Ok(result)
    }

    async fn stat(&self, path: &str) -> Result<FileInfo> {
        let key = Self::path_to_key(path);
        let store = self.store.read().await;

        match store.get(&key) {
            Some(entry) => {
                let name = key.split('/').last().unwrap_or(&key).to_string();
                Ok(FileInfo {
                    name,
                    size: entry.value.len() as u64,
                    mode: 0o644,
                    mod_time: entry.mod_time,
                    is_dir: false,
                })
            }
            None => Err(Error::not_found(path)),
        }
    }

    async fn rename(&self, old_path: &str, new_path: &str) -> Result<()> {
        let old_key = Self::path_to_key(old_path);
        let new_key = Self::path_to_key(new_path);
        let mut store = self.store.write().await;

        // Check old key exists
        let entry = store
            .get(&old_key)
            .ok_or_else(|| Error::not_found(old_path))?
            .clone();

        // Check new key doesn't exist
        if store.contains_key(&new_key) {
            return Err(Error::already_exists(new_path));
        }

        // Collect all child keys with old prefix
        let old_prefix = if old_key == "/" {
            "".to_string()
        } else {
            format!("{}/", old_key)
        };
        let new_prefix = if new_key == "/" {
            "".to_string()
        } else {
            format!("{}/", new_key)
        };

        let mut to_move = Vec::new();
        for key in store.keys() {
            if key == &old_key {
                continue;
            }
            if !old_prefix.is_empty() && key.starts_with(&old_prefix) {
                // Check for conflicts with new path
                let new_child_key = format!("{}{}", new_prefix, &key[old_prefix.len()..]);
                if store.contains_key(&new_child_key) {
                    // Convert back to path for error message
                    let new_child_path = if new_child_key == "/" {
                        "/".to_string()
                    } else {
                        format!("/{}", new_child_key)
                    };
                    return Err(Error::already_exists(&new_child_path));
                }
                to_move.push(key.clone());
            }
        }

        // Move the main entry
        store.remove(&old_key);
        store.insert(new_key, entry);

        // Move all child entries
        for old_child_key in to_move {
            let new_child_key = format!("{}{}", new_prefix, &old_child_key[old_prefix.len()..]);
            if let Some(child_entry) = store.remove(&old_child_key) {
                store.insert(new_child_key, child_entry);
            }
        }

        Ok(())
    }

    async fn chmod(&self, path: &str, _mode: u32) -> Result<()> {
        let key = Self::path_to_key(path);
        let mut store = self.store.write().await;

        match store.get_mut(&key) {
            Some(entry) => {
                entry.touch();
                Ok(())
            }
            None => Err(Error::not_found(path)),
        }
    }

    async fn truncate(&self, path: &str, size: u64) -> Result<()> {
        let key = Self::path_to_key(path);
        let mut store = self.store.write().await;

        match store.get_mut(&key) {
            Some(entry) => {
                entry.value.resize(size as usize, 0);
                entry.touch();
                Ok(())
            }
            None => Err(Error::not_found(path)),
        }
    }
}

/// KVFS plugin
pub struct KVFSPlugin;

#[async_trait]
impl ServicePlugin for KVFSPlugin {
    fn name(&self) -> &str {
        "kvfs"
    }

    fn version(&self) -> &str {
        "0.1.0"
    }

    fn description(&self) -> &str {
        "Key-value file system for simple storage"
    }

    fn readme(&self) -> &str {
        r#"# KVFS - Key-Value File System

A file system that treats files as key-value pairs. Each file's path
becomes a key, and the file content becomes the value.

## Features

- Simple key-value storage
- File paths map to keys
- Fast lookups
- In-memory storage (no persistence)

## Usage

Mount the filesystem:
```bash
curl -X POST http://localhost:8080/api/v1/mount \
  -H "Content-Type: application/json" \
  -d '{"plugin": "kvfs", "path": "/kvfs"}'
```

Store a value:
```bash
echo "value123" | curl -X PUT \
  "http://localhost:8080/api/v1/files?path=/kvfs/mykey" \
  --data-binary @-
```

Retrieve a value:
```bash
curl "http://localhost:8080/api/v1/files?path=/kvfs/mykey"
```

List all keys:
```bash
curl "http://localhost:8080/api/v1/directories?path=/kvfs"
```

## Use Cases

- Configuration storage
- Cache storage
- Session data
- Temporary key-value storage

## Configuration

KVFS has no configuration parameters.
"#
    }

    async fn validate(&self, _config: &PluginConfig) -> Result<()> {
        Ok(())
    }

    async fn initialize(&self, _config: PluginConfig) -> Result<Box<dyn FileSystem>> {
        Ok(Box::new(KVFileSystem::new()))
    }

    fn config_params(&self) -> &[ConfigParameter] {
        &[]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_kvfs_basic_operations() {
        let fs = KVFileSystem::new();

        // Create and write
        fs.write("/key1", b"value1", 0, WriteFlag::Create)
            .await
            .unwrap();

        // Read
        let data = fs.read("/key1", 0, 0).await.unwrap();
        assert_eq!(data, b"value1");

        // Update
        fs.write("/key1", b"value2", 0, WriteFlag::Truncate)
            .await
            .unwrap();

        let data = fs.read("/key1", 0, 0).await.unwrap();
        assert_eq!(data, b"value2");
    }

    #[tokio::test]
    async fn test_kvfs_list_keys() {
        let fs = KVFileSystem::new();

        fs.write("/key1", b"val1", 0, WriteFlag::Create)
            .await
            .unwrap();
        fs.write("/key2", b"val2", 0, WriteFlag::Create)
            .await
            .unwrap();
        fs.write("/key3", b"val3", 0, WriteFlag::Create)
            .await
            .unwrap();

        let entries = fs.read_dir("/").await.unwrap();
        assert_eq!(entries.len(), 3);
    }

    #[tokio::test]
    async fn test_kvfs_nested_keys() {
        let fs = KVFileSystem::new();

        // Create parent "directory" first
        fs.mkdir("/user", 0o755).await.unwrap();

        fs.write("/user/123", b"alice", 0, WriteFlag::Create)
            .await
            .unwrap();
        fs.write("/user/456", b"bob", 0, WriteFlag::Create)
            .await
            .unwrap();

        let entries = fs.read_dir("/user").await.unwrap();
        assert_eq!(entries.len(), 2);
    }

    #[tokio::test]
    async fn test_kvfs_delete() {
        let fs = KVFileSystem::new();

        fs.write("/key1", b"value1", 0, WriteFlag::Create)
            .await
            .unwrap();
        fs.remove("/key1").await.unwrap();

        assert!(fs.read("/key1", 0, 0).await.is_err());
    }

    #[tokio::test]
    async fn test_kvfs_rename() {
        let fs = KVFileSystem::new();

        fs.write("/oldkey", b"data", 0, WriteFlag::Create)
            .await
            .unwrap();
        fs.rename("/oldkey", "/newkey").await.unwrap();

        assert!(fs.read("/oldkey", 0, 0).await.is_err());
        let data = fs.read("/newkey", 0, 0).await.unwrap();
        assert_eq!(data, b"data");
    }

    #[tokio::test]
    async fn test_kvfs_plugin() {
        let plugin = KVFSPlugin;
        assert_eq!(plugin.name(), "kvfs");

        let config = PluginConfig {
            name: "kvfs".to_string(),
            mount_path: "/kvfs".to_string(),
            params: HashMap::new(),
        };

        assert!(plugin.validate(&config).await.is_ok());
        assert!(plugin.initialize(config).await.is_ok());
    }
}
