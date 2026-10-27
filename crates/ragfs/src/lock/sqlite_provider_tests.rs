use super::*;
use crate::core::FileSystem;
use crate::lock::{PathLockConfig, PathLockManager};
use crate::plugins::memfs::MemFileSystem;
use std::process::{Child, Command};
use std::time::Instant;
use tempfile::TempDir;

fn provider(dir: &TempDir, namespace: &str) -> SQLitePathLockProvider {
    SQLitePathLockProvider::new(dir.path().join("locks.db").to_str().unwrap(), namespace).unwrap()
}

fn request(path: &str, kind: PathLockKind) -> PathLockRequest {
    PathLockRequest {
        path: path.into(),
        kind,
    }
}

async fn acquire(
    provider: &SQLitePathLockProvider,
    path: &str,
    kind: PathLockKind,
    owner: &str,
    now: u128,
    stale: u128,
) -> PathLockResult<Vec<AtomicAcquisition>> {
    provider
        .try_acquire_batch_atomic(&[request(path, kind)], owner, now, stale)
        .await
        .map(Option::unwrap)
}

#[tokio::test]
async fn exact_and_tree_conflicts_are_shared_between_connections() {
    let dir = TempDir::new().unwrap();
    let first = provider(&dir, "test");
    let second = provider(&dir, "test");
    acquire(&first, "/a", PathLockKind::Exact, "one", 100, 0)
        .await
        .unwrap();
    // An ancestor Exact does not cover its children.
    acquire(&second, "/a/b", PathLockKind::Exact, "two", 100, 0)
        .await
        .unwrap();
    assert!(matches!(
        acquire(&second, "/a", PathLockKind::Tree, "two", 100, 0).await,
        Err(PathLockError::Conflict { .. })
    ));
    assert!(
        matches!(acquire(&first, "/a", PathLockKind::Tree, "one", 100, 0).await,
        Err(PathLockError::Conflict { lock_path, .. }) if lock_path == "/a/b")
    );
    second.remove_token("/a/b", "two", false).await.unwrap();
    acquire(&first, "/a", PathLockKind::Tree, "one", 101, 0)
        .await
        .unwrap();
    assert!(matches!(
        acquire(&second, "/a/b", PathLockKind::Exact, "two", 101, 0).await,
        Err(PathLockError::Conflict {
            kind: PathLockKind::Tree,
            ..
        })
    ));
    assert_eq!(
        second
            .is_path_locked_atomic("/a/b", 101, 0, true)
            .await
            .unwrap(),
        Some(true)
    );
}

#[tokio::test]
async fn prefix_boundaries_wildcards_unicode_root_and_namespaces() {
    let dir = TempDir::new().unwrap();
    let locks = provider(&dir, "one");
    for path in ["/ab/x", "/a%/x", "/a_/x", "/树/x", "/local/account2/x"] {
        acquire(&locks, path, PathLockKind::Exact, "one", 100, 0)
            .await
            .unwrap();
    }
    for path in ["/a", "/a%", "/a_", "/树", "/local/account2"] {
        let result = acquire(&locks, path, PathLockKind::Tree, "two", 100, 0).await;
        if path == "/a" {
            result.unwrap();
        } else {
            assert!(matches!(result, Err(PathLockError::Conflict { .. })));
        }
    }
    assert!(locks.scan_descendant_locks("/a").await.unwrap().is_empty());
    assert_eq!(locks.scan_descendant_locks("/").await.unwrap().len(), 6);
    assert!(matches!(
        acquire(&locks, "/", PathLockKind::Tree, "three", 100, 0).await,
        Err(PathLockError::Conflict { .. })
    ));
    // Separate services in the same DB do not conflict; account paths remain distinct.
    acquire(
        &provider(&dir, "two"),
        "/",
        PathLockKind::Tree,
        "three",
        100,
        0,
    )
    .await
    .unwrap();
    acquire(
        &locks,
        "/local/account1",
        PathLockKind::Tree,
        "three",
        100,
        0,
    )
    .await
    .unwrap();
}

