//! MountableFS - A filesystem that routes operations to mounted plugins
//!
//! This module implements the core MountableFS which acts as a router,
//! directing filesystem operations to the appropriate mounted plugin based
//! on the path prefix.

use async_trait::async_trait;
use radix_trie::{Trie, TrieCommon};
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::RwLock;

use super::errors::{Error, Result};
use super::filesystem::FileSystem;
use super::plugin::ServicePlugin;
use super::types::{FileInfo, PluginConfig, WriteFlag};

/// Information about a mounted filesystem
#[derive(Clone)]
struct MountInfo {
    /// The mount path (e.g., "/memfs")
    path: String,

    /// The filesystem instance
    fs: Arc<dyn FileSystem>,

    /// The plugin that created this filesystem
    plugin_name: String,
}

/// MountableFS routes filesystem operations to mounted plugins
///
/// This is the core component that allows multiple filesystem implementations
/// to coexist at different mount points. It uses a radix trie for efficient
/// path-based routing.
pub struct MountableFS {
    /// Radix trie for fast path lookup
    mounts: Arc<RwLock<Trie<String, MountInfo>>>,

    /// Plugin registry for creating new filesystem instances
    registry: Arc<RwLock<HashMap<String, Arc<dyn ServicePlugin>>>>,
}

impl MountableFS {
    /// Create a new MountableFS
    pub fn new() -> Self {
        Self {
            mounts: Arc::new(RwLock::new(Trie::new())),
            registry: Arc::new(RwLock::new(HashMap::new())),
        }
    }

    /// Register a plugin
    ///
    /// # Arguments
    /// * `plugin` - The plugin to register
    pub async fn register_plugin<P: ServicePlugin + 'static>(&self, plugin: P) {
        let name = plugin.name().to_string();
        let mut registry = self.registry.write().await;
        registry.insert(name, Arc::new(plugin));
    }

    /// Mount a filesystem at the specified path
    ///
    /// # Arguments
    /// * `config` - Plugin configuration including mount path
    ///
    /// # Errors
    /// * `Error::MountPointExists` - If a filesystem is already mounted at this path
    /// * `Error::Plugin` - If the plugin is not registered or initialization fails
    pub async fn mount(&self, config: PluginConfig) -> Result<()> {
        let mount_path = config.mount_path.clone();

        // Normalize path (ensure it starts with / and doesn't end with /)
        let normalized_path = normalize_path(&mount_path);

        // Check if already mounted
        {
            let mounts = self.mounts.read().await;
            if mounts.get(&normalized_path).is_some() {
                return Err(Error::MountPointExists(normalized_path));
            }
        }

        // Get plugin from registry
        let plugin = {
            let registry = self.registry.read().await;
            registry
                .get(&config.name)
                .cloned()
                .ok_or_else(|| Error::plugin(format!("Plugin '{}' not registered", config.name)))?
        };

        // Validate configuration
        plugin.validate(&config).await?;

        // Initialize filesystem
        let fs = plugin.initialize(config.clone()).await?;

        // Add to mounts
        let mount_info = MountInfo {
            path: normalized_path.clone(),
            fs: Arc::from(fs),
            plugin_name: config.name.clone(),
        };

        let mut mounts = self.mounts.write().await;
        mounts.insert(normalized_path, mount_info);

        Ok(())
    }

    /// Unmount a filesystem at the specified path
    ///
    /// # Arguments
    /// * `path` - The mount path to unmount
    ///
    /// # Errors
    /// * `Error::MountPointNotFound` - If no filesystem is mounted at this path
    pub async fn unmount(&self, path: &str) -> Result<()> {
        let normalized_path = normalize_path(path);

        let mut mounts = self.mounts.write().await;
        if mounts.remove(&normalized_path).is_none() {
            return Err(Error::MountPointNotFound(normalized_path));
        }

        Ok(())
    }

    /// List all mount points
    ///
    /// # Returns
    /// A vector of tuples containing (mount_path, plugin_name)
    pub async fn list_mounts(&self) -> Vec<(String, String)> {
        let mounts = self.mounts.read().await;
        mounts
            .iter()
            .map(|(path, info)| (path.clone(), info.plugin_name.clone()))
            .collect()
    }

    /// Find the mount point for a given path
    ///
    /// # Arguments
    /// * `path` - The path to look up
    ///
    /// # Returns
    /// A tuple of (mount_info, relative_path) where relative_path is the path
    /// relative to the mount point
    ///
    /// # Errors
    /// * `Error::MountPointNotFound` - If no mount point matches the path
    async fn find_mount(&self, path: &str) -> Result<(MountInfo, String)> {
        let normalized_path = normalize_path(path);
        let mounts = self.mounts.read().await;

        // Find the longest matching prefix using radix trie
        // Check for exact match first
        if let Some(mount_info) = mounts.get(&normalized_path) {
            return Ok((mount_info.clone(), "/".to_string()));
        }

        // Iterate through ancestors to find longest prefix match
        // Start with the longest possible prefix and work backwards
        let mut current = normalized_path.as_str();
        loop {
            if let Some(mount_info) = mounts.get(current) {
                let relative_path = if current == "/" {
                    normalized_path.clone()
                } else {
                    normalized_path[current.len()..].to_string()
                };
                return Ok((mount_info.clone(), relative_path));
            }

            if current == "/" {
                break;
            }

            // Find parent path by removing last component
            match current.rfind('/') {
                Some(0) => current = "/",
                Some(pos) => current = &current[..pos],
                None => break,
            }
        }

        Err(Error::MountPointNotFound(normalized_path))
    }
}

