use crate::git::snapshot_log::{SnapshotLogRow, SnapshotLogStore, SqliteSnapshotLogStore};

async fn make_snapshot_log_store() -> Arc<dyn SnapshotLogStore> {
    // The same commit/restore/tail-backfill regressions run against PostgreSQL
    // in the database CI job; absent an explicit DSN, retain SQLite coverage.
    #[cfg(feature = "postgres")]
    if let Ok(dsn) = std::env::var("SNAPSHOT_LOG_TEST_PG_DSN") {
        return Arc::new(
            crate::git::PostgresSnapshotLogStore::connect(&dsn, 4, 5_000)
                .await
                .expect("PostgreSQL SnapshotLog schema must be provisioned"),
        );
    }
    make_sqlite_snapshot_log_store().await
}

async fn make_sqlite_snapshot_log_store() -> Arc<SqliteSnapshotLogStore> {
    let dir = tempfile::tempdir().unwrap().keep();
    let db_path = dir.join("snapshot-log.db");
    Arc::new(
        SqliteSnapshotLogStore::connect(db_path.to_str().unwrap())
            .await
            .unwrap(),
    )
}

#[tokio::test]
async fn test_snapshot_log_commit_indexes_file_and_directory_paths() {
    let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct_snapshot_log");
    let store = make_snapshot_log_store().await;
    let svc = svc.with_snapshot_log(Some(store));

    vfs.put("resources/a.md", b"v1");
    let c1 = make_commit(&svc, "acct_snapshot_log", "main", "first").await;
    vfs.put("resources/a.md", b"v2");
    let c2 = make_commit(&svc, "acct_snapshot_log", "main", "second").await;

    let file_log = svc
        .log(LogRequest {
            account: "acct_snapshot_log".into(),
            branch: "main".into(),
            limit: 10,
            offset: 0,
            paths: Some(vec!["resources/a.md".into()]),
        })
        .await
        .unwrap();
    let file_oids: Vec<ObjectId> = file_log.into_iter().map(|entry| entry.oid).collect();
    assert_eq!(file_oids, vec![c2, c1]);

    let dir_log = svc
        .log(LogRequest {
            account: "acct_snapshot_log".into(),
            branch: "main".into(),
            limit: 10,
            offset: 0,
            paths: Some(vec!["resources".into()]),
        })
        .await
        .unwrap();
    let dir_oids: Vec<ObjectId> = dir_log.into_iter().map(|entry| entry.oid).collect();
    assert_eq!(dir_oids, vec![c2, c1]);
}

/// Verifies file-over-directory replacement indexes deleted descendant paths.
#[tokio::test]
async fn test_snapshot_log_file_replacing_directory_indexes_descendant_deletes() {
    let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct_snapshot_replace");
    let store = make_snapshot_log_store().await;
    let svc = svc.with_snapshot_log(Some(store));

    vfs.put("docs/old.md", b"v1");
    let c1 = make_commit(&svc, "acct_snapshot_replace", "main", "first").await;

    vfs.delete("docs/old.md");
    vfs.put("docs", b"now a file");
    let c2 = match svc
        .commit(req(
            "acct_snapshot_replace",
            "main",
            "replace directory with file",
            Some(vec!["docs".into()]),
        ))
        .await
        .unwrap()
    {
        CommitResponse::Created { commit_oid, .. } => commit_oid,
        other => panic!("expected Created, got {other:?}"),
    };

    let log = svc
        .log(LogRequest {
            account: "acct_snapshot_replace".into(),
            branch: "main".into(),
            limit: 10,
            offset: 0,
            paths: Some(vec!["docs/old.md".into()]),
        })
        .await
        .unwrap();
    let oids: Vec<ObjectId> = log.into_iter().map(|entry| entry.oid).collect();
    assert_eq!(oids, vec![c2, c1]);
}

