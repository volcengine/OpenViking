#[tokio::test]
async fn gc_reclaims_only_expired_unreachable_objects() {
    use std::time::{Duration, SystemTime};
    let (_dir, vfs, objects, _, svc) = make_service("acct");
    vfs.put("resources/unchanged", b"shared blob");
    let ids = delete_chain(&svc, &vfs, 3).await;
    let unique = gix_object::compute_hash(gix_hash::Kind::Sha1, gix_object::Kind::Blob, b"1");
    let shared =
        gix_object::compute_hash(gix_hash::Kind::Sha1, gix_object::Kind::Blob, b"shared blob");
    let grace = Duration::from_secs(3600);
    let later = SystemTime::now() + grace * 2;
    assert_eq!(svc.gc_at("acct", later, grace).await.unwrap().deleted, 0);
    svc.delete_commits(deletion(&ids[1..2], false))
        .await
        .unwrap();
    let current = svc.gc("acct").await.unwrap();
    assert_eq!(current.deleted, 0);
    assert!(objects.exists("acct", &unique).await.unwrap());
    let report = svc.gc_at("acct", later, grace).await.unwrap();
    assert!(report.deleted > 0);
    assert_eq!(report.tombstones_purged, 1);
    assert!(!report.used_commit_index);
    assert!(!objects.exists("acct", &unique).await.unwrap());
    assert!(objects.exists("acct", &shared).await.unwrap());
    assert!(!objects.exists("acct", &ids[1]).await.unwrap());
    assert_eq!(delete_log(&svc).await, vec![ids[2], ids[0]]);
    let overlay = svc.overlay("acct").await.unwrap();
    assert_eq!(overlay.deleted_at(&ids[1]).await.unwrap(), None);
}

#[tokio::test]
async fn gc_disabled_and_empty_account() {
    let (_dir, _, _, _, svc) = make_service("acct");
    assert_eq!(svc.gc("acct").await.unwrap().scanned, 0);
    let report = svc
        .with_gc(false, std::time::Duration::from_secs(3600))
        .gc("acct")
        .await
        .unwrap();
    assert_eq!(report.skipped.as_deref(), Some("disabled"));
}

fn gc_object_path(base: &std::path::Path, oid: ObjectId) -> std::path::PathBuf {
    let hex = oid.to_string();
    base.join("acct/objects").join(&hex[..2]).join(&hex[2..])
}

#[tokio::test]
async fn commit_freshens_reverted_blob_but_skips_unchanged_blob() {
    use std::time::{Duration, SystemTime};
    let (dir, vfs, _, _, svc) = make_service("acct");
    vfs.put("a", b"one");
    vfs.put("b", b"stable");
    make_commit(&svc, "acct", "main", "first").await;
    vfs.put("a", b"two");
    make_commit(&svc, "acct", "main", "second").await;
    let path = |body: &[u8]| {
        gc_object_path(
            dir.path(),
            gix_object::compute_hash(gix_hash::Kind::Sha1, gix_object::Kind::Blob, body),
        )
    };
    let old = SystemTime::now() - Duration::from_secs(7200);
    for object in [path(b"one"), path(b"stable")] {
        std::fs::File::open(object)
            .unwrap()
            .set_modified(old)
            .unwrap();
    }
    let modified = |body: &[u8]| std::fs::metadata(path(body)).unwrap().modified().unwrap();
    let stable_before = modified(b"stable");
    vfs.put("a", b"one");
    make_commit(&svc, "acct", "main", "revert").await;
    assert!(modified(b"one") > SystemTime::now() - Duration::from_secs(5));
    assert_eq!(modified(b"stable"), stable_before);
}