impl Default for MountableFS {
    fn default() -> Self {
        Self::new()
    }
}

/// Normalize a path by ensuring it starts with / and doesn't end with /
fn normalize_path(path: &str) -> String {
    let mut normalized = path.trim().to_string();

    // Ensure starts with /
    if !normalized.starts_with('/') {
        normalized.insert(0, '/');
    }

    // Remove trailing / (except for root)
    if normalized.len() > 1 && normalized.ends_with('/') {
        normalized.pop();
    }

    normalized
}

// Implement FileSystem trait for MountableFS by delegating to mounted filesystems
#[async_trait]
impl FileSystem for MountableFS {
    async fn create(&self, path: &str) -> Result<()> {
        let (mount_info, rel_path) = self.find_mount(path).await?;
        mount_info.fs.create(&rel_path).await
    }

    async fn mkdir(&self, path: &str, mode: u32) -> Result<()> {
        let (mount_info, rel_path) = self.find_mount(path).await?;
        mount_info.fs.mkdir(&rel_path, mode).await
    }

    async fn remove(&self, path: &str) -> Result<()> {
        let (mount_info, rel_path) = self.find_mount(path).await?;
        mount_info.fs.remove(&rel_path).await
    }

    async fn remove_all(&self, path: &str) -> Result<()> {
        let (mount_info, rel_path) = self.find_mount(path).await?;
        mount_info.fs.remove_all(&rel_path).await
    }

    async fn read(&self, path: &str, offset: u64, size: u64) -> Result<Vec<u8>> {
        let (mount_info, rel_path) = self.find_mount(path).await?;
        mount_info.fs.read(&rel_path, offset, size).await
    }

    async fn write(&self, path: &str, data: &[u8], offset: u64, flags: WriteFlag) -> Result<u64> {
        let (mount_info, rel_path) = self.find_mount(path).await?;
        mount_info.fs.write(&rel_path, data, offset, flags).await
    }

    async fn read_dir(&self, path: &str) -> Result<Vec<FileInfo>> {
        let (mount_info, rel_path) = self.find_mount(path).await?;
        mount_info.fs.read_dir(&rel_path).await
    }

    async fn stat(&self, path: &str) -> Result<FileInfo> {
        let (mount_info, rel_path) = self.find_mount(path).await?;
        mount_info.fs.stat(&rel_path).await
    }

    async fn rename(&self, old_path: &str, new_path: &str) -> Result<()> {
        let (mount_info_old, rel_old) = self.find_mount(old_path).await?;
        let (mount_info_new, rel_new) = self.find_mount(new_path).await?;

        // Ensure both paths are on the same mount
        if mount_info_old.path != mount_info_new.path {
            return Err(Error::InvalidOperation(
                "Cannot rename across different mount points".to_string(),
            ));
        }

        mount_info_old.fs.rename(&rel_old, &rel_new).await
    }

    async fn chmod(&self, path: &str, mode: u32) -> Result<()> {
        let (mount_info, rel_path) = self.find_mount(path).await?;
        mount_info.fs.chmod(&rel_path, mode).await
    }

