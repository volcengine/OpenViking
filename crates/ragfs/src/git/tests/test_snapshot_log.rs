use crate::git::snapshot_log::{SnapshotLogRow, SnapshotLogStore, SqliteSnapshotLogStore};

async fn make_snapshot_log_store() -> Arc<dyn SnapshotLogStore> {
    // The same commit/restore/tail-backfill regressions run against PostgreSQL
    // in the database CI job; absent an explicit DSN, retain SQLite coverage.
    #[cfg(feature = "postgres")]
    if let Ok(dsn) = std::env::var("SNAPSHOT_LOG_TEST_PG_DSN") {
        return Arc::new(
            crate::git::PostgresSnapshotLogStore::connect("test-service", &dsn, 4, 5_000)
                .await
                .expect("PostgreSQL SnapshotLog schema must be provisioned"),
        );
    }
    let dir = tempfile::tempdir().unwrap().keep();
    let db_path = dir.join("snapshot-log.db");
    Arc::new(
        SqliteSnapshotLogStore::connect("test-service", db_path.to_str().unwrap())
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
        crate::git::PostgresSnapshotLogStore::connect_with_timeout(
            "test-service", &dsn, 2, 5_000, 200,
        )
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
        .bind(format!("12:test-service|{}:{}main", account.len(), account))
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

/// Verify two services sharing one SQLite file keep independent index progress.
#[tokio::test]
async fn test_snapshot_log_service_name_isolates_shared_sqlite_db() {
    let dir = tempfile::tempdir().unwrap().keep();
    let path = dir.join("snapshot-log-isolation.db").to_str().unwrap().to_string();

    let store_a = SqliteSnapshotLogStore::connect("svc-a", &path).await.unwrap();
    let store_b = SqliteSnapshotLogStore::connect("svc-b", &path).await.unwrap();

    let root_row = vec![SnapshotLogRow {
        path: "/".into(),
        commit_oid: "1111111111111111111111111111111111111111".into(),
    }];
    store_a
        .append_rows_if_latest("acct", "main", None, &root_row)
        .await
        .unwrap();

    // service b must not see service a's indexed progress.
    assert!(store_b
        .load_latest_indexed_commit("acct", "main")
        .await
        .unwrap()
        .is_none());
    assert!(store_a
        .load_latest_indexed_commit("acct", "main")
        .await
        .unwrap()
        .is_some());

    // The same account/branch/commit under another service is not a unique-key conflict.
    store_b
        .append_rows_if_latest("acct", "main", None, &root_row)
        .await
        .unwrap();

    // Illegal service_name values are rejected before touching the database.
    assert!(SqliteSnapshotLogStore::connect("bad name", &path)
        .await
        .is_err());
    assert!(SqliteSnapshotLogStore::connect("", &path).await.is_err());
}