/// Verify filtered pages across a fixed SQLite boundary and reject unrepresentable offsets.
#[tokio::test]
async fn test_snapshot_log_tail_scan_merges_when_latest_is_behind_head() {
    let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct_snapshot_tail");
    let store = make_snapshot_log_store().await;
    let mut svc = svc.with_snapshot_log(Some(store.clone()));
    vfs.put("resources/a.md", b"v1");
    let c1 = make_commit(&svc, "acct_snapshot_tail", "main", "first").await;
    vfs.put("resources/a.md", b"v2");
    let c2 = make_commit(&svc, "acct_snapshot_tail", "main", "second").await;

    svc.snapshot_log_store = None;
    vfs.put("resources/other.md", b"other-v1");
    make_commit(&svc, "acct_snapshot_tail", "main", "unrelated").await;
    vfs.put("resources/a.md", b"v3");
    let c3 = make_commit(&svc, "acct_snapshot_tail", "main", "third").await;
    vfs.put("resources/other.md", b"other-v2");
    make_commit(&svc, "acct_snapshot_tail", "main", "unrelated again").await;
    vfs.put("resources/a.md", b"v4");
    let c4 = make_commit(&svc, "acct_snapshot_tail", "main", "fourth").await;
    svc.snapshot_log_store = Some(store);
    // Keep the indexed boundary at c2 while verifying each page.
    svc.snapshot_log_backfills
        .lock()
        .unwrap()
        .insert(("acct_snapshot_tail".into(), "main".into()));

    let request = LogRequest {
        account: "acct_snapshot_tail".into(),
        branch: "main".into(),
        limit: 2,
        offset: 0,
        paths: Some(vec!["resources/a.md".into()]),
    };
    for (offset, expected) in [
        (0, vec![c4, c3]),
        (1, vec![c3, c2]),
        (2, vec![c2, c1]),
        (3, vec![c1]),
        (4, vec![]),
    ] {
        let log = svc
            .log(LogRequest {
                offset,
                ..request.clone()
            })
            .await
            .unwrap();
        let oids: Vec<ObjectId> = log.into_iter().map(|entry| entry.oid).collect();
        assert_eq!(oids, expected, "offset={offset}");
    }
    #[cfg(target_pointer_width = "64")]
    {
        let err = svc
            .log(LogRequest {
                offset: i64::MAX as usize + 1,
                ..request
            })
            .await
            .unwrap_err();
        assert!(matches!(err, GitError::InvalidLogOffset(_)));
    }
}

#[tokio::test]
async fn test_snapshot_log_restore_indexes_project_dir_relative_paths() {
    let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct_snapshot_restore");
    let store = make_snapshot_log_store().await;
    let svc = svc.with_snapshot_log(Some(store));

    vfs.put("proj/a.md", b"v1");
    let c1 = make_commit(&svc, "acct_snapshot_restore", "main", "first").await;
    vfs.put("proj/a.md", b"v2");
    let c2 = make_commit(&svc, "acct_snapshot_restore", "main", "second").await;

    let restored = svc
        .restore(RestoreRequest {
            account: "acct_snapshot_restore".into(),
            branch: "main".into(),
            project_dir: Some("proj".into()),
            source_commit: c1.to_hex().to_string(),
            allow_missing_source: false,
            dry_run: false,
            message: None,
            author_name: "tester".into(),
            author_email: "tester@example.com".into(),
        })
        .await
        .unwrap();
    let c3 = match restored {
        RestoreResponse::Applied { new_commit_oid, .. } => new_commit_oid,
        other => panic!("expected Applied, got {other:?}"),
    };

    let log = svc
        .log(LogRequest {
            account: "acct_snapshot_restore".into(),
            branch: "main".into(),
            limit: 10,
            offset: 0,
            paths: Some(vec!["proj/a.md".into()]),
        })
        .await
        .unwrap();
    let oids: Vec<ObjectId> = log.into_iter().map(|entry| entry.oid).collect();
    assert_eq!(oids, vec![c3, c2, c1]);
}