#[tokio::test]
async fn stale_takeover_preserves_u128_boundary_and_ownership() {
    let dir = TempDir::new().unwrap();
    let locks = provider(&dir, "test");
    acquire(&locks, "/a/b", PathLockKind::Exact, "old", u128::MAX - 1, 0)
        .await
        .unwrap();
    assert!(matches!(
        acquire(
            &locks,
            "/a",
            PathLockKind::Tree,
            "new",
            u128::MAX,
            u128::MAX - 1
        )
        .await,
        Err(PathLockError::Conflict { .. })
    ));
    acquire(
        &locks,
        "/a",
        PathLockKind::Tree,
        "new",
        u128::MAX,
        u128::MAX,
    )
    .await
    .unwrap();
    assert_eq!(
        locks.read_token("/a").await.unwrap().unwrap().time_ns,
        u128::MAX
    );
    acquire(
        &locks,
        "/a/b",
        PathLockKind::Exact,
        "new",
        u128::MAX,
        u128::MAX,
    )
    .await
    .unwrap();
    assert!(!locks.refresh_token("/a/b", "old", 0).await.unwrap());
    assert!(!locks.remove_token("/a/b", "old", true).await.unwrap());
    assert!(locks.refresh_token("/a/b", "new", 9).await.unwrap());
    assert_eq!(
        locks
            .is_path_locked_atomic("/a", u128::MAX, u128::MAX, true)
            .await
            .unwrap(),
        Some(true)
    );
    locks.remove_token("/a", "new", false).await.unwrap();
    assert_eq!(
        locks
            .is_path_locked_atomic("/a/b", 10, 10, true)
            .await
            .unwrap(),
        Some(false)
    );
    assert_eq!(
        locks
            .is_path_locked_atomic("/a/b", 10, 10, false)
            .await
            .unwrap(),
        Some(true)
    );
    // Decimal length changes must not reverse staleness.
    assert!(matches!(
        acquire(&locks, "/a/b", PathLockKind::Exact, "next", 10, 9).await,
        Err(PathLockError::Conflict { .. })
    ));
    acquire(&locks, "/a/b", PathLockKind::Exact, "next", 10, 10)
        .await
        .unwrap();
}

#[tokio::test]
async fn conflict_rolls_back_the_whole_batch_including_upgrades() {
    let dir = TempDir::new().unwrap();
    let locks = provider(&dir, "test");
    acquire(&locks, "/a", PathLockKind::Exact, "one", 1, 0)
        .await
        .unwrap();
    acquire(&locks, "/z", PathLockKind::Exact, "two", 1, 0)
        .await
        .unwrap();
    let before = locks.read_token("/a").await.unwrap();
    let result = locks
        .try_acquire_batch_atomic(
            &[
                request("/a", PathLockKind::Tree),
                request("/b", PathLockKind::Exact),
                request("/z", PathLockKind::Exact),
            ],
            "one",
            2,
            0,
        )
        .await;
    assert!(matches!(result, Err(PathLockError::Conflict { .. })));
    assert_eq!(locks.read_token("/a").await.unwrap(), before);
    assert_eq!(locks.read_token("/b").await.unwrap(), None);
}

#[tokio::test]
async fn rollback_restores_upgrade_and_preserves_reentrant_tokens() {
    let dir = TempDir::new().unwrap();
    let locks = provider(&dir, "test");
    acquire(&locks, "/a", PathLockKind::Exact, "one", 1, 0)
        .await
        .unwrap();
    let before = locks.read_token("/a").await.unwrap();
    let changes = locks
        .try_acquire_batch_atomic(
            &[
                request("/a", PathLockKind::Exact),
                request("/a", PathLockKind::Tree),
                request("/b", PathLockKind::Exact),
                request("/b", PathLockKind::Tree),
            ],
            "one",
            2,
            0,
        )
        .await
        .unwrap()
        .unwrap();
    assert_eq!(changes[0].change, AcquisitionChange::Reentrant);
    locks
        .rollback_acquisitions_atomic(&changes, "one")
        .await
        .unwrap();
    assert_eq!(locks.read_token("/a").await.unwrap(), before);
    assert_eq!(locks.read_token("/b").await.unwrap(), None);
}

#[tokio::test]
async fn changed_rollback_is_atomic_and_cas_checks_the_entire_token() {
    let dir = TempDir::new().unwrap();
    let locks = provider(&dir, "test");
    let changes = locks
        .try_acquire_batch_atomic(
            &[
                request("/a", PathLockKind::Exact),
                request("/b", PathLockKind::Exact),
            ],
            "one",
            1,
            0,
        )
        .await
        .unwrap()
        .unwrap();
    let expected = locks.read_token("/a").await.unwrap().unwrap();
    assert!(locks.refresh_token("/a", "one", 2).await.unwrap());
    assert!(!locks
        .compare_and_write_token("/a", &expected, &expected)
        .await
        .unwrap());
    assert!(locks
        .rollback_acquisitions_atomic(&changes, "one")
        .await
        .is_err());
    // /b was removed first, but the transaction must roll that deletion back.
    assert!(locks.read_token("/b").await.unwrap().is_some());
    let current = locks.read_token("/a").await.unwrap().unwrap();
    assert!(locks
        .compare_and_write_token("/a", &current, &expected)
        .await
        .unwrap());
    locks
        .rollback_acquisitions_atomic(&changes, "one")
        .await
        .unwrap();
    assert!(locks.scan_descendant_locks("/").await.unwrap().is_empty());
}

