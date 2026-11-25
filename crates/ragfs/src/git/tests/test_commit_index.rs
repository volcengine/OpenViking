#[tokio::test]
async fn commit_index_append_and_delete_lifecycle() {
    use crate::git::snapshot_log::CommitIndexStore;
    let store = make_sqlite_snapshot_log_store().await;
    let oids = [1, 2, 3, 4].map(|n| format!("{n:040x}"));
    let [a, b, c, d] = &oids;
    for (i, oid) in oids[..3].iter().enumerate() {
        let parent = i.checked_sub(1).map(|p| oids[p].as_str());
        assert_eq!(
            store
                .append_commit_if_latest("acct", "main", parent, oid, a, 50)
                .await
                .unwrap(),
            i as i64 + 1
        );
    }
    assert!(store
        .append_commit_if_latest("acct", "main", Some(a), d, a, 99)
        .await
        .unwrap_err()
        .is_stale_latest());
    store
        .append_commit_if_latest("acct", "other", None, d, a, 50)
        .await
        .unwrap();
    store
        .mark_deleted("acct", std::slice::from_ref(b), 10)
        .await
        .unwrap();
    assert_eq!(
        store
            .prev_live("acct", "main", 3)
            .await
            .unwrap()
            .unwrap()
            .commit_oid,
        *a
    );
    assert_eq!(
        store
            .next_live("acct", "main", 1)
            .await
            .unwrap()
            .unwrap()
            .commit_oid,
        *c
    );
    store
        .purge_commits("acct", std::slice::from_ref(b))
        .await
        .unwrap();
    assert!(store.get_commit("acct", b).await.unwrap().is_none());
    assert_eq!(store.list_branch("acct", "main").await.unwrap().len(), 2);
    CommitIndexStore::clear_branch(store.as_ref(), "acct", "main")
        .await
        .unwrap();
    assert_eq!(store.list_branch("acct", "other").await.unwrap().len(), 1);
}

#[tokio::test]
async fn commit_and_restore_append_commit_index_rows() {
    use crate::git::CommitIndexStore;
    let (_dir, vfs, objects, _, svc) = make_service("acct_index");
    let store = make_sqlite_snapshot_log_store().await;
    let svc = svc.with_commit_index(Some(store.clone()));
    vfs.put("a", b"one");
    let a = make_commit(&svc, "acct_index", "main", "first").await;
    vfs.put("a", b"two");
    let b = make_commit(&svc, "acct_index", "main", "second").await;
    let restored = svc
        .restore(RestoreRequest {
            account: "acct_index".into(),
            branch: "main".into(),
            source_commit: a.to_string(),
            project_dir: None,
            allow_missing_source: false,
            dry_run: false,
            message: None,
            author_name: "tester".into(),
            author_email: "t@local".into(),
        })
        .await
        .unwrap();
    let RestoreResponse::Applied {
        new_commit_oid: c, ..
    } = restored
    else {
        panic!("expected restore commit")
    };
    let rows = store.list_branch("acct_index", "main").await.unwrap();
    assert_eq!(rows.len(), 3);
    for (i, (row, oid)) in rows.iter().zip([a, b, c]).enumerate() {
        assert_eq!(row.seq, i as i64 + 1);
        assert_eq!(row.commit_oid, oid.to_string());
        assert_eq!(
            row.tree_oid,
            commit_tree(objects.as_ref(), "acct_index", oid)
                .await
                .to_string()
        );
    }
}

#[tokio::test]
async fn commit_index_backfill_skips_grafted_commits() {
    use crate::git::{CommitIndexStore, DeleteRequest};
    let (_dir, vfs, _, _, svc) = make_service("acct_index");
    let store = make_sqlite_snapshot_log_store().await;
    let svc = svc.with_commit_index(Some(store.clone()));
    let mut history = Vec::new();
    for i in 0..4 {
        vfs.put("a", &[i]);
        history.push(make_commit(&svc, "acct_index", "main", &format!("{i}")).await);
    }
    svc.delete_commits(DeleteRequest {
        account: "acct_index".into(),
        commit_ids: vec![history[1].to_string()],
        dry_run: false,
        expected_overlay_root: None,
    })
    .await
    .unwrap();
    svc.invalidate_commit_index("acct_index", "main").await;
    tokio::time::timeout(std::time::Duration::from_secs(5), async {
        loop {
            let rows = store.list_branch("acct_index", "main").await.unwrap();
            if rows.len() == 3 {
                assert_eq!(
                    rows.iter()
                        .map(|r| r.commit_oid.clone())
                        .collect::<Vec<_>>(),
                    [history[0], history[2], history[3]].map(|id| id.to_string())
                );
                assert_eq!(rows[1].parent_oid, Some(history[1].to_string()));
                break;
            }
            tokio::time::sleep(std::time::Duration::from_millis(10)).await;
        }
    })
    .await
    .unwrap();
}

async fn open_test_commit_index(
    dir: &std::path::Path,
) -> (Arc<SqliteSnapshotLogStore>, sqlx::SqlitePool) {
    let db = dir.join("history.db");
    let store = SqliteSnapshotLogStore::connect(db.to_str().unwrap())
        .await
        .unwrap();
    let pool = sqlx::SqlitePool::connect(&format!("sqlite://{}", db.display()))
        .await
        .unwrap();
    (Arc::new(store), pool)
}

#[tokio::test]
async fn commit_index_rejects_corrupt_tree_even_when_parent_chain_is_valid() {
    use crate::git::CommitIndexStore;
    let dir = tempfile::tempdir().unwrap();
    let (store, pool) = open_test_commit_index(dir.path()).await;
    let oid = format!("{:040x}", 1);
    let tree = format!("{:040x}", 2);
    store
        .append_commit_if_latest("acct", "main", None, &oid, &tree, 99)
        .await
        .unwrap();
    sqlx::query("UPDATE snapshot_commit SET tree_oid=? WHERE commit_oid=?")
        .bind(&oid)
        .bind(&oid)
        .execute(&pool)
        .await
        .unwrap();
    assert!(store.list_branch("acct", "main").await.is_err());
    assert!(store.get_commit("acct", &oid).await.is_err());
    assert!(store.latest_commit("acct", "main").await.is_err());
    assert!(store.prev_live("acct", "main", 2).await.is_err());
    assert!(store.next_live("acct", "main", 0).await.is_err());
}