#[cfg(feature = "postgres")]
#[tokio::test]
async fn test_snapshot_log_postgres_restore_completes_with_locked_index_and_backfills() {
    let Ok(dsn) = std::env::var("SNAPSHOT_LOG_TEST_PG_DSN") else {
        return;
    };
    let account = uuid::Uuid::new_v4().simple().to_string();
    let (_dir, vfs, _object_store, _ref_store, svc) = make_service(&account);
    let store = Arc::new(
        crate::git::PostgresSnapshotLogStore::connect_with_timeout(&dsn, 2, 5_000, 200)
            .await
            .unwrap(),
    );
    let svc = svc.with_snapshot_log(Some(store.clone()));
    vfs.put("proj/a.md", b"v1");
    let c1 = make_commit(&svc, &account, "main", "first").await;
    vfs.put("proj/a.md", b"v2");
    let c2 = make_commit(&svc, &account, "main", "second").await;

    let pool = sqlx::postgres::PgPoolOptions::new()
        .max_connections(1)
        .connect(&dsn)
        .await
        .unwrap();
    let mut lock = pool.begin().await.unwrap();
    sqlx::query("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))")
        .bind(format!("{}:{}main", account.len(), account))
        .execute(&mut *lock)
        .await
        .unwrap();
    let response = tokio::time::timeout(
        std::time::Duration::from_secs(2),
        svc.restore(RestoreRequest {
            account: account.clone(),
            branch: "main".into(),
            project_dir: Some("proj".into()),
            source_commit: c1.to_hex().to_string(),
            allow_missing_source: false,
            dry_run: false,
            message: None,
            author_name: "tester".into(),
            author_email: "tester@example.com".into(),
        }),
    )
    .await
    .expect("restore must complete without releasing the index lock")
    .unwrap();
    let c3 = match response {
        RestoreResponse::Applied { new_commit_oid, .. } => new_commit_oid,
        other => panic!("expected Applied, got {other:?}"),
    };
    assert_eq!(
        vfs.read(&format!("/local/{account}/proj/a.md"), 0, u64::MAX)
            .await
            .unwrap(),
        b"v1",
    );
    let request = LogRequest {
        account: account.clone(),
        branch: "main".into(),
        limit: 10,
        offset: 0,
        paths: Some(vec!["proj/a.md".into()]),
    };
    // Missing index rows do not hide the restored commit: the Git tail scan wins.
    let log = svc.log(request.clone()).await.unwrap();
    assert_eq!(
        log.into_iter().map(|entry| entry.oid).collect::<Vec<_>>(),
        vec![c3, c2, c1]
    );
    lock.rollback().await.unwrap();
    tokio::time::timeout(std::time::Duration::from_secs(3), async {
        loop {
            if store
                .load_latest_indexed_commit(&account, "main")
                .await
                .unwrap()
                .is_some_and(|(_, oid)| oid == c3.to_hex().to_string())
            {
                break;
            }
            // Also retries backfill if the first background attempt timed out.
            svc.log(request.clone()).await.unwrap();
            tokio::time::sleep(std::time::Duration::from_millis(25)).await;
        }
    })
    .await
    .expect("index must catch up after the conflicting lock is released");
    sqlx::query("DELETE FROM snapshot_log WHERE account = $1")
        .bind(&account)
        .execute(&pool)
        .await
        .unwrap();
    pool.close().await;
}

#[tokio::test]
async fn test_snapshot_log_store_rejects_non_root_commit_when_empty() {
    let store = make_snapshot_log_store().await;
    let err = store
        .append_rows_if_latest(
            "acct_snapshot_empty",
            "main",
            Some("1111111111111111111111111111111111111111"),
            &[SnapshotLogRow {
                path: "/".into(),
                commit_oid: "2222222222222222222222222222222222222222".into(),
            }],
        )
        .await
        .unwrap_err();

    assert!(err.is_stale_latest());
    assert!(store
        .load_latest_indexed_commit("acct_snapshot_empty", "main")
        .await
        .unwrap()
        .is_none());
}

#[tokio::test]
async fn log_index_matches_scan_after_middle_and_root_delete() {
    use crate::git::DeleteRequest;
    let account = "acct_log_delete";
    let (_dir, vfs, _, _, svc) = make_service(account);
    let store = make_snapshot_log_store().await;
    let svc = svc.with_snapshot_log(Some(store.clone()));
    vfs.put("dir/root-only", b"constant");
    vfs.put("dir/reverted", b"0");
    let a = make_commit(&svc, account, "main", "first").await;
    vfs.put("dir/reverted", b"1");
    vfs.put("dir/b-only", b"1");
    let b = make_commit(&svc, account, "main", "second").await;
    vfs.put("dir/reverted", b"0");
    let c = make_commit(&svc, account, "main", "third").await;
    vfs.put("other", b"1");
    let _d = make_commit(&svc, account, "main", "fourth").await;

    for target in [b, a] {
        svc.delete_commits(DeleteRequest {
            account: account.into(),
            commit_ids: vec![target.to_string()],
            dry_run: false,
            expected_overlay_root: None,
        })
        .await
        .unwrap();
        let overlay = svc.overlay(account).await.unwrap();
        for path in [
            "dir/root-only",
            "dir/b-only",
            "dir/reverted",
            "dir",
            "other",
        ] {
            let req = LogRequest {
                account: account.into(),
                branch: "main".into(),
                limit: 10,
                offset: 0,
                paths: Some(vec![path.into()]),
            };
            let indexed = svc
                .log_with_snapshot_log(&req, &overlay)
                .await
                .unwrap()
                .expect("repaired SQL fast path");
            let indexed: Vec<_> = indexed.into_iter().map(|e| e.oid).collect();
            let scanned: Vec<_> = svc
                .log_with_scan_limit(req, 1000)
                .await
                .unwrap()
                .into_iter()
                .map(|e| e.oid)
                .collect();
            assert_eq!(indexed, scanned, "{path} after deleting {target}");
            if target == a && path == "dir/root-only" {
                assert_eq!(indexed, vec![c]);
            }
        }
    }
}