#[tokio::test]
async fn database_write_contention_is_retryable_busy() {
    let dir = TempDir::new().unwrap();
    let locks = provider(&dir, "test");
    let mut blocker = Connection::open(dir.path().join("locks.db")).unwrap();
    let tx = blocker
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .unwrap();
    let start = Instant::now();
    assert!(matches!(
        acquire(&locks, "/a", PathLockKind::Tree, "one", 1, 0).await,
        Err(PathLockError::Busy { .. })
    ));
    assert!(start.elapsed() >= SQLITE_BUSY_TIMEOUT);
    assert!(start.elapsed() < Duration::from_secs(3));
    drop(tx);
    acquire(&locks, "/a", PathLockKind::Tree, "one", 1, 0)
        .await
        .unwrap();
}

#[tokio::test]
async fn run_retries_busy_from_clean_transactions_without_duplicate_mutations() {
    let dir = TempDir::new().unwrap();
    let locks = provider(&dir, "test");
    acquire(&locks, "/old", PathLockKind::Exact, "dead", 1, 0)
        .await
        .unwrap();
    let mut attempts = 0;
    let result = locks
        .run(move |conn, namespace| {
            attempts += 1;
            assert!(conn.is_autocommit());
            let tx = conn
                .transaction_with_behavior(TransactionBehavior::Immediate)
                .map_err(db_error)?;
            // Both mutations from each failed attempt must have rolled back.
            assert!(read(&tx, namespace, "/old")?.is_some());
            assert!(read(&tx, namespace, "/new")?.is_none());
            let removed = tx
                .execute(
                    "DELETE FROM pathlock_tokens WHERE namespace=?1 AND path='/old'",
                    params![namespace],
                )
                .map_err(db_error)?;
            write(
                &tx,
                namespace,
                "/new",
                &LockToken {
                    owner_id: "new".into(),
                    time_ns: 2,
                    lock_type: PathLockKind::Tree,
                },
            )?;
            if attempts < SQLITE_BUSY_ATTEMPTS {
                return Err(PathLockError::Busy {
                    lock_path: "sqlite".into(),
                    operation: "injected after mutation".into(),
                });
            }
            tx.commit().map_err(db_error)?;
            Ok((attempts, removed))
        })
        .await
        .unwrap();
    assert_eq!(result, (SQLITE_BUSY_ATTEMPTS, 1));
    assert!(locks.read_token("/old").await.unwrap().is_none());
    assert_eq!(
        locks.read_token("/new").await.unwrap().unwrap().owner_id,
        "new"
    );
}

#[tokio::test]
async fn run_bounds_busy_attempts_and_does_not_retry_other_errors() {
    use std::sync::atomic::{AtomicUsize, Ordering};
    let dir = TempDir::new().unwrap();
    let locks = provider(&dir, "test");
    for kind in ["busy", "conflict", "invalid", "io"] {
        let calls = Arc::new(AtomicUsize::new(0));
        let observed = calls.clone();
        let result: PathLockResult<()> = locks
            .run(move |_, _| {
                observed.fetch_add(1, Ordering::SeqCst);
                Err(match kind {
                    "busy" => PathLockError::Busy {
                        lock_path: "sqlite".into(),
                        operation: "injected".into(),
                    },
                    "conflict" => PathLockError::Conflict {
                        lock_path: "/a".into(),
                        owner: "other".into(),
                        kind: PathLockKind::Tree,
                    },
                    "invalid" => PathLockError::InvalidRequest("injected".into()),
                    _ => PathLockError::Io("injected".into()),
                })
            })
            .await;
        assert_eq!(
            calls.load(Ordering::SeqCst),
            if kind == "busy" {
                SQLITE_BUSY_ATTEMPTS
            } else {
                1
            }
        );
        assert!(matches!(
            (kind, result),
            ("busy", Err(PathLockError::Busy { .. }))
                | ("conflict", Err(PathLockError::Conflict { .. }))
                | ("invalid", Err(PathLockError::InvalidRequest(_)))
                | ("io", Err(PathLockError::Io(_)))
        ));
    }
}

async fn with_transient_writer<T>(
    blocker: &mut Connection,
    operation: impl std::future::Future<Output = T>,
) -> T {
    let tx = blocker
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .unwrap();
    // Keep the writer longer than one busy timeout, then allow provider retries
    // to recover. Tokio's timer also verifies waits stay off the async executor.
    let (result, ()) = tokio::join!(operation, async {
        tokio::time::sleep(SQLITE_BUSY_TIMEOUT + Duration::from_millis(50)).await;
        drop(tx);
    });
    result
}

