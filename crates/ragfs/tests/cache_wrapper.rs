use async_trait::async_trait;
use bytes::Bytes;
use ragfs::cache::{
    CacheDecision, CacheError, CacheNamespace, CachePolicy, CacheProvider, CacheResult,
    CacheTraversalMode, CachedFileSystem, MemoryCacheProvider, MemoryMockProvider,
};
use ragfs::core::{FsContextInner, GrepResult, MultiWriteWrappedFS, TreeEntry, FS_CTX};
use ragfs::plugins::MemFileSystem;
use ragfs::{FileInfo, FileSystem, Result, WriteFlag};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

#[derive(Clone)]
struct CountingFileSystem {
    inner: Arc<MemFileSystem>,
    reads: Arc<AtomicU64>,
    read_dirs: Arc<AtomicU64>,
    stats: Arc<AtomicU64>,
    greps: Arc<AtomicU64>,
    trees: Arc<AtomicU64>,
    read_delay: Duration,
}

struct DeleteFailingProvider {
    inner: MemoryCacheProvider,
}

struct TrackingProvider {
    inner: MemoryCacheProvider,
    gets: AtomicU64,
    batch_gets: AtomicU64,
    active_gets: AtomicU64,
    max_active_gets: AtomicU64,
    seen_get_keys: Mutex<Vec<String>>,
    seen_batch_get_keys: Mutex<Vec<Vec<String>>>,
    get_delay: Duration,
}

struct UnavailableProvider;

impl DeleteFailingProvider {
    fn new() -> Self {
        Self {
            inner: MemoryCacheProvider::new(),
        }
    }
}

impl TrackingProvider {
    fn new() -> Self {
        Self {
            inner: MemoryCacheProvider::new(),
            gets: AtomicU64::new(0),
            batch_gets: AtomicU64::new(0),
            active_gets: AtomicU64::new(0),
            max_active_gets: AtomicU64::new(0),
            seen_get_keys: Mutex::new(Vec::new()),
            seen_batch_get_keys: Mutex::new(Vec::new()),
            get_delay: Duration::ZERO,
        }
    }

    fn with_get_delay(mut self, delay: Duration) -> Self {
        self.get_delay = delay;
        self
    }

    fn reset_observed_reads(&self) {
        self.gets.store(0, Ordering::Relaxed);
        self.batch_gets.store(0, Ordering::Relaxed);
        self.active_gets.store(0, Ordering::Relaxed);
        self.max_active_gets.store(0, Ordering::Relaxed);
        self.seen_get_keys.lock().unwrap().clear();
        self.seen_batch_get_keys.lock().unwrap().clear();
    }

    fn batch_get_count(&self) -> u64 {
        self.batch_gets.load(Ordering::Relaxed)
    }

    fn observed_read_keys(&self) -> Vec<String> {
        let mut keys = self.seen_get_keys.lock().unwrap().clone();
        keys.extend(
            self.seen_batch_get_keys
                .lock()
                .unwrap()
                .iter()
                .flat_map(|batch| batch.iter().cloned()),
        );
        keys
    }

    fn max_concurrent_gets(&self) -> u64 {
        self.max_active_gets.load(Ordering::Relaxed)
    }

    fn enter_get(&self) {
        let active = self.active_gets.fetch_add(1, Ordering::Relaxed) + 1;
        let mut current = self.max_active_gets.load(Ordering::Relaxed);
        while active > current {
            match self.max_active_gets.compare_exchange_weak(
                current,
                active,
                Ordering::Relaxed,
                Ordering::Relaxed,
            ) {
                Ok(_) => break,
                Err(observed) => current = observed,
            }
        }
    }

    fn exit_get(&self) {
        self.active_gets.fetch_sub(1, Ordering::Relaxed);
    }
}

#[async_trait]
impl CacheProvider for DeleteFailingProvider {
    fn name(&self) -> &'static str {
        "delete-failing"
    }

    async fn get(&self, key: &str) -> CacheResult<Option<Bytes>> {
        self.inner.get(key).await
    }

    async fn put(&self, key: &str, value: Bytes) -> CacheResult<()> {
        self.inner.put(key, value).await
    }

    async fn delete(&self, _key: &str) -> CacheResult<()> {
        Err(CacheError::Unavailable(
            "delete intentionally failed".to_string(),
        ))
    }
}

#[async_trait]
impl CacheProvider for TrackingProvider {
    fn name(&self) -> &'static str {
        "tracking"
    }

    fn capabilities(&self) -> ragfs::cache::ProviderCapabilities {
        self.inner.capabilities()
    }

    async fn get(&self, key: &str) -> CacheResult<Option<Bytes>> {
        self.gets.fetch_add(1, Ordering::Relaxed);
        self.seen_get_keys.lock().unwrap().push(key.to_string());
        self.enter_get();
        if !self.get_delay.is_zero() {
            tokio::time::sleep(self.get_delay).await;
        }
        let result = self.inner.get(key).await;
        self.exit_get();
        result
    }

    async fn put(&self, key: &str, value: Bytes) -> CacheResult<()> {
        self.inner.put(key, value).await
    }

    async fn delete(&self, key: &str) -> CacheResult<()> {
        self.inner.delete(key).await
    }

    async fn batch_get(&self, keys: &[String]) -> CacheResult<Vec<Option<Bytes>>> {
        self.batch_gets.fetch_add(1, Ordering::Relaxed);
        self.seen_batch_get_keys.lock().unwrap().push(keys.to_vec());
        self.inner.batch_get(keys).await
    }

    async fn batch_put(&self, entries: Vec<(String, Bytes)>) -> CacheResult<()> {
        self.inner.batch_put(entries).await
    }

    async fn invalidate(&self, keys: &[String]) -> CacheResult<()> {
        self.inner.invalidate(keys).await
    }
}

