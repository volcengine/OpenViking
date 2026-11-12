    #[tokio::test]
    async fn test_mock_vfs_write_then_read_round_trip() {
        let vfs = MockVfs::new("acct");
        let path = "/local/acct/x.md";
        vfs.files
            .lock()
            .unwrap()
            .insert(path.to_string(), Vec::new());
        FileSystem::write(vfs.as_ref(), path, b"hello", 0, WriteFlag::Create)
            .await
            .unwrap();
        let got = FileSystem::read(vfs.as_ref(), path, 0, 0).await.unwrap();
        assert_eq!(got, b"hello");
        FileSystem::remove(vfs.as_ref(), path).await.unwrap();
        let err = FileSystem::read(vfs.as_ref(), path, 0, 0)
            .await
            .unwrap_err();
        assert!(matches!(err, Error::NotFound(_)));
    }

    // ── restore: dry_run ───────────────────────────────────────────────
    #[tokio::test]
    async fn test_restore_dry_run_reports_diff_and_writes_nothing() {
        let (_dir, vfs, object_store, ref_store, svc) = make_service("acct");
        // Source state: resources/proj_a has files a.md, b.md
        vfs.put("resources/proj_a/a.md", b"A v1");
        vfs.put("resources/proj_a/b.md", b"B v1");
        let source_oid = make_commit(&svc, "acct", "main", "source").await;

        // HEAD state: a.md is rewritten, b.md is deleted, c.md is created.
        // We pass explicit paths (including the deleted b.md) so commit()
        // sees the tombstone — collect_all() only enumerates surviving files.
        vfs.put("resources/proj_a/a.md", b"A v2");
        vfs.delete("resources/proj_a/b.md");
        vfs.put("resources/proj_a/c.md", b"C new");
        let head_oid = match svc
            .commit(req(
                "acct",
                "main",
                "head",
                Some(vec![
                    "resources/proj_a/a.md".to_string(),
                    "resources/proj_a/b.md".to_string(),
                    "resources/proj_a/c.md".to_string(),
                ]),
            ))
            .await
            .unwrap()
        {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        };

        let resp = svc
            .restore(RestoreRequest {
                account: "acct".into(),
                branch: "main".into(),
                project_dir: Some("resources/proj_a".into()),
                source_commit: source_oid.to_hex().to_string(),
                allow_missing_source: false,
                dry_run: true,
                message: None,
                author_name: "tester".into(),
                author_email: "tester@example.com".into(),
            })
            .await
            .unwrap();

        match resp {
            RestoreResponse::DryRun { diff, head, source } => {
                assert_eq!(source, source_oid);
                assert_eq!(head, head_oid);
                // a.md needs to roll back to v1, b.md needs to come back,
                // c.md needs to go away. Sorted alphabetically by path.
                assert_eq!(diff.to_write.len(), 2);
                assert_eq!(diff.to_write[0].0, "a.md");
                assert_eq!(diff.to_write[1].0, "b.md");
                assert_eq!(diff.to_delete, vec!["c.md".to_string()]);
                assert!(diff.unchanged.is_empty());
            }
            other => panic!("expected DryRun, got {other:?}"),
        }

        // CRITICAL: dry_run wrote nothing through the VFS — c.md and the v2
        // version of a.md must still be visible on disk.
        let files = vfs.files.lock().unwrap();
        assert_eq!(
            files.get("/local/acct/resources/proj_a/a.md").unwrap(),
            b"A v2",
            "dry_run must not overwrite a.md",
        );
        assert!(
            files.contains_key("/local/acct/resources/proj_a/c.md"),
            "dry_run must not delete c.md",
        );
        // Branch ref must still point at head_oid.
        let head_after = ref_store.read("acct", "refs/heads/main").await.unwrap();
        assert_eq!(head_after, head_oid);
        let _ = object_store; // silence unused warning
    }

    // ── restore: apply ─────────────────────────────────────────────────
    #[tokio::test]
    async fn test_restore_apply_writes_new_commit_with_head_as_parent() {
        let (_dir, vfs, object_store, ref_store, svc) = make_service("acct");
        vfs.put("resources/proj_a/a.md", b"A v1");
        vfs.put("resources/proj_a/b.md", b"B v1");
        let source_oid = make_commit(&svc, "acct", "main", "source").await;

        vfs.put("resources/proj_a/a.md", b"A v2");
        vfs.delete("resources/proj_a/b.md");
        vfs.put("resources/proj_a/c.md", b"C new");
        // IMPORTANT: use explicit paths so the deletion of b.md is captured
        let head_oid = match svc
            .commit(req(
                "acct",
                "main",
                "head",
                Some(vec![
                    "resources/proj_a/a.md".to_string(),
                    "resources/proj_a/b.md".to_string(),
                    "resources/proj_a/c.md".to_string(),
                ]),
            ))
            .await
            .unwrap()
        {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        };

        let resp = svc
            .restore(RestoreRequest {
                account: "acct".into(),
                branch: "main".into(),
                project_dir: Some("resources/proj_a".into()),
                source_commit: source_oid.to_hex().to_string(),
                allow_missing_source: false,
                dry_run: false,
                message: Some("rewind proj_a".into()),
                author_name: "tester".into(),
                author_email: "tester@example.com".into(),
            })
            .await
            .unwrap();

        let new_oid = match resp {
            RestoreResponse::Applied {
                new_commit_oid,
                source_commit,
                parent_commit,
                written,
                deleted,
                unchanged,
                written_paths,
                deleted_paths,
            } => {
                assert_eq!(source_commit, source_oid);
                assert_eq!(parent_commit, head_oid, "parent MUST be HEAD, NOT source");
                assert_eq!(written, 2, "a.md (rewrite) + b.md (recreate) = 2");
                assert_eq!(deleted, 1, "c.md");
                assert_eq!(unchanged, 0);
                assert_eq!(written_paths.len(), 2);
                assert_eq!(deleted_paths.len(), 1);
                // Paths should be account-relative (project_dir-prefixed).
                for p in &written_paths {
                    assert!(
                        p.starts_with("resources/proj_a/"),
                        "written path missing project_dir prefix: {p}"
                    );
                }
                for p in &deleted_paths {
                    assert!(
                        p.starts_with("resources/proj_a/"),
                        "deleted path missing project_dir prefix: {p}"
                    );
                }
                new_commit_oid
            }
            other => panic!("expected Applied, got {other:?}"),
        };

        // Ref now points at new_oid.
        assert_eq!(
            ref_store.read("acct", "refs/heads/main").await.unwrap(),
            new_oid
        );
        // New commit's parents = [head_oid] (NOT source_oid — this is the key
        // invariant of restore vs. plain checkout).
        let parents =
            commit_parents(object_store.as_ref() as &dyn ObjectStore, "acct", new_oid).await;
        assert_eq!(parents, vec![head_oid]);

        // VFS rolled back as expected.
        let files = vfs.files.lock().unwrap();
        assert_eq!(
            files.get("/local/acct/resources/proj_a/a.md").unwrap(),
            b"A v1",
            "a.md rolled back",
        );
        assert_eq!(
            files.get("/local/acct/resources/proj_a/b.md").unwrap(),
            b"B v1",
            "b.md restored",
        );
        assert!(
            !files.contains_key("/local/acct/resources/proj_a/c.md"),
            "c.md deleted",
        );
    }

    // Partial-writeback regression suite. Before this fix, a single failed
    // `FileSystem::write` (or non-NotFound `remove`) during step 10 would let
    // the ref keep pointing at the new commit while `try_collect` short-
    // circuited the rest of the writeback. The caller then saw a generic
    // RuntimeError and never scheduled reindex, leaving HEAD and the working
    // tree (and any vector index) inconsistent. These tests pin the new
    // behavior: every per-path op runs to completion and partial failures
    // surface as a structured `GitError::RestoreWritebackPartial`.

    /// A forced write failure for one path must produce
    /// `GitError::RestoreWritebackPartial` whose payload still reports the
    /// other writes/deletes as succeeded so the caller can reindex them.
    #[tokio::test]
    async fn test_restore_writeback_partial_returns_partial_error_on_write_failure() {
        let (_dir, vfs, _object_store, ref_store, svc) = make_service("acct");
        // Source: two files at v1.
        vfs.put("resources/proj_a/a.md", b"A v1");
        vfs.put("resources/proj_a/b.md", b"B v1");
        let source_oid = make_commit(&svc, "acct", "main", "source").await;

        // HEAD diverges: a.md updated, b.md updated, c.md added.
        vfs.put("resources/proj_a/a.md", b"A v2");
        vfs.put("resources/proj_a/b.md", b"B v2");
        vfs.put("resources/proj_a/c.md", b"C new");
        let _head_oid = match svc
            .commit(req(
                "acct",
                "main",
                "head",
                Some(vec![
                    "resources/proj_a/a.md".to_string(),
                    "resources/proj_a/b.md".to_string(),
                    "resources/proj_a/c.md".to_string(),
                ]),
            ))
            .await
            .unwrap()
        {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        };

        // Force restore's writeback of a.md to fail. The diff also rewrites
        // b.md (success) and deletes c.md (success), so we expect a partial
        // error with exactly one failed write and the other operations
        // reported under the success buckets.
        vfs.fail_write("resources/proj_a/a.md");

        let err = svc
            .restore(RestoreRequest {
                account: "acct".into(),
                branch: "main".into(),
                project_dir: Some("resources/proj_a".into()),
                source_commit: source_oid.to_hex().to_string(),
                allow_missing_source: false,
                dry_run: false,
                message: Some("rewind partial".into()),
                author_name: "tester".into(),
                author_email: "tester@example.com".into(),
            })
            .await
            .expect_err("restore must surface partial failure");

        let partial = match err {
            GitError::RestoreWritebackPartial(p) => p,
            other => panic!("expected RestoreWritebackPartial, got {other:?}"),
        };

        // Ref already advanced — partial must report the new HEAD so the
        // caller knows the commit is durable even though writeback failed.
        let head_after = ref_store.read("acct", "refs/heads/main").await.unwrap();
        assert_eq!(partial.new_commit_oid, head_after);

        assert_eq!(partial.failed_writes.len(), 1, "exactly one write failed");
        assert_eq!(partial.failed_writes[0].0, "resources/proj_a/a.md");
        assert!(
            !partial.failed_writes[0].1.is_empty(),
            "failure entry must carry a message"
        );
        assert!(partial.failed_deletes.is_empty(), "no deletes should fail");

        // The other write (b.md) succeeded and so must show up under
        // written_paths; c.md was deleted and lands under deleted_paths.
        assert_eq!(partial.written_paths, vec!["resources/proj_a/b.md"]);
        assert_eq!(partial.deleted_paths, vec!["resources/proj_a/c.md"]);
        assert_eq!(partial.written, 1);
        assert_eq!(partial.deleted, 1);
    }

    /// With two forced write failures we must still collect *both* — the
    /// stream must not short-circuit after the first one. This is the
    /// behavior change relative to the old `try_collect`.
    #[tokio::test]
    async fn test_restore_writeback_partial_continues_after_failure() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/proj_a/a.md", b"A v1");
        vfs.put("resources/proj_a/b.md", b"B v1");
        vfs.put("resources/proj_a/c.md", b"C v1");
        let source_oid = make_commit(&svc, "acct", "main", "source").await;

        vfs.put("resources/proj_a/a.md", b"A v2");
        vfs.put("resources/proj_a/b.md", b"B v2");
        vfs.put("resources/proj_a/c.md", b"C v2");
        let _head_oid = match svc
            .commit(req(
                "acct",
                "main",
                "head",
                Some(vec![
                    "resources/proj_a/a.md".to_string(),
                    "resources/proj_a/b.md".to_string(),
                    "resources/proj_a/c.md".to_string(),
                ]),
            ))
            .await
            .unwrap()
        {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        };

        // Two of the three writes fail.
        vfs.fail_write("resources/proj_a/a.md");
        vfs.fail_write("resources/proj_a/c.md");

        let err = svc
            .restore(RestoreRequest {
                account: "acct".into(),
                branch: "main".into(),
                project_dir: Some("resources/proj_a".into()),
                source_commit: source_oid.to_hex().to_string(),
                allow_missing_source: false,
                dry_run: false,
                message: None,
                author_name: "tester".into(),
                author_email: "tester@example.com".into(),
            })
            .await
            .expect_err("partial expected");

        let partial = match err {
            GitError::RestoreWritebackPartial(p) => p,
            other => panic!("expected RestoreWritebackPartial, got {other:?}"),
        };
        assert_eq!(
            partial.failed_writes.len(),
            2,
            "stream must not short-circuit on the first failure"
        );
        let mut failed: Vec<String> = partial
            .failed_writes
            .iter()
            .map(|(p, _)| p.clone())
            .collect();
        failed.sort();
        assert_eq!(
            failed,
            vec![
                "resources/proj_a/a.md".to_string(),
                "resources/proj_a/c.md".to_string(),
            ]
        );
        // b.md still rolled back.
        assert_eq!(partial.written_paths, vec!["resources/proj_a/b.md"]);
    }

    /// A forced delete failure (non-NotFound) must surface in
    /// `failed_deletes` without aborting the rest of the stream.
    #[tokio::test]
    async fn test_restore_delete_failure_does_not_short_circuit() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        // Source has only a.md.
        vfs.put("resources/proj_a/a.md", b"A v1");
        let source_oid = make_commit(&svc, "acct", "main", "source").await;

        // HEAD adds b.md and c.md — restore must delete both.
        vfs.put("resources/proj_a/b.md", b"B new");
        vfs.put("resources/proj_a/c.md", b"C new");
        let _head_oid = match svc
            .commit(req(
                "acct",
                "main",
                "head",
                Some(vec![
                    "resources/proj_a/a.md".to_string(),
                    "resources/proj_a/b.md".to_string(),
                    "resources/proj_a/c.md".to_string(),
                ]),
            ))
            .await
            .unwrap()
        {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        };

        // Force b.md's delete to fail; c.md must still be deleted.
        vfs.fail_remove("resources/proj_a/b.md");

        let err = svc
            .restore(RestoreRequest {
                account: "acct".into(),
                branch: "main".into(),
                project_dir: Some("resources/proj_a".into()),
                source_commit: source_oid.to_hex().to_string(),
                allow_missing_source: false,
                dry_run: false,
                message: None,
                author_name: "tester".into(),
                author_email: "tester@example.com".into(),
            })
            .await
            .expect_err("partial expected");

        let partial = match err {
            GitError::RestoreWritebackPartial(p) => p,
            other => panic!("expected RestoreWritebackPartial, got {other:?}"),
        };
        assert_eq!(partial.failed_deletes.len(), 1);
        assert_eq!(partial.failed_deletes[0].0, "resources/proj_a/b.md");
        assert_eq!(partial.deleted_paths, vec!["resources/proj_a/c.md"]);
        assert!(partial.failed_writes.is_empty());
    }

    /// `Error::NotFound` from `remove` is idempotent (the path was already
    /// gone) and must NOT count as a failure. With strict_remove enabled
    /// and a delete target that is already missing, restore must still
    /// return `Applied`.
    #[tokio::test]
    async fn test_restore_delete_notfound_not_counted_as_failure() {
        // Use new_strict_remove so absent paths produce NotFound rather than
        // silently succeeding — that's what the real LocalFileSystem does.
        let dir = tempfile::tempdir().unwrap();
        let object_store = Arc::new(LocalObjectStore::new(dir.path()));
        let ref_store = Arc::new(LocalRefStore::new(dir.path()));
        let vfs = MockVfs::new_strict_remove("acct");
        let svc = GitService::new(
            vfs.clone() as Arc<dyn FileSystem>,
            object_store.clone() as Arc<dyn ObjectStore>,
            ref_store.clone() as Arc<dyn RefStore>,
        );

        vfs.put("resources/proj_a/a.md", b"A v1");
        let source_oid = make_commit(&svc, "acct", "main", "source").await;

        vfs.put("resources/proj_a/b.md", b"B new");
        let _head_oid = match svc
            .commit(req(
                "acct",
                "main",
                "head",
                Some(vec![
                    "resources/proj_a/a.md".to_string(),
                    "resources/proj_a/b.md".to_string(),
                ]),
            ))
            .await
            .unwrap()
        {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        };

        // Out-of-band: remove b.md from the VFS so the diff's delete plan
        // hits the NotFound path. Restore must still return Applied.
        vfs.delete("resources/proj_a/b.md");

        let resp = svc
            .restore(RestoreRequest {
                account: "acct".into(),
                branch: "main".into(),
                project_dir: Some("resources/proj_a".into()),
                source_commit: source_oid.to_hex().to_string(),
                allow_missing_source: false,
                dry_run: false,
                message: None,
                author_name: "tester".into(),
                author_email: "tester@example.com".into(),
            })
            .await
            .expect("idempotent delete must stay on the Applied path");

        match resp {
            RestoreResponse::Applied {
                deleted_paths,
                deleted,
                ..
            } => {
                assert_eq!(
                    deleted, 1,
                    "b.md counts as deleted even though already gone"
                );
                assert_eq!(deleted_paths, vec!["resources/proj_a/b.md"]);
            }
            other => panic!("expected Applied, got {other:?}"),
        }
    }

    #[tokio::test]
    async fn test_restore_full_tree_apply_replaces_account_tree() {
        let (_dir, vfs, object_store, ref_store, svc) = make_service("acct");
        vfs.put("a.md", b"A v1");
        vfs.put("b.md", b"B v1");
        let source_oid = make_commit(&svc, "acct", "main", "source").await;

        vfs.put("a.md", b"A v2");
        vfs.put("c.md", b"C new at head");
        vfs.delete("b.md");
        let head_oid = match svc
            .commit(req(
                "acct",
                "main",
                "head",
                Some(vec![
                    "a.md".to_string(),
                    "c.md".to_string(),
                    "b.md".to_string(),
                ]),
            ))
            .await
            .unwrap()
        {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        };

        let resp = svc
            .restore(RestoreRequest {
                account: "acct".into(),
                branch: "main".into(),
                project_dir: None,
                source_commit: source_oid.to_hex().to_string(),
                allow_missing_source: false,
                dry_run: false,
                message: None,
                author_name: "tester".into(),
                author_email: "tester@example.com".into(),
            })
            .await
            .unwrap();

        let new_oid = match resp {
            RestoreResponse::Applied {
                new_commit_oid,
                source_commit,
                parent_commit,
                written,
                deleted,
                unchanged,
                written_paths,
                deleted_paths,
            } => {
                assert_eq!(source_commit, source_oid);
                assert_eq!(parent_commit, head_oid);
                assert_eq!(written, 2);
                assert_eq!(deleted, 1);
                assert_eq!(unchanged, 0);
                assert_eq!(written_paths, vec!["a.md".to_string(), "b.md".to_string()]);
                assert_eq!(deleted_paths, vec!["c.md".to_string()]);
                new_commit_oid
            }
            other => panic!("expected Applied, got {other:?}"),
        };

        assert_eq!(
            ref_store.read("acct", "refs/heads/main").await.unwrap(),
            new_oid
        );
        let parents =
            commit_parents(object_store.as_ref() as &dyn ObjectStore, "acct", new_oid).await;
        assert_eq!(parents, vec![head_oid]);

        let files = vfs.files.lock().unwrap();
        assert_eq!(files.get("/local/acct/a.md").unwrap(), b"A v1");
        assert_eq!(files.get("/local/acct/b.md").unwrap(), b"B v1");
        assert!(!files.contains_key("/local/acct/c.md"));
    }

    // Regression: restoring to a revision where a whole subdirectory's files
    // are gone must not leave an empty directory husk behind. Git does not
    // track directories, so the delete diff only lists files — restore is
    // responsible for pruning directories emptied by those deletes.
    //
    // Backed by a real `LocalFileSystem`: the in-memory `MockVfs` models
    // directories implicitly (deleting the last file makes the dir vanish for
    // free) and so cannot reproduce the husk. LocalFS keeps the directory on
    // disk, exactly like production, which is what makes this test meaningful.
    #[tokio::test]
    async fn test_restore_prunes_directories_emptied_by_delete() {
        use crate::plugins::localfs::LocalFileSystem;

        let store_dir = tempfile::tempdir().unwrap();
        let object_store = Arc::new(LocalObjectStore::new(store_dir.path()));
        let ref_store = Arc::new(LocalRefStore::new(store_dir.path()));

        // Working tree root: /local/acct lives under this temp dir.
        let work_dir = tempfile::tempdir().unwrap();
        let acct_root = work_dir.path().join("local").join("acct");
        std::fs::create_dir_all(&acct_root).unwrap();
        let vfs: Arc<dyn FileSystem> =
            Arc::new(LocalFileSystem::new(work_dir.path().to_str().unwrap()).unwrap());

        let svc = GitService::new(vfs.clone(), object_store.clone(), ref_store.clone());

        // Source commit: keeper.md at the project root only.
        std::fs::create_dir_all(acct_root.join("resources/proj_a")).unwrap();
        std::fs::write(acct_root.join("resources/proj_a/keeper.md"), b"keep").unwrap();
        let source_oid = make_commit(&svc, "acct", "main", "source").await;

        // HEAD adds a nested subdir whose only files restore will delete.
        std::fs::create_dir_all(acct_root.join("resources/proj_a/nested/deep")).unwrap();
        std::fs::write(acct_root.join("resources/proj_a/nested/x.md"), b"x").unwrap();
        std::fs::write(acct_root.join("resources/proj_a/nested/deep/y.md"), b"y").unwrap();
        let _head_oid = make_commit(&svc, "acct", "main", "head").await;

        svc.restore(RestoreRequest {
            account: "acct".into(),
            branch: "main".into(),
            project_dir: Some("resources/proj_a".into()),
            source_commit: source_oid.to_hex().to_string(),
            allow_missing_source: false,
            dry_run: false,
            message: Some("rewind".into()),
            author_name: "tester".into(),
            author_email: "tester@example.com".into(),
        })
        .await
        .unwrap();

        // Files are gone, and so are the now-empty directories that held them
        // (deepest first: deep/, then nested/).
        assert!(
            !acct_root.join("resources/proj_a/nested/deep/y.md").exists(),
            "nested/deep/y.md must be deleted",
        );
        assert!(
            !acct_root.join("resources/proj_a/nested/x.md").exists(),
            "nested/x.md must be deleted",
        );
        assert!(
            !acct_root.join("resources/proj_a/nested/deep").exists(),
            "emptied directory nested/deep must be pruned",
        );
        assert!(
            !acct_root.join("resources/proj_a/nested").exists(),
            "emptied directory nested must be pruned",
        );
        // The surviving file and its (non-empty) parent are untouched.
        assert!(
            acct_root.join("resources/proj_a/keeper.md").exists(),
            "keeper.md must survive",
        );
        assert!(
            acct_root.join("resources/proj_a").is_dir(),
            "project_dir itself must remain (still holds keeper.md)",
        );
    }

    // Regression: restoring to a source whose subtree contains files under a
    // directory that HEAD removed entirely (e.g. `rm -r` recorded as a commit
    // deletion) must recreate the missing directory chain before writing the
    // blobs back. Before `ensure_parent_dirs` was added to the writeback, this
    // aborted with `vfs: not found: .../resources/proj_a/nested`.
    #[tokio::test]
    async fn test_restore_recreates_directory_removed_by_head() {
        use crate::plugins::localfs::LocalFileSystem;

        let store_dir = tempfile::tempdir().unwrap();
        let object_store = Arc::new(LocalObjectStore::new(store_dir.path()));
        let ref_store = Arc::new(LocalRefStore::new(store_dir.path()));

        let work_dir = tempfile::tempdir().unwrap();
        let acct_root = work_dir.path().join("local").join("acct");
        std::fs::create_dir_all(&acct_root).unwrap();
        let vfs: Arc<dyn FileSystem> =
            Arc::new(LocalFileSystem::new(work_dir.path().to_str().unwrap()).unwrap());

        let svc = GitService::new(vfs.clone(), object_store.clone(), ref_store.clone());

        // Source commit: a nested directory with a file plus a top-level keeper.
        std::fs::create_dir_all(acct_root.join("resources/proj_a/nested/deep")).unwrap();
        std::fs::write(acct_root.join("resources/proj_a/keeper.md"), b"keep").unwrap();
        std::fs::write(acct_root.join("resources/proj_a/nested/x.md"), b"x v1").unwrap();
        std::fs::write(acct_root.join("resources/proj_a/nested/deep/y.md"), b"y v1").unwrap();
        let source_oid = make_commit(&svc, "acct", "main", "source").await;

        // HEAD removes the whole `nested/` directory from disk (rm -r) and the
        // full-enumeration commit records the deletion, so HEAD's tree has no
        // `nested/` subtree at all.
        std::fs::remove_dir_all(acct_root.join("resources/proj_a/nested")).unwrap();
        assert!(!acct_root.join("resources/proj_a/nested").exists());
        let _head_oid = make_commit(&svc, "acct", "main", "head").await;

        // Restore back to source: the writeback must recreate `nested/` and
        // `nested/deep/` on disk before writing x.md / y.md.
        svc.restore(RestoreRequest {
            account: "acct".into(),
            branch: "main".into(),
            project_dir: Some("resources/proj_a".into()),
            source_commit: source_oid.to_hex().to_string(),
            allow_missing_source: false,
            dry_run: false,
            message: Some("rewind".into()),
            author_name: "tester".into(),
            author_email: "tester@example.com".into(),
        })
        .await
        .unwrap();

        assert_eq!(
            std::fs::read(acct_root.join("resources/proj_a/nested/x.md")).unwrap(),
            b"x v1",
            "nested/x.md must be recreated with v1 content",
        );
        assert_eq!(
            std::fs::read(acct_root.join("resources/proj_a/nested/deep/y.md")).unwrap(),
            b"y v1",
            "nested/deep/y.md must be recreated with v1 content",
        );
        assert!(
            acct_root.join("resources/proj_a/keeper.md").exists(),
            "keeper.md must survive",
        );
    }

    // Restore deletion is idempotent when a tracked file is already absent.
    #[tokio::test]
    async fn test_restore_tolerates_already_deleted_path() {
        let dir = tempfile::tempdir().unwrap();
        let object_store = Arc::new(LocalObjectStore::new(dir.path()));
        let ref_store = Arc::new(LocalRefStore::new(dir.path()));
        let vfs = MockVfs::new_strict_remove("acct");
        let svc = GitService::new(
            vfs.clone() as Arc<dyn FileSystem>,
            object_store.clone() as Arc<dyn ObjectStore>,
            ref_store.clone() as Arc<dyn RefStore>,
        );

        // The source excludes the file that HEAD adds.
        vfs.put("resources/proj_a/a.md", b"A v1");
        let source_oid = make_commit(&svc, "acct", "main", "source").await;

        vfs.put("resources/proj_a/obsolete.md", b"obsolete");
        vfs.put("resources/proj_a/a.md", b"A v2");
        let head_oid = match svc
            .commit(req(
                "acct",
                "main",
                "head",
                Some(vec![
                    "resources/proj_a/a.md".to_string(),
                    "resources/proj_a/obsolete.md".to_string(),
                ]),
            ))
            .await
            .unwrap()
        {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        };

        vfs.delete("resources/proj_a/obsolete.md");

        let resp = svc
            .restore(RestoreRequest {
                account: "acct".into(),
                branch: "main".into(),
                project_dir: Some("resources/proj_a".into()),
                source_commit: source_oid.to_hex().to_string(),
                allow_missing_source: false,
                dry_run: false,
                message: Some("rewind".into()),
                author_name: "tester".into(),
                author_email: "tester@example.com".into(),
            })
            .await
            .expect("restore must tolerate an already-deleted path");

        let new_oid = match resp {
            RestoreResponse::Applied {
                deleted,
                deleted_paths,
                new_commit_oid,
                ..
            } => {
                // The diff still *plans* the delete (count is unchanged); the
                // VFS apply just no-ops on the missing path.
                assert_eq!(deleted, 1, "obsolete.md");
                assert_eq!(deleted_paths.len(), 1);
                new_commit_oid
            }
            other => panic!("expected Applied, got {other:?}"),
        };

        // Branch ref advanced to the new commit on top of HEAD.
        assert_eq!(
            ref_store.read("acct", "refs/heads/main").await.unwrap(),
            new_oid
        );
        let parents =
            commit_parents(object_store.as_ref() as &dyn ObjectStore, "acct", new_oid).await;
        assert_eq!(parents, vec![head_oid]);
    }

    #[tokio::test]
    async fn test_restore_noop_when_source_equals_head() {
        let (_dir, vfs, _object_store, ref_store, svc) = make_service("acct");
        vfs.put("resources/proj_a/a.md", b"only file");
        let only_oid = make_commit(&svc, "acct", "main", "only").await;

        // No further changes to proj_a — restoring from `only_oid` is a noop.
        let resp = svc
            .restore(RestoreRequest {
                account: "acct".into(),
                branch: "main".into(),
                project_dir: Some("resources/proj_a".into()),
                source_commit: only_oid.to_hex().to_string(),
                allow_missing_source: false,
                dry_run: false,
                message: None,
                author_name: "tester".into(),
                author_email: "tester@example.com".into(),
            })
            .await
            .unwrap();

        match resp {
            RestoreResponse::Noop { head, source } => {
                assert_eq!(head, only_oid);
                assert_eq!(source, only_oid);
            }
            other => panic!("expected Noop, got {other:?}"),
        }
        // Ref unchanged.
        assert_eq!(
            ref_store.read("acct", "refs/heads/main").await.unwrap(),
            only_oid
        );
    }

    #[tokio::test]
    async fn test_restore_full_tree_dry_run_reports_account_relative_diff() {
        let (_dir, vfs, _object_store, ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"A v1");
        let source_oid = make_commit(&svc, "acct", "main", "source").await;

        vfs.put("resources/a.md", b"A v2");
        vfs.put("memory/new.md", b"new");
        let head_oid = match svc
            .commit(req(
                "acct",
                "main",
                "head",
                Some(vec![
                    "resources/a.md".to_string(),
                    "memory/new.md".to_string(),
                ]),
            ))
            .await
            .unwrap()
        {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        };

        let resp = svc
            .restore(RestoreRequest {
                account: "acct".into(),
                branch: "main".into(),
                project_dir: None,
                source_commit: source_oid.to_hex().to_string(),
                allow_missing_source: false,
                dry_run: true,
                message: None,
                author_name: "tester".into(),
                author_email: "tester@example.com".into(),
            })
            .await
            .unwrap();

        match resp {
            RestoreResponse::DryRun { diff, head, source } => {
                assert_eq!(head, head_oid);
                assert_eq!(source, source_oid);
                assert_eq!(diff.to_write.len(), 1);
                assert_eq!(diff.to_write[0].0, "resources/a.md");
                assert_eq!(diff.to_delete, vec!["memory/new.md".to_string()]);
            }
            other => panic!("expected DryRun, got {other:?}"),
        }

        let files = vfs.files.lock().unwrap();
        assert_eq!(files.get("/local/acct/resources/a.md").unwrap(), b"A v2");
        assert!(files.contains_key("/local/acct/memory/new.md"));
        drop(files);
        assert_eq!(
            ref_store.read("acct", "refs/heads/main").await.unwrap(),
            head_oid
        );
    }

    #[tokio::test]
    async fn test_restore_full_tree_noop_when_source_equals_head() {
        let (_dir, vfs, _object_store, ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"A v1");
        let head_oid = make_commit(&svc, "acct", "main", "source").await;

        let resp = svc
            .restore(RestoreRequest {
                account: "acct".into(),
                branch: "main".into(),
                project_dir: None,
                source_commit: head_oid.to_hex().to_string(),
                allow_missing_source: false,
                dry_run: false,
                message: None,
                author_name: "tester".into(),
                author_email: "tester@example.com".into(),
            })
            .await
            .unwrap();

        match resp {
            RestoreResponse::Noop { head, source } => {
                assert_eq!(head, head_oid);
                assert_eq!(source, head_oid);
            }
            other => panic!("expected Noop, got {other:?}"),
        }
        assert_eq!(
            ref_store.read("acct", "refs/heads/main").await.unwrap(),
            head_oid
        );
    }

    #[tokio::test]
    async fn test_restore_invalid_project_dir() {
        let (_dir, _vfs, _object_store, _ref_store, svc) = make_service("acct");
        let err = svc
            .restore(RestoreRequest {
                account: "acct".into(),
                branch: "main".into(),
                project_dir: Some("".into()), // empty
                source_commit: "main".into(),
                allow_missing_source: false,
                dry_run: true,
                message: None,
                author_name: "x".into(),
                author_email: "x@x".into(),
            })
            .await
            .unwrap_err();
        assert!(matches!(err, GitError::InvalidProjectDir(_)));
    }

    #[tokio::test]
    async fn test_restore_unknown_source_ref() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/proj_a/a.md", b"x");
        let _ = make_commit(&svc, "acct", "main", "init").await;
        let err = svc
            .restore(RestoreRequest {
                account: "acct".into(),
                branch: "main".into(),
                project_dir: Some("resources/proj_a".into()),
                source_commit: "does-not-exist".into(),
                allow_missing_source: false,
                dry_run: true,
                message: None,
                author_name: "x".into(),
                author_email: "x@x".into(),
            })
            .await
            .unwrap_err();
        assert!(matches!(
            err,
            GitError::RefStore(RefStoreError::NotFound(_))
        ));
    }

    #[tokio::test]
    async fn test_restore_unknown_branch_head() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/proj_a/a.md", b"x");
        let only = make_commit(&svc, "acct", "main", "only").await;
        let err = svc
            .restore(RestoreRequest {
                account: "acct".into(),
                branch: "ghost".into(), // doesn't exist
                project_dir: Some("resources/proj_a".into()),
                source_commit: only.to_hex().to_string(),
                allow_missing_source: false,
                dry_run: true,
                message: None,
                author_name: "x".into(),
                author_email: "x@x".into(),
            })
            .await
            .unwrap_err();
        assert!(matches!(
            err,
            GitError::RefStore(RefStoreError::NotFound(_))
        ));
    }

    #[tokio::test]
    async fn test_restore_project_dir_missing_in_source_commit() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        // Source commit only has resources/other_proj.
        vfs.put("resources/other_proj/x.md", b"x");
        let source_oid = make_commit(&svc, "acct", "main", "source").await;
        // HEAD has the project we will try to restore.
        vfs.put("resources/proj_a/a.md", b"a");
        let _ = make_commit(&svc, "acct", "main", "head").await;

        let err = svc
            .restore(RestoreRequest {
                account: "acct".into(),
                branch: "main".into(),
                project_dir: Some("resources/proj_a".into()),
                source_commit: source_oid.to_hex().to_string(),
                allow_missing_source: false,
                dry_run: true,
                message: None,
                author_name: "x".into(),
                author_email: "x@x".into(),
            })
            .await
            .unwrap_err();
        match err {
            GitError::SubtreeNotFoundInCommit {
                project_dir,
                commit,
            } => {
                assert_eq!(project_dir, "resources/proj_a");
                assert_eq!(commit, source_oid);
            }
            other => panic!("expected SubtreeNotFoundInCommit, got {other:?}"),
        }
        svc.restore(RestoreRequest {
            account: "acct".into(),
            branch: "main".into(),
            project_dir: Some("resources/proj_a".into()),
            source_commit: source_oid.to_hex().to_string(),
            allow_missing_source: true,
            dry_run: false,
            message: None,
            author_name: "x".into(),
            author_email: "x@x".into(),
        }).await.unwrap();
        assert_eq!(vfs.files.lock().unwrap().len(), 1);
    }

    #[tokio::test]
    async fn test_restore_cas_conflict_surfaces_as_error() {
        let dir = tempfile::tempdir().unwrap();
        let object_store = Arc::new(LocalObjectStore::new(dir.path()));
        let inner_ref = Arc::new(LocalRefStore::new(dir.path()));
        let vfs = MockVfs::new("acct");

        // Build a real first commit through a plain service so we have a HEAD.
        let bootstrap_svc = GitService::new(
            vfs.clone() as Arc<dyn FileSystem>,
            object_store.clone() as Arc<dyn ObjectStore>,
            inner_ref.clone() as Arc<dyn RefStore>,
        );
        vfs.put("resources/proj_a/a.md", b"v1");
        let source_oid = make_commit(&bootstrap_svc, "acct", "main", "source").await;
        vfs.put("resources/proj_a/a.md", b"v2");
        let head_oid = make_commit(&bootstrap_svc, "acct", "main", "head").await;

        // Now wrap the ref store to force the first cas_update to fail.
        let bogus = ObjectId::from_hex(b"deadbeefdeadbeefdeadbeefdeadbeefdeadbeef").unwrap();
        let conflict_ref = Arc::new(ConflictOnceRef {
            inner: inner_ref.clone(),
            fired: Mutex::new(false),
            actual: Some(bogus),
        });
        let svc = GitService::new(
            vfs.clone() as Arc<dyn FileSystem>,
            object_store.clone() as Arc<dyn ObjectStore>,
            conflict_ref as Arc<dyn RefStore>,
        );

        let err = svc
            .restore(RestoreRequest {
                account: "acct".into(),
                branch: "main".into(),
                project_dir: Some("resources/proj_a".into()),
                source_commit: source_oid.to_hex().to_string(),
                allow_missing_source: false,
                dry_run: false,
                message: None,
                author_name: "x".into(),
                author_email: "x@x".into(),
            })
            .await
            .unwrap_err();
        match err {
            GitError::ConcurrentCommit {
                ref_name,
                expected,
                actual,
            } => {
                assert_eq!(ref_name, "refs/heads/main");
                assert_eq!(expected, Some(head_oid));
                assert_eq!(actual, Some(bogus));
            }
            other => panic!("expected ConcurrentCommit, got {other:?}"),
        }
    }

    /// Regression: a losing CAS race during restore must leave the VFS
    /// byte-identical to its pre-restore (HEAD) state. The ref-consistency
    /// protocol now runs before any writeback, so a `ConcurrentCommit` error
    /// implies zero working-tree mutations — neither `to_write` content nor
    /// `to_delete` removals are applied. This keeps the failed request, the
    /// on-disk working tree, and the caller's reindex decision consistent.
    #[tokio::test]
    async fn test_restore_cas_conflict_leaves_vfs_unchanged() {
        let dir = tempfile::tempdir().unwrap();
        let object_store = Arc::new(LocalObjectStore::new(dir.path()));
        let inner_ref = Arc::new(LocalRefStore::new(dir.path()));
        let vfs = MockVfs::new("acct");

        let bootstrap_svc = GitService::new(
            vfs.clone() as Arc<dyn FileSystem>,
            object_store.clone() as Arc<dyn ObjectStore>,
            inner_ref.clone() as Arc<dyn RefStore>,
        );

        // Source commit: a single file under the project dir.
        vfs.put("resources/proj_a/a.md", b"v1");
        let source_oid = make_commit(&bootstrap_svc, "acct", "main", "source").await;

        // HEAD commit: a.md is modified (would be a `to_write`) and a brand new
        // file is added (absent in source → would be a `to_delete` on restore).
        vfs.put("resources/proj_a/a.md", b"v2");
        vfs.put("resources/proj_a/b.md", b"new");
        let head_oid = make_commit(&bootstrap_svc, "acct", "main", "head").await;

        // Snapshot the working tree exactly as it stands at HEAD.
        let before = vfs.files.lock().unwrap().clone();

        // Force the first cas_update to conflict.
        let bogus = ObjectId::from_hex(b"deadbeefdeadbeefdeadbeefdeadbeefdeadbeef").unwrap();
        let conflict_ref = Arc::new(ConflictOnceRef {
            inner: inner_ref.clone(),
            fired: Mutex::new(false),
            actual: Some(bogus),
        });
        let svc = GitService::new(
            vfs.clone() as Arc<dyn FileSystem>,
            object_store.clone() as Arc<dyn ObjectStore>,
            conflict_ref as Arc<dyn RefStore>,
        );

        let err = svc
            .restore(RestoreRequest {
                account: "acct".into(),
                branch: "main".into(),
                project_dir: Some("resources/proj_a".into()),
                source_commit: source_oid.to_hex().to_string(),
                allow_missing_source: false,
                dry_run: false,
                message: None,
                author_name: "x".into(),
                author_email: "x@x".into(),
            })
            .await
            .unwrap_err();
        assert!(
            matches!(err, GitError::ConcurrentCommit { .. }),
            "expected ConcurrentCommit, got {err:?}"
        );

        // The working tree must be untouched: a.md still v2 (not rewritten to
        // v1) and b.md still present (not deleted).
        let after = vfs.files.lock().unwrap().clone();
        assert_eq!(after, before, "VFS must not change on a CAS conflict");
        assert_eq!(
            after
                .get("/local/acct/resources/proj_a/a.md")
                .map(|v| v.as_slice()),
            Some(b"v2".as_slice()),
            "a.md must keep its HEAD content"
        );
        assert!(
            after.contains_key("/local/acct/resources/proj_a/b.md"),
            "b.md must not be deleted"
        );

        // And HEAD must still point at the original commit.
        let head_now = inner_ref.read("acct", "refs/heads/main").await.unwrap();
        assert_eq!(head_now, head_oid, "branch ref must be unchanged");
    }

    #[tokio::test]
    async fn test_restore_does_not_touch_paths_outside_project_dir() {
        let (_dir, vfs, object_store, _ref_store, svc) = make_service("acct");

        // Source: resources/proj_a + an UNRELATED file in another scope.
        vfs.put("resources/proj_a/a.md", b"A v1");
        vfs.put("agent/skills/unrelated.py", b"unrelated v1");
        let source_oid = make_commit(&svc, "acct", "main", "source").await;

        // HEAD: modify proj_a AND the unrelated file. Note we don't delete
        // anything in this test, so make_commit (which uses collect_all) is
        // fine — all files still exist in the VFS.
        vfs.put("resources/proj_a/a.md", b"A v2");
        vfs.put("agent/skills/unrelated.py", b"unrelated v2");
        vfs.put("agent/skills/new_skill.py", b"brand new");
        let _ = make_commit(&svc, "acct", "main", "head").await;

        let resp = svc
            .restore(RestoreRequest {
                account: "acct".into(),
                branch: "main".into(),
                project_dir: Some("resources/proj_a".into()),
                source_commit: source_oid.to_hex().to_string(),
                allow_missing_source: false,
                dry_run: false,
                message: None,
                author_name: "x".into(),
                author_email: "x@x".into(),
            })
            .await
            .unwrap();

        let new_oid = match resp {
            RestoreResponse::Applied { new_commit_oid, .. } => new_commit_oid,
            other => panic!("expected Applied, got {other:?}"),
        };

        // Verify the VFS: unrelated files keep their v2 / new state.
        let files = vfs.files.lock().unwrap();
        assert_eq!(
            files.get("/local/acct/agent/skills/unrelated.py").unwrap(),
            b"unrelated v2",
            "restore must NOT roll back unrelated.py",
        );
        assert!(
            files.contains_key("/local/acct/agent/skills/new_skill.py"),
            "restore must NOT delete new_skill.py",
        );
        // And proj_a/a.md DID roll back.
        assert_eq!(
            files.get("/local/acct/resources/proj_a/a.md").unwrap(),
            b"A v1",
        );
        drop(files);

        // Verify the tree: the new commit's tree should contain the v2 content
        // of unrelated.py and new_skill.py at their original oids. The easiest
        // way: lookup the oid of agent/skills/unrelated.py in both source and
        // new — they must DIFFER (source had v1, new still has v2).
        let new_tree =
            load_commit_meta(object_store.as_ref() as &dyn ObjectStore, "acct", &new_oid)
                .await
                .unwrap()
                .tree;
        let source_tree = load_commit_meta(
            object_store.as_ref() as &dyn ObjectStore,
            "acct",
            &source_oid,
        )
        .await
        .unwrap()
        .tree;
        let unrelated_in_new = crate::git::tree_builder::lookup(
            object_store.as_ref() as &dyn ObjectStore,
            "acct",
            new_tree,
            "agent/skills/unrelated.py",
        )
        .await
        .unwrap()
        .unwrap();
        let unrelated_in_source = crate::git::tree_builder::lookup(
            object_store.as_ref() as &dyn ObjectStore,
            "acct",
            source_tree,
            "agent/skills/unrelated.py",
        )
        .await
        .unwrap()
        .unwrap();
        assert_ne!(
            unrelated_in_new.0, unrelated_in_source.0,
            "agent/skills/unrelated.py in the new tree must be HEAD's v2 oid, not source's v1 oid",
        );
        assert!(
            crate::git::tree_builder::lookup(
                object_store.as_ref() as &dyn ObjectStore,
                "acct",
                new_tree,
                "agent/skills/new_skill.py",
            )
            .await
            .unwrap()
            .is_some(),
            "new_skill.py must still be present in the new tree",
        );
    }

    #[tokio::test]
    async fn test_restore_then_show_reflects_old_content() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");

        vfs.put("resources/proj_a/note.md", b"original");
        let src = make_commit(&svc, "acct", "main", "src").await;

        vfs.put("resources/proj_a/note.md", b"edited");
        let _ = make_commit(&svc, "acct", "main", "edit").await;

        // Sanity: show on HEAD shows "edited".
        let head_show = svc
            .show(ShowRequest {
                account: "acct".into(),
                target_ref: "main".into(),
                path: Some("resources/proj_a/note.md".into()),
            })
            .await
            .unwrap();
        match head_show {
            ShowResponse::Blob { bytes, .. } => assert_eq!(bytes.as_ref(), b"edited"),
            other => panic!("expected Blob, got {other:?}"),
        }

        // Restore.
        let new_oid = match svc
            .restore(RestoreRequest {
                account: "acct".into(),
                branch: "main".into(),
                project_dir: Some("resources/proj_a".into()),
                source_commit: src.to_hex().to_string(),
                allow_missing_source: false,
                dry_run: false,
                message: Some("rewind".into()),
                author_name: "x".into(),
                author_email: "x@x".into(),
            })
            .await
            .unwrap()
        {
            RestoreResponse::Applied { new_commit_oid, .. } => new_commit_oid,
            other => panic!("expected Applied, got {other:?}"),
        };

        // After restore: show on main should reflect the original content.
        let after_show = svc
            .show(ShowRequest {
                account: "acct".into(),
                target_ref: "main".into(),
                path: Some("resources/proj_a/note.md".into()),
            })
            .await
            .unwrap();
        match after_show {
            ShowResponse::Blob { bytes, .. } => assert_eq!(bytes.as_ref(), b"original"),
            other => panic!("expected Blob, got {other:?}"),
        }

        // And show on the new oid by hex resolves to the same content.
        let by_oid = svc
            .show(ShowRequest {
                account: "acct".into(),
                target_ref: new_oid.to_hex().to_string(),
                path: Some("resources/proj_a/note.md".into()),
            })
            .await
            .unwrap();
        match by_oid {
            ShowResponse::Blob { bytes, .. } => assert_eq!(bytes.as_ref(), b"original"),
            other => panic!("expected Blob, got {other:?}"),
        }
    }