#[tokio::test]
async fn snapshot_log_rebuilds_when_another_node_deletes() {
    use crate::git::DeleteRequest;
    let account = "acct_log_stale";
    let (_dir, vfs, _, _, svc) = make_service(account);
    let svc = svc.with_snapshot_log(Some(make_snapshot_log_store().await));
    vfs.put("root-only", b"constant");
    let a = make_commit(&svc, account, "main", "first").await;
    vfs.put("other", b"1");
    let b = make_commit(&svc, account, "main", "second").await;
    svc.clone()
        .with_snapshot_log(None)
        .delete_commits(DeleteRequest {
            account: account.into(),
            commit_ids: vec![a.to_string()],
            dry_run: false,
            expected_overlay_root: None,
        })
        .await
        .unwrap();
    let req = LogRequest {
        account: account.into(),
        branch: "main".into(),
        limit: 10,
        offset: 0,
        paths: Some(vec!["root-only".into()]),
    };
    assert_eq!(svc.log(req.clone()).await.unwrap()[0].oid, b);
    let overlay = svc.overlay(account).await.unwrap();
    tokio::time::timeout(std::time::Duration::from_secs(5), async {
        loop {
            if let Some(hits) = svc.log_with_snapshot_log(&req, &overlay).await.unwrap() {
                assert_eq!(hits.iter().map(|e| e.oid).collect::<Vec<_>>(), vec![b]);
                break;
            }
            tokio::time::sleep(std::time::Duration::from_millis(10)).await;
        }
    })
    .await
    .unwrap();
}

#[tokio::test]
async fn reparent_incomplete_rolls_back_partial_repairs() {
    let store = make_snapshot_log_store().await;
    let a = format!("{:040x}", 1);
    let b = format!("{:040x}", 2);
    let rows = |oid: &String, paths: &[&str]| -> Vec<_> {
        paths
            .iter()
            .map(|p| SnapshotLogRow {
                path: p.to_string(),
                commit_oid: oid.clone(),
            })
            .collect()
    };
    store
        .append_rows_if_latest("acct_repair_rollback", "main", None, &rows(&a, &["/", "old"]))
        .await
        .unwrap();
    store
        .append_rows_if_latest("acct_repair_rollback", "main", Some(&a), &rows(&b, &["/", "keep", "drop"]))
        .await
        .unwrap();
    let keep_paths = ["/", "old", "keep", "missing"].map(str::to_string);
    assert!(!store
        .reparent_after_delete("acct_repair_rollback", "main", std::slice::from_ref(&a), &b, &keep_paths)
        .await
        .unwrap());
    let hits = store
        .query_path_commits("acct_repair_rollback", "main", &["drop".into()], i64::MAX, 0, 10)
        .await
        .unwrap();
    assert_eq!(hits[0].commit_oid, b);
    let hits = store
        .query_path_commits("acct_repair_rollback", "main", &["old".into()], i64::MAX, 0, 10)
        .await
        .unwrap();
    assert_eq!(hits[0].commit_oid, a);
}

#[tokio::test]
async fn snapshot_log_stays_current_across_gc_and_subsequent_delete() {
    use std::time::{Duration, SystemTime};
    let account = "acct";
    let (_dir, vfs, _, _, svc) = make_service(account);
    let store = make_snapshot_log_store().await;
    let svc = svc.with_snapshot_log(Some(store.clone()));
    let ids = delete_chain(&svc, &vfs, 4).await;
    let first = svc
        .delete_commits(deletion(&ids[1..2], false))
        .await
        .unwrap();
    let grace = Duration::from_secs(3600);
    let gc = svc
        .gc_at(account, SystemTime::now() + grace * 2, grace)
        .await
        .unwrap();
    assert_eq!(gc.tombstones_purged, 1);
    let overlay = svc.overlay(account).await.unwrap();
    assert_ne!(overlay.root_oid, first.overlay_root);
    let req = LogRequest {
        account: account.into(),
        branch: "main".into(),
        paths: Some(vec!["resources/file".into()]),
        limit: 10,
        offset: 0,
    };
    assert!(
        svc.log_with_snapshot_log(&req, &overlay)
            .await
            .unwrap()
            .is_some(),
        "purging invisible commits must preserve the SQL fast path"
    );
    assert_eq!(
        store.generation(account, "main").await.unwrap(),
        first.overlay_root.unwrap().to_string()
    );
    let second = svc
        .delete_commits(deletion(&ids[2..3], false))
        .await
        .unwrap();
    assert_eq!(
        store.generation(account, "main").await.unwrap(),
        second.overlay_root.unwrap().to_string()
    );
    let hits = svc
        .log_with_snapshot_log(&req, &svc.overlay(account).await.unwrap())
        .await
        .unwrap()
        .unwrap();
    assert_eq!(
        hits.into_iter().map(|h| h.oid).collect::<Vec<_>>(),
        vec![ids[3], ids[0]]
    );
}