#[async_trait]
impl CacheProvider for UnavailableProvider {
    fn name(&self) -> &'static str {
        "unavailable"
    }

    async fn get(&self, _key: &str) -> CacheResult<Option<Bytes>> {
        Err(CacheError::Unavailable("provider is down".to_string()))
    }

    async fn put(&self, _key: &str, _value: Bytes) -> CacheResult<()> {
        Err(CacheError::Unavailable("provider is down".to_string()))
    }

    async fn delete(&self, _key: &str) -> CacheResult<()> {
        Err(CacheError::Unavailable("provider is down".to_string()))
    }
}

impl CountingFileSystem {
    fn new() -> Self {
        Self {
            inner: Arc::new(MemFileSystem::new()),
            reads: Arc::new(AtomicU64::new(0)),
            read_dirs: Arc::new(AtomicU64::new(0)),
            stats: Arc::new(AtomicU64::new(0)),
            greps: Arc::new(AtomicU64::new(0)),
            trees: Arc::new(AtomicU64::new(0)),
            read_delay: Duration::ZERO,
        }
    }

    fn with_read_delay(mut self, delay: Duration) -> Self {
        self.read_delay = delay;
        self
    }

    fn read_count(&self) -> u64 {
        self.reads.load(Ordering::Relaxed)
    }

    fn read_dir_count(&self) -> u64 {
        self.read_dirs.load(Ordering::Relaxed)
    }

    fn stat_count(&self) -> u64 {
        self.stats.load(Ordering::Relaxed)
    }

    fn grep_count(&self) -> u64 {
        self.greps.load(Ordering::Relaxed)
    }

    fn tree_count(&self) -> u64 {
        self.trees.load(Ordering::Relaxed)
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
        self.reads.fetch_add(1, Ordering::Relaxed);
        if !self.read_delay.is_zero() {
            tokio::time::sleep(self.read_delay).await;
        }
        self.inner.read(path, offset, size).await
    }

    async fn write(&self, path: &str, data: &[u8], offset: u64, flags: WriteFlag) -> Result<u64> {
        self.inner.write(path, data, offset, flags).await
    }

    async fn read_dir(&self, path: &str) -> Result<Vec<FileInfo>> {
        self.read_dirs.fetch_add(1, Ordering::Relaxed);
        self.inner.read_dir(path).await
    }

    async fn stat(&self, path: &str) -> Result<FileInfo> {
        self.stats.fetch_add(1, Ordering::Relaxed);
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
        self.greps.fetch_add(1, Ordering::Relaxed);
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
        self.trees.fetch_add(1, Ordering::Relaxed);
        self.inner
            .tree_directory(path, show_hidden, node_limit, level_limit)
            .await
    }
}

fn cached_fs(backend: CountingFileSystem) -> (Arc<CachedFileSystem>, Arc<MemoryCacheProvider>) {
    cached_fs_with_policy(backend, CachePolicy::default())
}

fn cached_fs_with_policy(
    backend: CountingFileSystem,
    policy: CachePolicy,
) -> (Arc<CachedFileSystem>, Arc<MemoryCacheProvider>) {
    let provider = Arc::new(MemoryCacheProvider::new());
    let fs = Arc::new(CachedFileSystem::new(
        Box::new(backend),
        provider.clone(),
        CacheNamespace::new("test"),
        policy,
    ));
    (fs, provider)
}

fn cached_fs_with_tracking_provider(
    backend: CountingFileSystem,
    policy: CachePolicy,
) -> (Arc<CachedFileSystem>, Arc<TrackingProvider>) {
    cached_fs_with_tracking_provider_instance(backend, policy, Arc::new(TrackingProvider::new()))
}

fn cached_fs_with_tracking_provider_instance(
    backend: CountingFileSystem,
    policy: CachePolicy,
    provider: Arc<TrackingProvider>,
) -> (Arc<CachedFileSystem>, Arc<TrackingProvider>) {
    let fs = Arc::new(CachedFileSystem::new(
        Box::new(backend),
        provider.clone(),
        CacheNamespace::new("tracking"),
        policy,
    ));
    (fs, provider)
}

#[test]
fn cache_policy_traversal_mode_defaults_to_backend() {
    assert_eq!(
        CachePolicy::default().traversal_mode(),
        CacheTraversalMode::Backend
    );
}

#[tokio::test]
async fn default_tree_directory_delegates_to_backend() {
    let backend = CountingFileSystem::new();
    backend.mkdir("/docs", 0o755).await.unwrap();
    backend
        .write("/docs/one.md", b"one", 0, WriteFlag::Create)
        .await
        .unwrap();
    let probe = backend.clone();
    let (fs, _) = cached_fs(backend);

    let entries = fs.tree_directory("/docs", false, None, None).await.unwrap();

    assert_eq!(entries.len(), 1);
    assert_eq!(probe.tree_count(), 1);
    assert_eq!(probe.read_dir_count(), 0);
}

#[tokio::test]
async fn default_grep_delegates_to_backend() {
    let backend = CountingFileSystem::new();
    backend.mkdir("/docs", 0o755).await.unwrap();
    backend
        .write("/docs/one.md", b"needle", 0, WriteFlag::Create)
        .await
        .unwrap();
    let probe = backend.clone();
    let (fs, _) = cached_fs(backend);

    let result = fs
        .grep("/docs", "needle", true, false, None, None, None)
        .await
        .unwrap();

    assert_eq!(result.count, 1);
    assert_eq!(probe.grep_count(), 1);
    assert_eq!(probe.read_dir_count(), 0);
    assert_eq!(probe.read_count(), 0);
}

