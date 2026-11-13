    // ── 9: log path filtering ─────────────────────────────────────────
    #[test]
    fn test_normalize_log_paths_rejects_more_than_32_unique_paths() {
        let paths = (0..=MAX_LOG_FILTER_PATHS)
            .map(|index| format!("resources/docs/{index}.md"))
            .collect();

        let err = normalize_log_paths(Some(paths)).unwrap_err();
        assert!(matches!(
            err,
            GitError::TooManyLogPaths {
                count: 33,
                limit: MAX_LOG_FILTER_PATHS,
            }
        ));
    }

    #[test]
    fn test_normalize_log_paths_deduplicates_before_counting() {
        let paths = vec!["resources/docs/a.md".to_string(); MAX_LOG_FILTER_PATHS + 1];

        let normalized = normalize_log_paths(Some(paths)).unwrap().unwrap();
        assert_eq!(normalized, vec!["resources/docs/a.md"]);
    }

    #[test]
    fn test_normalize_log_paths_enforces_64_component_depth() {
        let accepted = std::iter::repeat_n("segment", MAX_LOG_PATH_DEPTH)
            .collect::<Vec<_>>()
            .join("/");
        let rejected = std::iter::repeat_n("segment", MAX_LOG_PATH_DEPTH + 1)
            .collect::<Vec<_>>()
            .join("/");

        assert_eq!(
            normalize_log_paths(Some(vec![accepted.clone()]))
                .unwrap()
                .unwrap(),
            vec![accepted]
        );

        let err = normalize_log_paths(Some(vec![rejected.clone()])).unwrap_err();
        assert!(matches!(
            err,
            GitError::LogPathTooDeep {
                path,
                depth: 65,
                limit: MAX_LOG_PATH_DEPTH,
            } if path == rejected
        ));
    }

    #[test]
    fn test_normalize_log_paths_preserves_unfiltered_semantics() {
        assert!(normalize_log_paths(None).unwrap().is_none());
        assert!(normalize_log_paths(Some(Vec::new())).unwrap().is_none());
    }

    #[tokio::test]
    async fn test_log_with_paths_returns_matching_commits_up_to_limit() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/target.md", b"v1");
        vfs.put("resources/other.md", b"other-v1");
        let c1 = make_commit(&svc, "acct", "main", "target root").await;

        vfs.put("resources/other.md", b"other-v2");
        let _c2 = make_commit(&svc, "acct", "main", "unrelated").await;

        vfs.put("resources/target.md", b"v2");
        let c3 = make_commit(&svc, "acct", "main", "target edit").await;

        vfs.put("resources/docs/readme.md", b"docs");
        let c4 = make_commit(&svc, "acct", "main", "docs edit").await;

        let log = svc
            .log(crate::git::types::LogRequest {
                account: "acct".to_string(),
                branch: "main".to_string(),
                limit: 2,
                paths: Some(vec![
                    "resources/target.md".to_string(),
                    "resources/docs".to_string(),
                ]),
            })
            .await
            .unwrap();

        let oids: Vec<ObjectId> = log.into_iter().map(|entry| entry.oid).collect();
        assert_eq!(oids, vec![c4, c3], "limit counts matching commits only");

        let log = svc
            .log(crate::git::types::LogRequest {
                account: "acct".to_string(),
                branch: "main".to_string(),
                limit: 10,
                paths: Some(vec!["resources/target.md".to_string()]),
            })
            .await
            .unwrap();
        let oids: Vec<ObjectId> = log.into_iter().map(|entry| entry.oid).collect();
        assert_eq!(oids, vec![c3, c1]);
    }

    #[tokio::test]
    async fn test_log_with_empty_paths_is_unfiltered() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/first.md", b"v1");
        let c1 = make_commit(&svc, "acct", "main", "first").await;
        vfs.put("resources/second.md", b"v2");
        let c2 = make_commit(&svc, "acct", "main", "second").await;

        let log = svc
            .log(crate::git::types::LogRequest {
                account: "acct".to_string(),
                branch: "main".to_string(),
                limit: 10,
                paths: Some(Vec::new()),
            })
            .await
            .unwrap();

        let oids: Vec<ObjectId> = log.into_iter().map(|entry| entry.oid).collect();
        assert_eq!(oids, vec![c2, c1]);
    }

    #[tokio::test]
    async fn test_filtered_log_errors_when_uninspected_history_remains() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct_log_budget");
        vfs.put("resources/target.md", b"target");
        make_commit(&svc, "acct_log_budget", "main", "target root").await;
        vfs.put("resources/other.md", b"v1");
        make_commit(&svc, "acct_log_budget", "main", "unrelated one").await;
        vfs.put("resources/other.md", b"v2");
        make_commit(&svc, "acct_log_budget", "main", "unrelated two").await;

        let err = svc
            .log_with_scan_limit(
                LogRequest {
                    account: "acct_log_budget".into(),
                    branch: "main".into(),
                    limit: 1,
                    paths: Some(vec!["resources/target.md".into()]),
                },
                2,
            )
            .await
            .unwrap_err();

        assert!(matches!(
            err,
            GitError::LogScanLimitExceeded {
                scanned: 2,
                max_scanned: 2,
                matched: 0,
                requested: 1,
            }
        ));
    }

    #[tokio::test]
    async fn test_filtered_log_accepts_match_on_scan_boundary() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct_log_match");
        vfs.put("resources/seed.md", b"seed");
        make_commit(&svc, "acct_log_match", "main", "seed").await;
        vfs.put("resources/target.md", b"target");
        let target_commit = make_commit(&svc, "acct_log_match", "main", "target").await;
        vfs.put("resources/other.md", b"other");
        make_commit(&svc, "acct_log_match", "main", "unrelated").await;

        let log = svc
            .log_with_scan_limit(
                LogRequest {
                    account: "acct_log_match".into(),
                    branch: "main".into(),
                    limit: 1,
                    paths: Some(vec!["resources/target.md".into()]),
                },
                2,
            )
            .await
            .unwrap();

        assert_eq!(log.len(), 1);
        assert_eq!(log[0].oid, target_commit);
    }

    #[tokio::test]
    async fn test_filtered_log_returns_complete_empty_history_at_scan_boundary() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct_log_complete");
        vfs.put("resources/other.md", b"v1");
        make_commit(&svc, "acct_log_complete", "main", "one").await;
        vfs.put("resources/other.md", b"v2");
        make_commit(&svc, "acct_log_complete", "main", "two").await;

        let log = svc
            .log_with_scan_limit(
                LogRequest {
                    account: "acct_log_complete".into(),
                    branch: "main".into(),
                    limit: 1,
                    paths: Some(vec!["resources/never-created.md".into()]),
                },
                2,
            )
            .await
            .unwrap();

        assert!(log.is_empty());
    }

    #[tokio::test]
    async fn test_filtered_log_reads_each_commit_object_at_most_once() {
        let dir = tempfile::tempdir().unwrap();
        let object_store = Arc::new(GetSpyObjectStore {
            inner: LocalObjectStore::new(dir.path()),
            gets: std::sync::Mutex::new(Vec::new()),
        });
        let ref_store = Arc::new(LocalRefStore::new(dir.path()));
        let vfs = MockVfs::new("acct_log_reads");
        let svc = GitService::new(
            vfs.clone() as Arc<dyn FileSystem>,
            object_store.clone() as Arc<dyn ObjectStore>,
            ref_store as Arc<dyn RefStore>,
        );

        let mut commits = Vec::new();
        for version in 1..=4 {
            let content = format!("v{version}");
            vfs.put("resources/other.md", content.as_bytes());
            commits.push(
                make_commit(&svc, "acct_log_reads", "main", &format!("commit {version}")).await,
            );
        }
        object_store.reset();

        let err = svc
            .log_with_scan_limit(
                LogRequest {
                    account: "acct_log_reads".into(),
                    branch: "main".into(),
                    limit: 1,
                    paths: Some(vec!["resources/never-created.md".into()]),
                },
                3,
            )
            .await
            .unwrap_err();
        assert!(matches!(
            err,
            GitError::LogScanLimitExceeded { scanned: 3, .. }
        ));

        let commit_gets: usize = commits.iter().map(|oid| object_store.count_gets(oid)).sum();
        assert_eq!(commit_gets, 4, "three candidates plus one parent lookahead");
        for oid in commits {
            assert_eq!(object_store.count_gets(&oid), 1, "commit {oid} was re-read");
        }
    }

    #[tokio::test]
    async fn history_legacy_backfill_and_corruption_preserve_deleted_versions() {
        let dir = tempfile::tempdir().unwrap();
        let legacy_store = Arc::new(HistorySpy::new(dir.path(), false));
        let refs = Arc::new(LocalRefStore::new(dir.path()));
        let vfs = MockVfs::new("legacy");
        let legacy = GitService::new(vfs.clone(), legacy_store, refs.clone());
        vfs.put("docs/file", b"original");
        let first = make_commit(&legacy, "legacy", "main", "first").await;
        for i in 0..39 {
            vfs.put("other/file", format!("{i}").as_bytes());
            make_commit(&legacy, "legacy", "main", "other").await;
        }
        vfs.delete("docs/file");
        let deleted = match legacy
            .commit(req(
                "legacy",
                "main",
                "delete",
                Some(vec!["docs/file".into()]),
            ))
            .await
            .unwrap()
        {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("unexpected {other:?}"),
        };
        let store = Arc::new(HistorySpy::new(dir.path(), true));
        let query = || LogRequest {
            account: "legacy".into(),
            branch: "main".into(),
            limit: 20,
            paths: Some(vec!["docs/file".into()]),
        };
        // Repeated cold readers finish lazy backfill, even when one request's
        // filter work budget cannot cover the entire legacy chain.
        for _ in 0..4 {
            let reader = GitService::new(vfs.clone(), store.clone(), refs.clone());
            assert_eq!(
                reader
                    .log(query())
                    .await
                    .unwrap()
                    .iter()
                    .map(|c| c.oid)
                    .collect::<Vec<_>>(),
                vec![deleted, first]
            );
        }
        store.reset();
        let cold = GitService::new(vfs.clone(), store.clone(), refs.clone());
        assert_eq!(cold.log(query()).await.unwrap().len(), 2);
        assert!(
            store.counts().0 < 20,
            "legacy acceleration: {:?}",
            store.counts()
        );
        // Break a persisted segment. Fresh readers must recover from objects,
        // never interpret a damaged negative Bloom bit as an absent version.
        tokio::fs::write(
            dir.path()
                .join("legacy/info/history-v1")
                .join(deleted.to_string()),
            b"broken",
        )
        .await
        .unwrap();
        let cold = GitService::new(vfs.clone(), store.clone(), refs.clone());
        assert_eq!(
            cold.log(query())
                .await
                .unwrap()
                .iter()
                .map(|c| c.oid)
                .collect::<Vec<_>>(),
            vec![deleted, first]
        );
        // Restore uses its own commit path and must also publish a graph record.
        let restored = match cold
            .restore(RestoreRequest {
                account: "legacy".into(),
                branch: "main".into(),
                project_dir: Some("docs".into()),
                source_commit: first.to_string(),
                dry_run: false,
                message: None,
                author_name: "test".into(),
                author_email: "test@example.com".into(),
            })
            .await
            .unwrap()
        {
            RestoreResponse::Applied { new_commit_oid, .. } => new_commit_oid,
            other => panic!("unexpected restore {other:?}"),
        };
        let cold = GitService::new(vfs, store, refs);
        assert_eq!(
            cold.log(query())
                .await
                .unwrap()
                .iter()
                .map(|c| c.oid)
                .collect::<Vec<_>>(),
            vec![restored, deleted, first]
        );
    }

    #[tokio::test]
    async fn history_anchor_recovers_unindexed_prefix_without_losing_segments() {
        let dir = tempfile::tempdir().unwrap();
        let store = Arc::new(HistorySpy::new(dir.path(), true));
        let refs = Arc::new(LocalRefStore::new(dir.path()));
        let vfs = MockVfs::new("anchor");
        let indexed = GitService::new(vfs.clone(), store.clone(), refs.clone());
        for i in 0..260 {
            vfs.put("other/file", format!("{i}").as_bytes());
            make_commit(&indexed, "anchor", "main", "indexed").await;
        }
        // Simulate arbitrary-length missing maintenance (old writer or failed
        // post-CAS writes), not just a single failed HEAD update.
        let legacy = GitService::new(
            vfs.clone(),
            Arc::new(HistorySpy::new(dir.path(), false)),
            refs.clone(),
        );
        for i in 0..12 {
            vfs.put("other/file", format!("tail{i}").as_bytes());
            make_commit(&legacy, "anchor", "main", "unindexed").await;
        }
        store.reset();
        let reader = GitService::new(vfs, store.clone(), refs);
        assert!(reader
            .log(LogRequest {
                account: "anchor".into(),
                branch: "main".into(),
                limit: 20,
                paths: Some(vec!["absent".into()])
            })
            .await
            .unwrap()
            .is_empty());
        assert!(
            store.counts().0 < 120,
            "should only read the 12 unindexed commits, not all 272: {:?}",
            store.counts()
        );
        assert!(
            store.counts().1 <= 5,
            "should probe/load by segment, not by commit: {:?}",
            store.counts()
        );
    }

    #[tokio::test]
    async fn history_short_queries_keep_the_persisted_segment_tail() {
        let dir = tempfile::tempdir().unwrap();
        let store = Arc::new(HistorySpy::new(dir.path(), true));
        let refs = Arc::new(LocalRefStore::new(dir.path()));
        let vfs = MockVfs::new("short");
        let writer = GitService::new(vfs.clone(), store.clone(), refs.clone());
        let mut head = ObjectId::null(gix_hash::Kind::Sha1);
        for i in 0..10 {
            vfs.put("file", format!("{i}").as_bytes());
            head = make_commit(&writer, "short", "main", "change").await;
        }
        let bytes = store
            .get_history_segment("short", &head)
            .await
            .unwrap()
            .unwrap();
        let mut segment = crate::git::history::Segment::decode(&bytes, head).unwrap();
        for record in &mut segment.records {
            record.bloom = None;
        }
        tokio::fs::write(
            dir.path()
                .join("short/info/history-v1")
                .join(head.to_string()),
            segment.encode().unwrap(),
        )
        .await
        .unwrap();
        let a = GitService::new(vfs.clone(), store.clone(), refs.clone());
        let b = GitService::new(vfs, store.clone(), refs);
        let query = |limit| LogRequest {
            account: "short".into(),
            branch: "main".into(),
            limit,
            paths: Some(vec!["file".into()]),
        };
        let (one, four) = tokio::join!(a.log(query(1)), b.log(query(4)));
        assert_eq!(one.unwrap().len(), 1);
        assert_eq!(four.unwrap().len(), 4);
        let bytes = store
            .get_history_segment("short", &head)
            .await
            .unwrap()
            .unwrap();
        assert_eq!(
            crate::git::history::Segment::decode(&bytes, head)
                .unwrap()
                .records
                .len(),
            10
        );
    }

    #[tokio::test]
    async fn history_storage_failures_do_not_fail_commit_or_hide_versions() {
        let dir = tempfile::tempdir().unwrap();
        let store = Arc::new(HistorySpy::new(dir.path(), true));
        store
            .fail_history
            .store(true, std::sync::atomic::Ordering::Relaxed);
        let refs = Arc::new(LocalRefStore::new(dir.path()));
        let vfs = MockVfs::new("fail");
        let writer = GitService::new(vfs.clone(), store.clone(), refs.clone());
        vfs.put("file", b"first");
        let first = make_commit(&writer, "fail", "main", "first").await;
        vfs.put("file", b"second");
        let second = make_commit(&writer, "fail", "main", "second").await;
        let reader = GitService::new(vfs, store, refs);
        let result = reader
            .log(LogRequest {
                account: "fail".into(),
                branch: "main".into(),
                limit: 20,
                paths: Some(vec!["file".into()]),
            })
            .await
            .unwrap();
        assert_eq!(
            result.iter().map(|c| c.oid).collect::<Vec<_>>(),
            vec![second, first]
        );
    }

    struct HistorySpy {
        inner: LocalObjectStore,
        enabled: bool,
        fail_history: std::sync::atomic::AtomicBool,
        gets: std::sync::atomic::AtomicUsize,
        segments: std::sync::atomic::AtomicUsize,
    }
    #[async_trait]
    impl ObjectStore for HistorySpy {
        fn supports_history_segments(&self) -> bool {
            self.enabled
        }
        async fn get_history_segment(
            &self,
            account: &str,
            tip: &ObjectId,
        ) -> std::result::Result<Option<bytes::Bytes>, ObjectStoreError> {
            self.segments
                .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            if self.fail_history.load(std::sync::atomic::Ordering::Relaxed) {
                return Err(ObjectStoreError::Backend("history unavailable".into()));
            }
            self.inner.get_history_segment(account, tip).await
        }
        async fn put_history_segment(
            &self,
            account: &str,
            tip: &ObjectId,
            data: bytes::Bytes,
        ) -> std::result::Result<(), ObjectStoreError> {
            if self.fail_history.load(std::sync::atomic::Ordering::Relaxed) {
                return Err(ObjectStoreError::Backend("history unavailable".into()));
            }
            self.inner.put_history_segment(account, tip, data).await
        }
        async fn get_history_anchor(
            &self,
            account: &str,
            branch: &str,
        ) -> std::result::Result<Option<ObjectId>, ObjectStoreError> {
            self.inner.get_history_anchor(account, branch).await
        }
        async fn put_history_anchor(
            &self,
            account: &str,
            branch: &str,
            tip: ObjectId,
        ) -> std::result::Result<(), ObjectStoreError> {
            self.inner.put_history_anchor(account, branch, tip).await
        }
        async fn put(
            &self,
            account: &str,
            oid: &ObjectId,
            data: bytes::Bytes,
        ) -> std::result::Result<(), ObjectStoreError> {
            self.inner.put(account, oid, data).await
        }
        async fn get(
            &self,
            account: &str,
            oid: &ObjectId,
        ) -> std::result::Result<bytes::Bytes, ObjectStoreError> {
            self.gets.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            self.inner.get(account, oid).await
        }
        async fn exists(
            &self,
            account: &str,
            oid: &ObjectId,
        ) -> std::result::Result<bool, ObjectStoreError> {
            self.inner.exists(account, oid).await
        }
    }
    impl HistorySpy {
        fn new(path: &std::path::Path, enabled: bool) -> Self {
            Self {
                inner: LocalObjectStore::new(path),
                enabled,
                fail_history: false.into(),
                gets: 0.into(),
                segments: 0.into(),
            }
        }
        fn counts(&self) -> (usize, usize) {
            use std::sync::atomic::Ordering::Relaxed;
            (self.gets.load(Relaxed), self.segments.load(Relaxed))
        }
        fn reset(&self) {
            use std::sync::atomic::Ordering::Relaxed;
            self.gets.store(0, Relaxed);
            self.segments.store(0, Relaxed);
        }
    }

    #[tokio::test]
    async fn history_segments_reduce_cold_remote_reads_and_preserve_results() {
        let dir = tempfile::tempdir().unwrap();
        let store = Arc::new(HistorySpy::new(dir.path(), true));
        let refs = Arc::new(LocalRefStore::new(dir.path()));
        let vfs = MockVfs::new("history");
        let svc = GitService::new(vfs.clone(), store.clone(), refs.clone());
        vfs.put("alice/deep/file.md", b"alice");
        let first = make_commit(&svc, "history", "main", "alice").await;
        for i in 0..299 {
            vfs.put("bob/file.md", format!("{i}").as_bytes());
            make_commit(&svc, "history", "main", "bob").await;
        }
        let request = |path: &str| LogRequest {
            account: "history".into(),
            branch: "main".into(),
            limit: 20,
            paths: Some(vec![path.into()]),
        };
        // A fresh service must benefit from persisted segments, not writer memory.
        let cold = GitService::new(vfs.clone(), store.clone(), refs.clone());
        store.reset();
        let found = cold.log(request("alice/deep/file.md")).await.unwrap();
        assert_eq!(found.iter().map(|c| c.oid).collect::<Vec<_>>(), vec![first]);
        let cold_counts = store.counts();
        assert!(cold_counts.0 < 30, "cold object reads: {cold_counts:?}");
        assert!(cold_counts.1 <= 4, "cold segment reads: {cold_counts:?}");
        store.reset();
        assert!(cold
            .log(request("never/existed.md"))
            .await
            .unwrap()
            .is_empty());
        let warm_counts = store.counts();
        assert!(warm_counts.0 < 30, "absent object reads: {warm_counts:?}");
        assert_eq!(warm_counts.1, 0);
        let exact_store = Arc::new(HistorySpy::new(dir.path(), false));
        let exact = GitService::new(vfs, exact_store.clone(), refs);
        let result = exact.log(request("alice/deep/file.md")).await.unwrap();
        assert_eq!(
            result.iter().map(|c| c.oid).collect::<Vec<_>>(),
            vec![first]
        );
        assert!(exact_store.counts().0 >= 300);
        eprintln!("300 commits: cold indexed {cold_counts:?}, warm absent {warm_counts:?}, exact {:?} (object GETs, segment GETs)", exact_store.counts());
    }