#[tokio::test]
async fn gc_remarks_when_ref_moves_during_mark() {
    use std::time::{Duration, SystemTime};
    let (dir, vfs, objects, _, mut svc) = make_service("acct");
    make_commit(&svc, "acct", "main", "first").await;
    let blob =
        crate::git::util::write_object(objects.as_ref(), "acct", gix_object::Kind::Blob, b"old")
            .await
            .unwrap();
    std::fs::File::open(gc_object_path(dir.path(), blob))
        .unwrap()
        .set_modified(SystemTime::now() - Duration::from_secs(7200))
        .unwrap();
    let writer = svc.clone();
    svc.before_sweep_hook = Some(Arc::new(move || {
        let writer = writer.clone();
        let vfs = vfs.clone();
        Box::pin(async move {
            vfs.put("file", b"old");
            make_commit(&writer, "acct", "main", "reused").await;
        })
    }));
    let report = svc.gc("acct").await.unwrap();
    assert!(report.remarked);
    assert_eq!(report.deleted, 0);
    assert!(objects.exists("acct", &blob).await.unwrap());
}

#[tokio::test]
async fn gc_keeps_tombstone_until_object_is_physically_gone() {
    use std::time::{Duration, SystemTime};
    let (dir, vfs, _, _, svc) = make_service("acct");
    let ids = delete_chain(&svc, &vfs, 3).await;
    svc.delete_commits(deletion(&ids[1..2], false))
        .await
        .unwrap();
    let later = SystemTime::now() + Duration::from_secs(7200);
    std::fs::File::open(gc_object_path(dir.path(), ids[1]))
        .unwrap()
        .set_modified(later)
        .unwrap();
    let report = svc
        .gc_at("acct", later, Duration::from_secs(3600))
        .await
        .unwrap();
    assert_eq!(report.tombstones_purged, 0);
    assert!(svc
        .overlay("acct")
        .await
        .unwrap()
        .deleted_at(&ids[1])
        .await
        .unwrap()
        .is_some());
}

#[tokio::test]
async fn gc_rejects_inconsistent_index_and_walks() {
    use crate::git::CommitIndexStore;
    use std::time::{Duration, SystemTime};
    let (dir, vfs, _, _, svc) = make_service("acct");
    let (index, pool) = open_test_commit_index(dir.path()).await;
    let svc = svc.with_commit_index(Some(index.clone()));
    let ids = delete_chain(&svc, &vfs, 3).await;
    sqlx::query("UPDATE snapshot_commit SET parent_oid = NULL WHERE commit_oid = ?")
        .bind(ids[2].to_string())
        .execute(&pool)
        .await
        .unwrap();
    let grace = Duration::from_secs(600);
    let report = svc
        .gc_at("acct", SystemTime::now() + 2 * grace, grace)
        .await
        .unwrap();
    assert!(!report.used_commit_index);
    assert_eq!(report.deleted, 0);
    tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            if let Ok(rows) = index.list_branch("acct", "main").await {
                if rows.len() == 3 && rows[2].parent_oid == Some(ids[1].to_string()) {
                    break;
                }
            }
            tokio::task::yield_now().await;
        }
    })
    .await
    .unwrap();
}

#[tokio::test]
async fn gc_marks_non_branch_refs_and_keeps_fresh_dangling_objects() {
    use crate::git::util::write_object;
    use std::time::{Duration, SystemTime};
    let (dir, _, objects, refs, svc) = make_service("acct");
    let pinned = write_object(
        objects.as_ref(),
        "acct",
        gix_object::Kind::Blob,
        b"ref blob",
    )
    .await
    .unwrap();
    refs.cas_update("acct", "refs/custom/pin", None, pinned)
        .await
        .unwrap();
    let dangling = write_object(
        objects.as_ref(),
        "acct",
        gix_object::Kind::Blob,
        b"not referenced",
    )
    .await
    .unwrap();
    std::fs::File::open(gc_object_path(dir.path(), pinned))
        .unwrap()
        .set_modified(SystemTime::now() - Duration::from_secs(7200))
        .unwrap();
    assert_eq!(svc.gc("acct").await.unwrap().deleted, 0);
    for oid in [pinned, dangling] {
        assert!(objects.exists("acct", &oid).await.unwrap());
    }
}

