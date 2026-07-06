//! MountableFS - A filesystem that routes operations to mounted plugins
//!
//! This module implements the core MountableFS which acts as a router,
//! directing filesystem operations to the appropriate mounted plugin based
//! on the path prefix.

use async_trait::async_trait;
use radix_trie::{Trie, TrieCommon};
use serde_json::Value;
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::RwLock;
use tracing::warn;

use crate::multibackend::factory::build_multi_write_fs;
use crate::multibackend::types::MultiBackendBuildContext;
use crate::shape::validate::ensure_backend_shape;

/// Python-side transaction path-lock marker — hidden in all listing/grep operations.
const PATH_LOCK_FILE: &str = ".path.ovlock";

use super::encryption_wrapper::EncryptionWrappedFS;
use super::errors::{Error, Result};
use super::filesystem::{sort_directory_entries, FileSystem};
use super::multibackend_wrapper::MultiWriteWrappedFS;
use super::plugin::ServicePlugin;
use super::stats::{FilesystemStats, StatsCollector};
use super::stats_wrapper::StatsWrappedFS;
use super::types::{BackendsConfig, FileInfo, GrepResult, PluginConfig, TreeEntry, WriteFlag};
#[cfg(feature = "cache")]
use crate::cache::{
    CacheNamespace, CachePolicy, CacheProvider, CacheTraversalMode, CachedFileSystem,
};

/// Information about a mounted filesystem
#[derive(Clone)]
struct MountInfo {
    /// The mount path (e.g., "/memfs")
    path: String,

    /// The filesystem instance (wrapped with statistics)
    fs: Arc<dyn FileSystem>,

    /// The raw plugin backend before encryption wrapping (for read_raw/write_raw).
    /// None for multi-write mounts.
    raw_fs: Option<Arc<dyn FileSystem>>,

    /// The statistics collector for this mount
    stats: Arc<StatsCollector>,

    /// The plugin that created this filesystem
    plugin_name: String,
}

const DEFAULT_RAW_COPY_CHUNK_SIZE: usize = 8 * 1024 * 1024;

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

    /// Global encryption config (root_key + provider_type), shared across all mounts.
    /// When set, single-backend mounts are wrapped with EncryptionWrappedFS;
    /// multi-write mounts use it for per-backend encryption decisions.
    encryption_root_key: RwLock<Option<[u8; 32]>>,
    encryption_provider_type: RwLock<Option<u8>>,

    /// Optional cache configuration shared by mounted filesystems.
    #[cfg(feature = "cache")]
    cache: Option<MountCacheConfig>,
}

#[cfg(feature = "cache")]
#[derive(Clone)]
struct MountCacheConfig {
    provider: Arc<dyn CacheProvider>,
    namespace: CacheNamespace,
    policy: CachePolicy,
}

impl MountableFS {
    /// Return whether one plugin may be wrapped by EncryptionWrappedFS.
    ///
    /// Encrypted whole-file publish now depends on overwrite-on-publish
    /// semantics (`replace(temp, final)`). Keep this gate at the wrapper
    /// creation entrypoint so unsupported backends fail fast before mounting.
    fn supports_encrypted_publish(plugin_name: &str) -> bool {
        matches!(plugin_name, "localfs" | "s3fs" | "memfs")
    }