#[tokio::test]
async fn busy_retries_cover_zero_timeout_acquire_refresh_release_and_rollback() {
    let dir = TempDir::new().unwrap();
    let locks = Arc::new(provider(&dir, "test"));
    let manager = PathLockManager::new(
        Arc::new(MemFileSystem::new()),
        locks.clone(),
        PathLockConfig::default(),
    );
    let mut blocker = Connection::open(dir.path().join("locks.db")).unwrap();
    let lease = with_transient_writer(
        &mut blocker,
        manager.acquire_tree("/a", Duration::ZERO, None),
    )
    .await
    .unwrap();
    assert_eq!(
        with_transient_writer(&mut blocker, manager.refresh(&lease))
            .await
            .unwrap(),
        "refreshed"
    );
    assert!(with_transient_writer(
        &mut blocker,
        locks.refresh_token("/a", &lease.lease.owner_id, u128::MAX),
    )
    .await
    .unwrap());
    with_transient_writer(&mut blocker, manager.release(&lease))
        .await
        .unwrap();
    assert!(locks.read_token("/a").await.unwrap().is_none());

    let changes = acquire(&locks, "/rollback", PathLockKind::Tree, "owner", 1, 0)
        .await
        .unwrap();
    with_transient_writer(
        &mut blocker,
        locks.rollback_acquisitions_atomic(&changes, "owner"),
    )
    .await
    .unwrap();
    assert!(locks.read_token("/rollback").await.unwrap().is_none());
}

#[tokio::test]
async fn manager_zero_timeout_waits_for_transient_sqlite_writer() {
    let dir = TempDir::new().unwrap();
    let locks = Arc::new(provider(&dir, "test"));
    let manager = PathLockManager::new(
        Arc::new(MemFileSystem::new()),
        locks.clone(),
        PathLockConfig::default(),
    );
    let mut blocker = Connection::open(dir.path().join("locks.db")).unwrap();
    let tx = blocker
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .unwrap();
    let (lease, ()) = tokio::join!(manager.acquire_tree("/a", Duration::ZERO, None), async {
        tokio::time::sleep(Duration::from_millis(20)).await;
        drop(tx);
    });
    let lease = lease.unwrap();
    manager.release(&lease).await.unwrap();
    assert!(locks.read_token("/a").await.unwrap().is_none());
}

#[tokio::test]
async fn rejects_invalid_paths_and_owner_before_mutation() {
    let dir = TempDir::new().unwrap();
    let locks = provider(&dir, "test");
    for path in ["relative", "/a/", "/a//b", "/a/../b", "/./a", "/a\0b"] {
        assert!(matches!(
            acquire(&locks, path, PathLockKind::Exact, "one", 1, 0).await,
            Err(PathLockError::InvalidRequest(_))
        ));
    }
    for owner in ["", "bad:owner"] {
        assert!(matches!(
            acquire(&locks, "/a", PathLockKind::Exact, owner, 1, 0).await,
            Err(PathLockError::InvalidRequest(_))
        ));
    }
    assert!(locks.scan_descendant_locks("/").await.unwrap().is_empty());
    assert!(SQLitePathLockProvider::new(":memory:", "test").is_err());
    assert!(
        SQLitePathLockProvider::new(dir.path().join("other.db").to_str().unwrap(), "").is_err()
    );
}

#[tokio::test]
async fn manager_lifecycle_uses_logical_handles_and_never_reads_business_directories() {
    let dir = TempDir::new().unwrap();
    let fs = Arc::new(MemFileSystem::new());
    let locks = Arc::new(provider(&dir, "test"));
    let manager = PathLockManager::new(fs.clone(), locks.clone(), PathLockConfig::default());
    let other = PathLockManager::new(
        fs.clone(),
        Arc::new(provider(&dir, "test")),
        PathLockConfig::default(),
    );
    // This business directory does not exist. A filesystem scan would fail.
    let exact = manager
        .acquire_exact("/missing/file", Duration::ZERO, None)
        .await
        .unwrap();
    assert_eq!(exact.lease.lock_paths, vec!["/missing/file"]);
    assert!(other
        .acquire_tree("/missing", Duration::ZERO, None)
        .await
        .is_err());
    let tree = manager
        .acquire_tree(
            "/missing",
            Duration::ZERO,
            Some((&exact.lease.lease_ref, &exact.ownership_ref)),
        )
        .await
        .unwrap();
    assert!(other.is_locked("/missing/child", true).await.unwrap());
    manager.release(&tree).await.unwrap();
    manager.release(&exact).await.unwrap();
    let tree = other
        .acquire_tree("/missing", Duration::ZERO, None)
        .await
        .unwrap();
    other.release(&tree).await.unwrap();
    assert!(locks.scan_descendant_locks("/").await.unwrap().is_empty());
    assert_eq!(
        fs.read_dir("/", None, None, None, None)
            .await
            .unwrap()
            .len(),
        0
    );
}

