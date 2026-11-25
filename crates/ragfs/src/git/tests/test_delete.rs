use crate::git::DeleteRequest;

fn deletion(ids: &[ObjectId], dry_run: bool) -> DeleteRequest {
    DeleteRequest {
        account: "acct".into(),
        commit_ids: ids.iter().map(ToString::to_string).collect(),
        dry_run,
        expected_overlay_root: None,
    }
}
async fn delete_chain(svc: &GitService, vfs: &MockVfs, n: usize) -> Vec<ObjectId> {
    let mut commits = Vec::new();
    for i in 0..n {
        vfs.put("resources/file", i.to_string().as_bytes());
        commits.push(make_commit(svc, "acct", "main", &i.to_string()).await);
    }
    commits
}
async fn delete_log(svc: &GitService) -> Vec<ObjectId> {
    svc.log(LogRequest {
        account: "acct".into(),
        branch: "main".into(),
        limit: 100,
        offset: 0,
        paths: None,
    })
    .await
    .unwrap()
    .into_iter()
    .map(|entry| entry.oid)
    .collect()
}

#[tokio::test]
async fn delete_contiguous_range_single_graft() {
    let (_dir, vfs, _, _, svc) = make_service("acct");
    let ids = delete_chain(&svc, &vfs, 6).await;
    let r = svc
        .delete_commits(deletion(&[ids[3], ids[1], ids[2]], false))
        .await
        .unwrap();
    assert_eq!(r.reparented, vec![(ids[4], vec![ids[0]])]);
    assert_eq!(delete_log(&svc).await, vec![ids[5], ids[4], ids[0]]);
    let r = svc
        .delete_commits(deletion(&ids[..1], false))
        .await
        .unwrap();
    assert_eq!(r.reparented, vec![(ids[4], vec![])]);
    assert!(matches!(
        svc.show(ShowRequest {
            account: "acct".into(),
            target_ref: ids[2].to_string(),
            path: None
        })
        .await,
        Err(GitError::CommitNotFound(_))
    ));
}

#[tokio::test]
async fn delete_with_stale_expected_root_conflicts() {
    let (_dir, vfs, _, _, svc) = make_service("acct");
    let ids = delete_chain(&svc, &vfs, 4).await;
    let mut request = deletion(&ids[..1], true);
    let plan = svc.delete_commits(request.clone()).await.unwrap();
    assert_eq!(plan.plan[0].changed_paths, vec!["resources/file"]);
    assert!(plan.overlay_root.is_none());
    svc.delete_commits(deletion(&ids[1..2], false))
        .await
        .unwrap();
    request.expected_overlay_root = Some(plan.overlay_root);
    request.dry_run = false;
    assert!(matches!(
        svc.delete_commits(request).await,
        Err(GitError::OverlayConflict)
    ));
}

#[tokio::test]
async fn delete_unknown_or_deleted_is_not_found_atomically() {
    let (_dir, vfs, _, _, svc) = make_service("acct");
    let ids = delete_chain(&svc, &vfs, 3).await;
    for id in [ids[2], ObjectId::null(gix_hash::Kind::Sha1)] {
        assert!(svc
            .delete_commits(deletion(&[ids[0], id], false))
            .await
            .is_err());
        assert!(svc.overlay("acct").await.unwrap().root_oid.is_none());
    }
    let mut request = deletion(&ids[..1], false);
    request.commit_ids.push(ids[0].to_string().to_uppercase());
    assert_eq!(
        svc.delete_commits(request.clone()).await.unwrap().deleted,
        vec![ids[0]]
    );
    assert!(matches!(
        svc.delete_commits(request).await,
        Err(GitError::CommitNotFound(_))
    ));
}