#[tokio::test]
async fn cached_tree_traversal_reuses_directory_cache_after_warmup() {
    let backend = CountingFileSystem::new();
    backend.mkdir("/docs", 0o755).await.unwrap();
    backend.mkdir("/docs/sub", 0o755).await.unwrap();
    backend
        .write("/docs/one.md", b"one", 0, WriteFlag::Create)
        .await
        .unwrap();
    backend
        .write("/docs/sub/two.md", b"two", 0, WriteFlag::Create)
        .await
        .unwrap();
    let probe = backend.clone();
    let (fs, _) = cached_fs_with_policy(
        backend,
        CachePolicy::default().with_traversal_mode(CacheTraversalMode::CachedTraversal),
    );

    let first = fs.tree_directory("/docs", false, None, None).await.unwrap();
    assert_eq!(first.len(), 3);
    assert_eq!(probe.tree_count(), 0);
    assert_eq!(probe.read_dir_count(), 2);

    let second = fs.tree_directory("/docs", false, None, None).await.unwrap();
    assert_eq!(second.len(), 3);
    assert_eq!(
        probe.read_dir_count(),
        2,
        "second tree should reuse cached read_dir entries"
    );
}

#[tokio::test]
async fn cached_grep_traversal_reuses_directory_and_file_cache_after_warmup() {
    let backend = CountingFileSystem::new();
    backend.mkdir("/docs", 0o755).await.unwrap();
    backend.mkdir("/docs/sub", 0o755).await.unwrap();
    backend
        .write("/docs/a.md", b"needle\nplain", 0, WriteFlag::Create)
        .await
        .unwrap();
    backend
        .write("/docs/sub/b.md", b"other\nneedle", 0, WriteFlag::Create)
        .await
        .unwrap();
    let probe = backend.clone();
    let (fs, _) = cached_fs_with_policy(
        backend,
        CachePolicy::default().with_traversal_mode(CacheTraversalMode::CachedTraversal),
    );

    let first = fs
        .grep("/docs", "needle", true, false, None, None, None)
        .await
        .unwrap();
    assert_eq!(first.count, 2);
    assert_eq!(probe.grep_count(), 0);
    assert_eq!(probe.read_dir_count(), 2);
    assert_eq!(probe.read_count(), 2);
    assert_eq!(probe.stat_count(), 1);

    let second = fs
        .grep("/docs", "needle", true, false, None, None, None)
        .await
        .unwrap();
    assert_eq!(second.count, 2);
    assert_eq!(
        probe.read_dir_count(),
        2,
        "second grep should reuse cached read_dir entries"
    );
    assert_eq!(
        probe.read_count(),
        2,
        "second grep should reuse cached file contents"
    );
    assert_eq!(
        probe.stat_count(),
        2,
        "second grep should only stat the query root, not every cached entry"
    );
}

#[tokio::test]
async fn cached_grep_batches_generation_validation_after_warmup() {
    let backend = CountingFileSystem::new();
    backend.mkdir("/docs", 0o755).await.unwrap();
    backend.mkdir("/docs/sub", 0o755).await.unwrap();
    backend
        .write("/docs/a.md", b"needle\nplain", 0, WriteFlag::Create)
        .await
        .unwrap();
    backend
        .write("/docs/sub/b.md", b"other\nneedle", 0, WriteFlag::Create)
        .await
        .unwrap();
    let (fs, provider) = cached_fs_with_tracking_provider(
        backend,
        CachePolicy::default().with_traversal_mode(CacheTraversalMode::CachedTraversal),
    );

    fs.grep("/docs", "needle", true, false, None, None, None)
        .await
        .unwrap();
    provider.reset_observed_reads();

    let result = fs
        .grep("/docs", "needle", true, false, None, None, None)
        .await
        .unwrap();

    assert_eq!(result.count, 2);
    assert!(
        provider.batch_get_count() > 0,
        "warm cached grep should batch generation validation reads"
    );
}

#[tokio::test]
async fn cached_grep_memoizes_generation_keys_within_one_traversal() {
    let backend = CountingFileSystem::new();
    backend.mkdir("/docs", 0o755).await.unwrap();
    backend.mkdir("/docs/sub", 0o755).await.unwrap();
    backend
        .write("/docs/a.md", b"needle\nplain", 0, WriteFlag::Create)
        .await
        .unwrap();
    backend
        .write("/docs/sub/b.md", b"other\nneedle", 0, WriteFlag::Create)
        .await
        .unwrap();
    let (fs, provider) = cached_fs_with_tracking_provider(
        backend,
        CachePolicy::default().with_traversal_mode(CacheTraversalMode::CachedTraversal),
    );

    fs.grep("/docs", "needle", true, false, None, None, None)
        .await
        .unwrap();
    provider.reset_observed_reads();

    let result = fs
        .grep("/docs", "needle", true, false, None, None, None)
        .await
        .unwrap();

    assert_eq!(result.count, 2);
    let subtree_keys = provider
        .observed_read_keys()
        .into_iter()
        .filter(|key| key.contains(":subtree:"))
        .collect::<Vec<_>>();
    let unique = subtree_keys
        .iter()
        .collect::<std::collections::HashSet<_>>();
    assert_eq!(
        subtree_keys.len(),
        unique.len(),
        "one cached grep traversal should not re-read the same generation key"
    );
}