#[tokio::test]
async fn writer_with_long_grace_protects_reuse_from_short_grace_collector() {
    use std::time::{Duration, SystemTime};
    let (dir, vfs, objects, refs, svc) = make_service("acct");
    let writer = svc.with_gc(false, Duration::from_secs(3600));
    let collector =
        GitService::new(vfs.clone(), objects.clone(), refs).with_gc(true, Duration::from_secs(600));
    let blob =
        crate::git::util::write_object(objects.as_ref(), "acct", gix_object::Kind::Blob, b"reused")
            .await
            .unwrap();
    let now = SystemTime::now();
    std::fs::File::open(gc_object_path(dir.path(), blob))
        .unwrap()
        .set_modified(now - Duration::from_secs(900))
        .unwrap();
    let candidates = collector.object_store.list_objects("acct").await.unwrap();
    vfs.put("file", b"reused");
    make_commit(&writer, "acct", "main", "reused during sweep").await;
    let removed = collector
        .object_store
        .sweep_objects("acct", &candidates, now - Duration::from_secs(600))
        .await
        .unwrap();
    assert!(removed.is_empty());
    assert!(objects.exists("acct", &blob).await.unwrap());
}

#[tokio::test]
async fn gc_preserves_stat_index_and_refs_on_untouched_account() {
    use std::time::{Duration, SystemTime};
    let (dir, vfs, objects, refs, _) = make_service("acct");
    let stats = Arc::new(crate::git::LocalIndexStore::new(dir.path()));
    let svc = GitService::with_index(vfs.clone(), objects, refs, Some(stats));
    delete_chain(&svc, &vfs, 3).await;
    let index_path = dir.path().join("acct/index/main.json");
    let ref_path = dir.path().join("acct/refs/heads/main");
    let index = std::fs::read(&index_path).unwrap();
    let head = std::fs::read(&ref_path).unwrap();
    let grace = Duration::from_secs(600);
    assert_eq!(
        svc.gc_at("acct", SystemTime::now() + 2 * grace, grace)
            .await
            .unwrap()
            .deleted,
        0
    );
    assert_eq!(std::fs::read(index_path).unwrap(), index);
    assert_eq!(std::fs::read(ref_path).unwrap(), head);
}

#[tokio::test]
async fn gc_stops_before_sweep_on_corrupt_overlay_blob() {
    use crate::git::util::{parse_object_header, read_object, write_object, zlib_compress};
    use std::time::{Duration, SystemTime};

    let (dir, vfs, objects, refs, svc) = make_service("acct");
    let commits = delete_chain(&svc, &vfs, 3).await;
    svc.delete_commits(deletion(&commits[1..2], false))
        .await
        .unwrap();
    let view = svc.overlay("acct").await.unwrap();
    let root = view.root_oid.unwrap();
    let shard = view.blob_oids().into_iter().find(|id| *id != root).unwrap();
    let dangling = write_object(
        objects.as_ref(),
        "acct",
        gix_object::Kind::Blob,
        b"unreachable",
    )
    .await
    .unwrap();
    let grace = Duration::from_secs(600);
    for oid in [root, shard] {
        let path = gc_object_path(dir.path(), oid);
        let original = std::fs::read(&path).unwrap();
        let raw = read_object(objects.as_ref(), "acct", &oid).await.unwrap();
        let (_, size, offset) = parse_object_header(&raw).unwrap();
        // Wrong declared size on the root; valid JSON at the wrong OID on the shard.
        let mut corrupt = format!("blob {}\0", size + 1).into_bytes();
        corrupt.extend_from_slice(&raw[offset..]);
        if oid == shard {
            corrupt.push(b' ');
        }
        std::fs::write(&path, zlib_compress(&corrupt).unwrap()).unwrap();
        let reader = GitService::new(vfs.clone(), objects.clone(), refs.clone());
        assert!(matches!(
            reader
                .gc_at("acct", SystemTime::now() + 2 * grace, grace)
                .await,
            Err(GitError::CorruptedObject(_))
        ));
        assert!(objects.exists("acct", &dangling).await.unwrap());
        std::fs::write(path, original).unwrap();
    }
}