#[tokio::test]
async fn delete_concurrent_with_commit_keeps_chain_consistent() {
    let (_dir, vfs, _, _, svc) = make_service("acct");
    let index = make_sqlite_snapshot_log_store().await;
    let svc = svc.with_commit_index(Some(index.clone()));
    let ids = delete_chain(&svc, &vfs, 3).await;
    let mut req = deletion(&ids[1..2], true);
    let planned = svc.delete_commits(req.clone()).await.unwrap();
    vfs.put("resources/new", b"new");
    let fourth = make_commit(&svc, "acct", "main", "fourth").await;
    req.expected_overlay_root = Some(planned.overlay_root);
    req.dry_run = false;
    let response = svc.delete_commits(req).await.unwrap();
    assert_eq!(delete_log(&svc).await, vec![fourth, ids[2], ids[0]]);
    assert_eq!(
        crate::git::CommitIndexStore::get_commit(index.as_ref(), "acct", &ids[1].to_string())
            .await
            .unwrap()
            .unwrap()
            .deleted_at,
        Some(response.deleted_at)
    );
}

#[tokio::test]
async fn delete_preserves_sibling_branch_children_and_tag_tips() {
    let (_dir, vfs, _, refs, svc) = make_service("acct");
    let ids = delete_chain(&svc, &vfs, 3).await;
    refs.cas_update("acct", "refs/heads/topic", None, ids[1])
        .await
        .unwrap();
    assert!(matches!(
        svc.delete_commits(deletion(&ids[1..2], false)).await,
        Err(GitError::CommitIsRefTip { .. })
    ));
    vfs.put("topic", b"d");
    let d = make_commit(&svc, "acct", "topic", "topic").await;
    refs.cas_update("acct", "refs/tags/base", None, ids[0])
        .await
        .unwrap();
    assert!(matches!(
        svc.delete_commits(deletion(&ids[..1], false)).await,
        Err(GitError::CommitIsRefTip { .. })
    ));
    let response = svc
        .delete_commits(deletion(&ids[1..2], false))
        .await
        .unwrap();
    for child in [d, ids[2]] {
        assert!(response.reparented.contains(&(child, vec![ids[0]])));
    }
}

#[tokio::test]
#[ignore = "100k local objects and SQL commit index"]
async fn delete_oldest_in_100k_history_reads_constant_objects() {
    let account = uuid::Uuid::new_v4().simple().to_string();
    let (dir, vfs, objects, refs, svc) = make_service(&account);
    let sqlite = || async {
        Arc::new(
            SqliteSnapshotLogStore::connect(dir.path().join("history.db").to_str().unwrap())
                .await
                .unwrap(),
        )
    };
    let index: Arc<dyn crate::git::CommitIndexStore> = {
        #[cfg(feature = "postgres")]
        if let Ok(dsn) = std::env::var("SNAPSHOT_LOG_TEST_PG_DSN") {
            Arc::new(
                crate::git::PostgresSnapshotLogStore::connect(&dsn, 4, 5000)
                    .await
                    .unwrap(),
            )
        } else {
            sqlite().await
        }
        #[cfg(not(feature = "postgres"))]
        sqlite().await
    };
    let svc = svc.with_commit_index(Some(index.clone()));
    let started = std::time::Instant::now();
    let mut ids = Vec::with_capacity(100_000);
    for i in 0..100_000 {
        vfs.put("file", if i % 2 == 0 { b"a" } else { b"b" });
        ids.push(make_commit(&svc, &account, "main", &i.to_string()).await);
    }
    let spy = Arc::new(GetSpyObjectStore {
        inner: LocalObjectStore::new(dir.path()),
        gets: Default::default(),
    });
    let reader = GitService::new(vfs, spy.clone(), refs).with_commit_index(Some(index.clone()));
    let tick = std::time::Instant::now();
    reader
        .delete_commits(DeleteRequest {
            account: account.clone(),
            ..deletion(&ids[..10], false)
        })
        .await
        .unwrap();
    let commits: HashSet<_> = ids.iter().copied().collect();
    let count = spy
        .gets
        .lock()
        .unwrap()
        .iter()
        .filter(|oid| commits.contains(*oid))
        .count();
    eprintln!(
        "100k delete took {:?}, {count} commit reads",
        tick.elapsed()
    );
    assert!(count <= 2, "{count} commit reads");
    let grace = std::time::Duration::from_secs(600);
    svc.gc_at(&account, std::time::SystemTime::now() + 2 * grace, grace)
        .await
        .unwrap();
    assert!(!objects.exists(&account, &ids[0]).await.unwrap());
    assert_eq!(
        svc.log(LogRequest {
            account: account.clone(),
            branch: "main".into(),
            limit: 5,
            offset: 0,
            paths: None,
        })
        .await
        .unwrap()
        .into_iter()
        .map(|row| row.oid)
        .collect::<Vec<_>>(),
        ids.iter().rev().take(5).copied().collect::<Vec<_>>()
    );
    index.clear_branch(&account, "main").await.unwrap();
    eprintln!("100k fixture, delete and GC took {:?}", started.elapsed());
}