#[tokio::test]
async fn cached_grep_scans_cached_files_with_bounded_concurrency() {
    let backend = CountingFileSystem::new();
    backend.mkdir("/docs", 0o755).await.unwrap();
    for index in 0..8 {
        backend
            .write(
                &format!("/docs/{index}.md"),
                b"needle\nplain",
                0,
                WriteFlag::Create,
            )
            .await
            .unwrap();
    }
    let provider = Arc::new(TrackingProvider::new().with_get_delay(Duration::from_millis(30)));
    let (fs, provider) = cached_fs_with_tracking_provider_instance(
        backend,
        CachePolicy::default().with_traversal_mode(CacheTraversalMode::CachedTraversal),
        provider,
    );

    fs.grep("/docs", "needle", true, false, None, None, None)
        .await
        .unwrap();
    provider.reset_observed_reads();

    let result = fs
        .grep("/docs", "needle", true, false, None, None, None)
        .await
        .unwrap();

    assert_eq!(result.count, 8);
    let max_gets = provider.max_concurrent_gets();
    assert!(
        max_gets > 1,
        "warm cached grep should scan cached file reads concurrently"
    );
    assert!(
        max_gets <= 8,
        "cached grep file scan concurrency should stay bounded"
    );
}

#[tokio::test]
async fn cached_grep_traversal_matches_default_grep_semantics() {
    let backend = CountingFileSystem::new();
    backend.mkdir("/docs", 0o755).await.unwrap();
    backend.mkdir("/docs/sub", 0o755).await.unwrap();
    backend.mkdir("/docs/skip", 0o755).await.unwrap();
    backend
        .write("/docs/a.md", b"Needle\nneedle", 0, WriteFlag::Create)
        .await
        .unwrap();
    backend
        .write("/docs/sub/b.md", b"needle\nmiss", 0, WriteFlag::Create)
        .await
        .unwrap();
    backend
        .write("/docs/skip/c.md", b"needle", 0, WriteFlag::Create)
        .await
        .unwrap();
    let direct = backend.clone();
    let (fs, _) = cached_fs_with_policy(
        backend,
        CachePolicy::default().with_traversal_mode(CacheTraversalMode::CachedTraversal),
    );

    let cached = fs
        .grep(
            "/docs",
            "needle",
            true,
            true,
            Some(2),
            Some("/docs/skip"),
            Some(1),
        )
        .await
        .unwrap();
    let direct_result = direct
        .grep(
            "/docs",
            "needle",
            true,
            true,
            Some(2),
            Some("/docs/skip"),
            Some(1),
        )
        .await
        .unwrap();

    let tuples = |result: &GrepResult| {
        result
            .matches
            .iter()
            .map(|m| (m.file.clone(), m.line, m.content.clone()))
            .collect::<Vec<_>>()
    };
    assert_eq!(tuples(&cached), tuples(&direct_result));
}

#[tokio::test]
async fn cached_grep_traversal_falls_back_for_multiwrite_backend() {
    let primary = CountingFileSystem::new();
    primary.mkdir("/docs", 0o755).await.unwrap();
    primary
        .write("/docs/a.md", b"needle", 0, WriteFlag::Create)
        .await
        .unwrap();
    let primary_probe = primary.clone();
    let multiwrite = MultiWriteWrappedFS::builder(Arc::new(primary))
        .build()
        .unwrap();
    let fs = CachedFileSystem::new(
        Box::new(multiwrite),
        Arc::new(MemoryCacheProvider::new()),
        CacheNamespace::new("grep-multiwrite"),
        CachePolicy::default().with_traversal_mode(CacheTraversalMode::CachedTraversal),
    );

    let ctx = Arc::new(FsContextInner::new("acct"));
    let result = FS_CTX
        .scope(ctx, async {
            fs.grep("/docs", "needle", true, false, None, None, None)
                .await
                .unwrap()
        })
        .await;

    assert_eq!(result.count, 1);
    assert_eq!(
        primary_probe.grep_count(),
        1,
        "multi-write backends must keep their backend grep path"
    );
    assert_eq!(
        primary_probe.read_dir_count(),
        0,
        "cached traversal must not bypass multi-write grep semantics"
    );
}

#[tokio::test]
async fn cached_tree_traversal_matches_default_tree_semantics() {
    let backend = CountingFileSystem::new();
    backend.mkdir("/docs", 0o755).await.unwrap();
    backend.mkdir("/docs/sub", 0o755).await.unwrap();
    backend
        .write("/docs/a.md", b"a", 0, WriteFlag::Create)
        .await
        .unwrap();
    backend
        .write("/docs/.hidden.md", b"hidden", 0, WriteFlag::Create)
        .await
        .unwrap();
    backend
        .write("/docs/sub/b.md", b"b", 0, WriteFlag::Create)
        .await
        .unwrap();
    let direct = backend.clone();
    let (fs, _) = cached_fs_with_policy(
        backend,
        CachePolicy::default().with_traversal_mode(CacheTraversalMode::CachedTraversal),
    );

    let cached = fs
        .tree_directory("/docs", false, None, Some(1))
        .await
        .unwrap();
    let direct_entries = direct
        .tree_directory("/docs", false, None, Some(1))
        .await
        .unwrap();
    assert_eq!(
        cached
            .iter()
            .map(|entry| entry.rel_path.as_str())
            .collect::<Vec<_>>(),
        direct_entries
            .iter()
            .map(|entry| entry.rel_path.as_str())
            .collect::<Vec<_>>()
    );

    let with_hidden = fs.tree_directory("/docs", true, None, None).await.unwrap();
    assert!(
        with_hidden
            .iter()
            .any(|entry| entry.rel_path == ".hidden.md"),
        "show_hidden=true should include hidden files"
    );
}