mod diff_tests {
    use super::super::compute_subtree_diff;
    use crate::git::types::RestoreDiff;
    use gix_hash::ObjectId;

    fn oid(byte: u8) -> ObjectId {
        let mut bytes = [0u8; 20];
        bytes.fill(byte);
        ObjectId::from_bytes_or_panic(&bytes)
    }

    #[test]
    fn diff_empty_both() {
        let got = compute_subtree_diff(&[], &[]);
        assert_eq!(
            got,
            RestoreDiff {
                to_write: vec![],
                to_delete: vec![],
                unchanged: vec![]
            }
        );
    }

    #[test]
    fn diff_all_writes_when_head_empty() {
        let source = vec![("a.md".to_string(), oid(0xAA))];
        let got = compute_subtree_diff(&source, &[]);
        assert_eq!(got.to_write, vec![("a.md".to_string(), oid(0xAA))]);
        assert!(got.to_delete.is_empty());
        assert!(got.unchanged.is_empty());
    }

    #[test]
    fn diff_all_deletes_when_source_empty() {
        let head = vec![("b.md".to_string(), oid(0xBB))];
        let got = compute_subtree_diff(&[], &head);
        assert!(got.to_write.is_empty());
        assert_eq!(got.to_delete, vec!["b.md".to_string()]);
        assert!(got.unchanged.is_empty());
    }

