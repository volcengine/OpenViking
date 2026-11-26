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
