use async_trait::async_trait;
use ragfs::cache::{CacheNamespace, CachePolicy, CacheProvider, CachedFileSystem};
use ragfs::core::{GrepResult, TreeEntry};
use ragfs::plugins::MemFileSystem;
use ragfs::{FileInfo, FileSystem, Result, WriteFlag};
use ragfs_cache_mooncake::{
    MooncakeConfig, MooncakeObjectStore, MooncakeProvider, MooncakeReplicateConfig,
    MooncakeStoreError,
};
use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

#[derive(Default)]
struct SharedStore {
    values: Mutex<HashMap<String, Vec<u8>>>,
    available: AtomicBool,
}

impl SharedStore {
    fn available() -> Self {
        Self {
            available: AtomicBool::new(true),
            ..Self::default()
        }
    }

    fn check(&self) -> std::result::Result<(), MooncakeStoreError> {
        if self.available.load(Ordering::SeqCst) {
            Ok(())
        } else {
            Err(MooncakeStoreError::Unavailable("store unavailable".into()))
        }
    }
}

impl MooncakeObjectStore for SharedStore {
    fn health_check(&self) -> std::result::Result<(), MooncakeStoreError> {
        self.check()
    }

    fn is_exist(&self, key: &str) -> std::result::Result<bool, MooncakeStoreError> {
        self.check()?;
        Ok(self.values.lock().unwrap().contains_key(key))
    }

    fn get(&self, key: &str) -> std::result::Result<Vec<u8>, MooncakeStoreError> {
        self.check()?;
        self.values
            .lock()
            .unwrap()
            .get(key)
            .cloned()
            .ok_or(MooncakeStoreError::NotFound)
    }

    fn put(
        &self,
        key: &str,
        value: &[u8],
        _replicate: &MooncakeReplicateConfig,
    ) -> std::result::Result<(), MooncakeStoreError> {
        self.check()?;
        self.values
            .lock()
            .unwrap()
            .insert(key.to_owned(), value.to_vec());
        Ok(())
    }

    fn remove(&self, key: &str, _force: bool) -> std::result::Result<(), MooncakeStoreError> {
        self.check()?;
        self.values.lock().unwrap().remove(key);
        Ok(())
    }
}

#[derive(Clone)]
struct CountingFileSystem {
    inner: Arc<MemFileSystem>,
    reads: Arc<AtomicU64>,
}

impl CountingFileSystem {
    fn new() -> Self {
        Self {
            inner: Arc::new(MemFileSystem::new()),
            reads: Arc::new(AtomicU64::new(0)),
        }
    }

    fn read_count(&self) -> u64 {
        self.reads.load(Ordering::SeqCst)
    }
}

#[async_trait]
impl FileSystem for CountingFileSystem {
    async fn create(&self, path: &str) -> Result<()> {
        self.inner.create(path).await
    }

    async fn mkdir(&self, path: &str, mode: u32) -> Result<()> {
        self.inner.mkdir(path, mode).await
    }

    async fn remove(&self, path: &str) -> Result<()> {
        self.inner.remove(path).await
    }

    async fn remove_all(&self, path: &str) -> Result<()> {
        self.inner.remove_all(path).await
    }

    async fn read(&self, path: &str, offset: u64, size: u64) -> Result<Vec<u8>> {
        self.reads.fetch_add(1, Ordering::SeqCst);
        self.inner.read(path, offset, size).await
    }

    async fn write(&self, path: &str, data: &[u8], offset: u64, flags: WriteFlag) -> Result<u64> {
        self.inner.write(path, data, offset, flags).await
    }

    async fn read_dir(&self, path: &str) -> Result<Vec<FileInfo>> {
        self.inner.read_dir(path).await
    }

    async fn stat(&self, path: &str) -> Result<FileInfo> {
        self.inner.stat(path).await
    }

    async fn rename(&self, old_path: &str, new_path: &str) -> Result<()> {
        self.inner.rename(old_path, new_path).await
    }

    async fn chmod(&self, path: &str, mode: u32) -> Result<()> {
        self.inner.chmod(path, mode).await
    }

    async fn truncate(&self, path: &str, size: u64) -> Result<()> {
        self.inner.truncate(path, size).await
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
        self.inner
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
        self.inner
            .tree_directory(path, show_hidden, node_limit, level_limit)
            .await
    }
}

fn config() -> MooncakeConfig {
    MooncakeConfig {
        local_hostname: "127.0.0.1".into(),
        metadata_server: "http://127.0.0.1:8080/metadata".into(),
        master_server_addr: "127.0.0.1:50051".into(),
        protocol: "tcp".into(),
        device_name: String::new(),
        global_segment_size: 512 << 20,
        local_buffer_size: 128 << 20,
        replica_num: 2,
        sdk_concurrency: 4,
        operation_timeout_ms: 100,
    }
}

async fn cached_fs(
    backend: CountingFileSystem,
    store: Arc<SharedStore>,
    namespace: &str,
) -> CachedFileSystem {
    let provider: Arc<dyn CacheProvider> =
        Arc::new(MooncakeProvider::from_store(config(), store).await.unwrap());
    CachedFileSystem::new(
        Box::new(backend),
        provider,
        CacheNamespace::new(namespace),
        CachePolicy::default(),
    )
}