#[tokio::test]
async fn cached_tree_traversal_returns_oversized_directories_without_caching_them() {
    let backend = CountingFileSystem::new();
    backend.mkdir("/large", 0o755).await.unwrap();
    for index in 0..4097 {
        backend
            .write(
                &format!("/large/{index:04}.txt"),
                b"x",
                0,
                WriteFlag::Create,
            )
            .await
            .unwrap();
    }
    let probe = backend.clone();
    let (fs, _) = cached_fs_with_policy(
        backend,
        CachePolicy::default().with_traversal_mode(CacheTraversalMode::CachedTraversal),
    );

    assert_eq!(
        fs.tree_directory("/large", false, None, None)
            .await
            .unwrap()
            .len(),
        4097
    );
    assert_eq!(
        fs.tree_directory("/large", false, None, None)
            .await
            .unwrap()
            .len(),
        4097
    );
    assert_eq!(
        probe.read_dir_count(),
        2,
        "oversized directories should not be cached by tree traversal"
    );
}

#[tokio::test]
async fn cached_tree_traversal_falls_back_when_provider_is_unavailable() {
    let backend = CountingFileSystem::new();
    backend.mkdir("/docs", 0o755).await.unwrap();
    backend
        .write("/docs/a.md", b"a", 0, WriteFlag::Create)
        .await
        .unwrap();
    let probe = backend.clone();
    let fs = CachedFileSystem::new(
        Box::new(backend),
        Arc::new(UnavailableProvider),
        CacheNamespace::new("tree-unavailable"),
        CachePolicy::default().with_traversal_mode(CacheTraversalMode::CachedTraversal),
    );

    assert_eq!(
        fs.tree_directory("/docs", false, None, None)
            .await
            .unwrap()
            .len(),
        1
    );
    assert_eq!(
        fs.tree_directory("/docs", false, None, None)
            .await
            .unwrap()
            .len(),
        1
    );
    assert_eq!(probe.read_dir_count(), 2);
    assert!(fs.metrics().snapshot().errors >= 1);
}

#[tokio::test]
async fn cached_tree_traversal_falls_back_for_multiwrite_backend() {
    let primary = CountingFileSystem::new();
    primary.mkdir("/docs", 0o755).await.unwrap();
    primary
        .write("/docs/a.md", b"a", 0, WriteFlag::Create)
        .await
        .unwrap();
    let primary_probe = primary.clone();
    let multiwrite = MultiWriteWrappedFS::builder(Arc::new(primary))
        .build()
        .unwrap();
    let fs = CachedFileSystem::new(
        Box::new(multiwrite),
        Arc::new(MemoryCacheProvider::new()),
        CacheNamespace::new("tree-multiwrite"),
        CachePolicy::default().with_traversal_mode(CacheTraversalMode::CachedTraversal),
    );

    let ctx = Arc::new(FsContextInner::new("acct"));
    let entries = FS_CTX
        .scope(ctx, async {
            fs.tree_directory("/docs", false, None, None).await.unwrap()
        })
        .await;

    assert_eq!(entries.len(), 1);
    assert_eq!(
        primary_probe.tree_count(),
        1,
        "multi-write backends must keep their backend tree_directory path"
    );
    assert_eq!(
        primary_probe.read_dir_count(),
        0,
        "cached traversal must not bypass multi-write tree semantics"
    );
}

#[test]
fn cache_policy_supports_explicit_permission_sensitive_prefixes() {
    let policy = CachePolicy::default().with_bypass_prefix("/private");

    assert!(!policy.cache_file("/private/secret.md", 10));
    assert!(!policy.cache_directory("/private/docs"));
    assert!(policy.cache_file("/public/.overview.md", 10));
}

#[test]
fn cache_policy_marks_high_value_objects_as_preferred() {
    let policy = CachePolicy::default();

    assert_eq!(
        policy.file_decision("/docs/.abstract.md", 128),
        CacheDecision::Prefer
    );
    assert_eq!(
        policy.file_decision("/docs/.overview.md", 128),
        CacheDecision::Prefer
    );
    assert_eq!(
        policy.file_decision("/docs/note.md", 128),
        CacheDecision::Cache
    );
    assert_eq!(policy.directory_decision("/docs"), CacheDecision::Prefer);
}

#[tokio::test]
async fn memory_provider_satisfies_the_common_contract() {
    let provider = MemoryMockProvider::new();

    provider.put("one", Bytes::from_static(b"1")).await.unwrap();
    assert!(provider.exists("one").await.unwrap());
    assert_eq!(
        provider.get("one").await.unwrap(),
        Some(Bytes::from_static(b"1"))
    );

    provider
        .batch_put(vec![
            ("two".to_string(), Bytes::from_static(b"2")),
            ("three".to_string(), Bytes::from_static(b"3")),
        ])
        .await
        .unwrap();
    assert_eq!(
        provider
            .batch_get(&["one".to_string(), "missing".to_string()])
            .await
            .unwrap(),
        vec![Some(Bytes::from_static(b"1")), None]
    );

    provider
        .invalidate(&["one".to_string(), "two".to_string()])
        .await
        .unwrap();
    assert!(!provider.exists("one").await.unwrap());
    assert!(!provider.exists("two").await.unwrap());
    provider.flush().await.unwrap();
    assert!(!provider.exists("three").await.unwrap());
    provider.close().await.unwrap();
    assert!(provider.get("three").await.is_err());
}

#[tokio::test]
async fn full_file_reads_are_read_through_cached_but_range_reads_bypass() {
    let backend = CountingFileSystem::new();
    backend
        .write("/note.md", b"hello world", 0, WriteFlag::Create)
        .await
        .unwrap();
    let probe = backend.clone();
    let (fs, _) = cached_fs(backend);

    assert_eq!(fs.read("/note.md", 0, 0).await.unwrap(), b"hello world");
    assert_eq!(fs.read("/note.md", 0, 0).await.unwrap(), b"hello world");
    assert_eq!(probe.read_count(), 1);

    assert_eq!(fs.read("/note.md", 6, 5).await.unwrap(), b"world");
    assert_eq!(probe.read_count(), 2);

    let metrics = fs.metrics().snapshot();
    assert_eq!(metrics.file_hits, 1);
    assert_eq!(metrics.file_misses, 1);
    assert_eq!(metrics.backend_fallbacks, 1);
}

