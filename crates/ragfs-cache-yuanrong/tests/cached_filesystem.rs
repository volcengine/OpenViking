use async_trait::async_trait;
use ragfs::cache::{CacheNamespace, CachePolicy, CacheProvider, CachedFileSystem};
use ragfs::core::{GrepResult, TreeEntry};
use ragfs::plugins::MemFileSystem;
use ragfs::{FileInfo, FileSystem, Result as FsResult, WriteFlag};
use ragfs_cache_yuanrong::{YuanrongConfig, YuanrongKvStore, YuanrongProvider, YuanrongStoreError};
use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

#[derive(Default)]
struct SharedKvStore {
    values: Mutex<HashMap<String, Vec<u8>>>,
    unavailable: AtomicBool,
}

impl SharedKvStore {
    fn check(&self) -> std::result::Result<(), YuanrongStoreError> {
        if self.unavailable.load(Ordering::SeqCst) {
            Err(YuanrongStoreError::Unavailable("worker unavailable".into()))
        } else {
            Ok(())
        }
    }
}

impl YuanrongKvStore for SharedKvStore {
    fn health_check(&self) -> std::result::Result<(), YuanrongStoreError> {
        self.check()
    }

    fn get(&self, key: &str) -> std::result::Result<Option<Vec<u8>>, YuanrongStoreError> {
        self.check()?;
        Ok(self.values.lock().unwrap().get(key).cloned())
    }

    fn set(&self, key: &str, value: &[u8]) -> std::result::Result<(), YuanrongStoreError> {
        self.check()?;
        self.values
            .lock()
            .unwrap()
            .insert(key.to_owned(), value.to_vec());
        Ok(())
    }

    fn delete(&self, key: &str) -> std::result::Result<(), YuanrongStoreError> {
        self.check()?;
        self.values.lock().unwrap().remove(key);
        Ok(())
    }

    fn exists(&self, key: &str) -> std::result::Result<bool, YuanrongStoreError> {
        self.check()?;
        Ok(self.values.lock().unwrap().contains_key(key))
    }

    fn batch_get(
        &self,
        keys: &[String],
    ) -> std::result::Result<Vec<Option<Vec<u8>>>, YuanrongStoreError> {
        self.check()?;
        let values = self.values.lock().unwrap();
        Ok(keys.iter().map(|key| values.get(key).cloned()).collect())
    }

    fn batch_set(
        &self,
        entries: &[(String, Vec<u8>)],
    ) -> std::result::Result<(), YuanrongStoreError> {
        self.check()?;
        self.values.lock().unwrap().extend(entries.iter().cloned());
        Ok(())
    }

    fn batch_delete(&self, keys: &[String]) -> std::result::Result<(), YuanrongStoreError> {
        self.check()?;
        let mut values = self.values.lock().unwrap();
        for key in keys {
            values.remove(key);
        }
        Ok(())
    }

    fn shutdown(&self) -> std::result::Result<(), YuanrongStoreError> {
        Ok(())
    }
}

#[derive(Clone)]
struct CountingFileSystem {
    inner: Arc<MemFileSystem>,
    reads: Arc<AtomicU64>,
    read_dirs: Arc<AtomicU64>,
}

impl CountingFileSystem {
    fn new() -> Self {
        Self {
            inner: Arc::new(MemFileSystem::new()),
            reads: Arc::new(AtomicU64::new(0)),
            read_dirs: Arc::new(AtomicU64::new(0)),
        }
    }
}

#[async_trait]
impl FileSystem for CountingFileSystem {
    async fn create(&self, path: &str) -> FsResult<()> {
        self.inner.create(path).await
    }

    async fn mkdir(&self, path: &str, mode: u32) -> FsResult<()> {
        self.inner.mkdir(path, mode).await
    }

    async fn remove(&self, path: &str) -> FsResult<()> {
        self.inner.remove(path).await
    }

    async fn remove_all(&self, path: &str) -> FsResult<()> {
        self.inner.remove_all(path).await
    }

    async fn read(&self, path: &str, offset: u64, size: u64) -> FsResult<Vec<u8>> {
        self.reads.fetch_add(1, Ordering::SeqCst);
        self.inner.read(path, offset, size).await
    }

    async fn write(&self, path: &str, data: &[u8], offset: u64, flags: WriteFlag) -> FsResult<u64> {
        self.inner.write(path, data, offset, flags).await
    }