#[tokio::test]
async fn manager_upgrade_downgrades_and_handoff_adopts() {
    let dir = TempDir::new().unwrap();
    let locks = Arc::new(provider(&dir, "test"));
    let fs = Arc::new(MemFileSystem::new());
    let manager = PathLockManager::new(fs.clone(), locks.clone(), PathLockConfig::default());
    let exact = manager
        .acquire_exact("/a", Duration::ZERO, None)
        .await
        .unwrap();
    let tree = manager
        .acquire_tree(
            "/a",
            Duration::ZERO,
            Some((&exact.lease.lease_ref, &exact.ownership_ref)),
        )
        .await
        .unwrap();
    manager.release(&tree).await.unwrap();
    assert_eq!(
        locks.read_token("/a").await.unwrap().unwrap().lock_type,
        PathLockKind::Exact
    );
    let handoff = manager.to_handoff(&exact);
    manager.handoff(&exact).await.unwrap();
    let recipient = PathLockManager::new(
        fs,
        Arc::new(provider(&dir, "test")),
        PathLockConfig::default(),
    );
    let adopted = recipient.adopt(&handoff).await.unwrap();
    recipient.release(&adopted).await.unwrap();
    assert_eq!(locks.read_token("/a").await.unwrap(), None);
}

#[tokio::test]
async fn manager_refreshes_live_tokens() {
    let dir = TempDir::new().unwrap();
    let locks = Arc::new(provider(&dir, "test"));
    let config = PathLockConfig {
        lock_expire_secs: 1.0,
        ..PathLockConfig::default()
    };
    let manager = PathLockManager::new(Arc::new(MemFileSystem::new()), locks.clone(), config);
    let lease = manager
        .acquire_tree("/a", Duration::ZERO, None)
        .await
        .unwrap();
    let initial = locks.read_token("/a").await.unwrap().unwrap().time_ns;
    tokio::time::timeout(Duration::from_secs(3), async {
        loop {
            if locks.read_token("/a").await.unwrap().unwrap().time_ns > initial {
                break;
            }
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
    })
    .await
    .unwrap();
    manager.release(&lease).await.unwrap();
}

#[tokio::test]
async fn stale_cleanup_is_bounded_and_keeps_this_owners_tokens() {
    let dir = TempDir::new().unwrap();
    let locks = provider(&dir, "test");
    let requests: Vec<_> = (0..300)
        .map(|id| request(&format!("/old/{id}"), PathLockKind::Exact))
        .collect();
    locks
        .try_acquire_batch_atomic(&requests, "old", 1, 0)
        .await
        .unwrap();
    acquire(&locks, "/own", PathLockKind::Exact, "new", 1, 0)
        .await
        .unwrap();
    acquire(&locks, "/new", PathLockKind::Exact, "new", 2, 2)
        .await
        .unwrap();
    assert_eq!(locks.scan_descendant_locks("/old").await.unwrap().len(), 44);
    assert!(locks.read_token("/own").await.unwrap().is_some());
    acquire(&locks, "/new", PathLockKind::Exact, "new", 3, 2)
        .await
        .unwrap();
    assert!(locks
        .scan_descendant_locks("/old")
        .await
        .unwrap()
        .is_empty());
}

#[tokio::test]
async fn stale_conflicting_descendants_cannot_be_revived_after_tree_takeover() {
    for root in ["/a", "/"] {
        let dir = TempDir::new().unwrap();
        let old = provider(&dir, "test");
        let new = provider(&dir, "test");
        let requests: Vec<_> = (0..300)
            .map(|id| {
                request(
                    &format!("{}/child/{id:03}", root.trim_end_matches('/')),
                    if id % 2 == 0 {
                        PathLockKind::Exact
                    } else {
                        PathLockKind::Tree
                    },
                )
            })
            .collect();
        old.try_acquire_batch_atomic(&requests, "old", 1, 0)
            .await
            .unwrap();
        let result = new
            .try_acquire_batch_atomic_with_stats(&[request(root, PathLockKind::Tree)], "new", 10, 2)
            .await
            .unwrap()
            .unwrap();
        let paths: Vec<_> = requests
            .iter()
            .map(|request| request.path.clone())
            .collect();
        let refreshed = old.refresh_tokens(&paths, "old", 11).await;
        assert!(
            refreshed.iter().all(|result| matches!(result, Ok(false))),
            "expired descendants must not revive under {root}: {refreshed:?}"
        );
        assert_eq!(result.stale_tokens_removed, 300);
        assert!(!old.refresh_token(&paths[299], "old", 12).await.unwrap());
        assert!(new.scan_descendant_locks(root).await.unwrap().is_empty());
        assert_eq!(new.read_token(root).await.unwrap().unwrap().owner_id, "new");
        assert!(matches!(
            acquire(&old, &paths[299], PathLockKind::Exact, "old", 12, 2).await,
            Err(PathLockError::Conflict {
                kind: PathLockKind::Tree,
                ..
            })
        ));
    }
}

#[tokio::test]
async fn stale_target_and_ancestor_trees_are_cleaned_beyond_unrelated_budget() {
    let dir = TempDir::new().unwrap();
    let old = provider(&dir, "test");
    let new = provider(&dir, "test");
    let unrelated: Vec<_> = (0..300)
        .map(|id| request(&format!("/unrelated/{id:03}"), PathLockKind::Exact))
        .collect();
    old.try_acquire_batch_atomic(&unrelated, "old", 0, 0)
        .await
        .unwrap();
    let conflicting = [
        request("/", PathLockKind::Tree),
        request("/a", PathLockKind::Tree),
        request("/a/b/c", PathLockKind::Exact),
    ];
    old.try_acquire_batch_atomic(&conflicting, "old", 1, 0)
        .await
        .unwrap();
    acquire(&old, "/a/b", PathLockKind::Exact, "old", 1, 0)
        .await
        .unwrap();
    // A separate namespace survives the cleanup.
    let other = provider(&dir, "other");
    acquire(&other, "/", PathLockKind::Tree, "old", 1, 0)
        .await
        .unwrap();
    let result = new
        .try_acquire_batch_atomic_with_stats(
            &[request("/a/b/c", PathLockKind::Exact)],
            "new",
            10,
            2,
        )
        .await
        .unwrap()
        .unwrap();
    assert_eq!(result.stale_tokens_removed, 259);
    let paths: Vec<_> = conflicting
        .iter()
        .map(|request| request.path.clone())
        .collect();
    assert!(old
        .refresh_tokens(&paths, "old", 11)
        .await
        .iter()
        .all(|result| matches!(result, Ok(false))));
    assert_eq!(
        old.scan_descendant_locks("/unrelated").await.unwrap().len(),
        44
    );
    assert!(old.refresh_token("/a/b", "old", 11).await.unwrap());
    assert!(other.refresh_token("/", "old", 11).await.unwrap());
}

#[tokio::test]
async fn failed_batch_restores_stale_conflicts_beyond_cleanup_budget() {
    let dir = TempDir::new().unwrap();
    let old = provider(&dir, "test");
    let new = provider(&dir, "test");
    let requests: Vec<_> = (0..300)
        .map(|id| request(&format!("/a/{id:03}"), PathLockKind::Exact))
        .collect();
    old.try_acquire_batch_atomic(&requests, "old", 1, 0)
        .await
        .unwrap();
    acquire(&old, "/live", PathLockKind::Exact, "live", 10, 0)
        .await
        .unwrap();
    assert!(matches!(
        new.try_acquire_batch_atomic_with_stats(
            &[
                request("/a", PathLockKind::Tree),
                request("/live", PathLockKind::Exact),
            ],
            "new",
            10,
            2,
        )
        .await,
        Err(PathLockError::Conflict { .. })
    ));
    assert!(new.read_token("/a").await.unwrap().is_none());
    assert_eq!(old.scan_descendant_locks("/a").await.unwrap().len(), 300);
    let paths: Vec<_> = requests
        .iter()
        .map(|request| request.path.clone())
        .collect();
    assert!(old
        .refresh_tokens(&paths, "old", 11)
        .await
        .iter()
        .all(|result| matches!(result, Ok(true))));
}

fn wait_for_file(path: &Path) {
    let start = Instant::now();
    while !path.exists() {
        assert!(
            start.elapsed() < Duration::from_secs(15),
            "timed out waiting for {path:?}"
        );
        std::thread::sleep(Duration::from_millis(5));
    }
}

#[tokio::test]
async fn batch_refresh_preserves_order_ownership_kinds_and_large_batches() {
    let dir = TempDir::new().unwrap();
    let locks = provider(&dir, "test");
    let requests: Vec<_> = (0..600)
        .map(|id| {
            request(
                &format!("/batch/{id}"),
                if id % 2 == 0 {
                    PathLockKind::Tree
                } else {
                    PathLockKind::Exact
                },
            )
        })
        .collect();
    locks
        .try_acquire_batch_atomic(&requests, "one", 1, 0)
        .await
        .unwrap();
    acquire(&locks, "/foreign", PathLockKind::Exact, "two", 1, 0)
        .await
        .unwrap();
    let mut paths: Vec<_> = requests
        .iter()
        .map(|request| request.path.clone())
        .collect();
    paths.extend([
        "/missing".into(),
        "/foreign".into(),
        "invalid".into(),
        "/batch/0".into(),
    ]);
    let results = locks.refresh_tokens(&paths, "one", u128::MAX).await;
    assert_eq!(results.len(), paths.len());
    assert!(results[..600]
        .iter()
        .all(|result| matches!(result, Ok(true))));
    assert!(matches!(results[600], Ok(false)));
    assert!(matches!(results[601], Ok(false)));
    assert!(matches!(
        results[602],
        Err(PathLockError::InvalidRequest(_))
    ));
    assert!(matches!(results[603], Ok(true)));
    for request in requests {
        let token = locks.read_token(&request.path).await.unwrap().unwrap();
        assert_eq!(token.time_ns, u128::MAX);
        assert_eq!(token.lock_type, request.kind);
    }
    assert_eq!(
        locks.read_token("/foreign").await.unwrap().unwrap().time_ns,
        1
    );
    assert!(locks.refresh_tokens(&[], "one", 2).await.is_empty());
}

#[tokio::test]
async fn batch_refresh_reports_busy_for_every_path_and_does_not_partially_commit() {
    let dir = TempDir::new().unwrap();
    let locks = provider(&dir, "test");
    acquire(&locks, "/a", PathLockKind::Exact, "one", 1, 0)
        .await
        .unwrap();
    acquire(&locks, "/b", PathLockKind::Exact, "one", 1, 0)
        .await
        .unwrap();
    let mut blocker = Connection::open(dir.path().join("locks.db")).unwrap();
    let tx = blocker
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .unwrap();
    let results = locks
        .refresh_tokens(&["/a".into(), "/b".into()], "one", 2)
        .await;
    assert!(results
        .iter()
        .all(|result| matches!(result, Err(PathLockError::Busy { .. }))));
    drop(tx);
    assert_eq!(locks.read_token("/a").await.unwrap().unwrap().time_ns, 1);
    assert_eq!(locks.read_token("/b").await.unwrap().unwrap().time_ns, 1);
}

#[tokio::test]
async fn atomic_recovery_counts_committed_cleanup_and_stale_replacements_once() {
    let dir = TempDir::new().unwrap();
    let locks = provider(&dir, "test");
    let requests: Vec<_> = (0..300)
        .map(|id| request(&format!("/old/{id:03}"), PathLockKind::Exact))
        .collect();
    locks
        .try_acquire_batch_atomic(&requests, "old", 1, 0)
        .await
        .unwrap();
    let result = locks
        .try_acquire_batch_atomic_with_stats(
            &[request("/old/299", PathLockKind::Tree)],
            "new",
            10,
            2,
        )
        .await
        .unwrap()
        .unwrap();
    assert_eq!(result.stale_tokens_removed, 257);
    let result = locks
        .try_acquire_batch_atomic_with_stats(
            &[request("/old/299", PathLockKind::Tree)],
            "new",
            10,
            2,
        )
        .await
        .unwrap()
        .unwrap();
    assert_eq!(result.stale_tokens_removed, 43);
    let result = locks
        .try_acquire_batch_atomic_with_stats(
            &[request("/old/299", PathLockKind::Tree)],
            "new",
            10,
            2,
        )
        .await
        .unwrap()
        .unwrap();
    assert_eq!(result.stale_tokens_removed, 0);
}

#[tokio::test]
async fn manager_observes_only_committed_stale_recovery() {
    let dir = TempDir::new().unwrap();
    let locks = Arc::new(provider(&dir, "test"));
    acquire(&locks, "/old", PathLockKind::Exact, "dead", 1, 0)
        .await
        .unwrap();
    acquire(&locks, "/live", PathLockKind::Exact, "live", u128::MAX, 0)
        .await
        .unwrap();
    let manager = PathLockManager::new(
        Arc::new(MemFileSystem::new()),
        locks.clone(),
        PathLockConfig::default(),
    );
    assert!(manager
        .acquire_exact("/live", Duration::ZERO, None)
        .await
        .is_err());
    assert_eq!(manager.observe().await.stale_locks_removed, 0);
    assert!(locks.read_token("/old").await.unwrap().is_some());
    let lease = manager
        .acquire_tree("/old", Duration::ZERO, None)
        .await
        .unwrap();
    assert_eq!(manager.observe().await.stale_locks_removed, 1);
    manager.release(&lease).await.unwrap();
}

// Kill only subprocesses started by this test if a failed assertion unwinds.
struct Contender(Child);
impl Drop for Contender {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

#[test]
fn separate_processes_cannot_acquire_tree_and_descendant_simultaneously() {
    let dir = TempDir::new().unwrap();
    drop(provider(&dir, "test"));
    for (left, right, successes) in [
        ("/a", "/a/b", 1),
        ("/", "/a/b", 1),
        ("/a", "/a", 1),
        ("/a", "/b", 2),
    ] {
        let round = dir.path().join(uuid::Uuid::new_v4().to_string());
        std::fs::create_dir(&round).unwrap();
        let mut children = Vec::new();
        for (id, path, kind) in [("left", left, "T"), ("right", right, "E")] {
            let child = Command::new(std::env::current_exe().unwrap())
                .args([
                    "--exact",
                    "lock::sqlite_provider::tests::subprocess_contender",
                    "--ignored",
                    "--nocapture",
                ])
                .env("OV_SQLITE_TEST_DIR", dir.path())
                .env("OV_SQLITE_TEST_ROUND", &round)
                .env("OV_SQLITE_TEST_ID", id)
                .env("OV_SQLITE_TEST_PATH", path)
                .env("OV_SQLITE_TEST_KIND", kind)
                .spawn()
                .unwrap();
            children.push(Contender(child));
        }
        wait_for_file(&round.join("left.ready"));
        wait_for_file(&round.join("right.ready"));
        std::fs::write(round.join("go"), "").unwrap();
        wait_for_file(&round.join("left.result"));
        wait_for_file(&round.join("right.result"));
        let results: Vec<_> = ["left", "right"]
            .iter()
            .map(|id| std::fs::read_to_string(round.join(format!("{id}.result"))).unwrap())
            .collect();
        std::fs::write(round.join("release"), "").unwrap();
        for child in &mut children {
            assert!(child.0.wait().unwrap().success());
        }
        assert_eq!(
            results
                .iter()
                .filter(|result| *result == "acquired")
                .count(),
            successes
        );
        assert_eq!(
            results
                .iter()
                .filter(|result| *result == "conflict")
                .count(),
            2 - successes
        );
    }
}

#[test]
fn crashed_process_token_is_persistent_and_can_be_reclaimed() {
    let dir = TempDir::new().unwrap();
    drop(provider(&dir, "test"));
    let round = dir.path().join("crash");
    std::fs::create_dir(&round).unwrap();
    std::fs::write(round.join("go"), "").unwrap();
    let mut child = Contender(
        Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "lock::sqlite_provider::tests::subprocess_contender",
                "--ignored",
            ])
            .env("OV_SQLITE_TEST_DIR", dir.path())
            .env("OV_SQLITE_TEST_ROUND", &round)
            .env("OV_SQLITE_TEST_ID", "crashed")
            .env("OV_SQLITE_TEST_PATH", "/a/b")
            .env("OV_SQLITE_TEST_KIND", "E")
            .env("OV_SQLITE_TEST_CRASH", "1")
            .spawn()
            .unwrap(),
    );
    wait_for_file(&round.join("crashed.result"));
    assert!(child.0.wait().unwrap().success());
    let locks = provider(&dir, "test");
    let runtime = tokio::runtime::Runtime::new().unwrap();
    assert!(matches!(
        runtime.block_on(acquire(&locks, "/a", PathLockKind::Tree, "next", 101, 100)),
        Err(PathLockError::Conflict { .. })
    ));
    runtime
        .block_on(acquire(&locks, "/a", PathLockKind::Tree, "next", 101, 101))
        .unwrap();
    assert!(
        runtime
            .block_on(locks.remove_token("/a/b", "crashed", false))
            .unwrap()
            == false
    );
}