    #[test]
    fn diff_unchanged_same_oid_same_path() {
        let entries = vec![("a.md".to_string(), oid(0xCC))];
        let got = compute_subtree_diff(&entries, &entries);
        assert!(got.to_write.is_empty());
        assert!(got.to_delete.is_empty());
        assert_eq!(got.unchanged, vec!["a.md".to_string()]);
    }

    #[test]
    fn diff_overwrite_when_same_path_different_oid() {
        let source = vec![("a.md".to_string(), oid(0xAA))];
        let head = vec![("a.md".to_string(), oid(0xBB))];
        let got = compute_subtree_diff(&source, &head);
        assert_eq!(got.to_write, vec![("a.md".to_string(), oid(0xAA))]);
        assert!(got.to_delete.is_empty());
        assert!(got.unchanged.is_empty());
    }

    #[test]
    fn diff_mixed_buckets_sorted_deterministically() {
        let source = vec![
            ("keep.md".to_string(), oid(0x11)),
            ("change.md".to_string(), oid(0x22)),
            ("new.md".to_string(), oid(0x33)),
        ];
        let head = vec![
            ("keep.md".to_string(), oid(0x11)),
            ("change.md".to_string(), oid(0x99)),
            ("gone.md".to_string(), oid(0x44)),
        ];
        let got = compute_subtree_diff(&source, &head);
        assert_eq!(
            got.to_write,
            vec![
                ("change.md".to_string(), oid(0x22)),
                ("new.md".to_string(), oid(0x33)),
            ]
        );
        assert_eq!(got.to_delete, vec!["gone.md".to_string()]);
        assert_eq!(got.unchanged, vec!["keep.md".to_string()]);
    }