    async fn read_dir(&self, path: &str) -> FsResult<Vec<FileInfo>> {
        self.read_dirs.fetch_add(1, Ordering::SeqCst);
        self.inner.read_dir(path).await
    }

    async fn stat(&self, path: &str) -> FsResult<FileInfo> {
        self.inner.stat(path).await
    }

    async fn rename(&self, old_path: &str, new_path: &str) -> FsResult<()> {
        self.inner.rename(old_path, new_path).await
    }

    async fn chmod(&self, path: &str, mode: u32) -> FsResult<()> {
        self.inner.chmod(path, mode).await
    }

    async fn truncate(&self, path: &str, size: u64) -> FsResult<()> {
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
    ) -> FsResult<GrepResult> {
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
    ) -> FsResult<Vec<TreeEntry>> {
        self.inner
            .tree_directory(path, show_hidden, node_limit, level_limit)
            .await
    }
}

fn config() -> YuanrongConfig {
    YuanrongConfig {
        host: "127.0.0.1".into(),
        port: 9088,
        connect_timeout_ms: 1_000,
        request_timeout_ms: 100,
        sdk_concurrency: 4,
    }
}

async fn cached_fs(
    backend: CountingFileSystem,
    store: Arc<SharedKvStore>,
    namespace: &str,
) -> CachedFileSystem {
    let provider: Arc<dyn CacheProvider> =
        Arc::new(YuanrongProvider::from_store(config(), store).await.unwrap());
    CachedFileSystem::new(
        Box::new(backend),
        provider,
        CacheNamespace::new(namespace),
        CachePolicy::default(),
    )
}

#[tokio::test]
async fn yuanrong_hit_miss_fill_and_write_after_read_are_consistent() {
    let backend = CountingFileSystem::new();
    backend
        .write("/value.md", b"old", 0, WriteFlag::Create)
        .await
        .unwrap();
    let probe = backend.clone();
    let fs = cached_fs(backend, Arc::new(SharedKvStore::default()), "read-write").await;

    assert_eq!(fs.read("/value.md", 0, 0).await.unwrap(), b"old");
    assert_eq!(fs.read("/value.md", 0, 0).await.unwrap(), b"old");
    assert_eq!(probe.reads.load(Ordering::SeqCst), 1);

    fs.write("/value.md", b"new", 0, WriteFlag::Truncate)
        .await
        .unwrap();
    assert_eq!(fs.read("/value.md", 0, 0).await.unwrap(), b"new");
    assert_eq!(probe.reads.load(Ordering::SeqCst), 1);
}

#[tokio::test]
async fn delete_rename_remove_all_and_directory_changes_invalidate_yuanrong_keys() {
    let backend = CountingFileSystem::new();
    backend.mkdir("/root", 0o755).await.unwrap();
    backend.mkdir("/root/tree", 0o755).await.unwrap();
    backend
        .write("/root/tree/leaf", b"old", 0, WriteFlag::Create)
        .await
        .unwrap();
    let direct = backend.clone();
    let fs = cached_fs(backend, Arc::new(SharedKvStore::default()), "invalidation").await;

    assert_eq!(fs.read_dir("/root").await.unwrap().len(), 1);
    assert_eq!(fs.read_dir("/root").await.unwrap().len(), 1);
    assert_eq!(direct.read_dirs.load(Ordering::SeqCst), 1);
    fs.mkdir("/root/created", 0o755).await.unwrap();
    assert_eq!(fs.read_dir("/root").await.unwrap().len(), 2);
    assert_eq!(direct.read_dirs.load(Ordering::SeqCst), 2);

    assert_eq!(fs.read("/root/tree/leaf", 0, 0).await.unwrap(), b"old");
    fs.rename("/root/tree/leaf", "/root/tree/moved")
        .await
        .unwrap();
    assert!(fs.read("/root/tree/leaf", 0, 0).await.is_err());
    assert_eq!(fs.read("/root/tree/moved", 0, 0).await.unwrap(), b"old");
    fs.remove("/root/tree/moved").await.unwrap();
    assert!(fs.read("/root/tree/moved", 0, 0).await.is_err());

    direct
        .write("/root/tree/leaf", b"stale", 0, WriteFlag::Create)
        .await
        .unwrap();
    assert_eq!(fs.read("/root/tree/leaf", 0, 0).await.unwrap(), b"stale");
    fs.remove_all("/root/tree").await.unwrap();
    direct.mkdir("/root/tree", 0o755).await.unwrap();
    direct
        .write("/root/tree/leaf", b"fresh", 0, WriteFlag::Create)
        .await
        .unwrap();
    assert_eq!(fs.read("/root/tree/leaf", 0, 0).await.unwrap(), b"fresh");

    fs.rename("/root/tree", "/root/renamed").await.unwrap();
    assert!(fs.read("/root/tree/leaf", 0, 0).await.is_err());
    assert_eq!(fs.read("/root/renamed/leaf", 0, 0).await.unwrap(), b"fresh");
}