#[tokio::test]
async fn mooncake_provider_supports_miss_fill_hit_and_write_after_read() {
    let backend = CountingFileSystem::new();
    backend
        .write("/value.md", b"old", 0, WriteFlag::Create)
        .await
        .unwrap();
    let probe = backend.clone();
    let fs = cached_fs(backend, Arc::new(SharedStore::available()), "read-write").await;

    assert_eq!(fs.read("/value.md", 0, 0).await.unwrap(), b"old");
    assert_eq!(fs.read("/value.md", 0, 0).await.unwrap(), b"old");
    assert_eq!(probe.read_count(), 1);

    fs.write("/value.md", b"new", 0, WriteFlag::Truncate)
        .await
        .unwrap();
    assert_eq!(fs.read("/value.md", 0, 0).await.unwrap(), b"new");
    assert_eq!(probe.read_count(), 1);
}

#[tokio::test]
async fn mooncake_provider_preserves_invalidation_and_subtree_generation_rules() {
    let backend = CountingFileSystem::new();
    backend.mkdir("/tree", 0o755).await.unwrap();
    backend
        .write("/tree/leaf", b"old", 0, WriteFlag::Create)
        .await
        .unwrap();
    let direct = backend.clone();
    let fs = cached_fs(backend, Arc::new(SharedStore::available()), "invalidation").await;

    assert_eq!(fs.read("/tree/leaf", 0, 0).await.unwrap(), b"old");
    fs.rename("/tree/leaf", "/tree/moved").await.unwrap();
    assert!(fs.read("/tree/leaf", 0, 0).await.is_err());
    assert_eq!(fs.read("/tree/moved", 0, 0).await.unwrap(), b"old");
    fs.remove("/tree/moved").await.unwrap();
    assert!(fs.read("/tree/moved", 0, 0).await.is_err());

    direct
        .write("/tree/leaf", b"stale", 0, WriteFlag::Create)
        .await
        .unwrap();
    assert_eq!(fs.read("/tree/leaf", 0, 0).await.unwrap(), b"stale");
    fs.remove_all("/tree").await.unwrap();
    direct.mkdir("/tree", 0o755).await.unwrap();
    direct
        .write("/tree/leaf", b"fresh", 0, WriteFlag::Create)
        .await
        .unwrap();
    assert_eq!(fs.read("/tree/leaf", 0, 0).await.unwrap(), b"fresh");
}

#[tokio::test]
async fn mooncake_provider_caches_directory_entries_and_invalidates_directory_renames() {
    let backend = CountingFileSystem::new();
    backend.mkdir("/root", 0o755).await.unwrap();
    backend.mkdir("/root/old", 0o755).await.unwrap();
    backend
        .write("/root/old/leaf", b"value", 0, WriteFlag::Create)
        .await
        .unwrap();
    let fs = cached_fs(backend, Arc::new(SharedStore::available()), "directories").await;

    assert_eq!(fs.read_dir("/root").await.unwrap().len(), 1);
    assert_eq!(fs.read_dir("/root").await.unwrap().len(), 1);
    fs.mkdir("/root/created", 0o755).await.unwrap();
    assert_eq!(fs.read_dir("/root").await.unwrap().len(), 2);

    assert_eq!(fs.read("/root/old/leaf", 0, 0).await.unwrap(), b"value");
    fs.rename("/root/old", "/root/moved").await.unwrap();
    assert!(fs.read("/root/old/leaf", 0, 0).await.is_err());
    assert_eq!(fs.read("/root/moved/leaf", 0, 0).await.unwrap(), b"value");
}

#[tokio::test]
async fn unavailable_mooncake_falls_back_without_breaking_backend_reads() {
    let backend = CountingFileSystem::new();
    backend
        .write("/available.md", b"backend", 0, WriteFlag::Create)
        .await
        .unwrap();
    let probe = backend.clone();
    let store = Arc::new(SharedStore::available());
    let fs = cached_fs(backend, store.clone(), "fallback").await;
    store.available.store(false, Ordering::SeqCst);

    assert_eq!(fs.read("/available.md", 0, 0).await.unwrap(), b"backend");
    assert_eq!(fs.read("/available.md", 0, 0).await.unwrap(), b"backend");
    assert_eq!(probe.read_count(), 2);
    assert!(fs.metrics().snapshot().errors >= 1);
}

#[tokio::test]
async fn multiple_wrappers_share_one_mooncake_provider_without_key_collisions() {
    let store = Arc::new(SharedStore::available());
    let provider: Arc<dyn CacheProvider> =
        Arc::new(MooncakeProvider::from_store(config(), store).await.unwrap());
    let first_backend = CountingFileSystem::new();
    first_backend
        .write("/same.md", b"first", 0, WriteFlag::Create)
        .await
        .unwrap();
    let second_backend = CountingFileSystem::new();
    second_backend
        .write("/same.md", b"second", 0, WriteFlag::Create)
        .await
        .unwrap();
    let first = CachedFileSystem::new(
        Box::new(first_backend),
        provider.clone(),
        CacheNamespace::new("mount-one"),
        CachePolicy::default(),
    );
    let second = CachedFileSystem::new(
        Box::new(second_backend),
        provider,
        CacheNamespace::new("mount-two"),
        CachePolicy::default(),
    );

    assert_eq!(first.read("/same.md", 0, 0).await.unwrap(), b"first");
    assert_eq!(second.read("/same.md", 0, 0).await.unwrap(), b"second");
    assert_eq!(first.read("/same.md", 0, 0).await.unwrap(), b"first");
    assert_eq!(second.read("/same.md", 0, 0).await.unwrap(), b"second");
}