#[tokio::test(start_paused = true)]
async fn delete_schedules_single_gc_per_account() {
    let (_dir, vfs, _, _, mut svc) = make_service("acct");
    let ids = delete_chain(&svc, &vfs, 3).await;
    let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
    svc.before_sweep_hook = Some(Arc::new(move || {
        let tx = tx.clone();
        Box::pin(async move {
            tx.send(()).unwrap();
        })
    }));
    svc.delete_commits(deletion(&ids[0..1], false))
        .await
        .unwrap();
    svc.delete_commits(deletion(&ids[1..2], false))
        .await
        .unwrap();
    assert_eq!(svc.scheduled_gc.lock().unwrap().len(), 1);
    tokio::time::advance(svc.gc_grace + std::time::Duration::from_secs(61)).await;
    rx.recv().await.unwrap();
    while !svc.scheduled_gc.lock().unwrap().is_empty() {
        tokio::task::yield_now().await;
    }
    assert!(rx.try_recv().is_err());
}

#[tokio::test]
async fn delete_uses_object_history_with_corrupt_or_lagging_index() {
    use crate::git::CommitIndexStore;
    let (dir, vfs, _, _, svc) = make_service("acct");
    let db = dir.path().join("history.db");
    let index = Arc::new(
        SqliteSnapshotLogStore::connect(db.to_str().unwrap())
            .await
            .unwrap(),
    );
    let svc = svc.with_commit_index(Some(index.clone()));
    let ids = delete_chain(&svc, &vfs, 5).await;
    let pool = sqlx::SqlitePool::connect(&format!("sqlite://{}", db.display()))
        .await
        .unwrap();
    sqlx::query("UPDATE snapshot_commit SET tree_oid=? WHERE account=? AND commit_oid=?")
        .bind("0".repeat(40))
        .bind("acct")
        .bind(ids[1].to_string())
        .execute(&pool)
        .await
        .unwrap();
    let planned = svc
        .delete_commits(deletion(&ids[1..2], true))
        .await
        .unwrap();
    assert_eq!(planned.plan[0].changed_paths, ["resources/file"]);
    tokio::time::timeout(std::time::Duration::from_secs(5), async {
        loop {
            if matches!(
                index.get_commit("acct", &ids[1].to_string()).await,
                Ok(Some(_))
            ) {
                break;
            }
            tokio::time::sleep(std::time::Duration::from_millis(10)).await;
        }
    })
    .await
    .unwrap();
    CommitIndexStore::clear_branch(index.as_ref(), "acct", "main")
        .await
        .unwrap();
    let r = svc
        .delete_commits(deletion(&ids[1..3], false))
        .await
        .unwrap();
    assert_eq!(r.reparented, [(ids[3], vec![ids[0]])]);
    assert_eq!(delete_log(&svc).await, [ids[4], ids[3], ids[0]]);
}