    #[test]
    fn diff_handles_nested_paths() {
        let source = vec![
            ("docs/a.md".to_string(), oid(0xAA)),
            ("docs/sub/b.md".to_string(), oid(0xBB)),
        ];
        let head = vec![("docs/a.md".to_string(), oid(0xAA))];
        let got = compute_subtree_diff(&source, &head);
        assert_eq!(got.to_write, vec![("docs/sub/b.md".to_string(), oid(0xBB))]);
        assert!(got.to_delete.is_empty());
        assert_eq!(got.unchanged, vec!["docs/a.md".to_string()]);
    }
}

mod validate_tests {
    use super::*;
    // ── account id validation ───────────────────────────────────────────
    #[test]
    fn validate_account_id_accepts_valid() {
        for ok in ["acct", "a", "user-1", "u_2", "name.tag", "a@b", "ABC123"] {
            assert!(validate_account_id(ok).is_ok(), "{ok:?} should be valid");
        }
    }

    #[test]
    fn validate_account_id_rejects_malicious() {
        for bad in [
            "",        // empty
            ".",       // dot
            "..",      // parent
            "../x",    // traversal
            "a/b",     // slash
            "a\\b",    // backslash
            "a\0b",    // NUL
            "a\nb",    // newline / control
            "a b",     // space
            "a@b@c",   // multiple @
            "_system", // leading underscore
        ] {
            assert!(
                matches!(validate_account_id(bad), Err(GitError::InvalidAccountId(_))),
                "{bad:?} should be rejected",
            );
        }
    }

