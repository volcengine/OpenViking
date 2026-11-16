use crate::git::snapshot_log::{SnapshotLogRow, SnapshotLogStore, SqliteSnapshotLogStore};

async fn make_snapshot_log_store() -> Arc<SqliteSnapshotLogStore> {
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
            paths: Some(vec!["docs/old.md".into()]),
        })
        .await
        .unwrap();
    let oids: Vec<ObjectId> = log.into_iter().map(|entry| entry.oid).collect();
    assert_eq!(oids, vec![c2, c1]);
}

#[tokio::test]
async fn test_snapshot_log_tail_scan_merges_when_latest_is_behind_head() {
    let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct_snapshot_tail");
    vfs.put("resources/a.md", b"v1");
    let c1 = make_commit(&svc, "acct_snapshot_tail", "main", "first").await;
    vfs.put("resources/a.md", b"v2");
    let c2 = make_commit(&svc, "acct_snapshot_tail", "main", "second").await;

    let store = make_snapshot_log_store().await;
    store
        .append_rows_if_latest(
            "acct_snapshot_tail",
            "main",
            None,
            &[
                SnapshotLogRow {
                    path: "/".into(),
                    commit_oid: c1.to_hex().to_string(),
                },
                SnapshotLogRow {
                    path: "resources".into(),
                    commit_oid: c1.to_hex().to_string(),
                },
                SnapshotLogRow {
                    path: "resources/a.md".into(),
                    commit_oid: c1.to_hex().to_string(),
                },
            ],
        )
        .await
        .unwrap();

    let svc = svc.with_snapshot_log(Some(store));
    let log = svc
        .log(LogRequest {
            account: "acct_snapshot_tail".into(),
            branch: "main".into(),
            limit: 10,
            paths: Some(vec!["resources/a.md".into()]),
        })
        .await
        .unwrap();
    let oids: Vec<ObjectId> = log.into_iter().map(|entry| entry.oid).collect();
    assert_eq!(oids, vec![c2, c1]);
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
            paths: Some(vec!["proj/a.md".into()]),
        })
        .await
        .unwrap();
    let oids: Vec<ObjectId> = log.into_iter().map(|entry| entry.oid).collect();
    assert_eq!(oids, vec![c3, c2, c1]);
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
    assert!(
        store
            .load_latest_indexed_commit("acct_snapshot_empty", "main")
            .await
            .unwrap()
            .is_none()
    );
}