#[tokio::test]
async fn delete_rejects_invalid_ids_before_reading_history() {
    let (_dir, _, _, _, svc) = make_service("acct");
    for ids in [
        vec![],
        vec!["abc".to_owned()],
        vec!["z".repeat(40)],
        vec!["0".repeat(41)],
        (0..101).map(|i| format!("{i:040x}")).collect(),
    ] {
        let mut req = deletion(&[], true);
        req.commit_ids = ids;
        assert!(matches!(
            svc.delete_commits(req).await,
            Err(GitError::InvalidCommitId(_))
        ));
    }
}

#[tokio::test]
async fn delete_root_then_create_independent_branch_with_same_content() {
    let (_dir, vfs, _, _, svc) = make_service("acct");
    vfs.put("file", b"same");
    let root = make_commit(&svc, "acct", "main", "initial").await;
    vfs.put("file", b"new");
    make_commit(&svc, "acct", "main", "second").await;
    svc.delete_commits(deletion(&[root], false)).await.unwrap();
    vfs.put("file", b"same");
    let other = make_commit(&svc, "acct", "other", "initial").await;
    assert_ne!(
        other, root,
        "independent branch must not publish a tombstoned commit"
    );
    assert!(svc.path_is_file("acct", &other, "file").await.unwrap());
}

#[tokio::test]
async fn delete_peer_stale_child_falls_back_to_live_history() {
    for collect in [false, true] {
        let (_dir, vfs, _, _, peer) = make_service("acct");
        let reader = peer
            .clone()
            .with_commit_index(Some(make_sqlite_snapshot_log_store().await));
        let ids = delete_chain(&reader, &vfs, 3).await;
        peer.delete_commits(deletion(&ids[1..2], false))
            .await
            .unwrap();
        if collect {
            let grace = std::time::Duration::from_secs(600);
            peer.gc_at("acct", std::time::SystemTime::now() + 2 * grace, grace)
                .await
                .unwrap();
        }
        let preview = reader
            .delete_commits(deletion(&ids[..1], true))
            .await
            .unwrap();
        assert_eq!(preview.reparented, [(ids[2], vec![])]);
        let mut request = deletion(&ids[..1], false);
        request.expected_overlay_root = Some(preview.overlay_root);
        reader.delete_commits(request).await.unwrap();
        assert_eq!(delete_log(&reader).await, [ids[2]]);
    }
}

#[tokio::test]
async fn delete_peer_lag_triggers_backfill_even_when_preview_succeeds() {
    use crate::git::CommitIndexStore;
    for empty in [false, true] {
        let (dir, vfs, _, refs, writer) = make_service("acct");
        let spy = Arc::new(GetSpyObjectStore {
            inner: LocalObjectStore::new(dir.path()),
            gets: Default::default(),
        });
        let index = make_sqlite_snapshot_log_store().await;
        let reader =
            GitService::new(vfs.clone(), spy.clone(), refs).with_commit_index(Some(index.clone()));
        let mut ids = delete_chain(if empty { &writer } else { &reader }, &vfs, 3).await;
        ids.extend(delete_chain(&writer, &vfs, 6).await);
        reader
            .delete_commits(deletion(&ids[..1], true))
            .await
            .unwrap();
        let head = ids.last().unwrap().to_string();
        tokio::time::timeout(std::time::Duration::from_secs(3), async {
            while !index
                .latest_commit("acct", "main")
                .await
                .unwrap()
                .is_some_and(|row| row.commit_oid == head)
            {
                tokio::time::sleep(std::time::Duration::from_millis(10)).await;
            }
        })
        .await
        .expect("successful preview should also schedule index catch-up");
        spy.reset();
        reader
            .delete_commits(deletion(&ids[..1], true))
            .await
            .unwrap();
        assert_eq!(
            spy.gets
                .lock()
                .unwrap()
                .iter()
                .filter(|oid| ids.contains(oid))
                .count(),
            2
        );
    }
}