    async fn truncate(&self, path: &str, size: u64) -> Result<()> {
        let (mount_info, rel_path) = self.find_mount(path).await?;
        mount_info.fs.truncate(&rel_path, size).await
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    // Mock filesystem for testing
    struct MockFS {
        name: String,
    }

    impl MockFS {
        fn new(name: &str) -> Self {
            Self {
                name: name.to_string(),
            }
        }
    }

    #[async_trait]
    impl FileSystem for MockFS {
        async fn create(&self, _path: &str) -> Result<()> {
            Ok(())
        }

        async fn mkdir(&self, _path: &str, _mode: u32) -> Result<()> {
            Ok(())
        }

        async fn remove(&self, _path: &str) -> Result<()> {
            Ok(())
        }

        async fn remove_all(&self, _path: &str) -> Result<()> {
            Ok(())
        }

        async fn read(&self, _path: &str, _offset: u64, _size: u64) -> Result<Vec<u8>> {
            Ok(self.name.as_bytes().to_vec())
        }

        async fn write(&self, _path: &str, data: &[u8], _offset: u64, _flags: WriteFlag) -> Result<u64> {
            Ok(data.len() as u64)
        }

        async fn read_dir(&self, _path: &str) -> Result<Vec<FileInfo>> {
            Ok(vec![])
        }

        async fn stat(&self, path: &str) -> Result<FileInfo> {
            Ok(FileInfo::new_file(path.to_string(), 0, 0o644))
        }

        async fn rename(&self, _old_path: &str, _new_path: &str) -> Result<()> {
            Ok(())
        }

        async fn chmod(&self, _path: &str, _mode: u32) -> Result<()> {
            Ok(())
        }
    }

    // Mock plugin for testing
    struct MockPlugin {
        name: String,
    }

    impl MockPlugin {
        fn new(name: &str) -> Self {
            Self {
                name: name.to_string(),
            }
        }
    }

    #[async_trait]
    impl ServicePlugin for MockPlugin {
        fn name(&self) -> &str {
            &self.name
        }

        fn readme(&self) -> &str {
            "Mock plugin for testing"
        }

        async fn validate(&self, _config: &PluginConfig) -> Result<()> {
            Ok(())
        }

        async fn initialize(&self, _config: PluginConfig) -> Result<Box<dyn FileSystem>> {
            Ok(Box::new(MockFS::new(&self.name)))
        }

        fn config_params(&self) -> &[super::super::types::ConfigParameter] {
            &[]
        }
    }

    #[test]
    fn test_normalize_path() {
        assert_eq!(normalize_path("/test"), "/test");
        assert_eq!(normalize_path("/test/"), "/test");
        assert_eq!(normalize_path("test"), "/test");
        assert_eq!(normalize_path("/"), "/");
        assert_eq!(normalize_path(""), "/");
    }

    #[tokio::test]
    async fn test_mountable_fs_creation() {
        let mfs = MountableFS::new();
        let mounts = mfs.list_mounts().await;
        assert!(mounts.is_empty());
    }

    #[tokio::test]
    async fn test_mount_and_unmount() {
        let mfs = MountableFS::new();

        // Register plugin
        mfs.register_plugin(MockPlugin::new("mock")).await;

        // Mount filesystem
        let config = PluginConfig {
            name: "mock".to_string(),
            mount_path: "/mock".to_string(),
            params: HashMap::new(),
        };

        assert!(mfs.mount(config).await.is_ok());

        // Check mount list
        let mounts = mfs.list_mounts().await;
        assert_eq!(mounts.len(), 1);
        assert_eq!(mounts[0].0, "/mock");
        assert_eq!(mounts[0].1, "mock");

        // Unmount
        assert!(mfs.unmount("/mock").await.is_ok());

        // Check mount list is empty
        let mounts = mfs.list_mounts().await;
        assert!(mounts.is_empty());
    }

    #[tokio::test]
    async fn test_mount_duplicate_error() {
        let mfs = MountableFS::new();
        mfs.register_plugin(MockPlugin::new("mock")).await;

        let config = PluginConfig {
            name: "mock".to_string(),
            mount_path: "/mock".to_string(),
            params: HashMap::new(),
        };

        // First mount should succeed
        assert!(mfs.mount(config.clone()).await.is_ok());

        // Second mount at same path should fail
        let result = mfs.mount(config).await;
        assert!(result.is_err());
        assert!(matches!(result.unwrap_err(), Error::MountPointExists(_)));
    }

    #[tokio::test]
    async fn test_unmount_not_found() {
        let mfs = MountableFS::new();

        let result = mfs.unmount("/nonexistent").await;
        assert!(result.is_err());
        assert!(matches!(result.unwrap_err(), Error::MountPointNotFound(_)));
    }

    #[tokio::test]
    async fn test_filesystem_operations() {
        let mfs = MountableFS::new();
        mfs.register_plugin(MockPlugin::new("mock")).await;

        let config = PluginConfig {
            name: "mock".to_string(),
            mount_path: "/mock".to_string(),
            params: HashMap::new(),
        };

        mfs.mount(config).await.unwrap();

        // Test read operation
        let data = mfs.read("/mock/test.txt", 0, 0).await.unwrap();
        assert_eq!(data, b"mock");

        // Test write operation
        let written = mfs.write("/mock/test.txt", b"hello", 0, WriteFlag::Create).await.unwrap();
        assert_eq!(written, 5);

        // Test stat operation
        let info = mfs.stat("/mock/test.txt").await.unwrap();
        assert_eq!(info.name, "/test.txt");
    }

    #[tokio::test]
    async fn test_path_routing() {
        let mfs = MountableFS::new();
        mfs.register_plugin(MockPlugin::new("mock1")).await;
        mfs.register_plugin(MockPlugin::new("mock2")).await;

        // Mount two filesystems
        let config1 = PluginConfig {
            name: "mock1".to_string(),
            mount_path: "/fs1".to_string(),
            params: HashMap::new(),
        };

        let config2 = PluginConfig {
            name: "mock2".to_string(),
            mount_path: "/fs2".to_string(),
            params: HashMap::new(),
        };

        mfs.mount(config1).await.unwrap();
        mfs.mount(config2).await.unwrap();

        // Test routing to different filesystems
        let data1 = mfs.read("/fs1/file.txt", 0, 0).await.unwrap();
        assert_eq!(data1, b"mock1");

        let data2 = mfs.read("/fs2/file.txt", 0, 0).await.unwrap();
        assert_eq!(data2, b"mock2");
    }

    #[tokio::test]
    async fn test_rename_across_mounts_error() {
        let mfs = MountableFS::new();
        mfs.register_plugin(MockPlugin::new("mock1")).await;
        mfs.register_plugin(MockPlugin::new("mock2")).await;

        let config1 = PluginConfig {
            name: "mock1".to_string(),
            mount_path: "/fs1".to_string(),
            params: HashMap::new(),
        };

        let config2 = PluginConfig {
            name: "mock2".to_string(),
            mount_path: "/fs2".to_string(),
            params: HashMap::new(),
        };

        mfs.mount(config1).await.unwrap();
        mfs.mount(config2).await.unwrap();

        // Try to rename across different mounts - should fail
        let result = mfs.rename("/fs1/file.txt", "/fs2/file.txt").await;
        assert!(result.is_err());
        assert!(matches!(result.unwrap_err(), Error::InvalidOperation(_)));
    }

    #[tokio::test]
    async fn test_concurrent_operations() {
        use tokio::task;

        let mfs = Arc::new(MountableFS::new());
        mfs.register_plugin(MockPlugin::new("mock")).await;

        let config = PluginConfig {
            name: "mock".to_string(),
            mount_path: "/mock".to_string(),
            params: HashMap::new(),
        };

        mfs.mount(config).await.unwrap();

        // Spawn multiple concurrent read operations
        let mut handles = vec![];
        for i in 0..10 {
            let mfs_clone = Arc::clone(&mfs);
            let handle = task::spawn(async move {
                let path = format!("/mock/file{}.txt", i);
                mfs_clone.read(&path, 0, 0).await
            });
            handles.push(handle);
        }

        // Wait for all operations to complete
        for handle in handles {
            let result = handle.await.unwrap();
            assert!(result.is_ok());
            assert_eq!(result.unwrap(), b"mock");
        }
    }

    #[tokio::test]
    async fn test_concurrent_mount_unmount() {
        use tokio::task;

        let mfs = Arc::new(MountableFS::new());

        // Register multiple plugins
        for i in 0..5 {
            mfs.register_plugin(MockPlugin::new(&format!("mock{}", i))).await;
        }

        // Spawn concurrent mount operations
        let mut handles = vec![];
        for i in 0..5 {
            let mfs_clone = Arc::clone(&mfs);
            let handle = task::spawn(async move {
                let config = PluginConfig {
                    name: format!("mock{}", i),
                    mount_path: format!("/mock{}", i),
                    params: HashMap::new(),
                };
                mfs_clone.mount(config).await
            });
            handles.push(handle);
        }

        // Wait for all mounts to complete
        for handle in handles {
            let result = handle.await.unwrap();
            assert!(result.is_ok());
        }

        // Verify all mounts
        let mounts = mfs.list_mounts().await;
        assert_eq!(mounts.len(), 5);

        // Concurrent unmount
        let mut handles = vec![];
        for i in 0..5 {
            let mfs_clone = Arc::clone(&mfs);
            let handle = task::spawn(async move {
                mfs_clone.unmount(&format!("/mock{}", i)).await
            });
            handles.push(handle);
        }

        // Wait for all unmounts
        for handle in handles {
            let result = handle.await.unwrap();
            assert!(result.is_ok());
        }

        // Verify all unmounted
        let mounts = mfs.list_mounts().await;
        assert!(mounts.is_empty());
    }
}
