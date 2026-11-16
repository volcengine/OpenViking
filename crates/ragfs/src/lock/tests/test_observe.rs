use super::*;

#[tokio::test]
async fn account_observation_isolates_leases_conflicts_and_cancelled_waiters() {
    let mgr = Arc::new(make_manager().await);
    let a = FS_CTX.scope(
        Arc::new(FsContextInner::new("A")),
        mgr.acquire_exact("/data/a", Duration::ZERO, None),
    ).await.unwrap();
    let b = FS_CTX.scope(
        Arc::new(FsContextInner::new("B")),
        mgr.acquire_exact("/data/b", Duration::ZERO, None),
    ).await.unwrap();
    assert_eq!(mgr.observe().await.active_locks, 2);
    assert_eq!(mgr.observe_account("A").await.active_locks, 1);
    assert_eq!(mgr.observe_account("C").await.active_locks, 0);
    let waiting_mgr = mgr.clone();
    let waiter = tokio::spawn(async move {
        FS_CTX.scope(
            Arc::new(FsContextInner::new("B")),
            waiting_mgr.acquire_exact("/data/b", Duration::from_secs(5), None),
        ).await
    });
    tokio::time::timeout(Duration::from_secs(1), async {
        loop {
            let snapshot = mgr.observe_account("B").await;
            if snapshot.waiting_locks == 1 && !snapshot.conflicts.is_empty() {
                break;
            }
            tokio::task::yield_now().await;
        }
    }).await.unwrap();
    assert_eq!(mgr.observe_account("A").await.waiting_locks, 0);
    assert!(mgr.observe_account("A").await.conflicts.is_empty());
    assert!(!mgr.observe_account("B").await.conflicts.is_empty());
    waiter.abort();
    let _ = waiter.await;
    assert_eq!(mgr.observe_account("B").await.waiting_locks, 0);
    mgr.release(&a).await.unwrap();
    mgr.release(&b).await.unwrap();
    assert_eq!(mgr.observe_account("A").await.active_locks, 0);
    assert_eq!(mgr.observe_account("B").await.active_locks, 0);
}

    #[tokio::test]
    async fn opaque_lease_ref_must_cover_requested_path() {
        let mgr = make_manager().await;
        let lease = mgr
            .acquire_tree("/data", Duration::from_secs(1), None)
            .await
            .unwrap();

        assert_ne!(lease.lease.lease_ref, lease.lease.owner_id);
        assert!(mgr
            .require_covered_lease_ref(
                &lease.lease.owner_id,
                &[PathLockRequest {
                    path: "/data/sub/file.txt".to_string(),
                    kind: PathLockKind::Exact,
                }],
            )
            .await
            .is_err());
        assert!(mgr
            .require_covered_lease_ref(
                &lease.lease.lease_ref,
                &[PathLockRequest {
                    path: "/data/sub/file.txt".to_string(),
                    kind: PathLockKind::Exact,
                }],
            )
            .await
            .is_ok());
        assert!(mgr
            .require_covered_lease_ref(
                &lease.lease.lease_ref,
                &[PathLockRequest {
                    path: "/other/file.txt".to_string(),
                    kind: PathLockKind::Exact,
                }],
            )
            .await
            .is_err());
    }

    #[tokio::test]
    async fn is_locked_detects_root_tree_lock() {
        let mgr = make_manager().await;
        assert!(!mgr.is_locked("/data/file.txt", true).await.unwrap());
        let _root = mgr.acquire_tree("/", Duration::ZERO, None).await.unwrap();
        assert!(mgr.is_locked("/data/file.txt", true).await.unwrap());
    }