#[tokio::test]
async fn read_dir_is_cached_and_parent_changes_invalidate_it() {
    let backend = CountingFileSystem::new();
    backend.mkdir("/docs", 0o755).await.unwrap();
    backend
        .write("/docs/one.md", b"one", 0, WriteFlag::Create)
        .await
        .unwrap();
    let probe = backend.clone();
    let (fs, _) = cached_fs(backend);

    assert_eq!(fs.read_dir("/docs").await.unwrap().len(), 1);
    assert_eq!(fs.read_dir("/docs").await.unwrap().len(), 1);
    assert_eq!(probe.read_dir_count(), 1);

    fs.write("/docs/two.md", b"two", 0, WriteFlag::Create)
        .await
        .unwrap();
    let entries = fs.read_dir("/docs").await.unwrap();
    assert_eq!(entries.len(), 2);
    assert_eq!(probe.read_dir_count(), 2);

    let metrics = fs.metrics().snapshot();
    assert_eq!(metrics.read_dir_hits, 1);
    assert_eq!(metrics.read_dir_misses, 2);
    assert!(metrics.invalidations >= 1);
}

#[tokio::test]
async fn oversized_directories_bypass_directory_cache() {
    let cacheable = CountingFileSystem::new();
    cacheable.mkdir("/small", 0o755).await.unwrap();
    for index in 0..4096 {
        cacheable
            .write(
                &format!("/small/{index:04}.txt"),
                b"x",
                0,
                WriteFlag::Create,
            )
            .await
            .unwrap();
    }
    let cacheable_probe = cacheable.clone();
    let (cacheable_fs, _) = cached_fs(cacheable);

    assert_eq!(cacheable_fs.read_dir("/small").await.unwrap().len(), 4096);
    assert_eq!(cacheable_fs.read_dir("/small").await.unwrap().len(), 4096);
    assert_eq!(cacheable_probe.read_dir_count(), 1);

    let oversized = CountingFileSystem::new();
    oversized.mkdir("/large", 0o755).await.unwrap();
    for index in 0..4097 {
        oversized
            .write(
                &format!("/large/{index:04}.txt"),
                b"x",
                0,
                WriteFlag::Create,
            )
            .await
            .unwrap();
    }
    let oversized_probe = oversized.clone();
    let (oversized_fs, _) = cached_fs(oversized);

    assert_eq!(oversized_fs.read_dir("/large").await.unwrap().len(), 4097);
    assert_eq!(oversized_fs.read_dir("/large").await.unwrap().len(), 4097);
    assert_eq!(oversized_probe.read_dir_count(), 2);
}

#[tokio::test]
async fn all_directory_membership_mutations_invalidate_parent_entries() {
    let backend = CountingFileSystem::new();
    backend.mkdir("/root", 0o755).await.unwrap();
    backend.mkdir("/root/old", 0o755).await.unwrap();
    backend
        .write("/root/file.txt", b"file", 0, WriteFlag::Create)
        .await
        .unwrap();
    let probe = backend.clone();
    let (fs, _) = cached_fs(backend);

    assert_eq!(fs.read_dir("/root").await.unwrap().len(), 2);
    assert_eq!(fs.read_dir("/root").await.unwrap().len(), 2);

    fs.mkdir("/root/new", 0o755).await.unwrap();
    assert_eq!(fs.read_dir("/root").await.unwrap().len(), 3);

    fs.rename("/root/old", "/root/moved").await.unwrap();
    let names: Vec<String> = fs
        .read_dir("/root")
        .await
        .unwrap()
        .into_iter()
        .map(|entry| entry.name)
        .collect();
    assert!(names.contains(&"moved".to_string()));
    assert!(!names.contains(&"old".to_string()));

    fs.remove("/root/file.txt").await.unwrap();
    assert_eq!(fs.read_dir("/root").await.unwrap().len(), 2);

    fs.remove_all("/root/new").await.unwrap();
    assert_eq!(fs.read_dir("/root").await.unwrap().len(), 1);
    assert_eq!(probe.read_dir_count(), 5);
}

#[tokio::test]
async fn writes_and_deletes_never_leave_stale_file_cache_entries() {
    let backend = CountingFileSystem::new();
    backend
        .write("/value.txt", b"old", 0, WriteFlag::Create)
        .await
        .unwrap();
    let probe = backend.clone();
    let (fs, _) = cached_fs(backend);

    assert_eq!(fs.read("/value.txt", 0, 0).await.unwrap(), b"old");
    fs.write("/value.txt", b"new", 0, WriteFlag::Truncate)
        .await
        .unwrap();
    assert_eq!(fs.read("/value.txt", 0, 0).await.unwrap(), b"new");

    fs.remove("/value.txt").await.unwrap();
    assert!(fs.read("/value.txt", 0, 0).await.is_err());
    assert_eq!(probe.read_count(), 2);
}

#[tokio::test]
async fn full_writes_populate_cache_before_returning() {
    let backend = CountingFileSystem::new();
    let probe = backend.clone();
    let (fs, _) = cached_fs(backend);

    fs.write("/fresh.md", b"fresh", 0, WriteFlag::Create)
        .await
        .unwrap();
    assert_eq!(fs.read("/fresh.md", 0, 0).await.unwrap(), b"fresh");
    assert_eq!(probe.read_count(), 0);
}