    #[tokio::test]
    async fn commit_rejects_traversal_account() {
        // A crafted account must be rejected before any path is built, so the
        // ref store is never even touched.
        let (_dir, _vfs, _object_store, _ref_store, svc) = make_service("acct");
        let err = svc.commit(req("../escape", "main", "msg", None)).await;
        assert!(matches!(err, Err(GitError::InvalidAccountId(_))));
    }

    #[tokio::test]
    async fn commit_rejects_slash_account() {
        let (_dir, _vfs, _object_store, _ref_store, svc) = make_service("acct");
        let err = svc.commit(req("a/b", "main", "msg", None)).await;
        assert!(matches!(err, Err(GitError::InvalidAccountId(_))));
    }

    #[tokio::test]
    async fn show_rejects_traversal_account() {
        let (_dir, _vfs, _object_store, _ref_store, svc) = make_service("acct");
        let err = svc
            .show(ShowRequest {
                account: "../escape".into(),
                target_ref: "main".into(),
                path: None,
            })
            .await;
        assert!(matches!(err, Err(GitError::InvalidAccountId(_))));
    }

    #[tokio::test]
    async fn restore_rejects_traversal_account() {
        let (_dir, _vfs, _object_store, _ref_store, svc) = make_service("acct");
        let err = svc
            .restore(RestoreRequest {
                account: "../escape".into(),
                branch: "main".into(),
                project_dir: Some("resources/x".into()),
                source_commit: "deadbeef".into(),
                allow_missing_source: false,
                dry_run: false,
                message: None,
                author_name: "n".into(),
                author_email: "e".into(),
            })
            .await;
        assert!(matches!(err, Err(GitError::InvalidAccountId(_))));
    }