#[test]
#[ignore = "subprocess helper invoked by separate_processes test"]
fn subprocess_contender() {
    let dir = std::path::PathBuf::from(std::env::var("OV_SQLITE_TEST_DIR").unwrap());
    let round = std::path::PathBuf::from(std::env::var("OV_SQLITE_TEST_ROUND").unwrap());
    let id = std::env::var("OV_SQLITE_TEST_ID").unwrap();
    let path = std::env::var("OV_SQLITE_TEST_PATH").unwrap();
    let kind = if std::env::var("OV_SQLITE_TEST_KIND").unwrap() == "T" {
        PathLockKind::Tree
    } else {
        PathLockKind::Exact
    };
    let locks =
        SQLitePathLockProvider::new(dir.join("locks.db").to_str().unwrap(), "test").unwrap();
    let runtime = tokio::runtime::Runtime::new().unwrap();
    std::fs::write(round.join(format!("{id}.ready")), "").unwrap();
    wait_for_file(&round.join("go"));
    if std::env::var_os("OV_SQLITE_TEST_CRASH").is_some() {
        runtime
            .block_on(acquire(&locks, &path, kind, &id, 100, 0))
            .unwrap();
        std::fs::write(round.join(format!("{id}.result")), "acquired").unwrap();
        // Skip connection/runtime destructors as with an abruptly terminated owner.
        std::process::exit(0);
    }
    let manager = runtime.block_on(async {
        PathLockManager::new(
            Arc::new(MemFileSystem::new()),
            Arc::new(locks),
            PathLockConfig::default(),
        )
    });
    // Exercise the production path with the configured zero logical wait timeout.
    // No test-side Busy retry may hide a transient database contention failure.
    let lease = match runtime.block_on(manager.acquire_batch(
        &[request(&path, kind)],
        Duration::ZERO,
        None,
    )) {
        Ok(lease) => Some(lease),
        Err(PathLockError::Timeout { .. }) => {
            assert!(
                !runtime.block_on(manager.observe()).conflicts.is_empty(),
                "zero-timeout acquisition failed without an actual logical lock conflict"
            );
            None
        }
        Err(error) => panic!("{error}"),
    };
    std::fs::write(
        round.join(format!("{id}.result")),
        if lease.is_some() {
            "acquired"
        } else {
            "conflict"
        },
    )
    .unwrap();
    wait_for_file(&round.join("release"));
    if let Some(lease) = lease {
        runtime.block_on(manager.release(&lease)).unwrap();
    }
}
