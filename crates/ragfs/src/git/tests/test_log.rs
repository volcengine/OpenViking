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
