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

fn gc_object_path(base: &std::path::Path, oid: ObjectId) -> std::path::PathBuf {
    let hex = oid.to_string();
    base.join("acct/objects").join(&hex[..2]).join(&hex[2..])
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
async fn gc_rejects_inconsistent_index_and_walks() {
    use crate::git::CommitIndexStore;
    use std::time::{Duration, SystemTime};
    let (dir, vfs, _, _, svc) = make_service("acct");
    let db = dir.path().join("history.db");
    let index = Arc::new(
        SqliteSnapshotLogStore::connect(db.to_str().unwrap())
            .await
            .unwrap(),
    );
    let pool = sqlx::SqlitePool::connect(&format!("sqlite://{}", db.display()))
        .await
        .unwrap();
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

#[tokio::test(start_paused = true)]
async fn scheduled_gc_spaces_scans_from_completion_and_respects_pending_grace() {
    use tokio::time::{sleep, Duration, Instant};
    for grace_seconds in [600, 7200] {
        let (_dir, _, _, _, mut svc) = make_service("acct");
        svc.gc_grace = Duration::from_secs(grace_seconds);
        let (runs, mut observed) = tokio::sync::mpsc::unbounded_channel();
        svc.before_sweep_hook = Some(Arc::new(move || {
            let runs = runs.clone();
            Box::pin(async move {
                runs.send(Instant::now()).unwrap();
                sleep(Duration::from_secs(120)).await;
            })
        }));
        svc.schedule_gc("acct");
        let first = observed.recv().await.unwrap();
        svc.schedule_gc("acct");
        let pending_deadline = svc.scheduled_gc.lock().unwrap()["acct"];
        let second = observed.recv().await.unwrap();
        assert!(second >= pending_deadline);
        assert!(second >= first + Duration::from_secs(120 + 3600));
    }
}
