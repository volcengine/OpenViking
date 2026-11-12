//! Run explicitly against a disposable PostgreSQL database:
//! OV_TEST_PG_DSN='host=127.0.0.1 port=... user=... dbname=postgres sslmode=disable'
//! cargo test -p ragfs --features postgres --test postgres_pathlock -- --ignored
#![cfg(feature = "postgres")]

use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use ragfs::core::builder::{build_default_stack, RagfsConfig};
use ragfs::lock::types::PathLockError;
use ragfs::lock::{
    LockToken, PathLockConfig, PathLockKind, PathLockManager, PathLockProvider, PathLockRequest,
    PostgresPathLockProvider,
};
use ragfs::plugins::memfs::MemFileSystem;

fn now() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos()
}
fn request(path: &str, kind: PathLockKind) -> PathLockRequest {
    PathLockRequest {
        path: path.into(),
        kind,
    }
}
fn namespace() -> String {
    format!("pg-test-{}", uuid::Uuid::new_v4())
}
async fn provider(namespace: &str) -> Arc<PostgresPathLockProvider> {
    let namespace = namespace.to_owned();
    Arc::new(
        tokio::task::spawn_blocking(move || {
            PostgresPathLockProvider::from_env("OV_TEST_PG_DSN", &namespace, 30.0)
        })
        .await
        .unwrap()
        .expect("set OV_TEST_PG_DSN to a disposable PostgreSQL database"),
    )
}
async fn acquire(
    provider: &PostgresPathLockProvider,
    path: &str,
    kind: PathLockKind,
    owner: &str,
    holder: &str,
) -> Result<(), PathLockError> {
    let now = now();
    provider
        .try_acquire_holders_atomic(
            &[request(path, kind)],
            owner,
            holder,
            now,
            now.saturating_sub(30_000_000_000),
            None,
        )
        .await
        .map(|_| ())
}
async fn cleanup(provider: &PostgresPathLockProvider, paths: &[&str]) {
    for path in paths {
        if let Some(value) = provider.read_token(path).await.unwrap() {
            assert!(provider
                .compare_and_remove_token(path, &value)
                .await
                .unwrap());
        }
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires OV_TEST_PG_DSN"]
async fn independent_connections_serialize_tree_and_descendant() {
    let ns = namespace();
    let mut providers = Vec::new();
    for _ in 0..8 {
        providers.push(provider(&ns).await);
    }
    let gate = Arc::new(tokio::sync::Barrier::new(8));
    let mut tasks = Vec::new();
    for (i, provider) in providers.iter().cloned().enumerate() {
        let gate = gate.clone();
        tasks.push(tokio::spawn(async move {
            gate.wait().await;
            acquire(
                &provider,
                "/a",
                PathLockKind::Tree,
                &format!("owner-{i}"),
                "holder",
            )
            .await
        }));
    }
    let mut winners = 0;
    for task in tasks {
        match task.await.unwrap() {
            Ok(()) => winners += 1,
            Err(PathLockError::Conflict { .. }) => {}
            Err(error) => panic!("unexpected error: {error}"),
        }
    }
    assert_eq!(winners, 1);
    assert!(matches!(
        acquire(&providers[0], "/a/child", PathLockKind::Exact, "other", "h").await,
        Err(PathLockError::Conflict { .. })
    ));
    // Segment boundaries, UTF-8 and root scans use PostgreSQL C collation.
    acquire(
        &providers[0],
        "/a-backup/中文",
        PathLockKind::Exact,
        "other",
        "h",
    )
    .await
    .unwrap();
    assert_eq!(
        providers[0].scan_descendant_locks("/a").await.unwrap(),
        Vec::<String>::new()
    );
    assert_eq!(
        providers[0].scan_descendant_locks("/").await.unwrap().len(),
        2
    );
    assert!(matches!(
        acquire(&providers[0], "/", PathLockKind::Tree, "third", "h").await,
        Err(PathLockError::Conflict { .. })
    ));
    cleanup(&providers[0], &["/a", "/a-backup/中文"]).await;
    acquire(&providers[0], "/a/child", PathLockKind::Exact, "one", "h")
        .await
        .unwrap();
    assert!(matches!(
        acquire(&providers[1], "/a", PathLockKind::Tree, "two", "h").await,
        Err(PathLockError::Conflict { .. })
    ));
    cleanup(&providers[0], &["/a/child"]).await;
}

#[tokio::test]
#[ignore = "requires OV_TEST_PG_DSN"]
async fn rollback_cas_and_namespace_isolation() {
    let ns = namespace();
    let p = provider(&ns).await;
    let other = provider(&namespace()).await;
    acquire(&p, "/blocked", PathLockKind::Exact, "one", "h")
        .await
        .unwrap();
    acquire(&other, "/blocked", PathLockKind::Exact, "two", "h")
        .await
        .unwrap();
    let expired = LockToken::held("expired", "h", PathLockKind::Exact, 1);
    p.try_create_token("/expired", &expired).await.unwrap();
    let now = now();
    assert!(matches!(
        p.try_acquire_holders_atomic(
            &[
                request("/new", PathLockKind::Exact),
                request("/blocked", PathLockKind::Exact)
            ],
            "two",
            "h",
            now,
            now - 30_000_000_000,
            None,
        )
        .await,
        Err(PathLockError::Conflict { .. })
    ));
    assert!(p.read_token("/new").await.unwrap().is_none());
    // The global cleanup must roll back along with a failed acquisition.
    assert_eq!(p.read_token("/expired").await.unwrap(), Some(expired));
    let initial = p.read_token("/blocked").await.unwrap().unwrap();
    assert!(!p
        .compare_and_remove_token(
            "/blocked",
            &LockToken::held("wrong", "h", PathLockKind::Exact, 1)
        )
        .await
        .unwrap());
    let mut next = initial.clone();
    next.add_holder("second", PathLockKind::Tree, next.time_ns, 0, None);
    assert!(p
        .compare_and_write_token("/blocked", &initial, &next)
        .await
        .unwrap());
    assert!(!p
        .compare_and_remove_token("/blocked", &initial)
        .await
        .unwrap());
    cleanup(&p, &["/blocked", "/expired"]).await;
    cleanup(&other, &["/blocked"]).await;
}

#[tokio::test]
#[ignore = "requires OV_TEST_PG_DSN"]
async fn database_clock_expiry_takeover_and_ordered_renewal() {
    let p = provider(&namespace()).await;
    // Simulate a client clock far in the future. Only its duration is used.
    let fake_now = u128::MAX;
    p.try_acquire_holders_atomic(
        &[request("/a", PathLockKind::Tree)],
        "owner",
        "h",
        fake_now,
        fake_now - 1_000_000_000,
        None,
    )
    .await
    .unwrap();
    let initial = p.read_token("/a").await.unwrap().unwrap();
    assert!(initial.time_ns.abs_diff(now()) < 10_000_000_000);
    let mut expired = initial.clone();
    expired.holders[0].time_ns = initial.time_ns - 2_000_000_000;
    expired.recompute();
    assert!(p
        .compare_and_write_token("/a", &initial, &expired)
        .await
        .unwrap());
    assert!(!p
        .mutate_holder(
            "/a",
            "owner",
            "h",
            fake_now,
            fake_now - 1_000_000_000,
            false
        )
        .await
        .unwrap());
    acquire(&p, "/a/child", PathLockKind::Exact, "new", "h")
        .await
        .unwrap_err();
    // Use the same configured expiry for all cooperating participants.
    p.try_acquire_holders_atomic(
        &[request("/a/child", PathLockKind::Exact)],
        "new",
        "h",
        fake_now,
        fake_now - 1_000_000_000,
        None,
    )
    .await
    .unwrap();
    assert!(p.read_token("/a").await.unwrap().is_none());
    assert!(!p
        .mutate_holder(
            "/a",
            "owner",
            "h",
            fake_now,
            fake_now - 1_000_000_000,
            false
        )
        .await
        .unwrap());
    let results = p
        .refresh_holders(
            &["/missing".into(), "/a/child".into()],
            "new",
            "h",
            fake_now,
            fake_now - 1_000_000_000,
        )
        .await;
    assert_eq!(
        results.into_iter().map(Result::unwrap).collect::<Vec<_>>(),
        vec![false, true]
    );
    assert_eq!(
        p.is_path_locked_atomic("/a/child", fake_now, fake_now - 1_000_000_000, true)
            .await
            .unwrap(),
        Some(true)
    );
    cleanup(&p, &["/a/child"]).await;
}

#[tokio::test]
#[ignore = "requires OV_TEST_PG_DSN"]
async fn manager_upgrade_handoff_and_database_clock_rollback() {
    let ns = namespace();
    let p = provider(&ns).await;
    let fs = Arc::new(MemFileSystem::new());
    let manager = PathLockManager::new(fs.clone(), p.clone(), PathLockConfig::default());
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
        p.read_token("/a").await.unwrap().unwrap().lock_type,
        PathLockKind::Exact
    );
    let handoff = manager.to_handoff(&exact);
    manager.handoff(&exact).await.unwrap();
    let recipient = PathLockManager::new(fs, provider(&ns).await, PathLockConfig::default());
    let adopted = recipient.adopt(&handoff).await.unwrap();
    recipient.release(&adopted).await.unwrap();
    assert!(p.read_token("/a").await.unwrap().is_none());

    acquire(&p, "/rollback", PathLockKind::Exact, "owner", "source")
        .await
        .unwrap();
    let original = p.read_token("/rollback").await.unwrap().unwrap();
    p.try_acquire_holders_atomic(
        &[request("/rollback", PathLockKind::Exact)],
        "owner",
        "target",
        u128::MAX,
        u128::MAX - 30_000_000_000,
        Some("source"),
    )
    .await
    .unwrap();
    p.replace_holder(
        "/rollback",
        "owner",
        "target",
        original.holders[0].clone(),
        u128::MAX - 30_000_000_000,
    )
    .await
    .unwrap();
    assert_eq!(p.read_token("/rollback").await.unwrap().unwrap(), original);
    cleanup(&p, &["/rollback"]).await;
}

#[tokio::test]
#[ignore = "requires OV_TEST_PG_DSN"]
async fn connection_loss_reconnects_without_replaying_mutations() {
    let ns = namespace();
    let p = provider(&ns).await;
    acquire(&p, "/a", PathLockKind::Exact, "owner", "h")
        .await
        .unwrap();
    // Terminate the provider connection by its unique application_name.
    let dsn = std::env::var("OV_TEST_PG_DSN").unwrap();
    tokio::task::spawn_blocking(move || {
        let mut admin = postgres::Client::connect(&dsn, postgres::NoTls).unwrap();
        let terminated = admin
            .query(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity
             WHERE application_name=$1 AND pid<>pg_backend_pid()",
                &[&format!("ov-pathlock:{ns}")],
            )
            .unwrap();
        assert_eq!(terminated.len(), 1);
        assert!(terminated[0].get::<_, bool>(0));
    })
    .await
    .unwrap();
    // The first operation may detect closure before starting, or report its loss.
    let first = p.read_token("/a").await;
    if first.is_err() {
        assert!(p.read_token("/a").await.unwrap().is_some());
    } else {
        assert!(first.unwrap().is_some());
    }
    cleanup(&p, &["/a"]).await;
}

#[test]
#[ignore = "requires OV_TEST_PG_DSN; subprocess entry"]
fn process_worker() {
    let Ok(ns) = std::env::var("OV_PG_WORKER_NAMESPACE") else {
        return;
    };
    let p = PostgresPathLockProvider::from_env("OV_TEST_PG_DSN", &ns, 30.0).unwrap();
    let rt = tokio::runtime::Runtime::new().unwrap();
    let result = rt.block_on(acquire(
        &p,
        "/tree/child",
        PathLockKind::Exact,
        "child",
        "h",
    ));
    assert!(matches!(result, Err(PathLockError::Conflict { .. })));
}

#[tokio::test]
#[ignore = "requires OV_TEST_PG_DSN"]
async fn process_boundary_shares_authoritative_locks() {
    let ns = namespace();
    let p = provider(&ns).await;
    acquire(&p, "/tree", PathLockKind::Tree, "parent", "h")
        .await
        .unwrap();
    let status = std::process::Command::new(std::env::current_exe().unwrap())
        .args(["--exact", "process_worker", "--ignored", "--nocapture"])
        .env("OV_PG_WORKER_NAMESPACE", &ns)
        .status()
        .unwrap();
    assert!(status.success());
    cleanup(&p, &["/tree"]).await;
}

#[tokio::test]
#[ignore = "requires OV_TEST_PG_DSN"]
async fn configured_stacks_share_postgres_locks_without_cache() {
    let ns = namespace();
    let config = || RagfsConfig {
        pathlock: PathLockConfig {
            provider: "postgres".into(),
            service_name: Some(ns.clone()),
            postgres_dsn_env: Some("OV_TEST_PG_DSN".into()),
            ..PathLockConfig::default()
        },
        ..RagfsConfig::default()
    };
    let first = build_default_stack(config()).await.unwrap();
    let second = build_default_stack(config()).await.unwrap();
    let held = first
        .pathlock_manager
        .acquire_tree("/shared", Duration::ZERO, None)
        .await
        .unwrap();
    assert!(second
        .pathlock_manager
        .acquire_exact("/shared/child", Duration::ZERO, None)
        .await
        .is_err());
    first.pathlock_manager.release(&held).await.unwrap();
    let held = second
        .pathlock_manager
        .acquire_exact("/shared/child", Duration::ZERO, None)
        .await
        .unwrap();
    second.pathlock_manager.release(&held).await.unwrap();
}

#[tokio::test]
#[ignore = "requires OV_TEST_PG_DSN"]
async fn database_contention_returns_busy_and_recovers() {
    let ns = namespace();
    let p = provider(&ns).await;
    let dsn = std::env::var("OV_TEST_PG_DSN").unwrap();
    let (ready_tx, ready_rx) = tokio::sync::oneshot::channel();
    let (release_tx, release_rx) = std::sync::mpsc::channel();
    let blocker = tokio::task::spawn_blocking(move || {
        let mut admin = postgres::Client::connect(&dsn, postgres::NoTls).unwrap();
        let mut tx = admin.transaction().unwrap();
        tx.query_one(
            "SELECT pg_advisory_xact_lock(187003452, hashtext($1))",
            &[&ns],
        )
        .unwrap();
        ready_tx.send(()).unwrap();
        release_rx.recv_timeout(Duration::from_secs(15)).unwrap();
        tx.commit().unwrap();
    });
    ready_rx.await.unwrap();
    let result = p.read_token("/busy").await;
    release_tx.send(()).unwrap();
    blocker.await.unwrap();
    assert!(matches!(result, Err(PathLockError::Busy { .. })));
    acquire(&p, "/busy", PathLockKind::Exact, "owner", "h")
        .await
        .unwrap();
    cleanup(&p, &["/busy"]).await;
}

#[tokio::test]
#[ignore = "requires OV_TEST_PG_DSN"]
async fn rollback_preserves_renewed_source_and_rejects_expired_source() {
    let p = provider(&namespace()).await;
    acquire(&p, "/rollback", PathLockKind::Exact, "owner", "source")
        .await
        .unwrap();
    let original = p.read_token("/rollback").await.unwrap().unwrap();
    acquire(&p, "/rollback", PathLockKind::Exact, "owner", "target")
        .await
        .unwrap();
    let current = p.read_token("/rollback").await.unwrap().unwrap();
    let mut renewed = current.clone();
    renewed
        .holders
        .iter_mut()
        .find(|h| h.id == "source")
        .unwrap()
        .time_ns += 1;
    renewed.recompute();
    assert!(p
        .compare_and_write_token("/rollback", &current, &renewed)
        .await
        .unwrap());
    let mut expected = renewed.clone();
    expected.holders.retain(|h| h.id != "target");
    expected.recompute();
    // A future client threshold must not discard database-clock live holders.
    p.replace_holder(
        "/rollback",
        "owner",
        "target",
        original.holders[0].clone(),
        u128::MAX,
    )
    .await
    .unwrap();
    assert_eq!(p.read_token("/rollback").await.unwrap().unwrap(), expected);

    acquire(&p, "/rollback", PathLockKind::Exact, "owner", "target")
        .await
        .unwrap();
    let current = p.read_token("/rollback").await.unwrap().unwrap();
    let mut expired = current.clone();
    let source = expired
        .holders
        .iter_mut()
        .find(|h| h.id == "source")
        .unwrap();
    source.time_ns -= 60_000_000_000;
    let old_source = source.clone();
    expired.recompute();
    assert!(p
        .compare_and_write_token("/rollback", &current, &expired)
        .await
        .unwrap());
    // A client threshold of zero must not revive an expired database-clock source.
    p.replace_holder("/rollback", "owner", "target", old_source, 0)
        .await
        .unwrap();
    assert!(p.read_token("/rollback").await.unwrap().is_none());
}

#[test]
fn invalid_expiry_is_rejected_before_connecting() {
    for expiry in [0.0, 0.5, -1.0, f64::NAN, f64::INFINITY, f64::MAX] {
        assert!(matches!(
            PostgresPathLockProvider::from_env("OV_UNUSED_ENV", "test", expiry),
            Err(PathLockError::InvalidRequest(_))
        ));
    }
}
