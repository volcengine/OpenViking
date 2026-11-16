    // ── 1 ──────────────────────────────────────────────────────────────
    #[tokio::test]
    async fn test_commit_first_creates_root_commit() {
        let (_dir, vfs, object_store, ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"hello");

        let resp = svc
            .commit(req("acct", "main", "first", None))
            .await
            .unwrap();

        match resp {
            CommitResponse::Created {
                commit_oid,
                changed,
                ..
            } => {
                assert!(changed >= 1, "should record at least one change");
                let parents = commit_parents(
                    object_store.as_ref() as &dyn ObjectStore,
                    "acct",
                    commit_oid,
                )
                .await;
                assert!(parents.is_empty(), "root commit must have no parents");
                let tree = commit_tree(
                    object_store.as_ref() as &dyn ObjectStore,
                    "acct",
                    commit_oid,
                )
                .await;
                assert_ne!(tree, ObjectId::empty_tree(gix_hash::Kind::Sha1));
                let head = ref_store.read("acct", "refs/heads/main").await.unwrap();
                assert_eq!(head, commit_oid);
            }
            other => panic!("expected Created, got {other:?}"),
        }
    }

    // ── 2 ──────────────────────────────────────────────────────────────
    #[tokio::test]
    async fn test_commit_second_links_to_first() {
        let (_dir, vfs, object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"hello");
        let first = svc
            .commit(req("acct", "main", "first", None))
            .await
            .unwrap();
        let first_oid = match first {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        };

        vfs.put("resources/a.md", b"world");
        let second = svc
            .commit(req("acct", "main", "second", None))
            .await
            .unwrap();
        let second_oid = match second {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        };

        let parents = commit_parents(
            object_store.as_ref() as &dyn ObjectStore,
            "acct",
            second_oid,
        )
        .await;
        assert_eq!(parents, vec![first_oid]);
    }

    // ── 3 ──────────────────────────────────────────────────────────────
    #[tokio::test]
    async fn test_commit_noop_when_nothing_changed() {
        let (_dir, vfs, _object_store, ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"hello");
        let first = svc
            .commit(req("acct", "main", "first", None))
            .await
            .unwrap();
        let first_oid = match first {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        };

        let second = svc.commit(req("acct", "main", "noop", None)).await.unwrap();
        match second {
            CommitResponse::Noop { commit_oid, .. } => assert_eq!(commit_oid, first_oid),
            other => panic!("expected Noop, got {other:?}"),
        }

        let head = ref_store.read("acct", "refs/heads/main").await.unwrap();
        assert_eq!(head, first_oid);
    }

    // ── 4 ──────────────────────────────────────────────────────────────
    #[tokio::test]
    async fn test_commit_handles_deletes() {
        let (_dir, vfs, object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"hello");
        vfs.put("resources/b.md", b"world");
        let _ = svc
            .commit(req("acct", "main", "first", None))
            .await
            .unwrap();

        vfs.delete("resources/a.md");
        let resp = svc
            .commit(req(
                "acct",
                "main",
                "delete-a",
                Some(vec!["resources/a.md".to_string()]),
            ))
            .await
            .unwrap();
        let second_oid = match resp {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        };

        let tree = commit_tree(
            object_store.as_ref() as &dyn ObjectStore,
            "acct",
            second_oid,
        )
        .await;
        let all = flatten(
            object_store.as_ref() as &dyn ObjectStore,
            "acct",
            tree,
            &None,
        )
        .await
        .unwrap();
        let paths: Vec<String> = all.into_iter().map(|(p, _)| p).collect();
        assert_eq!(paths, vec!["resources/b.md".to_string()]);
    }

    /// Helper: list every blob path in a commit's tree, sorted.
    async fn commit_paths(
        store: &dyn ObjectStore,
        account: &str,
        commit_oid: ObjectId,
    ) -> Vec<String> {
        let tree = commit_tree(store, account, commit_oid).await;
        let all = flatten(store, account, tree, &None).await.unwrap();
        all.into_iter().map(|(p, _)| p).collect()
    }

    // ── 4b ─────────────────────────────────────────────────────────────
    /// Full enumeration (`paths=None`) must capture a deletion: a file gone
    /// from disk but present in prev_tree is dropped from the new snapshot.
    #[tokio::test]
    async fn test_full_commit_captures_delete() {
        let (_dir, vfs, object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"hello");
        vfs.put("resources/b.md", b"world");
        let _ = make_commit(&svc, "acct", "main", "first").await;

        vfs.delete("resources/a.md");
        let resp = svc
            .commit(req("acct", "main", "full-delete", None))
            .await
            .unwrap();
        let oid = match resp {
            CommitResponse::Created {
                commit_oid,
                changed,
                ..
            } => {
                assert_eq!(changed, 1, "exactly one path (a.md) was removed");
                commit_oid
            }
            other => panic!("expected Created, got {other:?}"),
        };

        let paths = commit_paths(object_store.as_ref() as &dyn ObjectStore, "acct", oid).await;
        assert_eq!(paths, vec!["resources/b.md".to_string()]);
    }

    // ── 4c ─────────────────────────────────────────────────────────────
    /// Full enumeration must capture deletion of an entire subdirectory.
    #[tokio::test]
    async fn test_full_commit_captures_subdir_delete() {
        let (_dir, vfs, object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/keep.md", b"keep");
        vfs.put("resources/sub/a.md", b"a");
        vfs.put("resources/sub/b.md", b"b");
        let _ = make_commit(&svc, "acct", "main", "first").await;

        vfs.delete("resources/sub/a.md");
        vfs.delete("resources/sub/b.md");
        let resp = svc
            .commit(req("acct", "main", "drop-sub", None))
            .await
            .unwrap();
        let oid = match resp {
            CommitResponse::Created {
                commit_oid,
                changed,
                ..
            } => {
                assert_eq!(changed, 2, "both files under sub/ were removed");
                commit_oid
            }
            other => panic!("expected Created, got {other:?}"),
        };

        let paths = commit_paths(object_store.as_ref() as &dyn ObjectStore, "acct", oid).await;
        assert_eq!(paths, vec!["resources/keep.md".to_string()]);
    }

    // ── 4d ─────────────────────────────────────────────────────────────
    /// Full enumeration must handle a file→dir transition: `foo` was a file,
    /// now `foo/bar.md` is a directory entry. The stale blob is replaced.
    #[tokio::test]
    async fn test_full_commit_file_to_dir_transition() {
        let (_dir, vfs, object_store, _ref_store, svc) = make_service("acct");
        vfs.put("foo", b"i am a file");
        let _ = make_commit(&svc, "acct", "main", "first").await;

        vfs.delete("foo");
        vfs.put("foo/bar.md", b"now a dir");
        let resp = svc
            .commit(req("acct", "main", "file-to-dir", None))
            .await
            .unwrap();
        let oid = match resp {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        };

        let paths = commit_paths(object_store.as_ref() as &dyn ObjectStore, "acct", oid).await;
        assert_eq!(paths, vec!["foo/bar.md".to_string()]);
    }

    /// A previously snapshotted file can become a real directory. MockVfs reports
    /// directories as NotFound, so it misses both the read-directory error when
    /// committing a parent and the stale blob when committing the exact directory.
    #[tokio::test]
    async fn test_commit_file_to_directory_on_localfs() {
        use crate::git::backends::local::LocalIndexStore;
        use crate::git::index_store::IndexStore;
        use crate::plugins::localfs::LocalFileSystem;

        for scope in [None, Some("docs"), Some("docs/note.md")] {
            for with_child in [false, true] {
                let store_dir = tempfile::tempdir().unwrap();
                let object_store = Arc::new(LocalObjectStore::new(store_dir.path()));
                let ref_store = Arc::new(LocalRefStore::new(store_dir.path()));
                let index_store = Arc::new(LocalIndexStore::new(store_dir.path()));
                let work_dir = tempfile::tempdir().unwrap();
                let acct_root = work_dir.path().join("local/acct");
                std::fs::create_dir_all(acct_root.join("docs")).unwrap();
                std::fs::write(acct_root.join("docs/note.md"), b"old file").unwrap();
                std::fs::write(acct_root.join("outside.md"), b"keep").unwrap();
                let vfs: Arc<dyn FileSystem> =
                    Arc::new(LocalFileSystem::new(work_dir.path().to_str().unwrap()).unwrap());
                let svc = GitService::with_index(
                    vfs,
                    object_store.clone(),
                    ref_store,
                    Some(index_store.clone()),
                );
                make_commit(&svc, "acct", "main", "before").await;
                std::fs::remove_file(acct_root.join("docs/note.md")).unwrap();
                std::fs::create_dir(acct_root.join("docs/note.md")).unwrap();
                if with_child {
                    std::fs::write(acct_root.join("docs/note.md/child.md"), b"new child").unwrap();
                }
                let paths = scope.map(|path| vec![path.to_string()]);
                let response = svc
                    .commit(req("acct", "main", "after", paths.clone()))
                    .await
                    .unwrap();
                let commit_oid = match response {
                    CommitResponse::Created { commit_oid, .. } => commit_oid,
                    other => {
                        panic!("expected Created for {scope:?}, child={with_child}: {other:?}")
                    }
                };
                let actual = commit_paths(object_store.as_ref(), "acct", commit_oid).await;
                let expected = if with_child {
                    vec![
                        "docs/note.md/child.md".to_string(),
                        "outside.md".to_string(),
                    ]
                } else {
                    vec!["outside.md".to_string()]
                };
                assert_eq!(actual, expected, "scope={scope:?}, child={with_child}");
                let index = index_store.load("acct", "main").await.unwrap().unwrap();
                assert!(!index.entries.contains_key("docs/note.md"));
                assert_eq!(
                    index.entries.contains_key("docs/note.md/child.md"),
                    with_child
                );
                assert!(matches!(
                    svc.commit(req("acct", "main", "unchanged", paths))
                        .await
                        .unwrap(),
                    CommitResponse::Noop { .. }
                ));
            }
        }
    }

    // ── 4e ─────────────────────────────────────────────────────────────
    /// Full enumeration must handle a dir→file transition: `foo/bar.md` was a
    /// directory, now `foo` is a file. The stale subtree is dropped.
    #[tokio::test]
    async fn test_full_commit_dir_to_file_transition() {
        let (_dir, vfs, object_store, _ref_store, svc) = make_service("acct");
        vfs.put("foo/bar.md", b"i am in a dir");
        let _ = make_commit(&svc, "acct", "main", "first").await;

        vfs.delete("foo/bar.md");
        vfs.put("foo", b"now a file");
        let resp = svc
            .commit(req("acct", "main", "dir-to-file", None))
            .await
            .unwrap();
        let oid = match resp {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        };

        let paths = commit_paths(object_store.as_ref() as &dyn ObjectStore, "acct", oid).await;
        assert_eq!(paths, vec!["foo".to_string()]);
    }

    // ── 4f ─────────────────────────────────────────────────────────────
    /// Multi-level dir→file: `foo/bar/baz.md` collapses to a file `foo`.
    #[tokio::test]
    async fn test_full_commit_dir_to_file_transition_multilevel() {
        let (_dir, vfs, object_store, _ref_store, svc) = make_service("acct");
        vfs.put("foo/bar/baz.md", b"deep");
        let _ = make_commit(&svc, "acct", "main", "first").await;

        vfs.delete("foo/bar/baz.md");
        vfs.put("foo", b"now a file");
        let resp = svc
            .commit(req("acct", "main", "deep-collapse", None))
            .await
            .unwrap();
        let oid = match resp {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        };

        let paths = commit_paths(object_store.as_ref() as &dyn ObjectStore, "acct", oid).await;
        assert_eq!(paths, vec!["foo".to_string()]);
    }

    // ── 5 ──────────────────────────────────────────────────────────────
    #[tokio::test]
    async fn test_commit_with_explicit_paths_skips_others() {
        let (_dir, vfs, object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"A");
        vfs.put("resources/b.md", b"B");
        vfs.put("resources/c.md", b"C");

        let resp = svc
            .commit(req(
                "acct",
                "main",
                "only-a",
                Some(vec!["resources/a.md".to_string()]),
            ))
            .await
            .unwrap();
        let oid = match resp {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        };

        let tree = commit_tree(object_store.as_ref() as &dyn ObjectStore, "acct", oid).await;
        let all = flatten(
            object_store.as_ref() as &dyn ObjectStore,
            "acct",
            tree,
            &None,
        )
        .await
        .unwrap();
        let paths: Vec<String> = all.into_iter().map(|(p, _)| p).collect();
        assert_eq!(paths, vec!["resources/a.md".to_string()]);
        // Sanity-check the blob is reachable via lookup too.
        let found = lookup(
            object_store.as_ref() as &dyn ObjectStore,
            "acct",
            tree,
            "resources/a.md",
        )
        .await
        .unwrap();
        assert!(found.is_some());
    }

    // ── 6 ──────────────────────────────────────────────────────────────

    /// Wrapping RefStore that forces the next `cas_update` call to fail
    /// with `Conflict`, then delegates to the inner store afterwards.
    struct ConflictOnceRef {
        inner: Arc<LocalRefStore>,
        fired: Mutex<bool>,
        actual: Option<ObjectId>,
    }

    #[async_trait]
    impl RefStore for ConflictOnceRef {
        async fn read(
            &self,
            account: &str,
            ref_name: &str,
        ) -> std::result::Result<ObjectId, RefStoreError> {
            self.inner.read(account, ref_name).await
        }

        async fn cas_update(
            &self,
            account: &str,
            ref_name: &str,
            expected: Option<ObjectId>,
            new: ObjectId,
        ) -> std::result::Result<(), RefStoreError> {
            let should_conflict = {
                let mut fired = self.fired.lock().unwrap();
                if !*fired {
                    *fired = true;
                    true
                } else {
                    false
                }
            };
            if should_conflict {
                return Err(RefStoreError::Conflict {
                    expected,
                    actual: self.actual,
                });
            }
            self.inner
                .cas_update(account, ref_name, expected, new)
                .await
        }

        async fn list(
            &self,
            account: &str,
            prefix: &str,
        ) -> std::result::Result<Vec<(String, ObjectId)>, RefStoreError> {
            self.inner.list(account, prefix).await
        }
    }

    #[tokio::test]
    async fn test_commit_cas_conflict_surfaces_as_error() {
        let dir = tempfile::tempdir().unwrap();
        let object_store = Arc::new(LocalObjectStore::new(dir.path()));
        let inner_ref = Arc::new(LocalRefStore::new(dir.path()));
        let bogus = ObjectId::from_hex(b"deadbeefdeadbeefdeadbeefdeadbeefdeadbeef").unwrap();
        let ref_store = Arc::new(ConflictOnceRef {
            inner: inner_ref.clone(),
            fired: Mutex::new(false),
            actual: Some(bogus),
        });
        let vfs = MockVfs::new("acct");
        vfs.put("resources/a.md", b"hello");
        let svc = GitService::new(
            vfs.clone() as Arc<dyn FileSystem>,
            object_store.clone() as Arc<dyn ObjectStore>,
            ref_store.clone() as Arc<dyn RefStore>,
        );

        let result = svc.commit(req("acct", "main", "boom", None)).await;
        match result {
            Err(GitError::ConcurrentCommit {
                ref_name,
                expected,
                actual,
            }) => {
                assert_eq!(ref_name, "refs/heads/main");
                assert_eq!(expected, None);
                assert_eq!(actual, Some(bogus));
            }
            other => panic!("expected ConcurrentCommit, got {other:?}"),
        }
    }

    // ── 7 ──────────────────────────────────────────────────────────────
    // Verifies the incremental commit path reuses unchanged subtree OIDs:
    // modifying a file under `resources/` must NOT rewrite the `agent/`
    // subtree object — its OID must be byte-identical across commits.
    #[tokio::test]
    async fn test_commit_incremental_reuses_unchanged_subtree_oids() {
        let (_dir, vfs, object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"hello");
        vfs.put("agent/b.py", b"print('hi')");

        let first = svc
            .commit(req("acct", "main", "first", None))
            .await
            .unwrap();
        let first_oid = match first {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        };
        let first_tree =
            commit_tree(object_store.as_ref() as &dyn ObjectStore, "acct", first_oid).await;
        let agent_first = lookup(
            object_store.as_ref() as &dyn ObjectStore,
            "acct",
            first_tree,
            "agent",
        )
        .await
        .unwrap()
        .expect("agent subtree must exist after first commit");
        assert!(agent_first.1.is_tree(), "agent entry must be a tree");

        // Touch only resources/a.md.
        vfs.put("resources/a.md", b"world");
        let second = svc
            .commit(req("acct", "main", "second", None))
            .await
            .unwrap();
        let second_oid = match second {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        };
        let second_tree = commit_tree(
            object_store.as_ref() as &dyn ObjectStore,
            "acct",
            second_oid,
        )
        .await;
        assert_ne!(
            first_tree, second_tree,
            "root tree must change because resources/a.md changed",
        );
        let agent_second = lookup(
            object_store.as_ref() as &dyn ObjectStore,
            "acct",
            second_tree,
            "agent",
        )
        .await
        .unwrap()
        .expect("agent subtree must still exist after second commit");

        assert_eq!(
            agent_first.0, agent_second.0,
            "unchanged agent/ subtree OID must be reused across commits",
        );
    }

    // ── 8 ──────────────────────────────────────────────────────────────
    #[tokio::test]
    async fn test_commit_skips_pruned_paths() {
        let (_dir, vfs, object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"hello");
        vfs.put("resources/x.faiss", b"FAISS");
        vfs.put("_system/lock", b"L");

        let resp = svc
            .commit(req("acct", "main", "filtered", None))
            .await
            .unwrap();
        let oid = match resp {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        };

        let tree = commit_tree(object_store.as_ref() as &dyn ObjectStore, "acct", oid).await;
        let all = flatten(
            object_store.as_ref() as &dyn ObjectStore,
            "acct",
            tree,
            &None,
        )
        .await
        .unwrap();
        let paths: Vec<String> = all.into_iter().map(|(p, _)| p).collect();
        assert_eq!(paths, vec!["resources/a.md".to_string()]);
    }

    // ── commit: paths supports directories ──────────────────────────────
    /// A directory in `paths` is expanded to every file under it that
    /// survives pruning. Files under the directory that were in the
    /// previous tree but have since been deleted from the VFS must drop
    /// out of the new snapshot.
    ///
    /// Backed by `LocalFileSystem`: `MockVfs::stat` returns NotFound for
    /// any directory entry, which would route this test through Step 2.5's
    /// NotFound branch instead of the Directory branch. A real filesystem
    /// is the only fixture where `stat("/local/acct/docs")` returns
    /// `is_dir = true`.
    #[tokio::test]
    async fn test_commit_paths_expands_directory_and_drops_deleted_files() {
        use crate::plugins::localfs::LocalFileSystem;

        let store_dir = tempfile::tempdir().unwrap();
        let object_store = Arc::new(LocalObjectStore::new(store_dir.path()));
        let ref_store = Arc::new(LocalRefStore::new(store_dir.path()));
        let work_dir = tempfile::tempdir().unwrap();
        let acct_root = work_dir.path().join("local").join("acct");
        std::fs::create_dir_all(acct_root.join("docs")).unwrap();
        std::fs::create_dir_all(acct_root.join("other")).unwrap();
        std::fs::write(acct_root.join("docs/a.md"), b"AA").unwrap();
        std::fs::write(acct_root.join("docs/b.md"), b"BB").unwrap();
        std::fs::write(acct_root.join("other/c.md"), b"CC").unwrap();
        let vfs: Arc<dyn FileSystem> =
            Arc::new(LocalFileSystem::new(work_dir.path().to_str().unwrap()).unwrap());
        let svc = GitService::new(vfs, object_store.clone(), ref_store);

        let _ = make_commit(&svc, "acct", "main", "first").await;

        // Delete b.md from VFS, add d.md, leave a.md unchanged.
        std::fs::remove_file(acct_root.join("docs/b.md")).unwrap();
        std::fs::write(acct_root.join("docs/d.md"), b"DD").unwrap();

        let resp = svc
            .commit(req("acct", "main", "scoped", Some(vec!["docs".into()])))
            .await
            .unwrap();
        let commit_oid = match resp {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        };

        // Verify the new tree through show():
        //   docs/a.md still present, docs/b.md gone, docs/d.md present,
        //   other/c.md untouched.
        let oid_hex = commit_oid.to_hex().to_string();
        assert!(matches!(
            svc.show(ShowRequest {
                account: "acct".into(),
                target_ref: oid_hex.clone(),
                path: Some("docs/a.md".into()),
            })
            .await,
            Ok(ShowResponse::Blob { .. })
        ));
        assert!(matches!(
            svc.show(ShowRequest {
                account: "acct".into(),
                target_ref: oid_hex.clone(),
                path: Some("docs/b.md".into()),
            })
            .await,
            Err(GitError::PathNotFound(_))
        ));
        assert!(matches!(
            svc.show(ShowRequest {
                account: "acct".into(),
                target_ref: oid_hex.clone(),
                path: Some("docs/d.md".into()),
            })
            .await,
            Ok(ShowResponse::Blob { .. })
        ));
        assert!(matches!(
            svc.show(ShowRequest {
                account: "acct".into(),
                target_ref: oid_hex,
                path: Some("other/c.md".into()),
            })
            .await,
            Ok(ShowResponse::Blob { .. })
        ));
    }

    /// If the directory passed in `paths` does not exist in the VFS at all,
    /// every file under that prefix in prev_tree is dropped from the new
    /// snapshot. A `warn!` is emitted but no error is returned.
    /// Uses MockVfs: the directory is "missing" so Step 2.5 sees NotFound.
    #[tokio::test]
    async fn test_commit_paths_notfound_directory_drops_subtree() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        vfs.put("docs/a.md", b"AA");
        vfs.put("docs/b.md", b"BB");
        vfs.put("other/c.md", b"CC");
        let _ = make_commit(&svc, "acct", "main", "first").await;

        // Whole directory disappears.
        vfs.delete("docs/a.md");
        vfs.delete("docs/b.md");

        let resp = svc
            .commit(req("acct", "main", "drop dir", Some(vec!["docs".into()])))
            .await
            .unwrap();
        let commit_oid = match resp {
            CommitResponse::Created {
                commit_oid,
                changed,
                ..
            } => {
                assert_eq!(changed, 3, "three files removed from snapshot");
                commit_oid
            }
            other => panic!("expected Created, got {other:?}"),
        };

        let oid_hex = commit_oid.to_hex().to_string();
        assert!(matches!(
            svc.show(ShowRequest {
                account: "acct".into(),
                target_ref: oid_hex.clone(),
                path: Some("docs/a.md".into()),
            })
            .await,
            Err(GitError::PathNotFound(_))
        ));
        assert!(matches!(
            svc.show(ShowRequest {
                account: "acct".into(),
                target_ref: oid_hex,
                path: Some("other/c.md".into()),
            })
            .await,
            Ok(ShowResponse::Blob { .. })
        ));
    }

    /// Pruning applies to explicit directories: passing `_system` results
    /// in a Noop commit (the directory does not exist in the VFS, but even
    /// if it did, every entry under it would be pruned).
    #[tokio::test]
    async fn test_commit_paths_pruned_directory_is_noop() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"AA");
        let first = make_commit(&svc, "acct", "main", "first").await;

        let resp = svc
            .commit(req(
                "acct",
                "main",
                "pruned dir",
                Some(vec!["_system".into()]),
            ))
            .await
            .unwrap();
        match resp {
            CommitResponse::Noop { commit_oid, .. } => assert_eq!(commit_oid, first),
            other => panic!("expected Noop, got {other:?}"),
        }
    }

    /// Pruning applies to explicit files: passing a pruned file path is
    /// equivalent to passing nothing. Noop on top of an existing commit.
    #[tokio::test]
    async fn test_commit_paths_pruned_file_is_noop() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"AA");
        vfs.put("_system/lock", b"LL"); // pruned, never committed
        let first = make_commit(&svc, "acct", "main", "first").await;

        let resp = svc
            .commit(req(
                "acct",
                "main",
                "pruned file",
                Some(vec!["_system/lock".into()]),
            ))
            .await
            .unwrap();
        match resp {
            CommitResponse::Noop { commit_oid, .. } => assert_eq!(commit_oid, first),
            other => panic!("expected Noop, got {other:?}"),
        }
    }

    #[tokio::test]
    async fn test_commit_ovgitignore_excludes_matching_files_and_tracks_itself() {
        let (_dir, vfs, object_store, _ref_store, svc) = make_service("acct_ignore");
        vfs.put(OVGITIGNORE_PATH, b"*.log\n");
        vfs.put("resources/a.md", b"keep");
        vfs.put("resources/a.log", b"skip");

        let resp = svc
            .commit(req("acct_ignore", "main", "ignore", None))
            .await
            .unwrap();
        let (commit_oid, ignored) = match resp {
            CommitResponse::Created {
                commit_oid,
                ignored,
                ..
            } => (commit_oid, ignored),
            other => panic!("expected Created, got {other:?}"),
        };
        assert_eq!(ignored, 1);

        let tree = commit_tree(object_store.as_ref() as &dyn ObjectStore, "acct_ignore", commit_oid).await;
        let all = flatten(
            object_store.as_ref() as &dyn ObjectStore,
            "acct_ignore",
            tree,
            &None,
        )
        .await
        .unwrap();
        let paths: Vec<String> = all.into_iter().map(|(p, _)| p).collect();
        assert_eq!(
            paths,
            vec![
                OVGITIGNORE_PATH.to_string(),
                "resources/a.md".to_string(),
            ]
        );
    }

    #[tokio::test]
    async fn test_commit_ovgitignore_removes_previously_tracked_file_from_snapshot() {
        let (_dir, vfs, object_store, _ref_store, svc) = make_service("acct_ignore_remove");
        vfs.put("resources/a.log", b"tracked before ignore");
        let first = make_commit(&svc, "acct_ignore_remove", "main", "first").await;
        let first_tree = commit_tree(
            object_store.as_ref() as &dyn ObjectStore,
            "acct_ignore_remove",
            first,
        )
        .await;
        assert!(lookup(
            object_store.as_ref() as &dyn ObjectStore,
            "acct_ignore_remove",
            first_tree,
            "resources/a.log",
        )
        .await
        .unwrap()
        .is_some());

        vfs.put(OVGITIGNORE_PATH, b"*.log\n");
        let second = svc
            .commit(req("acct_ignore_remove", "main", "second", None))
            .await
            .unwrap();
        let (second_oid, ignored) = match second {
            CommitResponse::Created {
                commit_oid,
                ignored,
                ..
            } => (commit_oid, ignored),
            other => panic!("expected Created, got {other:?}"),
        };
        // The ignored file is both on disk and in prev_tree; it must be
        // counted exactly once (regression: a previous double-count via
        // include_path in both passes reported 2).
        assert_eq!(ignored, 1);

        let second_tree = commit_tree(
            object_store.as_ref() as &dyn ObjectStore,
            "acct_ignore_remove",
            second_oid,
        )
        .await;
        assert!(lookup(
            object_store.as_ref() as &dyn ObjectStore,
            "acct_ignore_remove",
            second_tree,
            "resources/a.log",
        )
        .await
        .unwrap()
        .is_none());
        assert!(lookup(
            object_store.as_ref() as &dyn ObjectStore,
            "acct_ignore_remove",
            second_tree,
            OVGITIGNORE_PATH,
        )
        .await
        .unwrap()
        .is_some());
    }

    #[tokio::test]
    async fn test_commit_scoped_paths_respect_ovgitignore() {
        let (_dir, vfs, object_store, _ref_store, svc) = make_service("acct_ignore_scoped");
        vfs.put(OVGITIGNORE_PATH, b"*.log\n");
        vfs.put("resources/a.md", b"keep");
        vfs.put("resources/a.log", b"skip");

        let resp = svc
            .commit(req(
                "acct_ignore_scoped",
                "main",
                "scoped",
                Some(vec![
                    "resources/a.md".to_string(),
                    "resources/a.log".to_string(),
                ]),
            ))
            .await
            .unwrap();
        let (commit_oid, ignored) = match resp {
            CommitResponse::Created {
                commit_oid,
                ignored,
                ..
            } => (commit_oid, ignored),
            other => panic!("expected Created, got {other:?}"),
        };
        assert_eq!(ignored, 1);

        let tree = commit_tree(
            object_store.as_ref() as &dyn ObjectStore,
            "acct_ignore_scoped",
            commit_oid,
        )
        .await;
        assert!(lookup(
            object_store.as_ref() as &dyn ObjectStore,
            "acct_ignore_scoped",
            tree,
            "resources/a.md",
        )
        .await
        .unwrap()
        .is_some());
        assert!(lookup(
            object_store.as_ref() as &dyn ObjectStore,
            "acct_ignore_scoped",
            tree,
            "resources/a.log",
        )
        .await
        .unwrap()
        .is_none());
    }

    #[tokio::test]
    async fn test_commit_invalid_ovgitignore_fails() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct_bad_ignore");
        vfs.put(OVGITIGNORE_PATH, b"!keep.log\n");

        let err = svc
            .commit(req("acct_bad_ignore", "main", "bad", None))
            .await
            .unwrap_err();
        assert!(matches!(err, GitError::InvalidIgnoreFile { .. }));
    }

    /// A scoped commit listing an explicit file that is BOTH deleted from disk
    /// AND newly matched by `.ovgitignore` must still drop it from the
    /// snapshot. Regression: the NotFound branch routed `p` through
    /// `include_path`, which returns false for ignored paths, so `p` never
    /// entered the candidate set and the prev_tree prefix loop (`p/`) does not
    /// match the file path itself — the stale blob lingered and `changed`
    /// stayed 0 (Noop), silently losing the user's deletion. The File (Ok)
    /// branch handles the on-disk analogue; this asserts symmetry.
    #[tokio::test]
    async fn test_commit_scoped_notfound_ignored_file_is_removed_from_snapshot() {
        let (_dir, vfs, object_store, _ref_store, svc) =
            make_service("acct_nf_ignore");
        vfs.put("resources/secret.log", b"tracked before ignore");
        let first = make_commit(&svc, "acct_nf_ignore", "main", "first").await;
        let first_tree = commit_tree(
            object_store.as_ref() as &dyn ObjectStore,
            "acct_nf_ignore",
            first,
        )
        .await;
        assert!(lookup(
            object_store.as_ref() as &dyn ObjectStore,
            "acct_nf_ignore",
            first_tree,
            "resources/secret.log",
        )
        .await
        .unwrap()
        .is_some());

        // Ignore logs, delete the file from disk, then commit it by name.
        vfs.put(OVGITIGNORE_PATH, b"*.log\n");
        vfs.delete("resources/secret.log");
        let resp = svc
            .commit(req(
                "acct_nf_ignore",
                "main",
                "second",
                Some(vec!["resources/secret.log".to_string()]),
            ))
            .await
            .unwrap();
        let second_oid = match resp {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created (deletion), got {other:?}"),
        };
        let second_tree = commit_tree(
            object_store.as_ref() as &dyn ObjectStore,
            "acct_nf_ignore",
            second_oid,
        )
        .await;
        assert!(
            lookup(
                object_store.as_ref() as &dyn ObjectStore,
                "acct_nf_ignore",
                second_tree,
                "resources/secret.log",
            )
            .await
            .unwrap()
            .is_none(),
            "deleted-and-ignored explicit file must not linger in the snapshot"
        );
    }

    /// Duplicate or overlapping entries in `paths` must not inflate the
    /// `ignored`/`changed` counters. Regression: the scoped branches counted
    /// per listed entry (before BTreeSet dedup) and `was_tracked` probed the
    /// immutable prev_tree, so listing an ignored file twice reported
    /// `ignored=2, changed=2` for a single file. The tree itself was correct
    /// (editor.remove is idempotent); only the reported counts were wrong.
    #[tokio::test]
    async fn test_commit_scoped_duplicate_paths_do_not_double_count() {
        let (_dir, vfs, object_store, _ref_store, svc) = make_service("acct_dup");
        // First commit tracks resources/a.log (no ignore rule yet).
        vfs.put("resources/a.log", b"tracked before ignore");
        let _first = make_commit(&svc, "acct_dup", "main", "first").await;

        // Now ignore logs and commit a.log by name TWICE. It is both on disk
        // and in prev_tree, so each distinct path must contribute exactly one
        // `ignored` and one removal `changed`.
        vfs.put(OVGITIGNORE_PATH, b"*.log\n");
        let resp = svc
            .commit(req(
                "acct_dup",
                "main",
                "dup",
                Some(vec![
                    "resources/a.log".to_string(),
                    "resources/a.log".to_string(),
                ]),
            ))
            .await
            .unwrap();
        let (second_oid, ignored, changed) = match resp {
            CommitResponse::Created {
                commit_oid,
                ignored,
                changed,
            } => (commit_oid, ignored, changed),
            other => panic!("expected Created, got {other:?}"),
        };
        assert_eq!(ignored, 1, "duplicate ignored path counted once");
        assert_eq!(changed, 1, "duplicate removal counted once");

        let second_tree = commit_tree(
            object_store.as_ref() as &dyn ObjectStore,
            "acct_dup",
            second_oid,
        )
        .await;
        assert!(lookup(
            object_store.as_ref() as &dyn ObjectStore,
            "acct_dup",
            second_tree,
            "resources/a.log",
        )
        .await
        .unwrap()
        .is_none());
    }

    /// A scoped commit listing a *directory* must drop previously-tracked
    /// files under that directory that the new `.ovgitignore` now excludes.
    /// Regression: the directory branch's prev_tree loop only skipped such
    /// paths without removing them from the editor, so the stale ignored
    /// file lingered in the snapshot (the full-enumeration branch already
    /// removed them). Uses LocalFileSystem so the Directory branch runs.
    #[tokio::test]
    async fn test_commit_scoped_directory_removes_now_ignored_prev_file() {
        use crate::plugins::localfs::LocalFileSystem;

        let store_dir = tempfile::tempdir().unwrap();
        let object_store = Arc::new(LocalObjectStore::new(store_dir.path()));
        let ref_store = Arc::new(LocalRefStore::new(store_dir.path()));
        let work_dir = tempfile::tempdir().unwrap();
        let acct_root = work_dir.path().join("local").join("acct_dir_ignore");
        std::fs::create_dir_all(acct_root.join("docs")).unwrap();
        std::fs::write(acct_root.join("docs/keep.md"), b"keep").unwrap();
        std::fs::write(acct_root.join("docs/secret.log"), b"tracked before ignore").unwrap();
        let vfs: Arc<dyn FileSystem> =
            Arc::new(LocalFileSystem::new(work_dir.path().to_str().unwrap()).unwrap());
        let svc = GitService::new(vfs, object_store.clone(), ref_store);

        let first = make_commit(&svc, "acct_dir_ignore", "main", "first").await;
        let first_tree = commit_tree(
            object_store.as_ref() as &dyn ObjectStore,
            "acct_dir_ignore",
            first,
        )
        .await;
        assert!(lookup(
            object_store.as_ref() as &dyn ObjectStore,
            "acct_dir_ignore",
            first_tree,
            "docs/secret.log",
        )
        .await
        .unwrap()
        .is_some());

        // Add an ignore rule and commit ONLY the docs directory (scoped).
        std::fs::write(acct_root.join(OVGITIGNORE_PATH), b"*.log\n").unwrap();
        let resp = svc
            .commit(req(
                "acct_dir_ignore",
                "main",
                "scoped-ignore",
                Some(vec!["docs".into()]),
            ))
            .await
            .unwrap();
        let (second_oid, ignored) = match resp {
            CommitResponse::Created {
                commit_oid,
                ignored,
                ..
            } => (commit_oid, ignored),
            other => panic!("expected Created, got {other:?}"),
        };
        // secret.log is both on disk and in prev_tree; counted once.
        assert_eq!(ignored, 1);

        let second_tree = commit_tree(
            object_store.as_ref() as &dyn ObjectStore,
            "acct_dir_ignore",
            second_oid,
        )
        .await;
        assert!(lookup(
            object_store.as_ref() as &dyn ObjectStore,
            "acct_dir_ignore",
            second_tree,
            "docs/keep.md",
        )
        .await
        .unwrap()
        .is_some());
        assert!(lookup(
            object_store.as_ref() as &dyn ObjectStore,
            "acct_dir_ignore",
            second_tree,
            "docs/secret.log",
        )
        .await
        .unwrap()
        .is_none());
    }

    /// Mixing a file and a directory containing that file processes each
    /// candidate exactly once. The resulting commit must record exactly
    /// the directory's content, not double-process the listed file. Uses
    /// LocalFileSystem so the Directory branch actually runs.
    #[tokio::test]
    async fn test_commit_paths_mixed_file_and_dir_dedup() {
        use crate::plugins::localfs::LocalFileSystem;

        let store_dir = tempfile::tempdir().unwrap();
        let object_store = Arc::new(LocalObjectStore::new(store_dir.path()));
        let ref_store = Arc::new(LocalRefStore::new(store_dir.path()));
        let work_dir = tempfile::tempdir().unwrap();
        let acct_root = work_dir.path().join("local").join("acct");
        std::fs::create_dir_all(acct_root.join("docs")).unwrap();
        std::fs::write(acct_root.join("docs/a.md"), b"AA").unwrap();
        std::fs::write(acct_root.join("docs/b.md"), b"BB").unwrap();
        let vfs: Arc<dyn FileSystem> =
            Arc::new(LocalFileSystem::new(work_dir.path().to_str().unwrap()).unwrap());
        let svc = GitService::new(vfs, object_store, ref_store);

        let _ = make_commit(&svc, "acct", "main", "first").await;

        // Mutate one file, then commit with both an exact file path and
        // its parent directory.
        std::fs::write(acct_root.join("docs/a.md"), b"AA2").unwrap();
        let resp = svc
            .commit(req(
                "acct",
                "main",
                "mixed",
                Some(vec!["docs/a.md".into(), "docs".into()]),
            ))
            .await
            .unwrap();
        match resp {
            CommitResponse::Created { changed, .. } => {
                assert_eq!(changed, 1, "only docs/a.md content changed");
            }
            other => panic!("expected Created, got {other:?}"),
        }
    }

    // ── Fast Path 3: blob exists precheck ───────────────────────────────
    /// ObjectStore wrapper that counts `put` / `exists` calls, delegating to
    /// an inner `LocalObjectStore`.
    struct CountingObjectStore {
        inner: LocalObjectStore,
        puts: std::sync::atomic::AtomicUsize,
        exists_calls: std::sync::atomic::AtomicUsize,
    }

    #[async_trait]
    impl ObjectStore for CountingObjectStore {
        async fn put(
            &self,
            account: &str,
            oid: &ObjectId,
            zlib_body: bytes::Bytes,
        ) -> std::result::Result<(), ObjectStoreError> {
            self.puts.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            self.inner.put(account, oid, zlib_body).await
        }
        async fn get(
            &self,
            account: &str,
            oid: &ObjectId,
        ) -> std::result::Result<bytes::Bytes, ObjectStoreError> {
            self.inner.get(account, oid).await
        }
        async fn exists(
            &self,
            account: &str,
            oid: &ObjectId,
        ) -> std::result::Result<bool, ObjectStoreError> {
            self.exists_calls
                .fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            self.inner.exists(account, oid).await
        }
    }

    #[tokio::test]
    async fn test_commit_fast_path_3_skips_put_for_duplicate_blob() {
        use std::sync::atomic::Ordering;
        let dir = tempfile::tempdir().unwrap();
        let object_store = Arc::new(CountingObjectStore {
            inner: LocalObjectStore::new(dir.path()),
            puts: std::sync::atomic::AtomicUsize::new(0),
            exists_calls: std::sync::atomic::AtomicUsize::new(0),
        });
        let ref_store = Arc::new(LocalRefStore::new(dir.path()));
        let vfs = MockVfs::new("acct");
        // No index store → slow path runs → Fast Path 3 active (default on).
        let svc = GitService::new(
            vfs.clone() as Arc<dyn FileSystem>,
            object_store.clone() as Arc<dyn ObjectStore>,
            ref_store as Arc<dyn RefStore>,
        );

        vfs.put("a.md", b"dup");
        svc.commit(req("acct", "main", "first", None))
            .await
            .unwrap();

        // Commit a second file with identical content → same blob oid. The
        // blob `put` must be skipped (exists hit); only the new root tree and
        // commit object are written (2 puts, no blob put).
        vfs.put("b.md", b"dup");
        let puts_before = object_store.puts.load(Ordering::SeqCst);
        let exists_before = object_store.exists_calls.load(Ordering::SeqCst);
        svc.commit(req("acct", "main", "second", Some(vec!["b.md".into()])))
            .await
            .unwrap();
        let put_delta = object_store.puts.load(Ordering::SeqCst) - puts_before;

        // exists() was consulted on the second commit's slow path.
        assert!(object_store.exists_calls.load(Ordering::SeqCst) > exists_before);
        // Only tree + commit objects were put — the duplicate blob was skipped.
        assert_eq!(put_delta, 2, "duplicate blob must not be re-put");
    }

    #[tokio::test]
    async fn test_commit_fast_path_3_disabled_reputs_duplicate_blob() {
        use std::sync::atomic::Ordering;
        let dir = tempfile::tempdir().unwrap();
        let object_store = Arc::new(CountingObjectStore {
            inner: LocalObjectStore::new(dir.path()),
            puts: std::sync::atomic::AtomicUsize::new(0),
            exists_calls: std::sync::atomic::AtomicUsize::new(0),
        });
        let ref_store = Arc::new(LocalRefStore::new(dir.path()));
        let vfs = MockVfs::new("acct");
        let svc = GitService::new(
            vfs.clone() as Arc<dyn FileSystem>,
            object_store.clone() as Arc<dyn ObjectStore>,
            ref_store as Arc<dyn RefStore>,
        )
        .with_blob_exists_precheck(false);

        vfs.put("a.md", b"dup");
        svc.commit(req("acct", "main", "first", None))
            .await
            .unwrap();
        let exists_after_first = object_store.exists_calls.load(Ordering::SeqCst);

        vfs.put("b.md", b"dup");
        let puts_before = object_store.puts.load(Ordering::SeqCst);
        svc.commit(req("acct", "main", "second", Some(vec!["b.md".into()])))
            .await
            .unwrap();

        // With precheck off, the slow path calls write_object unconditionally
        // → at least one put for the dup blob (idempotent at the backend).
        assert!(object_store.puts.load(Ordering::SeqCst) > puts_before);
        // And no extra exists() calls were issued from the blob write path on
        // the second commit (precheck disabled). The backend's own put
        // idempotency uses try_exists internally, not ObjectStore::exists.
        assert_eq!(
            object_store.exists_calls.load(Ordering::SeqCst),
            exists_after_first
        );

        // Result correctness: both files resolve to the same content.
        let show_b = svc
            .show(ShowRequest {
                account: "acct".into(),
                target_ref: "main".into(),
                path: Some("b.md".into()),
            })
            .await
            .unwrap();
        match show_b {
            ShowResponse::Blob { bytes, .. } => assert_eq!(bytes.as_ref(), b"dup"),
            other => panic!("expected Blob, got {other:?}"),
        }
    }

    #[tokio::test]
    async fn test_commit_prev_tree_lookup_cache_amortises_ancestors() {
        // Build a prev tree where many candidates share the same depth-3
        // ancestor chain (root → resources → docs). Without the lookup cache,
        // each candidate would re-fetch all three trees from object_store on
        // its `lookup(prev_tree, ...)` call. With the cache, every ancestor
        // along that chain is fetched at most once for the whole commit.
        let dir = tempfile::tempdir().unwrap();
        let object_store = Arc::new(GetSpyObjectStore {
            inner: LocalObjectStore::new(dir.path()),
            gets: std::sync::Mutex::new(Vec::new()),
        });
        let ref_store = Arc::new(LocalRefStore::new(dir.path()));
        let vfs = MockVfs::new("acct");
        let svc = GitService::new(
            vfs.clone() as Arc<dyn FileSystem>,
            object_store.clone() as Arc<dyn ObjectStore>,
            ref_store as Arc<dyn RefStore>,
        );

        // First commit: seed prev_tree with 5 files all under resources/docs/.
        for name in ["a", "b", "c", "d", "e"] {
            vfs.put(&format!("resources/docs/{}.md", name), name.as_bytes());
        }
        svc.commit(req("acct", "main", "seed", None)).await.unwrap();

        // Capture root tree oid and its ancestor chain to resources/docs.
        let head_resp = svc
            .show(ShowRequest {
                account: "acct".into(),
                target_ref: "main".into(),
                path: None,
            })
            .await
            .unwrap();
        let root_tree_oid = match head_resp {
            ShowResponse::Commit { tree, .. } => tree,
            _ => panic!("expected commit"),
        };
        // Resolve resources/docs tree oid by walking root once.
        let mut cache = crate::git::tree_builder::TreeLookupCache::new();
        let (resources_oid, _) = crate::git::tree_builder::lookup_cached(
            object_store.as_ref(),
            "acct",
            root_tree_oid,
            "resources",
            &mut cache,
        )
        .await
        .unwrap()
        .unwrap();
        let (docs_oid, _) = crate::git::tree_builder::lookup_cached(
            object_store.as_ref(),
            "acct",
            root_tree_oid,
            "resources/docs",
            &mut cache,
        )
        .await
        .unwrap()
        .unwrap();

        // Reset spy, then run a commit that touches all 5 candidates with the
        // same content (so every Fast Path 1 miss path runs the prev lookup
        // for the no-op skip check). The assertion proves each ancestor tree
        // is fetched at most once across all 5 lookups.
        object_store.reset();
        let candidates: Vec<String> = ["a", "b", "c", "d", "e"]
            .iter()
            .map(|n| format!("resources/docs/{}.md", n))
            .collect();
        svc.commit(req("acct", "main", "rewrite", Some(candidates)))
            .await
            .unwrap();

        // Each ancestor on the prev_tree chain was fetched at most once.
        assert!(
            object_store.count_gets(&root_tree_oid) <= 1,
            "root tree was fetched {} times, expected ≤1 (cache miss)",
            object_store.count_gets(&root_tree_oid)
        );
        assert!(
            object_store.count_gets(&resources_oid) <= 1,
            "resources tree was fetched {} times, expected ≤1 (cache miss)",
            object_store.count_gets(&resources_oid)
        );
        assert!(
            object_store.count_gets(&docs_oid) <= 1,
            "resources/docs tree was fetched {} times, expected ≤1 (cache miss)",
            object_store.count_gets(&docs_oid)
        );
    }
