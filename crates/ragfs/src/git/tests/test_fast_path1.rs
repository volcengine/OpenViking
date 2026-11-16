    // Tests for Fast Path 1 (the persistent stat cache). The strategy:
    //   - Wrap `MockFsCounting` around a tiny in-memory map with controllable
    //     `mod_time` per path AND a `reads` counter.
    //   - Wrap `LocalIndexStore` in a temp dir as the index backend.
    //   - Run two commits and assert `reads` only goes up by the expected
    //     amount on the second one.
    //
    // Each assertion pins a distinct invariant of Fast Path 1: cache hit
    // skips read; (size, mtime_ns) mismatch invalidates; parent_oid mismatch
    // disables the cache; corruption is silent; partial-paths preserves
    // uncovered entries; deletion removes entries.
    use super::*;
    use async_trait::async_trait;
    use std::collections::HashMap;
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::sync::{Arc, Mutex};
    use std::time::{Duration, SystemTime, UNIX_EPOCH};

    use crate::core::errors::{Error, Result};
    use crate::core::filesystem::FileSystem;
    use crate::core::types::{FileInfo, TreeEntry, WriteFlag};
    use crate::git::backends::local::{LocalIndexStore, LocalObjectStore, LocalRefStore};
    use crate::git::index_store::IndexStore;

    struct CountingVfs {
        account: String,
        // path -> (bytes, mtime_ns)
        files: Arc<Mutex<HashMap<String, (Vec<u8>, i128)>>>,
        reads: AtomicU64,
    }

    impl CountingVfs {
        fn new(account: &str) -> Arc<Self> {
            Arc::new(Self {
                account: account.to_string(),
                files: Arc::new(Mutex::new(HashMap::new())),
                reads: AtomicU64::new(0),
            })
        }

        fn put(&self, rel: &str, data: &[u8], mtime_ns: i128) {
            let abs = format!("/local/{}/{}", self.account, rel);
            self.files
                .lock()
                .unwrap()
                .insert(abs, (data.to_vec(), mtime_ns));
        }

        fn delete(&self, rel: &str) {
            let abs = format!("/local/{}/{}", self.account, rel);
            self.files.lock().unwrap().remove(&abs);
        }

        fn reads(&self) -> u64 {
            self.reads.load(Ordering::SeqCst)
        }
    }

    fn nanos_to_systemtime(ns: i128) -> SystemTime {
        // Tests use small positive nanos, so the cast is lossless.
        let secs = (ns / 1_000_000_000) as u64;
        let sub = (ns % 1_000_000_000) as u32;
        UNIX_EPOCH + Duration::new(secs, sub)
    }

    #[async_trait]
    impl FileSystem for CountingVfs {
        async fn create(&self, _path: &str) -> Result<()> {
            unimplemented!()
        }
        async fn mkdir(&self, _path: &str, _mode: u32) -> Result<()> {
            unimplemented!()
        }
        async fn remove(&self, path: &str) -> Result<()> {
            self.files.lock().unwrap().remove(path);
            Ok(())
        }
        async fn remove_all(&self, _path: &str) -> Result<()> {
            unimplemented!()
        }

        async fn read(&self, path: &str, _offset: u64, _size: u64) -> Result<Vec<u8>> {
            let g = self.files.lock().unwrap();
            match g.get(path) {
                Some((bytes, _)) => {
                    self.reads.fetch_add(1, Ordering::SeqCst);
                    Ok(bytes.clone())
                }
                None => Err(Error::not_found(path)),
            }
        }

        async fn write(
            &self,
            path: &str,
            data: &[u8],
            _offset: u64,
            _flags: WriteFlag,
        ) -> Result<u64> {
            self.files
                .lock()
                .unwrap()
                .insert(path.to_string(), (data.to_vec(), 0));
            Ok(data.len() as u64)
        }
        async fn read_dir(
            &self,
            _path: &str,
            _offset: Option<usize>,
            _limit: Option<usize>,
            _sort_by: Option<crate::core::ListSortBy>,
            _sort_order: Option<crate::core::SortOrder>,
        ) -> Result<Vec<FileInfo>> {
            unimplemented!()
        }

        async fn stat(&self, path: &str) -> Result<FileInfo> {
            let g = self.files.lock().unwrap();
            if let Some((bytes, mtime_ns)) = g.get(path) {
                let name = path.rsplit('/').next().unwrap_or(path).to_string();
                return Ok(FileInfo::new(
                    name,
                    bytes.len() as u64,
                    0o644,
                    nanos_to_systemtime(*mtime_ns),
                    false,
                ));
            }
            Err(Error::not_found(path))
        }

        async fn rename(&self, _o: &str, _n: &str) -> Result<()> {
            unimplemented!()
        }
        async fn chmod(&self, _path: &str, _mode: u32) -> Result<()> {
            unimplemented!()
        }

        async fn tree_directory(
            &self,
            path: &str,
            _show_hidden: bool,
            _node_limit: Option<usize>,
            _level_limit: Option<usize>,
            _offset: Option<usize>,
            _sort_by: Option<crate::core::ListSortBy>,
            _sort_order: Option<crate::core::SortOrder>,
            _directories_only: bool,
        ) -> Result<Vec<TreeEntry>> {
            let prefix = if path == "/" {
                "/".to_string()
            } else {
                format!("{}/", path)
            };
            let g = self.files.lock().unwrap();
            let mut out = Vec::new();
            for (full_path, (_bytes, _mtime)) in g.iter() {
                if !full_path.starts_with(&prefix) {
                    continue;
                }
                let rel = full_path
                    .strip_prefix(&prefix)
                    .unwrap_or(full_path)
                    .to_string();
                let name = full_path
                    .rsplit('/')
                    .next()
                    .unwrap_or(full_path)
                    .to_string();
                out.push(TreeEntry {
                    path: full_path.clone(),
                    rel_path: rel,
                    info: FileInfo::new_file(name, 0, 0o644),
                    extra: HashMap::new(),
                });
            }
            Ok(out)
        }
    }

    fn make_service_with_index(
        account: &str,
    ) -> (
        tempfile::TempDir,
        Arc<CountingVfs>,
        Arc<LocalIndexStore>,
        GitService,
    ) {
        let dir = tempfile::tempdir().unwrap();
        let object_store = Arc::new(LocalObjectStore::new(dir.path()));
        let ref_store = Arc::new(LocalRefStore::new(dir.path()));
        let index_store = Arc::new(LocalIndexStore::new(dir.path()));
        let vfs = CountingVfs::new(account);
        let svc = GitService::with_index(
            vfs.clone() as Arc<dyn FileSystem>,
            object_store as Arc<dyn ObjectStore>,
            ref_store as Arc<dyn RefStore>,
            Some(index_store.clone() as Arc<dyn IndexStore>),
        );
        (dir, vfs, index_store, svc)
    }

    fn make_service_no_index(account: &str) -> (tempfile::TempDir, Arc<CountingVfs>, GitService) {
        let dir = tempfile::tempdir().unwrap();
        let object_store = Arc::new(LocalObjectStore::new(dir.path()));
        let ref_store = Arc::new(LocalRefStore::new(dir.path()));
        let vfs = CountingVfs::new(account);
        let svc = GitService::new(
            vfs.clone() as Arc<dyn FileSystem>,
            object_store as Arc<dyn ObjectStore>,
            ref_store as Arc<dyn RefStore>,
        );
        (dir, vfs, svc)
    }

    fn req(account: &str, branch: &str, paths: Option<Vec<String>>) -> CommitRequest {
        CommitRequest {
            account: account.to_string(),
            branch: branch.to_string(),
            message: "m".to_string(),
            paths,
            author_name: "tester".to_string(),
            author_email: "t@x".to_string(),
        }
    }

    #[tokio::test]
    async fn cached_stat_match_skips_read() {
        // Two files. First commit reads both. Second commit, with
        // identical (size, mtime_ns), reads NEITHER — Fast Path 1 hits.
        // mtimes are well in the past (year 2001 / 2004) so they predate the
        // real index file's save time → not racy → Fast Path 1 is trusted.
        let (_dir, vfs, _idx, svc) = make_service_with_index("acct");
        vfs.put("a.md", b"hello", 1_000_000_000_000_000_000);
        vfs.put("b.md", b"world", 1_100_000_000_000_000_000);

        let _ = svc.commit(req("acct", "main", None)).await.unwrap();
        let reads_after_first = vfs.reads();
        assert_eq!(reads_after_first, 2, "first commit must read both files");

        // Second commit, no changes → Noop, but commit() still walks
        // candidates and decides whether to read each. Fast Path 1 means
        // (size, mtime_ns) match → no read needed.
        let resp = svc.commit(req("acct", "main", None)).await.unwrap();
        match resp {
            CommitResponse::Noop { .. } => {}
            other => panic!("expected Noop, got {other:?}"),
        }
        assert_eq!(
            vfs.reads(),
            reads_after_first,
            "Fast Path 1 hit: no extra reads on second commit",
        );
    }

    #[tokio::test]
    async fn racy_clean_same_size_same_mtime_is_not_lost() {
        // Regression for the "racy clean" data-loss bug: a file changed to the
        // same byte length within the same filesystem clock tick as the index
        // write must NOT be skipped by Fast Path 1.
        //
        // We simulate "the file's mtime is in (or after) the index's clock
        // tick" by giving the working-tree file a mtime in the far future
        // (year 2033), which is guaranteed to be >= the real index file's
        // save time. Both versions are 2 bytes with the SAME (size, mtime),
        // exactly the signature Fast Path 1 keys on.
        let racy_mtime = 2_000_000_000_000_000_000; // ~year 2033, >= index save time
        let (_dir, vfs, _idx, svc) = make_service_with_index("acct");

        vfs.put("a.md", b"v1", racy_mtime);
        let _ = svc.commit(req("acct", "main", None)).await.unwrap();
        let reads_after_v1 = vfs.reads();

        // Change content to a different 2-byte value, keeping (size, mtime)
        // identical — the pathological case the stat signature cannot detect.
        vfs.put("a.md", b"v2", racy_mtime);
        let resp = svc.commit(req("acct", "main", None)).await.unwrap();

        // The racy-clean guard must force a slow-path read and capture the
        // change as a real commit. Without the guard this would be a Noop
        // (Fast Path 1 reusing v1's blob oid) — silently dropping v2.
        match resp {
            CommitResponse::Created { .. } => {}
            other => panic!("expected Created (v2 must be committed), got {other:?}"),
        }
        assert_eq!(
            vfs.reads(),
            reads_after_v1 + 1,
            "racy entry (mtime >= index save time) must be re-read, not trusted",
        );

        // And the committed blob must actually be v2, not the stale v1.
        let shown = svc
            .show(ShowRequest {
                account: "acct".to_string(),
                target_ref: "main".to_string(),
                path: Some("a.md".to_string()),
            })
            .await
            .unwrap();
        match shown {
            ShowResponse::Blob { bytes, .. } => {
                assert_eq!(&bytes[..], b"v2", "committed blob must reflect v2, not v1");
            }
            other => panic!("expected Blob, got {other:?}"),
        }
    }

    #[tokio::test]
    async fn size_mismatch_invalidates_cache_entry() {
        let (_dir, vfs, _idx, svc) = make_service_with_index("acct");
        vfs.put("a.md", b"hello", 1_000_000_000_000_000_000);
        let _ = svc.commit(req("acct", "main", None)).await.unwrap();
        let reads_after_first = vfs.reads();

        // Same mtime, different size → Fast Path 1 must MISS for this file.
        vfs.put("a.md", b"helloX", 1_000_000_000_000_000_000);
        let _ = svc.commit(req("acct", "main", None)).await.unwrap();
        assert_eq!(
            vfs.reads(),
            reads_after_first + 1,
            "size mismatch must trigger one extra read",
        );
    }

    #[tokio::test]
    async fn mtime_mismatch_invalidates_cache_entry() {
        let (_dir, vfs, _idx, svc) = make_service_with_index("acct");
        vfs.put("a.md", b"hello", 1_000_000_000_000_000_000);
        let _ = svc.commit(req("acct", "main", None)).await.unwrap();
        let reads_after_first = vfs.reads();

        // Same size, different mtime → cache miss.
        vfs.put("a.md", b"hello", 2_000_000_000_000_000_000);
        let resp = svc.commit(req("acct", "main", None)).await.unwrap();
        // Same content → identical oid → no editor change → Noop.
        match resp {
            CommitResponse::Noop { .. } => {}
            other => panic!("expected Noop, got {other:?}"),
        }
        assert_eq!(
            vfs.reads(),
            reads_after_first + 1,
            "mtime mismatch must trigger one extra read",
        );
    }

    #[tokio::test]
    async fn parent_oid_mismatch_disables_cache() {
        // Build a service whose index file's parent_oid is stale relative
        // to the branch HEAD: drop in a hand-crafted CommitIndex pointing at
        // a bogus parent, then commit and assert ALL files were re-read.
        let (dir, vfs, idx, svc) = make_service_with_index("acct");
        vfs.put("a.md", b"hello", 1_000_000_000_000_000_000);
        vfs.put("b.md", b"world", 2_000_000_000_000_000_000);
        let _ = svc.commit(req("acct", "main", None)).await.unwrap();
        let reads_after_first = vfs.reads();

        // Overwrite the index on disk with one whose parent_oid is bogus.
        let bogus = ObjectId::from_hex(b"deadbeefdeadbeefdeadbeefdeadbeefdeadbeef").unwrap();
        let stale = CommitIndex {
            parent_oid: bogus,
            entries: HashMap::new(), // doesn't matter; whole file is rejected
            saved_at_ns: None,
        };
        idx.save("acct", "main", &stale).await.unwrap();
        let _ = dir; // keep tempdir alive

        // Re-commit with same contents. Cache parent_oid != HEAD → cache
        // discarded entirely → both files re-read.
        let _ = svc.commit(req("acct", "main", None)).await.unwrap();
        assert_eq!(
            vfs.reads(),
            reads_after_first + 2,
            "parent_oid mismatch must force read of every candidate",
        );
    }

    #[tokio::test]
    async fn deleted_path_is_removed_from_index() {
        let (_dir, vfs, idx, svc) = make_service_with_index("acct");
        vfs.put("a.md", b"a", 1_000_000_000_000_000_000);
        vfs.put("b.md", b"b", 2_000_000_000_000_000_000);
        let _ = svc.commit(req("acct", "main", None)).await.unwrap();

        // Delete a.md, commit with explicit paths so the deletion is
        // observed. Commit succeeds; the persisted index must drop a.md
        // and keep b.md.
        vfs.delete("a.md");
        let _ = svc
            .commit(req(
                "acct",
                "main",
                Some(vec!["a.md".into(), "b.md".into()]),
            ))
            .await
            .unwrap();

        let saved = idx.load("acct", "main").await.unwrap().unwrap();
        assert!(
            !saved.entries.contains_key("a.md"),
            "deleted path must be removed from the index",
        );
        assert!(
            saved.entries.contains_key("b.md"),
            "surviving path must remain in the index",
        );
    }

    #[tokio::test]
    async fn corrupted_index_falls_back_silently() {
        // Drop a malformed file at the index path BEFORE the first commit,
        // then commit normally. The corrupt file makes load() return None,
        // so commit takes the slow path — but it MUST still succeed and
        // overwrite the corrupt file with a valid one.
        let (dir, vfs, idx, svc) = make_service_with_index("acct");
        let path = dir.path().join("acct").join("index").join("main.json");
        tokio::fs::create_dir_all(path.parent().unwrap())
            .await
            .unwrap();
        tokio::fs::write(&path, b"NOT-JSON-AT-ALL").await.unwrap();

        vfs.put("a.md", b"hi", 1_000_000_000_000_000_000);
        let resp = svc.commit(req("acct", "main", None)).await.unwrap();
        assert!(matches!(resp, CommitResponse::Created { .. }));
        // The save after commit succeeded → load now returns a real index.
        let loaded = idx.load("acct", "main").await.unwrap().unwrap();
        assert!(loaded.entries.contains_key("a.md"));
    }

    #[tokio::test]
    async fn partial_paths_preserves_uncovered_entries() {
        // First commit covers a + b.
        // Second commit lists ONLY [a]; b is never enumerated. The new
        // index must still contain b's entry so a future full-enum commit
        // can still hit the cache for b.
        let (_dir, vfs, idx, svc) = make_service_with_index("acct");
        vfs.put("a.md", b"a", 1_000_000_000_000_000_000);
        vfs.put("b.md", b"b", 2_000_000_000_000_000_000);
        let _ = svc.commit(req("acct", "main", None)).await.unwrap();

        // Touch a (mtime change) — partial commit on a.md alone.
        vfs.put("a.md", b"a", 3_000_000_000_000_000_000);
        let _ = svc
            .commit(req("acct", "main", Some(vec!["a.md".into()])))
            .await
            .unwrap();

        let saved = idx.load("acct", "main").await.unwrap().unwrap();
        let a = saved
            .entries
            .get("a.md")
            .expect("a.md must be in the index");
        let b = saved.entries.get("b.md").expect(
            "b.md was uncovered by paths=[a.md] but must be preserved \
             from the previous index",
        );
        assert_eq!(a.mtime_ns, 3_000_000_000_000_000_000);
        assert_eq!(b.mtime_ns, 2_000_000_000_000_000_000);
    }

    #[tokio::test]
    async fn no_index_store_disables_fast_path() {
        // Sanity: with index_store=None the slow path runs every commit;
        // a noop second commit still reads every file.
        let (_dir, vfs, svc) = make_service_no_index("acct");
        vfs.put("a.md", b"hi", 1_000_000_000_000_000_000);
        let _ = svc.commit(req("acct", "main", None)).await.unwrap();
        let reads_after_first = vfs.reads();

        let _ = svc.commit(req("acct", "main", None)).await.unwrap();
        assert_eq!(
            vfs.reads(),
            reads_after_first + 1,
            "without an IndexStore the slow path runs every commit",
        );
    }

    /// Partial commit with a directory entry must purge `new_index_entries`
    /// by prefix — otherwise a file deleted under that directory leaves a
    /// stale row in the persisted commit index, which the *next* commit
    /// might serve to fast-path 1 as a valid cached oid.
    ///
    /// Uses `LocalFileSystem` because the Directory branch of Step 2.5
    /// only runs when `stat` returns `is_dir = true`. `CountingVfs::stat`
    /// returns NotFound for any directory and so would route this test
    /// through the NotFound branch — which has different prefix-cleanup
    /// behavior (NotFound also clears the exact key).
    #[tokio::test]
    async fn partial_commit_with_directory_path_purges_index_by_prefix() {
        use crate::git::object_store::ObjectStore;
        use crate::git::ref_store::RefStore;
        use crate::plugins::localfs::LocalFileSystem;

        let store_dir = tempfile::tempdir().unwrap();
        let object_store = Arc::new(LocalObjectStore::new(store_dir.path()));
        let ref_store = Arc::new(LocalRefStore::new(store_dir.path()));
        let index_store = Arc::new(LocalIndexStore::new(store_dir.path()));

        let work_dir = tempfile::tempdir().unwrap();
        let acct_root = work_dir.path().join("local").join("acct");
        std::fs::create_dir_all(acct_root.join("docs")).unwrap();
        std::fs::create_dir_all(acct_root.join("other")).unwrap();
        std::fs::write(acct_root.join("docs/a.md"), b"AA").unwrap();
        std::fs::write(acct_root.join("docs/b.md"), b"BB").unwrap();
        std::fs::write(acct_root.join("other/c.md"), b"CC").unwrap();

        let vfs: Arc<dyn FileSystem> =
            Arc::new(LocalFileSystem::new(work_dir.path().to_str().unwrap()).unwrap());
        let svc = GitService::with_index(
            vfs,
            object_store as Arc<dyn ObjectStore>,
            ref_store as Arc<dyn RefStore>,
            Some(index_store.clone() as Arc<dyn IndexStore>),
        );

        let full = CommitRequest {
            account: "acct".into(),
            branch: "main".into(),
            message: "m".into(),
            paths: None,
            author_name: "tester".into(),
            author_email: "t@x".into(),
        };
        let _ = svc.commit(full).await.unwrap();
        let loaded = index_store.load("acct", "main").await.unwrap().unwrap();
        assert!(loaded.entries.contains_key("docs/a.md"));
        assert!(loaded.entries.contains_key("docs/b.md"));
        assert!(loaded.entries.contains_key("other/c.md"));

        // Delete docs/b.md, partial-commit with paths=["docs"].
        std::fs::remove_file(acct_root.join("docs/b.md")).unwrap();
        let partial = CommitRequest {
            account: "acct".into(),
            branch: "main".into(),
            message: "m".into(),
            paths: Some(vec!["docs".into()]),
            author_name: "tester".into(),
            author_email: "t@x".into(),
        };
        let _ = svc.commit(partial).await.unwrap();

        let loaded = index_store.load("acct", "main").await.unwrap().unwrap();
        assert!(
            !loaded.entries.contains_key("docs/b.md"),
            "stale entry for deleted docs/b.md must not survive prefix cleanup"
        );
        assert!(
            loaded.entries.contains_key("docs/a.md"),
            "surviving file under docs/ must have a fresh entry"
        );
        assert!(
            loaded.entries.contains_key("other/c.md"),
            "files outside the partial scope must be preserved verbatim"
        );
    }

    #[tokio::test]
    async fn commit_directory_path_sets_fs_context_for_mountable_multiwrite() {
        use crate::core::builder::{build_default_stack, RagfsConfig};
        use crate::core::{BackendItemConfig, BackendsConfig, ConfigValue, PluginConfig};

        let store_dir = tempfile::tempdir().unwrap();
        let primary_dir = tempfile::tempdir().unwrap();
        let backup_dir = tempfile::tempdir().unwrap();

        std::fs::create_dir_all(primary_dir.path().join("acct").join("docs")).unwrap();
        std::fs::write(
            primary_dir.path().join("acct").join("docs").join("a.md"),
            b"AA",
        )
        .unwrap();

        let mut ragfs_config = RagfsConfig::default();
        ragfs_config.pathlock.provider = "memory".to_string();
        let stack = build_default_stack(ragfs_config).await.unwrap();
        let mut params = HashMap::new();
        params.insert(
            "local_dir".to_string(),
            ConfigValue::String(primary_dir.path().to_str().unwrap().to_string()),
        );
        stack
            .mountable
            .mount(PluginConfig {
                name: "localfs".to_string(),
                mount_path: "/local".to_string(),
                params,
                backups: Some(BackendsConfig {
                    service_name: "mw-test".to_string(),
                    initial_partitions: 16,
                    checkpoint_interval_secs: 86_400,
                    reconcile_existing_data: false,
                    provider: "filesystem".to_string(),
                    items: vec![BackendItemConfig {
                        name: "backup1".to_string(),
                        backend: "localfs".to_string(),
                        params: serde_json::json!({
                            "local_dir": backup_dir.path().to_str().unwrap(),
                        }),
                        encryption: None,
                    }],
                }),
                ..PluginConfig::default()
            })
            .await
            .unwrap();

        let object_store = Arc::new(LocalObjectStore::new(store_dir.path()));
        let ref_store = Arc::new(LocalRefStore::new(store_dir.path()));
        let svc = GitService::new(stack.top.clone(), object_store, ref_store);

        let resp = svc
            .commit(req("acct", "main", Some(vec!["docs".into()])))
            .await
            .expect("commit should provide FS_CTX to mountable multi-write VFS");
        assert!(
            matches!(resp, CommitResponse::Created { changed: 1, .. }),
            "directory commit should succeed through mountable multi-write stack"
        );
    }