#[tokio::test]
async fn partial_writes_and_truncate_invalidate_cached_file_contents() {
    let backend = CountingFileSystem::new();
    backend
        .write("/partial.txt", b"hello", 0, WriteFlag::Create)
        .await
        .unwrap();
    let probe = backend.clone();
    let (fs, _) = cached_fs(backend);

    assert_eq!(fs.read("/partial.txt", 0, 0).await.unwrap(), b"hello");
    fs.write("/partial.txt", b"X", 1, WriteFlag::None)
        .await
        .unwrap();
    assert_eq!(fs.read("/partial.txt", 0, 0).await.unwrap(), b"hXllo");

    fs.truncate("/partial.txt", 2).await.unwrap();
    assert_eq!(fs.read("/partial.txt", 0, 0).await.unwrap(), b"hX");
    assert_eq!(probe.read_count(), 3);
}

#[tokio::test]
async fn file_rename_invalidates_old_and_new_paths() {
    let backend = CountingFileSystem::new();
    backend
        .write("/old.txt", b"old", 0, WriteFlag::Create)
        .await
        .unwrap();
    backend
        .write("/new.txt", b"historical", 0, WriteFlag::Create)
        .await
        .unwrap();
    let direct = backend.clone();
    let (fs, _) = cached_fs(backend);

    assert_eq!(fs.read("/old.txt", 0, 0).await.unwrap(), b"old");
    assert_eq!(fs.read("/new.txt", 0, 0).await.unwrap(), b"historical");
    direct.remove("/new.txt").await.unwrap();

    fs.rename("/old.txt", "/new.txt").await.unwrap();

    assert!(fs.read("/old.txt", 0, 0).await.is_err());
    assert_eq!(fs.read("/new.txt", 0, 0).await.unwrap(), b"old");
}

#[tokio::test]
async fn remove_all_generation_rejects_residual_descendant_cache_entries() {
    let backend = CountingFileSystem::new();
    backend.mkdir("/tree", 0o755).await.unwrap();
    backend
        .write("/tree/leaf.txt", b"old", 0, WriteFlag::Create)
        .await
        .unwrap();
    let direct = backend.clone();
    let probe = backend.clone();
    let (fs, _) = cached_fs(backend);

    assert_eq!(fs.read("/tree/leaf.txt", 0, 0).await.unwrap(), b"old");
    fs.remove_all("/tree").await.unwrap();

    direct.mkdir("/tree", 0o755).await.unwrap();
    direct
        .write("/tree/leaf.txt", b"new", 0, WriteFlag::Create)
        .await
        .unwrap();

    assert_eq!(fs.read("/tree/leaf.txt", 0, 0).await.unwrap(), b"new");
    assert_eq!(probe.read_count(), 2);
}

#[tokio::test]
async fn shared_provider_generation_bump_invalidates_other_wrappers() {
    let backend = CountingFileSystem::new();
    backend.mkdir("/tree", 0o755).await.unwrap();
    backend
        .write("/tree/leaf.txt", b"old", 0, WriteFlag::Create)
        .await
        .unwrap();
    let direct = backend.clone();
    let probe = backend.clone();
    let provider = Arc::new(MemoryCacheProvider::new());

    let first = CachedFileSystem::new(
        Box::new(backend.clone()),
        provider.clone(),
        CacheNamespace::new("shared"),
        CachePolicy::default(),
    );
    let second = CachedFileSystem::new(
        Box::new(backend),
        provider,
        CacheNamespace::new("shared"),
        CachePolicy::default(),
    );

    assert_eq!(first.read("/tree/leaf.txt", 0, 0).await.unwrap(), b"old");
    second.remove_all("/tree").await.unwrap();

    direct.mkdir("/tree", 0o755).await.unwrap();
    direct
        .write("/tree/leaf.txt", b"new", 0, WriteFlag::Create)
        .await
        .unwrap();

    assert_eq!(first.read("/tree/leaf.txt", 0, 0).await.unwrap(), b"new");
    assert_eq!(probe.read_count(), 2);
}

#[tokio::test]
async fn provider_generation_eviction_after_restart_cannot_revive_old_descendants() {
    let backend = CountingFileSystem::new();
    backend.mkdir("/tree", 0o755).await.unwrap();
    backend
        .write("/tree/leaf.txt", b"old", 0, WriteFlag::Create)
        .await
        .unwrap();
    let direct = backend.clone();
    let probe = backend.clone();
    let provider = Arc::new(MemoryCacheProvider::new());

    let first = CachedFileSystem::new(
        Box::new(backend.clone()),
        provider.clone(),
        CacheNamespace::new("restart"),
        CachePolicy::default(),
    );
    assert_eq!(first.read("/tree/leaf.txt", 0, 0).await.unwrap(), b"old");
    first.remove_all("/tree").await.unwrap();

    direct.mkdir("/tree", 0o755).await.unwrap();
    direct
        .write("/tree/leaf.txt", b"new", 0, WriteFlag::Create)
        .await
        .unwrap();

    for key in provider.keys().await {
        if key.contains(":subtree:") {
            provider.delete(&key).await.unwrap();
        }
    }
    drop(first);

    let restarted = CachedFileSystem::new(
        Box::new(backend),
        provider,
        CacheNamespace::new("restart"),
        CachePolicy::default(),
    );
    assert_eq!(
        restarted.read("/tree/leaf.txt", 0, 0).await.unwrap(),
        b"new"
    );
    assert_eq!(probe.read_count(), 2);
}