    // ── direct-binding traversal defence: account is OK, but the caller
    //    tries to escape via paths / project_dir / show path. These must be
    //    rejected before any VFS / object-store I/O. ──────────────────────
    #[tokio::test]
    async fn commit_rejects_traversal_in_paths() {
        let (_dir, _vfs, _object_store, _ref_store, svc) = make_service("acct");
        for bad in [
            "../other/file.md",
            "a/../../other.md",
            "/abs.md",
            "a/./b.md",
            "a\\b.md",
        ] {
            let err = svc
                .commit(req("acct", "main", "msg", Some(vec![bad.to_string()])))
                .await;
            assert!(
                matches!(err, Err(GitError::InvalidPath(_))),
                "{bad:?} should yield InvalidPath, got {err:?}",
            );
        }
    }

    #[tokio::test]
    async fn show_rejects_traversal_path() {
        let (_dir, _vfs, _object_store, _ref_store, svc) = make_service("acct");
        for bad in ["../other.md", ".", "..", "a/../b", ""] {
            let err = svc
                .show(ShowRequest {
                    account: "acct".into(),
                    target_ref: "main".into(),
                    path: Some(bad.to_string()),
                })
                .await;
            assert!(
                matches!(err, Err(GitError::InvalidPath(_))),
                "{bad:?} should yield InvalidPath, got {err:?}",
            );
        }
    }

