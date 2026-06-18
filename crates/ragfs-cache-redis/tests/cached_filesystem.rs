use async_trait::async_trait;
use ragfs::cache::{CacheNamespace, CachePolicy, CacheProvider, CachedFileSystem};
use ragfs::core::{GrepResult, TreeEntry};
use ragfs::plugins::MemFileSystem;
use ragfs::{FileInfo, FileSystem, Result as FsResult, WriteFlag};
use ragfs_cache_redis::{RedisConfig, RedisProvider};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

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

fn config(test_name: &str) -> Option<RedisConfig> {
    let endpoint = std::env::var("REDIS_URL").ok()?;
    Some(RedisConfig {
        endpoints: vec![endpoint],
        key_prefix: format!("ragfs-cache-fs-test:{}:{}", std::process::id(), test_name),
        connect_timeout_ms: 30_000,
        command_timeout_ms: 1_000,
        default_ttl_seconds: 60,
        ..RedisConfig::default()
    })
}

async fn cached_fs(backend: CountingFileSystem, test_name: &str) -> Option<CachedFileSystem> {
    let provider: Arc<dyn CacheProvider> =
        Arc::new(RedisProvider::connect(config(test_name)?).await.unwrap());
    Some(CachedFileSystem::new(
        Box::new(backend),
        provider,
        CacheNamespace::new(test_name),
        CachePolicy::default(),
    ))
}

#[tokio::test]
async fn redis_hit_miss_fill_and_write_after_read_are_consistent() {
    let backend = CountingFileSystem::new();
    backend
        .write("/value.md", b"old", 0, WriteFlag::Create)
        .await
        .unwrap();
    let probe = backend.clone();
    let Some(fs) = cached_fs(backend, "redis-read-write").await else {
        return;
    };

    assert_eq!(fs.read("/value.md", 0, 0).await.unwrap(), b"old");
    assert_eq!(fs.read("/value.md", 0, 0).await.unwrap(), b"old");
    assert_eq!(probe.reads.load(Ordering::SeqCst), 1);

    fs.write("/value.md", b"new", 0, WriteFlag::Truncate)
        .await
        .unwrap();
    assert_eq!(fs.read("/value.md", 0, 0).await.unwrap(), b"new");
    assert_eq!(probe.reads.load(Ordering::SeqCst), 1);
    fs.provider().flush().await.unwrap();
    fs.provider().close().await.unwrap();
}

#[tokio::test]
async fn redis_directory_and_mutation_invalidation_stay_consistent() {
    let backend = CountingFileSystem::new();
    backend.mkdir("/root", 0o755).await.unwrap();
    backend.mkdir("/root/tree", 0o755).await.unwrap();
    backend
        .write("/root/tree/leaf", b"old", 0, WriteFlag::Create)
        .await
        .unwrap();
    let direct = backend.clone();
    let Some(fs) = cached_fs(backend, "redis-invalidation").await else {
        return;
    };

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
    fs.provider().flush().await.unwrap();
    fs.provider().close().await.unwrap();
}

#[tokio::test]
async fn closed_redis_provider_falls_back_without_breaking_backend_reads() {
    let backend = CountingFileSystem::new();
    backend
        .write("/available.md", b"backend", 0, WriteFlag::Create)
        .await
        .unwrap();
    let probe = backend.clone();
    let Some(fs) = cached_fs(backend, "redis-fallback").await else {
        return;
    };
    fs.provider().close().await.unwrap();

    assert_eq!(fs.read("/available.md", 0, 0).await.unwrap(), b"backend");
    assert_eq!(fs.read("/available.md", 0, 0).await.unwrap(), b"backend");
    assert_eq!(probe.reads.load(Ordering::SeqCst), 2);
    assert!(fs.metrics().snapshot().errors >= 1);
}