#[tokio::test]
async fn unavailable_yuanrong_falls_back_without_breaking_backend_reads() {
    let backend = CountingFileSystem::new();
    backend
        .write("/available.md", b"backend", 0, WriteFlag::Create)
        .await
        .unwrap();
    let probe = backend.clone();
    let store = Arc::new(SharedKvStore::default());
    let fs = cached_fs(backend, store.clone(), "fallback").await;
    store.unavailable.store(true, Ordering::SeqCst);

    assert_eq!(fs.read("/available.md", 0, 0).await.unwrap(), b"backend");
    assert_eq!(fs.read("/available.md", 0, 0).await.unwrap(), b"backend");
    assert_eq!(probe.reads.load(Ordering::SeqCst), 2);
    assert!(fs.metrics().snapshot().errors >= 1);
}

#[cfg(feature = "yuanrong-native")]
#[tokio::test]
async fn native_yuanrong_cached_filesystem_hits_fills_writes_and_invalidates() {
    if std::env::var("OPENVIKING_RUN_YUANRONG_INTEGRATION").as_deref() != Ok("true") {
        return;
    }

    let backend = CountingFileSystem::new();
    backend.mkdir("/native", 0o755).await.unwrap();
    backend
        .write("/native/value", b"backend-old", 0, WriteFlag::Create)
        .await
        .unwrap();
    let direct = backend.clone();
    let native_config = YuanrongConfig {
        host: std::env::var("YUANRONG_WORKER_HOST").unwrap_or_else(|_| "127.0.0.1".into()),
        port: std::env::var("YUANRONG_WORKER_PORT")
            .ok()
            .and_then(|value| value.parse().ok())
            .unwrap_or(31501),
        connect_timeout_ms: 5_000,
        request_timeout_ms: 5_000,
        sdk_concurrency: 4,
    };
    let provider: Arc<dyn CacheProvider> =
        Arc::new(YuanrongProvider::connect(native_config).await.unwrap());
    let namespace = format!("native-fs-{}", std::process::id());
    let fs = CachedFileSystem::new(
        Box::new(backend),
        provider.clone(),
        CacheNamespace::new(namespace),
        CachePolicy::default(),
    );

    assert_eq!(
        fs.read("/native/value", 0, 0).await.unwrap(),
        b"backend-old"
    );
    direct
        .write("/native/value", b"backend-mutated", 0, WriteFlag::Truncate)
        .await
        .unwrap();
    assert_eq!(
        fs.read("/native/value", 0, 0).await.unwrap(),
        b"backend-old",
        "second read must come from Yuanrong rather than the mutated backend"
    );

    fs.write("/native/value", b"write-through", 0, WriteFlag::Truncate)
        .await
        .unwrap();
    assert_eq!(
        fs.read("/native/value", 0, 0).await.unwrap(),
        b"write-through"
    );
    fs.write("/native/empty", b"", 0, WriteFlag::Create)
        .await
        .unwrap();
    assert_eq!(fs.read("/native/empty", 0, 0).await.unwrap(), b"");
    fs.rename("/native/value", "/native/moved").await.unwrap();
    assert!(fs.read("/native/value", 0, 0).await.is_err());
    assert_eq!(
        fs.read("/native/moved", 0, 0).await.unwrap(),
        b"write-through"
    );
    fs.remove("/native/moved").await.unwrap();
    assert!(fs.read("/native/moved", 0, 0).await.is_err());

    direct
        .write("/native/leaf", b"stale", 0, WriteFlag::Create)
        .await
        .unwrap();
    assert_eq!(fs.read("/native/leaf", 0, 0).await.unwrap(), b"stale");
    fs.remove_all("/native").await.unwrap();
    direct.mkdir("/native", 0o755).await.unwrap();
    direct
        .write("/native/leaf", b"fresh", 0, WriteFlag::Create)
        .await
        .unwrap();
    assert_eq!(fs.read("/native/leaf", 0, 0).await.unwrap(), b"fresh");
    provider.close().await.unwrap();
}