    #[tokio::test]
    async fn restore_rejects_traversal_project_dir() {
        let (_dir, _vfs, _object_store, _ref_store, svc) = make_service("acct");
        for bad in ["../other", ".", "..", "a/../b", "a/./b", "a\\b"] {
            let err = svc
                .restore(RestoreRequest {
                    account: "acct".into(),
                    branch: "main".into(),
                    project_dir: Some(bad.to_string()),
                    source_commit: "deadbeef".into(),
                    allow_missing_source: false,
                    dry_run: false,
                    message: None,
                    author_name: "n".into(),
                    author_email: "e".into(),
                })
                .await;
            assert!(
                matches!(err, Err(GitError::InvalidProjectDir(_))),
                "{bad:?} should yield InvalidProjectDir, got {err:?}",
            );
        }
    }

    #[test]
    fn validate_rejects_empty_string() {
        let err = validate_project_dir("").unwrap_err();
        assert!(matches!(err, GitError::InvalidProjectDir(_)));
    }

    #[test]
    fn validate_rejects_leading_slash() {
        assert!(matches!(
            validate_project_dir("/resources/proj_a").unwrap_err(),
            GitError::InvalidProjectDir(_)
        ));
    }

    #[test]
    fn validate_rejects_trailing_slash() {
        assert!(matches!(
            validate_project_dir("resources/proj_a/").unwrap_err(),
            GitError::InvalidProjectDir(_)
        ));
    }