#[tokio::test]
async fn directory_rename_invalidates_old_and_historical_new_subtrees() {
    let backend = CountingFileSystem::new();
    backend.mkdir("/old", 0o755).await.unwrap();
    backend
        .write("/old/leaf.txt", b"moved", 0, WriteFlag::Create)
        .await
        .unwrap();
    let direct = backend.clone();
    let (fs, _) = cached_fs(backend);

    assert_eq!(fs.read("/old/leaf.txt", 0, 0).await.unwrap(), b"moved");
    fs.rename("/old", "/new").await.unwrap();
    assert!(fs.read("/old/leaf.txt", 0, 0).await.is_err());
    assert_eq!(fs.read("/new/leaf.txt", 0, 0).await.unwrap(), b"moved");

    fs.remove_all("/new").await.unwrap();
    direct.mkdir("/new", 0o755).await.unwrap();
    direct
        .write("/new/leaf.txt", b"replacement", 0, WriteFlag::Create)
        .await
        .unwrap();
    assert_eq!(
        fs.read("/new/leaf.txt", 0, 0).await.unwrap(),
        b"replacement"
    );
}

#[tokio::test]
async fn concurrent_misses_share_one_backend_read() {
    let backend = CountingFileSystem::new().with_read_delay(Duration::from_millis(30));
    backend
        .write("/hot.md", b"hot", 0, WriteFlag::Create)
        .await
        .unwrap();
    let probe = backend.clone();
    let (fs, _) = cached_fs(backend);

    let mut tasks = Vec::new();
    for _ in 0..12 {
        let fs = fs.clone();
        tasks.push(tokio::spawn(async move {
            fs.read("/hot.md", 0, 0).await.unwrap()
        }));
    }
    for task in tasks {
        assert_eq!(task.await.unwrap(), b"hot");
    }

    assert_eq!(probe.read_count(), 1);
    let metrics = fs.metrics().snapshot();
    assert_eq!(metrics.inflight_leaders, 1);
    assert_eq!(metrics.inflight_followers, 11);
    assert_eq!(metrics.inflight_backend_saved, 11);
}

#[tokio::test]
async fn cache_policy_bypasses_lock_and_control_files() {
    let backend = CountingFileSystem::new();
    backend
        .write("/state.lock", b"first", 0, WriteFlag::Create)
        .await
        .unwrap();
    backend.mkdir("/queue", 0o755).await.unwrap();
    backend
        .write("/queue/peek", b"live", 0, WriteFlag::Create)
        .await
        .unwrap();
    let probe = backend.clone();
    let (fs, _) = cached_fs(backend);

    fs.read("/state.lock", 0, 0).await.unwrap();
    fs.read("/state.lock", 0, 0).await.unwrap();
    fs.read("/queue/peek", 0, 0).await.unwrap();
    fs.read("/queue/peek", 0, 0).await.unwrap();

    assert_eq!(probe.read_count(), 4);
    assert_eq!(fs.metrics().snapshot().policy_bypasses, 4);
}

#[tokio::test]
async fn failed_invalidation_bypasses_cache_instead_of_serving_stale_data() {
    let backend = CountingFileSystem::new();
    backend
        .write("/value.txt", b"old", 0, WriteFlag::Create)
        .await
        .unwrap();
    let probe = backend.clone();
    let fs = CachedFileSystem::new(
        Box::new(backend),
        Arc::new(DeleteFailingProvider::new()),
        CacheNamespace::new("delete-failure"),
        CachePolicy::default(),
    );

    assert_eq!(fs.read("/value.txt", 0, 0).await.unwrap(), b"old");
    fs.write("/value.txt", b"new", 0, WriteFlag::Truncate)
        .await
        .unwrap();
    assert_eq!(fs.read("/value.txt", 0, 0).await.unwrap(), b"new");
    assert_eq!(probe.read_count(), 2);

    let metrics = fs.metrics().snapshot();
    assert!(metrics.errors >= 1);
    assert!(metrics.policy_bypasses >= 1);
}

#[tokio::test]
async fn unavailable_provider_falls_back_to_backend_and_enters_bypass() {
    let backend = CountingFileSystem::new();
    backend
        .write("/available.txt", b"backend", 0, WriteFlag::Create)
        .await
        .unwrap();
    let probe = backend.clone();
    let fs = CachedFileSystem::new(
        Box::new(backend),
        Arc::new(UnavailableProvider),
        CacheNamespace::new("unavailable"),
        CachePolicy::default(),
    );

    assert_eq!(fs.read("/available.txt", 0, 0).await.unwrap(), b"backend");
    assert_eq!(fs.read("/available.txt", 0, 0).await.unwrap(), b"backend");
    assert_eq!(probe.read_count(), 2);

    let metrics = fs.metrics().snapshot();
    assert!(metrics.errors >= 2);
    assert!(metrics.policy_bypasses >= 1);
}

#[tokio::test]
async fn metrics_cover_operations_bytes_latency_and_errors() {
    let backend = CountingFileSystem::new();
    backend
        .write("/metrics.txt", b"metrics", 0, WriteFlag::Create)
        .await
        .unwrap();
    let (fs, _) = cached_fs(backend);

    assert_eq!(fs.read("/metrics.txt", 0, 0).await.unwrap(), b"metrics");
    assert_eq!(fs.read("/metrics.txt", 0, 0).await.unwrap(), b"metrics");
    fs.write("/metrics.txt", b"updated", 0, WriteFlag::Truncate)
        .await
        .unwrap();

    let metrics = fs.metrics().snapshot();
    assert_eq!(metrics.file_hits, 1);
    assert_eq!(metrics.file_misses, 1);
    assert_eq!(metrics.backend_fallbacks, 1);
    assert!(metrics.puts >= 1);
    assert!(metrics.deletes >= 1);
    assert!(metrics.invalidations >= 1);
    assert_eq!(metrics.backend_bytes, 7);
    assert_eq!(metrics.cache_bytes, 7);
    assert!(metrics.get_latency_ns > 0);
    assert!(metrics.put_latency_ns > 0);
    assert!(metrics.delete_latency_ns > 0);
    assert_eq!(metrics.errors, 0);
}