    /// Return the raw backend for one mounted path when raw access is supported.
    fn raw_backend_for_mount<'a>(
        mount_info: &'a MountInfo,
        op_name: &str,
    ) -> Result<&'a Arc<dyn FileSystem>> {
        if Self::as_multiwrite(&mount_info.fs).is_some() {
            return Err(Error::invalid_operation(format!(
                "{} is not supported for multi-write mounts",
                op_name
            )));
        }
        mount_info.raw_fs.as_ref().ok_or_else(|| {
            Error::internal(format!(
                "raw backend is unavailable for mount '{}'",
                mount_info.path
            ))
        })
    }

    /// Try to unwrap a mounted filesystem stack to the underlying multi-write wrapper.
    fn as_multiwrite(fs: &Arc<dyn FileSystem>) -> Option<&MultiWriteWrappedFS> {
        Self::as_multiwrite_ref(fs.as_ref())
    }

    fn as_multiwrite_ref(fs: &dyn FileSystem) -> Option<&MultiWriteWrappedFS> {
        let any = fs as &dyn std::any::Any;
        if let Some(mw) = any.downcast_ref::<MultiWriteWrappedFS>() {
            return Some(mw);
        }
        if let Some(stats) = any.downcast_ref::<StatsWrappedFS>() {
            return Self::as_multiwrite(stats.inner_fs());
        }
        if let Some(enc) = any.downcast_ref::<EncryptionWrappedFS>() {
            return Self::as_multiwrite(enc.inner_fs());
        }
        #[cfg(feature = "cache")]
        if let Some(cache) = any.downcast_ref::<CachedFileSystem>() {
            return Self::as_multiwrite_ref(cache.inner_fs());
        }
        #[cfg(feature = "cache")]
        if let Some(arc) = any.downcast_ref::<ArcFileSystem>() {
            return Self::as_multiwrite(&arc.0);
        }
        None
    }

    #[cfg(feature = "cache")]
    fn as_cached(fs: &Arc<dyn FileSystem>) -> Option<&CachedFileSystem> {
        let any = fs.as_ref() as &dyn std::any::Any;
        if let Some(cache) = any.downcast_ref::<CachedFileSystem>() {
            return Some(cache);
        }
        if let Some(stats) = any.downcast_ref::<StatsWrappedFS>() {
            return Self::as_cached(stats.inner_fs());
        }
        if let Some(enc) = any.downcast_ref::<EncryptionWrappedFS>() {
            return Self::as_cached(enc.inner_fs());
        }
        None
    }

    #[cfg(feature = "cache")]
    async fn invalidate_cache_after_raw_write(mount_info: &MountInfo, rel_path: &str) {
        if let Some(cache) = Self::as_cached(&mount_info.fs) {
            cache.invalidate_external_write(rel_path).await;
        }
    }

    /// Query multi-write sync status for a mounted path.
    pub async fn system_sync_status(&self, path: &str) -> Result<Value> {
        let (mount_info, rel_path) = self.find_mount(path).await?;
        let multiwrite = Self::as_multiwrite(&mount_info.fs)
            .ok_or_else(|| Error::invalid_operation("mounted filesystem is not multi-write"))?;
        multiwrite.system_sync_status(&rel_path).await
    }

    /// Manually retry pending multi-write sync operations under a mounted path.
    pub async fn system_sync_retry(&self, path: &str) -> Result<Value> {
        let (mount_info, rel_path) = self.find_mount(path).await?;
        let multiwrite = Self::as_multiwrite(&mount_info.fs)
            .ok_or_else(|| Error::invalid_operation("mounted filesystem is not multi-write"))?;
        multiwrite.system_sync_retry(&rel_path).await
    }

    /// Create a new MountableFS
    pub fn new() -> Self {
        Self {
            mounts: Arc::new(RwLock::new(Trie::new())),
            registry: Arc::new(RwLock::new(HashMap::new())),
            encryption_root_key: RwLock::new(None),
            encryption_provider_type: RwLock::new(None),
            #[cfg(feature = "cache")]
            cache: None,
        }
    }

    /// Create a new MountableFS that transparently wraps mounted backends with cache.
    ///
    /// Encrypted multi-write mounts skip the mount-level cache because their encryption boundary
    /// lives inside `MultiWriteWrappedFS`; caching outside it would store plaintext.
    #[cfg(feature = "cache")]
    pub fn with_cache(
        provider: Arc<dyn CacheProvider>,
        namespace: CacheNamespace,
        policy: CachePolicy,
    ) -> Self {
        Self {
            mounts: Arc::new(RwLock::new(Trie::new())),
            registry: Arc::new(RwLock::new(HashMap::new())),
            encryption_root_key: RwLock::new(None),
            encryption_provider_type: RwLock::new(None),
            cache: Some(MountCacheConfig {
                provider,
                namespace,
                policy,
            }),
        }
    }

    /// Set the global encryption configuration for per-mount encryption wrapping.
    pub async fn set_encryption_config(
        &self,
        root_key: Option<[u8; 32]>,
        provider_type: Option<u8>,
    ) {
        *self.encryption_root_key.write().await = root_key;
        *self.encryption_provider_type.write().await = provider_type;
    }

    /// Get the encryption root key and provider type.
    async fn get_encryption_config(&self) -> (Option<[u8; 32]>, Option<u8>) {
        let rk = *self.encryption_root_key.read().await;
        let pt = *self.encryption_provider_type.read().await;
        (rk, pt)
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

        let (enc_root_key, enc_provider_type) = self.get_encryption_config().await;
        #[cfg(feature = "cache")]
        let encryption_enabled = enc_root_key.is_some() && enc_provider_type.is_some();

        // Branch: multi-write vs single backend
        let (inner_fs, raw_fs): (Arc<dyn FileSystem>, Option<Arc<dyn FileSystem>>) = match &config
            .backups
        {
            None => {
                // Single backend: initialize plugin, optionally wrap raw storage with cache, then
                // wrap with encryption. This keeps shared cache providers ciphertext-only.
                // Control plugins (queuefs, serverinfofs) are dynamic virtual filesystems; never
                // cache or encrypt them.
                let raw = plugin.initialize(config.clone()).await?;
                let raw_arc: Arc<dyn FileSystem> = Arc::from(raw);
                let is_control_plugin = matches!(config.name.as_str(), "queuefs" | "serverinfofs");
                if !is_control_plugin {
                    ensure_backend_shape(
                        &raw_arc,
                        &config.name,
                        enc_root_key.is_some() && enc_provider_type.is_some(),
                        enc_provider_type,
                        enc_root_key,
                    )
                    .await?;
                }
                #[cfg(feature = "cache")]
                let storage_fs = if is_control_plugin {
                    raw_arc.clone()
                } else {
                    self.maybe_wrap_cache(raw_arc.clone(), &normalized_path)
                };
                #[cfg(not(feature = "cache"))]
                let storage_fs = raw_arc.clone();

                let inner: Arc<dyn FileSystem> = if is_control_plugin {
                    storage_fs
                } else {
                    match (enc_root_key, enc_provider_type) {
                        (Some(rk), Some(pt)) => {
                            if !Self::supports_encrypted_publish(&config.name) {
                                return Err(Error::config(format!(
                                      "encrypted backend '{}' must support replace() semantics",
                                      config.name
                                  )));
                            }
                            Arc::new(EncryptionWrappedFS::new(storage_fs, rk, pt))
                        }
                        _ => storage_fs,
                    }
                };
                (inner, Some(raw_arc))
            }
            Some(bc) => {
                // Multi-write: build MultiWriteWrappedFS
                warn!(
                    "multi-write metadata locking is process-local only; multi-instance shared-primary mounts are not safe"
                );
                let mw = self.build_multi_write_fs(&config, bc).await?;
                let arc: Arc<dyn FileSystem> = Arc::new(mw);
                #[cfg(feature = "cache")]
                let arc = if encryption_enabled {
                    // Multi-write owns per-backend encryption internally. A mount-level cache
                    // would sit outside that boundary and store plaintext, so skip it.
                    warn!(
                        "cache is disabled for encrypted multi-write mount '{}': mount-level cache would store plaintext",
                        normalized_path
                    );
                    arc
                } else {
                    match &self.cache {
                        Some(cache) => Arc::new(CachedFileSystem::new(
                            Box::new(ArcFileSystem(arc)),
                            cache.provider.clone(),
                            mount_namespace(&cache.namespace, &normalized_path),
                            cache
                                .policy
                                .clone()
                                .with_traversal_mode(CacheTraversalMode::Backend),
                        )),
                        None => arc,
                    }
                };
                (arc, None)
            }
        };

        // Wrap with statistics
        let wrapped_fs = StatsWrappedFS::with_arc(inner_fs);
        let stats_collector = wrapped_fs.stats_collector();

        // Add to mounts
        let mount_info = MountInfo {
            path: normalized_path.clone(),
            fs: Arc::from(Box::new(wrapped_fs) as Box<dyn FileSystem>),
            raw_fs,
            stats: stats_collector,
            plugin_name: config.name.clone(),
        };

        let mut mounts = self.mounts.write().await;
        mounts.insert(normalized_path, mount_info);

        Ok(())
    }

    /// Build a MultiWriteWrappedFS from primary + backup configurations.
    ///
    /// Steps:
    /// 1. Initialize primary backend via registry, optionally wrap with EncryptionWrappedFS
    /// 2. Initialize each backup backend via registry, optionally wrap with EncryptionWrappedFS
    /// 3. Validate: unique names, primary has no operations, redirect targets exist
    /// 4. Assemble MultiWriteWrappedFS
    async fn build_multi_write_fs(
        &self,
        config: &PluginConfig,
        bc: &BackendsConfig,
    ) -> Result<MultiWriteWrappedFS> {
        let (enc_root_key, enc_provider_type) = self.get_encryption_config().await;
        build_multi_write_fs(
            &self.registry,
            config,
            bc,
            MultiBackendBuildContext {
                enc_root_key,
                enc_provider_type,
            },
        )
        .await
    }

    #[cfg(feature = "cache")]
    fn maybe_wrap_cache(&self, fs: Arc<dyn FileSystem>, mount_path: &str) -> Arc<dyn FileSystem> {
        match &self.cache {
            Some(cache) => {
                let policy = if cache.policy.traversal_mode() == CacheTraversalMode::CachedTraversal
                    && Self::as_multiwrite(&fs).is_some()
                {
                    cache
                        .policy
                        .clone()
                        .with_traversal_mode(CacheTraversalMode::Backend)
                } else {
                    cache.policy.clone()
                };
                Arc::new(CachedFileSystem::new(
                    Box::new(ArcFileSystem(fs)),
                    cache.provider.clone(),
                    mount_namespace(&cache.namespace, mount_path),
                    policy,
                ))
            }
            None => fs,
        }
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
        let mount_info = {
            let mounts = self.mounts.read().await;
            mounts
                .get(&normalized_path)
                .cloned()
                .ok_or_else(|| Error::MountPointNotFound(normalized_path.clone()))?
        };

        if let Some(multiwrite) = Self::as_multiwrite(&mount_info.fs) {
            multiwrite.shutdown().await?;
        }

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

    /// Get statistics for a specific mount point
    ///
    /// # Arguments
    /// * `path` - The mount path
    ///
    /// # Returns
    /// The filesystem statistics for the mount
    pub async fn get_mount_stats(&self, path: &str) -> Result<FilesystemStats> {
        let normalized_path = normalize_path(path);
        let mounts = self.mounts.read().await;

        let mount_info = mounts
            .get(&normalized_path)
            .ok_or_else(|| Error::MountPointNotFound(normalized_path.clone()))?;

        Ok(mount_info.stats.snapshot().await)
    }

    /// Get aggregated statistics for all mount points
    ///
    /// # Returns
    /// A map of mount path to filesystem statistics
    pub async fn get_all_stats(&self) -> HashMap<String, (String, FilesystemStats)> {
        let mounts = self.mounts.read().await;
        let mut result = HashMap::new();

        for (path, info) in mounts.iter() {
            let stats = info.stats.snapshot().await;
            result.insert(path.clone(), (info.plugin_name.clone(), stats));
        }

        result
    }

    /// Read raw bytes from the underlying plugin backend, bypassing the encryption layer.
    ///
    /// Used by tests to verify ciphertext on disk and by cp/persist for verbatim blob copies.
    pub async fn read_raw(&self, path: &str, offset: u64, size: u64) -> Result<Vec<u8>> {
        let (mount_info, rel_path) = self.find_mount(path).await?;
        let raw = Self::raw_backend_for_mount(&mount_info, "read_raw")?;
        raw.read(&rel_path, offset, size).await
    }

    /// Write raw bytes to the underlying plugin backend, bypassing the encryption layer.
    ///
    /// Counterpart to `read_raw` for cp/persist verbatim blob copies.
    pub async fn write_raw(&self, path: &str, data: &[u8], flags: WriteFlag) -> Result<u64> {
        let (mount_info, rel_path) = self.find_mount(path).await?;
        let raw = Self::raw_backend_for_mount(&mount_info, "write_raw")?;
        raw.write(&rel_path, data, 0, flags).await
    }

    /// Copy one file within the same mounted filesystem while preserving raw bytes when possible.
    pub async fn copy_within_mount(&self, src_path: &str, dst_path: &str) -> Result<bool> {
        let (src_mount, src_rel_path) = self.find_mount(src_path).await?;
        let (dst_mount, dst_rel_path) = self.find_mount(dst_path).await?;

        if src_mount.path != dst_mount.path {
            return Ok(false);
        }

        if normalize_path(&src_rel_path) == normalize_path(&dst_rel_path) {
            return Ok(true);
        }

        if let Some(multiwrite) = Self::as_multiwrite(&src_mount.fs) {
            return multiwrite
                .copy_within_primary(&src_rel_path, &dst_rel_path)
                .await;
        }

        let Some(raw_backend) = src_mount.raw_fs.clone() else {
            return Ok(false);
        };

        Self::copy_raw_within_mount(raw_backend, &src_rel_path, &dst_rel_path).await?;
        #[cfg(feature = "cache")]
        Self::invalidate_cache_after_raw_write(&dst_mount, &dst_rel_path).await;
        Ok(true)
    }

    /// Copy one file within one raw backend in bounded-size chunks.
    async fn copy_raw_within_mount(
        raw_backend: Arc<dyn FileSystem>,
        src_rel_path: &str,
        dst_rel_path: &str,
    ) -> Result<()> {
        let source_info = raw_backend.stat(src_rel_path).await?;
        if source_info.is_dir {
            return Err(Error::IsADirectory(src_rel_path.to_string()));
        }

        raw_backend.ensure_parent_dirs(dst_rel_path, 0o755).await?;

        if source_info.size == 0 {
            raw_backend
                .write(dst_rel_path, &[], 0, WriteFlag::Create)
                .await?;
            return Ok(());
        }

        let mut offset = 0u64;
        while offset < source_info.size {
            let chunk_len = (source_info.size - offset).min(DEFAULT_RAW_COPY_CHUNK_SIZE as u64);
            let chunk = raw_backend.read(src_rel_path, offset, chunk_len).await?;
            let flag = if offset == 0 {
                WriteFlag::Create
            } else {
                WriteFlag::None
            };
            raw_backend
                .write(dst_rel_path, &chunk, offset, flag)
                .await?;
            offset = offset.saturating_add(chunk.len() as u64);
            if chunk.is_empty() {
                return Err(Error::internal(format!(
                    "raw backend returned empty chunk while copying '{}' -> '{}'",
                    src_rel_path, dst_rel_path
                )));
            }
        }

        Ok(())
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

#[cfg(feature = "cache")]
struct ArcFileSystem(Arc<dyn FileSystem>);

#[cfg(feature = "cache")]
#[async_trait]
impl FileSystem for ArcFileSystem {
    async fn create(&self, path: &str) -> Result<()> {
        self.0.create(path).await
    }

    async fn mkdir(&self, path: &str, mode: u32) -> Result<()> {
        self.0.mkdir(path, mode).await
    }

    async fn remove(&self, path: &str) -> Result<()> {
        self.0.remove(path).await
    }

    async fn remove_all(&self, path: &str) -> Result<()> {
        self.0.remove_all(path).await
    }

    async fn read(&self, path: &str, offset: u64, size: u64) -> Result<Vec<u8>> {
        self.0.read(path, offset, size).await
    }

    async fn write(&self, path: &str, data: &[u8], offset: u64, flags: WriteFlag) -> Result<u64> {
        self.0.write(path, data, offset, flags).await
    }

    async fn read_dir(&self, path: &str) -> Result<Vec<FileInfo>> {
        self.0.read_dir(path).await
    }

    async fn stat(&self, path: &str) -> Result<FileInfo> {
        self.0.stat(path).await
    }

    async fn rename(&self, old_path: &str, new_path: &str) -> Result<()> {
        self.0.rename(old_path, new_path).await
    }

    async fn replace(&self, src_path: &str, dst_path: &str) -> Result<()> {
        self.0.replace(src_path, dst_path).await
    }

    async fn chmod(&self, path: &str, mode: u32) -> Result<()> {
        self.0.chmod(path, mode).await
    }
}

#[cfg(feature = "cache")]
fn mount_namespace(base: &CacheNamespace, mount_path: &str) -> CacheNamespace {
    CacheNamespace::new(format!("{}:{}", base.as_str(), mount_path))
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
        let mut entries = mount_info.fs.read_dir(&rel_path).await?;
        entries.retain(|e| e.name != PATH_LOCK_FILE);
        sort_directory_entries(&mut entries);
        Ok(entries)
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

    async fn replace(&self, src_path: &str, dst_path: &str) -> Result<()> {
        let (mount_info_src, rel_src) = self.find_mount(src_path).await?;
        let (mount_info_dst, rel_dst) = self.find_mount(dst_path).await?;

        if mount_info_src.path != mount_info_dst.path {
            return Err(Error::InvalidOperation(
                "Cannot replace across different mount points".to_string(),
            ));
        }

        mount_info_src.fs.replace(&rel_src, &rel_dst).await
    }

    async fn chmod(&self, path: &str, mode: u32) -> Result<()> {
        let (mount_info, rel_path) = self.find_mount(path).await?;
        mount_info.fs.chmod(&rel_path, mode).await
    }

    async fn truncate(&self, path: &str, size: u64) -> Result<()> {
        let (mount_info, rel_path) = self.find_mount(path).await?;
        mount_info.fs.truncate(&rel_path, size).await
    }

    async fn ensure_parent_dirs(&self, path: &str, mode: u32) -> Result<()> {
        let (mount_info, rel_path) = self.find_mount(path).await?;
        mount_info.fs.ensure_parent_dirs(&rel_path, mode).await
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
        // Route grep to the mounted plugin so plugin-specific fast paths (e.g. localfs + rg)
        // can take effect. If a plugin doesn't override grep, it will fall back to the trait
        // default implementation on that plugin instance (still correct, just slower).
        let (mount_info, rel_path) = self.find_mount(path).await?;

        // Exclude path only applies when it resolves to the same mount point; otherwise it
        // should not affect searching under `path`.
        let exclude_rel: Option<String> = match exclude_path {
            None => None,
            Some(excl_abs) => match self.find_mount(excl_abs).await {
                Ok((exclude_mount, excl_rel)) => {
                    if exclude_mount.path == mount_info.path {
                        Some(excl_rel)
                    } else {
                        None
                    }
                }
                Err(_) => None,
            },
        };

        let mut result = mount_info
            .fs
            .grep(
                &rel_path,
                pattern,
                recursive,
                case_insensitive,
                node_limit,
                exclude_rel.as_deref(),
                level_limit,
            )
            .await?;

        // Filter out path-lock markers.
        result.matches.retain(|m| {
            m.file
                .rsplit('/')
                .next()
                .map_or(true, |name| name != PATH_LOCK_FILE)
        });
        result.count = result.matches.len();
        Ok(result)
    }

    async fn tree_directory(
        &self,
        path: &str,
        show_hidden: bool,
        node_limit: Option<usize>,
        level_limit: Option<usize>,
    ) -> Result<Vec<TreeEntry>> {
        let (mount_info, rel_path) = self.find_mount(path).await?;

        let mount_prefix = if mount_info.path == "/" {
            String::new()
        } else {
            mount_info.path.clone()
        };

        let mut entries = mount_info
            .fs
            .tree_directory(&rel_path, show_hidden, node_limit, level_limit)
            .await?;

        for entry in &mut entries {
            if !mount_prefix.is_empty() {
                entry.path = if entry.path == "/" {
                    mount_prefix.clone()
                } else {
                    format!("{}{}", mount_prefix, entry.path)
                };
            }
        }

        // Filter out path-lock markers.
        entries.retain(|e| {
            e.path
                .rsplit('/')
                .next()
                .map_or(true, |name| name != PATH_LOCK_FILE)
        });

        Ok(entries)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::BackendItemConfig;
    use crate::core::RedirectPolicy;
    use crate::shape::SHAPE_MANIFEST_PATH;
    use serde_json::Value;
    use std::collections::HashMap;
    use std::sync::atomic::{AtomicU64, Ordering};

    /// Helper to create a PluginConfig for tests with new fields defaulted.
    fn test_config(name: &str, mount_path: &str) -> PluginConfig {
        PluginConfig::single_backend(name, mount_path, HashMap::new())
    }

    /// Helper to create a simple multi-write PluginConfig for tests.
    fn multiwrite_test_config(
        primary_backend: &str,
        backup_backend: &str,
        mount_path: &str,
    ) -> PluginConfig {
        PluginConfig {
            name: primary_backend.to_string(),
            mount_path: mount_path.to_string(),
            params: HashMap::new(),
            backups: Some(BackendsConfig {
                sync_type: "async".to_string(),
                write_ack_count: None,
                write_ack_timeout_ms: None,
                write_concurrency: None,
                retry_interval_ms: None,
                retry_backoff_base_ms: None,
                retry_max_retries_per_round: None,
                retry_quarantine_after_failures: None,
                read_probe_cache_ttl_ms: None,
                items: vec![BackendItemConfig {
                    name: "backup1".to_string(),
                    backend: backup_backend.to_string(),
                    params: Value::Null,
                    timeout: None,
                    encryption: None,
                    operations: None,
                    excludes: None,
                }],
            }),
            ..PluginConfig::default()
        }
    }

    // Mock filesystem for testing
    struct MockFS {
        name: String,
        tree_entries: Vec<TreeEntry>,
    }

    impl MockFS {
        fn new(name: &str) -> Self {
            Self {
                name: name.to_string(),
                tree_entries: vec![],
            }
        }

        fn with_tree_entries(name: &str, entries: Vec<TreeEntry>) -> Self {
            Self {
                name: name.to_string(),
                tree_entries: entries,
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

        async fn read(&self, path: &str, _offset: u64, _size: u64) -> Result<Vec<u8>> {
            if path == SHAPE_MANIFEST_PATH
                || path.ends_with(".redirect.json")
                || path.ends_with(".sync_log.json")
            {
                return Err(Error::not_found(path));
            }
            Ok(self.name.as_bytes().to_vec())
        }

        async fn write(
            &self,
            _path: &str,
            data: &[u8],
            _offset: u64,
            _flags: WriteFlag,
        ) -> Result<u64> {
            Ok(data.len() as u64)
        }

        async fn read_dir(&self, _path: &str) -> Result<Vec<FileInfo>> {
            Ok(self
                .tree_entries
                .iter()
                .map(|entry| entry.info.clone())
                .collect())
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

        async fn grep(
            &self,
            path: &str,
            pattern: &str,
            _recursive: bool,
            _case_insensitive: bool,
            _node_limit: Option<usize>,
            _exclude_path: Option<&str>,
            _level_limit: Option<usize>,
        ) -> Result<GrepResult> {
            let mut out = GrepResult::new();
            // Encode the received rel_path into the match so the test can assert routing worked.
            out.add_match(path.to_string(), 1, pattern.to_string());
            Ok(out)
        }

        async fn tree_directory(
            &self,
            _path: &str,
            _show_hidden: bool,
            _node_limit: Option<usize>,
            _level_limit: Option<usize>,
        ) -> Result<Vec<TreeEntry>> {
            Ok(self.tree_entries.clone())
        }
    }

    // Mock plugin for testing
    struct MockPlugin {
        name: String,
        tree_entries: Option<Vec<TreeEntry>>,
    }

    struct CountingPlugin {
        reads: Arc<AtomicU64>,
    }

    struct CountingFs {
        reads: Arc<AtomicU64>,
    }

    impl MockPlugin {
        fn new(name: &str) -> Self {
            Self {
                name: name.to_string(),
                tree_entries: None,
            }
        }

        fn with_tree_entries(name: &str, entries: Vec<TreeEntry>) -> Self {
            Self {
                name: name.to_string(),
                tree_entries: Some(entries),
            }
        }
    }

    #[async_trait]
    impl ServicePlugin for CountingPlugin {
        fn name(&self) -> &str {
            "counting"
        }

        fn readme(&self) -> &str {
            "Counting plugin for cache tests"
        }

        async fn validate(&self, _config: &PluginConfig) -> Result<()> {
            Ok(())
        }

        async fn initialize(&self, _config: PluginConfig) -> Result<Box<dyn FileSystem>> {
            Ok(Box::new(CountingFs {
                reads: self.reads.clone(),
            }))
        }

        fn config_params(&self) -> &[super::super::types::ConfigParameter] {
            &[]
        }
    }

    #[async_trait]
    impl FileSystem for CountingFs {
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

        async fn read(&self, path: &str, _offset: u64, _size: u64) -> Result<Vec<u8>> {
            self.reads.fetch_add(1, Ordering::Relaxed);
            Ok(format!("backend:{path}").into_bytes())
        }

        async fn write(
            &self,
            _path: &str,
            data: &[u8],
            _offset: u64,
            _flags: WriteFlag,
        ) -> Result<u64> {
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
            if let Some(ref entries) = self.tree_entries {
                Ok(Box::new(MockFS::with_tree_entries(
                    &self.name,
                    entries.clone(),
                )))
            } else {
                Ok(Box::new(MockFS::new(&self.name)))
            }
        }

        fn config_params(&self) -> &[super::super::types::ConfigParameter] {
            &[]
        }
    }

    /// Create a MountableFS with one registered and mounted mock plugin.
    async fn mounted_mock(name: &str, mount_path: &str) -> MountableFS {
        let mfs = MountableFS::new();
        mfs.register_plugin(MockPlugin::new(name)).await;
        mfs.mount(test_config(name, mount_path)).await.unwrap();
        mfs
    }

    /// Create a MountableFS with two mounted mock plugins.
    async fn mounted_two_mocks() -> MountableFS {
        let mfs = MountableFS::new();
        for (name, mount_path) in [("mock1", "/fs1"), ("mock2", "/fs2")] {
            mfs.register_plugin(MockPlugin::new(name)).await;
            mfs.mount(test_config(name, mount_path)).await.unwrap();
        }
        mfs
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
        let config = test_config("mock", "/mock");

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

    #[cfg(feature = "cache")]
    #[tokio::test]
    async fn mount_wraps_backend_with_cache_when_configured() {
        use crate::cache::{CacheNamespace, CachePolicy, MemoryCacheProvider};

        let reads = Arc::new(AtomicU64::new(0));
        let mfs = MountableFS::with_cache(
            Arc::new(MemoryCacheProvider::new()),
            CacheNamespace::new("mount-test"),
            CachePolicy::default(),
        );
        mfs.register_plugin(CountingPlugin {
            reads: reads.clone(),
        })
        .await;

        let config = PluginConfig::single_backend("counting", "/cached", HashMap::new());

        mfs.mount(config).await.unwrap();
        reads.store(0, Ordering::Relaxed);

        assert_eq!(
            mfs.read("/cached/file.txt", 0, 0).await.unwrap(),
            b"backend:/file.txt"
        );
        assert_eq!(
            mfs.read("/cached/file.txt", 0, 0).await.unwrap(),
            b"backend:/file.txt"
        );
        assert_eq!(reads.load(Ordering::Relaxed), 1);
    }

    #[cfg(feature = "cache")]
    #[tokio::test]
    async fn queuefs_mount_bypasses_cache_even_when_cache_is_configured() {
        use crate::cache::{CacheNamespace, CachePolicy, MemoryCacheProvider};
        use crate::plugins::QueueFSPlugin;

        let provider = Arc::new(MemoryCacheProvider::new());
        let mfs = MountableFS::with_cache(
            provider.clone(),
            CacheNamespace::new("queue-cache-test"),
            CachePolicy::default().with_bypass_prefix("/queue"),
        );
        mfs.register_plugin(QueueFSPlugin::new()).await;
        mfs.mount(PluginConfig::single_backend(
            "queuefs",
            "/queue",
            HashMap::new(),
        ))
        .await
        .unwrap();
        mfs.mkdir("/queue/Embedding", 0o755).await.unwrap();

        assert_eq!(
            mfs.read("/queue/Embedding/size", 0, 0).await.unwrap(),
            b"0"
        );
        mfs.write(
            "/queue/Embedding/enqueue",
            br#"{"id":"one"}"#,
            0,
            WriteFlag::Create,
        )
        .await
        .unwrap();

        assert_eq!(
            mfs.read("/queue/Embedding/size", 0, 0).await.unwrap(),
            b"1",
            "queuefs size is dynamic and must not be served from cache"
        );
        assert!(
            provider.keys().await.is_empty(),
            "queuefs control filesystem should not populate shared cache"
        );
    }

    #[cfg(feature = "cache")]
    #[tokio::test]
    async fn copy_within_mount_overwrite_invalidates_cached_destination() {
        use crate::cache::{CacheNamespace, CachePolicy, MemoryCacheProvider};
        use crate::plugins::MemFSPlugin;

        let mfs = MountableFS::with_cache(
            Arc::new(MemoryCacheProvider::new()),
            CacheNamespace::new("copy-cache-test"),
            CachePolicy::default(),
        );
        mfs.register_plugin(MemFSPlugin).await;
        mfs.mount(test_config("memfs", "/local")).await.unwrap();
        mfs.mkdir("/local/dir", 0o755).await.unwrap();
        mfs.write("/local/dir/a.md", b"new-content", 0, WriteFlag::Create)
            .await
            .unwrap();
        mfs.write("/local/dir/b.md", b"old", 0, WriteFlag::Create)
            .await
            .unwrap();

        assert_eq!(mfs.read("/local/dir/b.md", 0, 0).await.unwrap(), b"old");
        assert_eq!(
            mfs.read_dir("/local/dir")
                .await
                .unwrap()
                .into_iter()
                .find(|entry| entry.name == "b.md")
                .unwrap()
                .size,
            3
        );

        assert!(mfs
            .copy_within_mount("/local/dir/a.md", "/local/dir/b.md")
            .await
            .unwrap());

        let copied = mfs.read("/local/dir/b.md", 0, 0).await.unwrap();
        let copied_size = mfs
            .read_dir("/local/dir")
            .await
            .unwrap()
            .into_iter()
            .find(|entry| entry.name == "b.md")
            .unwrap()
            .size;
        assert_eq!(copied, b"new-content");
        assert_eq!(copied_size, b"new-content".len() as u64);
    }

    #[cfg(feature = "cache")]
    #[tokio::test]
    async fn encrypted_mount_caches_ciphertext_below_account_validation() {
        use crate::cache::{CacheNamespace, CachePolicy, CacheProvider, MemoryCacheProvider};
        use crate::core::{FsContextInner, FS_CTX};
        use crate::plugins::MemFSPlugin;

        let provider = Arc::new(MemoryCacheProvider::new());
        let mfs = MountableFS::with_cache(
            provider.clone(),
            CacheNamespace::new("encrypted-mount-test"),
            CachePolicy::default(),
        );
        mfs.register_plugin(MemFSPlugin).await;
        mfs.set_encryption_config(Some([9u8; 32]), Some(1)).await;
        mfs.mount(test_config("memfs", "/local")).await.unwrap();
        mfs.mkdir("/local/shared", 0o755).await.unwrap();

        let tenant_a = Arc::new(FsContextInner::new("tenant-a"));
        let tenant_b = Arc::new(FsContextInner::new("tenant-b"));
        FS_CTX
            .scope(tenant_a.clone(), async {
                mfs.write(
                    "/local/shared/doc.md",
                    b"tenant-a-secret",
                    0,
                    WriteFlag::Create,
                )
                .await
                .unwrap();
            })
            .await;

        assert_eq!(
            FS_CTX
                .scope(tenant_a, mfs.read("/local/shared/doc.md", 0, 0))
                .await
                .unwrap(),
            b"tenant-a-secret"
        );
        assert!(
            FS_CTX
                .scope(tenant_b, mfs.read("/local/shared/doc.md", 0, 0))
                .await
                .is_err(),
            "a different account must not receive cached plaintext"
        );
        assert!(
            mfs.read("/local/shared/doc.md", 0, 0).await.is_err(),
            "missing account context must not receive cached plaintext"
        );

        let file_key = provider
            .keys()
            .await
            .into_iter()
            .find(|key| key.contains(":file:"))
            .expect("encrypted read should populate one file cache object");
        let encoded = provider
            .get(&file_key)
            .await
            .unwrap()
            .expect("file cache object should exist");
        let envelope: Value = serde_json::from_slice(&encoded).unwrap();
        let payload = envelope["payload"]["File"]
            .as_array()
            .expect("file payload should be a byte array")
            .iter()
            .map(|value| value.as_u64().unwrap() as u8)
            .collect::<Vec<_>>();
        assert!(
            payload.starts_with(b"OVE1"),
            "shared cache providers must store encrypted file envelopes"
        );
    }

    #[cfg(feature = "cache")]
    #[tokio::test]
    async fn encrypted_multiwrite_mount_does_not_install_plaintext_cache() {
        use crate::cache::{CacheNamespace, CachePolicy, MemoryCacheProvider};

        let mfs = MountableFS::with_cache(
            Arc::new(MemoryCacheProvider::new()),
            CacheNamespace::new("encrypted-multiwrite-test"),
            CachePolicy::default(),
        );
        mfs.register_plugin(MockPlugin::new("primary")).await;
        mfs.register_plugin(MockPlugin::new("backupfs")).await;
        mfs.set_encryption_config(Some([9u8; 32]), Some(1)).await;

        let mut config = multiwrite_test_config("primary", "backupfs", "/local");
        config.server_encryption_enabled = true;
        config.primary_encryption_enabled = true;
        mfs.mount(config).await.unwrap();

        let mounts = mfs.mounts.read().await;
        let mount_info = mounts.get("/local").expect("mounted entry should exist");
        let stats = (mount_info.fs.as_ref() as &dyn std::any::Any)
            .downcast_ref::<StatsWrappedFS>()
            .expect("stats wrapper should be outermost");
        assert!(
            (stats.inner_fs().as_ref() as &dyn std::any::Any)
                .downcast_ref::<MultiWriteWrappedFS>()
                .is_some(),
            "encrypted multi-write must not install a plaintext cache outside MultiWriteWrappedFS"
        );
    }

    #[cfg(feature = "cache")]
    #[tokio::test]
    async fn cached_unencrypted_multiwrite_keeps_admin_and_copy_fast_paths() {
        use crate::cache::{CacheNamespace, CachePolicy, MemoryCacheProvider};
        use crate::core::{FsContextInner, FS_CTX};
        use crate::plugins::MemFSPlugin;

        let mfs = MountableFS::with_cache(
            Arc::new(MemoryCacheProvider::new()),
            CacheNamespace::new("cached-multiwrite-test"),
            CachePolicy::default(),
        );
        mfs.register_plugin(MemFSPlugin).await;

        let mut config = multiwrite_test_config("memfs", "memfs", "/local");
        config.backups.as_mut().unwrap().items[0].name = "backup1".to_string();
        mfs.mount(config).await.unwrap();

        let ctx = Arc::new(FsContextInner::new("acct"));
        FS_CTX
            .scope(ctx.clone(), async {
                let status = mfs.system_sync_status("/local").await.unwrap();
                assert_eq!(status["path"], "/");
                assert_eq!(status["entry_count"], 0);

                let retry = mfs.system_sync_retry("/local").await.unwrap();
                assert_eq!(retry["path"], "/");
                assert_eq!(retry["retried"], 0);

                mfs.mkdir("/local/docs", 0o755).await.unwrap();
                mfs.write("/local/docs/src.md", b"copied", 0, WriteFlag::Create)
                    .await
                    .unwrap();

                assert!(mfs
                    .copy_within_mount("/local/docs/src.md", "/local/docs/dst.md")
                    .await
                    .unwrap());
                assert_eq!(
                    mfs.read("/local/docs/dst.md", 0, 0).await.unwrap(),
                    b"copied"
                );
            })
            .await;

        mfs.unmount("/local").await.unwrap();
        assert!(mfs.list_mounts().await.is_empty());
    }

    #[tokio::test]
    async fn test_mount_duplicate_error() {
        let mfs = MountableFS::new();
        mfs.register_plugin(MockPlugin::new("mock")).await;

        let config = test_config("mock", "/mock");

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
        let mfs = mounted_mock("mock", "/mock").await;

        // Test read operation
        let data = mfs.read("/mock/test.txt", 0, 0).await.unwrap();
        assert_eq!(data, b"mock");

        // Test write operation
        let written = mfs
            .write("/mock/test.txt", b"hello", 0, WriteFlag::Create)
            .await
            .unwrap();
        assert_eq!(written, 5);

        // Test stat operation
        let info = mfs.stat("/mock/test.txt").await.unwrap();
        assert_eq!(info.name, "/test.txt");
    }

    #[tokio::test]
    async fn test_path_routing() {
        let mfs = mounted_two_mocks().await;

        // Test routing to different filesystems
        let data1 = mfs.read("/fs1/file.txt", 0, 0).await.unwrap();
        assert_eq!(data1, b"mock1");

        let data2 = mfs.read("/fs2/file.txt", 0, 0).await.unwrap();
        assert_eq!(data2, b"mock2");
    }

    #[tokio::test]
    async fn test_rename_across_mounts_error() {
        let mfs = mounted_two_mocks().await;

        // Try to rename across different mounts - should fail
        let result = mfs.rename("/fs1/file.txt", "/fs2/file.txt").await;
        assert!(result.is_err());
        assert!(matches!(result.unwrap_err(), Error::InvalidOperation(_)));
    }

    #[tokio::test]
    async fn test_concurrent_operations() {
        use tokio::task;

        let mfs = Arc::new(mounted_mock("mock", "/mock").await);

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
            mfs.register_plugin(MockPlugin::new(&format!("mock{}", i)))
                .await;
        }

        // Spawn concurrent mount operations
        let mut handles = vec![];
        for i in 0..5 {
            let mfs_clone = Arc::clone(&mfs);
            let handle = task::spawn(async move {
                let config = test_config(&format!("mock{}", i), &format!("/mock{}", i));
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
            let handle =
                task::spawn(async move { mfs_clone.unmount(&format!("/mock{}", i)).await });
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

    #[tokio::test]
    async fn test_grep_routes_to_plugin() {
        let mfs = mounted_mock("mock", "/mock").await;

        let result = mfs
            .grep("/mock/a.txt", "foo", false, false, None, None, None)
            .await
            .unwrap();

        assert_eq!(result.count, 1);
        assert_eq!(result.matches.len(), 1);
        assert_eq!(result.matches[0].file, "/a.txt");
        assert_eq!(result.matches[0].line, 1);
        assert_eq!(result.matches[0].content, "foo");
    }

    fn make_tree_entry(path: &str, rel_path: &str, name: &str, is_dir: bool) -> TreeEntry {
        TreeEntry {
            path: path.to_string(),
            rel_path: rel_path.to_string(),
            info: if is_dir {
                FileInfo::new_dir(name.to_string(), 0o755)
            } else {
                FileInfo::new_file(name.to_string(), 100, 0o644)
            },
            extra: HashMap::new(),
        }
    }

    #[tokio::test]
    async fn test_read_dir_sorts_dirs_first_then_name() {
        let mfs = MountableFS::new();
        let plugin = MockPlugin::with_tree_entries(
            "sorted",
            vec![
                make_tree_entry("/b.txt", "b.txt", "b.txt", false),
                make_tree_entry("/A.txt", "A.txt", "A.txt", false),
                make_tree_entry("/c", "c", "c", true),
                make_tree_entry("/B", "B", "B", true),
            ],
        );
        mfs.register_plugin(plugin).await;
        mfs.mount(test_config("sorted", "/sorted")).await.unwrap();

        let names: Vec<String> = mfs
            .read_dir("/sorted")
            .await
            .unwrap()
            .into_iter()
            .map(|entry| entry.name)
            .collect();

        assert_eq!(names, vec!["B", "c", "A.txt", "b.txt"]);
    }

    #[tokio::test]
    async fn test_tree_directory_no_mount_returns_error() {
        let mfs = MountableFS::new();
        let result = mfs
            .tree_directory("/nonexistent/subdir", false, None, None)
            .await;
        assert!(result.is_err());
    }

    #[tokio::test]
    async fn test_tree_directory_path_rewriting() {
        let mfs = MountableFS::new();
        let plugin = MockPlugin::with_tree_entries(
            "rewrite",
            vec![make_tree_entry(
                "/sub/file.txt",
                "sub/file.txt",
                "file.txt",
                false,
            )],
        );
        mfs.register_plugin(plugin).await;
        mfs.mount(test_config("rewrite", "/rewrite")).await.unwrap();

        let result = mfs
            .tree_directory("/rewrite/sub", false, None, None)
            .await
            .unwrap();
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].path, "/rewrite/sub/file.txt");
        assert_eq!(result[0].rel_path, "sub/file.txt");
    }

    #[tokio::test]
    async fn test_tree_directory_rel_path_unchanged_by_routing() {
        let mfs = MountableFS::new();
        let plugin = MockPlugin::with_tree_entries(
            "relpath",
            vec![make_tree_entry("/a/b/c.txt", "a/b/c.txt", "c.txt", false)],
        );
        mfs.register_plugin(plugin).await;
        mfs.mount(test_config("relpath", "/local/test_account"))
            .await
            .unwrap();

        let result = mfs
            .tree_directory("/local/test_account/a", false, None, None)
            .await
            .unwrap();
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].path, "/local/test_account/a/b/c.txt");
        assert_eq!(result[0].rel_path, "a/b/c.txt");
    }

    #[tokio::test]
    async fn test_build_multi_write_fs_rejects_primary_encryption_disable() {
        let mfs = MountableFS::new();
        mfs.register_plugin(MockPlugin::new("primary")).await;
        mfs.register_plugin(MockPlugin::new("backupfs")).await;
        mfs.set_encryption_config(Some([7u8; 32]), Some(1)).await;

        let backups = BackendsConfig {
            sync_type: "async".to_string(),
            write_ack_count: None,
            write_ack_timeout_ms: None,
            write_concurrency: None,
            retry_interval_ms: None,
            retry_backoff_base_ms: None,
            retry_max_retries_per_round: None,
            retry_quarantine_after_failures: None,
            read_probe_cache_ttl_ms: None,
            items: vec![BackendItemConfig {
                name: "backup1".to_string(),
                backend: "backupfs".to_string(),
                params: serde_json::Value::Null,
                timeout: None,
                encryption: None,
                operations: None,
                excludes: None,
            }],
        };
        let config = PluginConfig {
            name: "primary".to_string(),
            mount_path: "/local".to_string(),
            params: HashMap::new(),
            backups: Some(backups.clone()),
            server_encryption_enabled: true,
            ..PluginConfig::default()
        };

        let result = mfs.build_multi_write_fs(&config, &backups).await;
        assert!(result.is_err());
        assert!(matches!(result.err(), Some(Error::Config(_))));
    }

    #[tokio::test]
    async fn test_build_multi_write_fs_rejects_exclude_target_field() {
        let mfs = MountableFS::new();
        mfs.register_plugin(MockPlugin::new("primary")).await;
        mfs.register_plugin(MockPlugin::new("backupfs")).await;

        let backups = BackendsConfig {
            sync_type: "async".to_string(),
            write_ack_count: None,
            write_ack_timeout_ms: None,
            write_concurrency: None,
            retry_interval_ms: None,
            retry_backoff_base_ms: None,
            retry_max_retries_per_round: None,
            retry_quarantine_after_failures: None,
            read_probe_cache_ttl_ms: None,
            items: vec![BackendItemConfig {
                name: "backup1".to_string(),
                backend: "backupfs".to_string(),
                params: serde_json::Value::Null,
                timeout: None,
                encryption: None,
                operations: None,
                excludes: Some(vec![RedirectPolicy::FileExtensionPolicy {
                    extensions: vec!["\\.tmp$".to_string()],
                    target: Some(vec!["should-not-exist".to_string()]),
                }]),
            }],
        };
        let config = PluginConfig {
            name: "primary".to_string(),
            mount_path: "/local".to_string(),
            params: HashMap::new(),
            backups: Some(backups.clone()),
            ..PluginConfig::default()
        };

        let result = mfs.build_multi_write_fs(&config, &backups).await;
        assert!(result.is_err());
        assert!(matches!(result.err(), Some(Error::Config(_))));
    }

    #[tokio::test]
    async fn test_build_multi_write_fs_rejects_reserved_primary_backup_name() {
        let mfs = MountableFS::new();
        mfs.register_plugin(MockPlugin::new("primary")).await;
        mfs.register_plugin(MockPlugin::new("backupfs")).await;

        let backups = BackendsConfig {
            sync_type: "async".to_string(),
            write_ack_count: None,
            write_ack_timeout_ms: None,
            write_concurrency: None,
            retry_interval_ms: None,
            retry_backoff_base_ms: None,
            retry_max_retries_per_round: None,
            retry_quarantine_after_failures: None,
            read_probe_cache_ttl_ms: None,
            items: vec![BackendItemConfig {
                name: "primary".to_string(),
                backend: "backupfs".to_string(),
                params: serde_json::Value::Null,
                timeout: None,
                encryption: None,
                operations: None,
                excludes: None,
            }],
        };
        let config = PluginConfig {
            name: "primary".to_string(),
            mount_path: "/local".to_string(),
            params: HashMap::new(),
            backups: Some(backups.clone()),
            ..PluginConfig::default()
        };

        let result = mfs.build_multi_write_fs(&config, &backups).await;
        assert!(result.is_err());
        assert!(matches!(result.err(), Some(Error::Config(_))));
    }

    #[tokio::test]
    async fn test_single_backend_mount_wraps_stats_outside_encryption() {
        let mfs = MountableFS::new();
        mfs.register_plugin(MockPlugin::new("mock")).await;
        mfs.set_encryption_config(Some([9u8; 32]), Some(1)).await;

        mfs.mount(test_config("mock", "/mock")).await.unwrap();

        let mounts = mfs.mounts.read().await;
        let mount_info = mounts.get("/mock").expect("mounted entry should exist");
        let wrapped = (mount_info.fs.as_ref() as &dyn std::any::Any)
            .downcast_ref::<StatsWrappedFS>()
            .expect("stats wrapper should be outermost");
        assert!(
            (wrapped.inner_fs().as_ref() as &dyn std::any::Any)
                .downcast_ref::<EncryptionWrappedFS>()
                .is_some(),
            "single-backend encrypted mount should place encryption under stats"
        );
    }

    #[tokio::test]
    async fn test_multiwrite_mount_wraps_stats_outside_multiwrite() {
        let mfs = MountableFS::new();
        mfs.register_plugin(MockPlugin::new("primary")).await;
        mfs.register_plugin(MockPlugin::new("backupfs")).await;

        mfs.mount(multiwrite_test_config("primary", "backupfs", "/local"))
            .await
            .unwrap();

        let mounts = mfs.mounts.read().await;
        let mount_info = mounts.get("/local").expect("mounted entry should exist");
        let wrapped = (mount_info.fs.as_ref() as &dyn std::any::Any)
            .downcast_ref::<StatsWrappedFS>()
            .expect("stats wrapper should be outermost");
        assert!(
            (wrapped.inner_fs().as_ref() as &dyn std::any::Any)
                .downcast_ref::<MultiWriteWrappedFS>()
                .is_some(),
            "multi-write mount should place MultiWriteWrappedFS directly under stats"
        );
    }

    #[tokio::test]
    async fn test_multiwrite_raw_access_is_rejected() {
        let mfs = MountableFS::new();
        mfs.register_plugin(MockPlugin::new("primary")).await;
        mfs.register_plugin(MockPlugin::new("backupfs")).await;

        mfs.mount(multiwrite_test_config("primary", "backupfs", "/local"))
            .await
            .unwrap();

        let read_err = mfs.read_raw("/local/data.txt", 0, 0).await.unwrap_err();
        assert!(matches!(read_err, Error::InvalidOperation(_)));

        let write_err = mfs
            .write_raw("/local/data.txt", b"raw", WriteFlag::Create)
            .await
            .unwrap_err();
        assert!(matches!(write_err, Error::InvalidOperation(_)));
    }

    #[tokio::test]
    async fn test_mountable_multiwrite_admin_smoke() {
        let mfs = MountableFS::new();
        mfs.register_plugin(MockPlugin::new("primary")).await;
        mfs.register_plugin(MockPlugin::new("backupfs")).await;

        mfs.mount(multiwrite_test_config("primary", "backupfs", "/local"))
            .await
            .unwrap();

        let status = crate::core::FS_CTX
            .scope(
                Arc::new(crate::core::context::FsContextInner::new(
                    "acct".to_string(),
                )),
                async { mfs.system_sync_status("/local").await.unwrap() },
            )
            .await;
        assert_eq!(status["path"], "/");
        assert_eq!(status["entry_count"], 0);
        assert_eq!(status["pending_target_count"], 0);
        assert_eq!(status["capabilities"]["multi_instance_safe"], false);

        let retry = crate::core::FS_CTX
            .scope(
                Arc::new(crate::core::context::FsContextInner::new(
                    "acct".to_string(),
                )),
                async { mfs.system_sync_retry("/local").await.unwrap() },
            )
            .await;
        assert_eq!(retry["path"], "/");
        assert_eq!(retry["retried"], 0);
        assert_eq!(retry["failed"], 0);
    }
}