    #[test]
    fn validate_rejects_double_slash() {
        assert!(matches!(
            validate_project_dir("resources//proj_a").unwrap_err(),
            GitError::InvalidProjectDir(_)
        ));
    }

    #[test]
    fn validate_accepts_simple_path() {
        validate_project_dir("resources/proj_a").unwrap();
    }

    #[test]
    fn validate_accepts_single_segment() {
        validate_project_dir("resources").unwrap();
    }

    // ── project_dir hardening (traversal / backslash / control) ─────────
    #[test]
    fn validate_project_dir_rejects_dotdot_segment() {
        for bad in ["..", "../other", "resources/../other", "a/.."] {
            assert!(
                matches!(
                    validate_project_dir(bad),
                    Err(GitError::InvalidProjectDir(_))
                ),
                "{bad:?} should be rejected",
            );
        }
    }

    #[test]
    fn validate_project_dir_rejects_dot_segment() {
        for bad in [".", "./x", "a/./b"] {
            assert!(matches!(
                validate_project_dir(bad),
                Err(GitError::InvalidProjectDir(_))
            ));
        }
    }

    #[test]
    fn validate_project_dir_rejects_backslash_and_control() {
        for bad in ["a\\b", "a\0b", "a\nb"] {
            assert!(
                matches!(
                    validate_project_dir(bad),
                    Err(GitError::InvalidProjectDir(_))
                ),
                "{bad:?} should be rejected",
            );
        }
    }

    // ── relative path validation (commit / show) ────────────────────────
    #[test]
    fn validate_relative_path_accepts_normal_paths() {
        for ok in ["a.md", "dir/a.md", "a/b/c/d.txt", "..hidden", "a..b"] {
            validate_relative_path(ok)
                .unwrap_or_else(|e| panic!("{ok:?} should be valid, got {e:?}"));
        }
    }

    #[test]
    fn validate_relative_path_rejects_malicious() {
        for bad in [
            "",          // empty
            "/abs/path", // leading slash
            "trailing/", // trailing slash
            "a//b",      // empty segment
            ".",         // dot
            "..",        // dotdot
            "../escape", // traversal at root
            "a/../b",    // traversal mid-path
            "a/./b",     // dot mid-path
            "a\\b",      // backslash
            "a\0b",      // NUL
            "a\nb",      // control char
        ] {
            assert!(
                matches!(validate_relative_path(bad), Err(GitError::InvalidPath(_))),
                "{bad:?} should be rejected",
            );
        }
    }
}
