//! Run explicitly against a disposable PostgreSQL database:
//! OV_TEST_PG_DSN='host=127.0.0.1 port=... user=... dbname=postgres sslmode=disable'
//! cargo test -p ragfs --features postgres --test postgres_pathlock -- --ignored
#![cfg(feature = "postgres")]

use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use ragfs::core::builder::{build_default_stack, RagfsConfig};
use ragfs::lock::types::PathLockError;
use ragfs::lock::{
    LockToken, LockTokenCodec, PathLockConfig, PathLockKind, PathLockManager, PathLockProvider,
    PathLockRequest, PostgresPathLockProvider,
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

async fn database_now() -> u128 {
    tokio::task::spawn_blocking(|| {
        let dsn = std::env::var("OV_TEST_PG_DSN").unwrap();
        let mut admin = postgres::Client::connect(&dsn, postgres::NoTls).unwrap();
        let raw: String = admin
            .query_one(
                "SELECT floor(extract(epoch FROM clock_timestamp()) * 1000000000)::text",
                &[],
            )
            .unwrap()
            .get(0);
        raw.parse().unwrap()
    })
    .await
    .unwrap()
}

// Fault injection bypasses production writes, which always normalize timestamps.
// Only overwrite records already created in this test's isolated namespace.
async fn inject_token(namespace: &str, paths: &[String], token: &LockToken) {
    let (namespace, paths, token) = (namespace.to_owned(), paths.to_vec(), token.clone());
    tokio::task::spawn_blocking(move || {
        let dsn = std::env::var("OV_TEST_PG_DSN").unwrap();
        let mut admin = postgres::Client::connect(&dsn, postgres::NoTls).unwrap();
        let mut tx = admin.transaction().unwrap();
        tx.query_one(
            "SELECT pg_advisory_xact_lock(187003452, hashtext($1))",
            &[&namespace],
        )
        .unwrap();
        for path in paths {
            assert_eq!(
                tx.execute(
                    "UPDATE public.ov_pathlock_tokens SET owner=$3,time_ns=$4,token=$5
                     WHERE md5(namespace)=md5($1) AND namespace=$1
                     AND md5(path)=md5($2) AND path=$2",
                    &[
                        &namespace,
                        &path,
                        &token.owner_id,
                        &format!("{:039}", token.time_ns),
                        &LockTokenCodec::encode(&token),
                    ],
                )
                .unwrap(),
                1
            );
        }
        tx.commit().unwrap();
    })
    .await
    .unwrap();
}

// Deterministic, poorly compressible names: repeated characters would hide
// PostgreSQL's B-tree size limit through compression.
fn long_path() -> String {
    let mut state = 0x1234_5678_9abc_def0_u64;
    let alphabet = b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
    let mut path = String::new();
    for _ in 0..16 {
        path.push('/');
        for _ in 0..200 {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            path.push(alphabet[(state % alphabet.len() as u64) as usize] as char);
        }
    }
    assert_eq!(path.len(), 3216);
    path
}

#[tokio::test]
#[ignore = "requires OV_TEST_PG_DSN"]
async fn manager_long_paths_batch_renewal_and_tree_boundaries() {
    let root = long_path();
    // Exercise long namespace values too: no index may store them in full.
    let ns = format!("{}-{}", namespace(), root);
    let p = provider(&ns).await;
    let manager = PathLockManager::new(
        Arc::new(MemFileSystem::new()),
        p.clone(),
        PathLockConfig::default(),
    );
    let other = PathLockManager::new(
        Arc::new(MemFileSystem::new()),
        provider(&ns).await,
        PathLockConfig::default(),
    );
    let batch = manager
        .acquire_batch(
            &[
                request("/short", PathLockKind::Exact),
                request(&root, PathLockKind::Exact),
            ],
            Duration::ZERO,
            None,
        )
        .await
        .unwrap();
    let token = p.read_token(&root).await.unwrap().unwrap();
    let time = now();
    assert!(p
        .mutate_holder(
            &root,
            &token.owner_id,
            &token.holders[0].id,
            time,
            time - 30_000_000_000,
            false,
        )
        .await
        .unwrap());
    assert!(matches!(
        other.acquire_exact(&root, Duration::ZERO, None).await,
        Err(PathLockError::Timeout { .. })
    ));
    manager.release(&batch).await.unwrap();
    assert!(p.read_token("/short").await.unwrap().is_none());
    assert!(p.read_token(&root).await.unwrap().is_none());

    let child = format!("{root}/中文/child");
    let sibling = format!("{root}-backup/child");
    let tree = manager
        .acquire_tree(&root, Duration::ZERO, None)
        .await
        .unwrap();
    assert!(matches!(
        other.acquire_exact(&child, Duration::ZERO, None).await,
        Err(PathLockError::Timeout { .. })
    ));
    let separate = other
        .acquire_exact(&sibling, Duration::ZERO, None)
        .await
        .unwrap();
    assert!(p.scan_descendant_locks(&root).await.unwrap().is_empty());
    manager.release(&tree).await.unwrap();
    let leaf = other
        .acquire_exact(&child, Duration::ZERO, None)
        .await
        .unwrap();
    assert_eq!(
        p.scan_descendant_locks(&root).await.unwrap(),
        vec![child.clone()]
    );
    assert!(matches!(
        manager.acquire_tree(&root, Duration::ZERO, None).await,
        Err(PathLockError::Timeout { .. })
    ));
    assert!(matches!(
        manager.acquire_tree("/", Duration::ZERO, None).await,
        Err(PathLockError::Timeout { .. })
    ));
    // A short lock inserted before a conflicting long path must roll back.
    assert!(matches!(
        manager
            .acquire_batch(
                &[
                    request("/rolled-back", PathLockKind::Exact),
                    request(&child, PathLockKind::Exact),
                ],
                Duration::ZERO,
                None,
            )
            .await,
        Err(PathLockError::Timeout { .. })
    ));
    assert!(p.read_token("/rolled-back").await.unwrap().is_none());
    other.release(&leaf).await.unwrap();
    other.release(&separate).await.unwrap();
}

#[tokio::test]
#[ignore = "requires OV_TEST_PG_DSN"]
async fn binary_prefix_utf8_truncation_preserves_deep_subtree_boundaries() {
    // 2048 bytes end inside an emoji. All paths below share that binary prefix,
    // including a sibling that must be excluded by the complete path bounds.
    let root = format!("/{}", "😀".repeat(800));
    let child = format!("{root}/中文/leaf");
    let sibling = format!("{root}-backup/leaf");
    let ns = namespace();
    let p = provider(&ns).await;
    let other_ns = provider(&namespace()).await;
    acquire(&p, &root, PathLockKind::Exact, "self", "h")
        .await
        .unwrap();
    acquire(&p, &sibling, PathLockKind::Exact, "sibling", "h")
        .await
        .unwrap();
    acquire(&other_ns, &child, PathLockKind::Exact, "foreign", "h")
        .await
        .unwrap();
    assert!(p.scan_descendant_locks(&root).await.unwrap().is_empty());
    // The root itself, the prefix-sharing sibling and another namespace do
    // not prevent upgrading our own exact holder to Tree.
    acquire(&p, &root, PathLockKind::Tree, "self", "tree")
        .await
        .unwrap();
    assert!(matches!(
        acquire(&p, &child, PathLockKind::Exact, "child", "h").await,
        Err(PathLockError::Conflict { .. })
    ));
    cleanup(&p, &[&root]).await;
    acquire(&p, &child, PathLockKind::Exact, "child", "h")
        .await
        .unwrap();
    assert_eq!(
        p.scan_descendant_locks(&root).await.unwrap(),
        vec![child.clone()]
    );
    assert!(matches!(
        acquire(&p, &root, PathLockKind::Tree, "self", "h").await,
        Err(PathLockError::Conflict { .. })
    ));
    cleanup(&p, &[&child, &sibling]).await;
    cleanup(&other_ns, &[&child]).await;
}

#[tokio::test]
#[ignore = "requires OV_TEST_PG_DSN"]
async fn long_paths_cas_and_stale_subtree_cleanup() {
    let ns = namespace();
    let p = provider(&ns).await;
    let root = long_path();
    let child = format!("{root}/child");
    let sibling = format!("{root}-backup");
    let expired = LockToken::held("expired", "h", PathLockKind::Exact, 1);
    acquire(&p, &sibling, PathLockKind::Exact, "sibling-owner", "h")
        .await
        .unwrap();
    p.try_create_token(&child, &expired).await.unwrap();
    let initial = p.read_token(&child).await.unwrap().unwrap();
    let mut changed = initial.clone();
    changed.add_holder("second", PathLockKind::Exact, 2, 0, None);
    assert!(p
        .compare_and_write_token(&child, &initial, &changed)
        .await
        .unwrap());
    assert!(!p.compare_and_remove_token(&child, &initial).await.unwrap());
    // More stale descendants than the bounded global cleanup limit guarantee
    // that targeted subtree cleanup must remove the remainder.
    let mut expired_paths = vec![child.clone()];
    for i in 0..260 {
        let path = format!("{child}/{i}");
        p.try_create_token(&path, &expired).await.unwrap();
        expired_paths.push(path);
    }
    inject_token(&ns, &expired_paths, &expired).await;
    acquire(&p, &root, PathLockKind::Tree, "new", "h")
        .await
        .unwrap();
    assert!(p.read_token(&child).await.unwrap().is_none());
    assert!(p.scan_descendant_locks(&root).await.unwrap().is_empty());
    assert_eq!(
        p.read_token(&sibling).await.unwrap().unwrap().owner_id,
        "sibling-owner"
    );
    cleanup(&p, &[&root, &sibling]).await;
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
    inject_token(&ns, &["/expired".into()], &expired).await;
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
async fn raw_create_and_legacy_refresh_use_database_clock() {
    let p = provider(&namespace()).await;
    for client_time in [0, 1, u128::MAX] {
        let legacy = LockToken {
            owner_id: "owner".into(),
            time_ns: client_time,
            lock_type: PathLockKind::Tree,
            holders: Vec::new(),
        };
        let mut held = LockToken::held("owner", "first", PathLockKind::Exact, client_time);
        held.add_holder(
            "second",
            PathLockKind::Tree,
            u128::MAX - client_time,
            0,
            None,
        );
        for (path, supplied) in [("/legacy", &legacy), ("/held", &held)] {
            let before = database_now().await;
            p.try_create_token(path, supplied).await.unwrap();
            let after = database_now().await;
            let stored = p.read_token(path).await.unwrap().unwrap();
            assert!((before..=after).contains(&stored.time_ns));
            assert_eq!(stored.owner_id, supplied.owner_id);
            assert_eq!(stored.lock_type, supplied.lock_type);
            assert!(stored.holders.iter().all(|h| h.time_ns == stored.time_ns));
            assert!(matches!(
                p.try_create_token(path, supplied).await,
                Err(PathLockError::Conflict { .. })
            ));
            assert_eq!(p.read_token(path).await.unwrap(), Some(stored));
        }
        let held = p.read_token("/held").await.unwrap();
        assert!(!p
            .refresh_token("/held", "owner", client_time)
            .await
            .unwrap());
        assert_eq!(p.read_token("/held").await.unwrap(), held);
        let legacy = p.read_token("/legacy").await.unwrap();
        assert!(!p
            .refresh_token("/legacy", "wrong", client_time)
            .await
            .unwrap());
        assert_eq!(p.read_token("/legacy").await.unwrap(), legacy);
        assert!(!p
            .refresh_token("/missing", "owner", client_time)
            .await
            .unwrap());
        let before = database_now().await;
        assert!(p
            .refresh_token("/legacy", "owner", client_time)
            .await
            .unwrap());
        let after = database_now().await;
        let refreshed = p.read_token("/legacy").await.unwrap().unwrap();
        assert!((before..=after).contains(&refreshed.time_ns));
        assert_eq!(refreshed.lock_type, PathLockKind::Tree);
        assert_eq!(
            p.is_path_locked_atomic("/legacy/child", u128::MAX, u128::MAX - 30_000_000_000, true)
                .await
                .unwrap(),
            Some(true)
        );
        cleanup(&p, &["/legacy", "/held"]).await;
    }
}

#[tokio::test]
#[ignore = "requires OV_TEST_PG_DSN"]
async fn raw_cas_rebases_changed_holders_and_preserves_other_references() {
    let ns = namespace();
    let p = provider(&ns).await;
    let mut initial = LockToken::held("owner", "first", PathLockKind::Exact, 0);
    initial.add_holder("second", PathLockKind::Exact, 0, 0, None);
    p.try_create_token("/cas", &initial).await.unwrap();
    // Separate historic DB time from the CAS time without a timing-dependent sleep.
    let historic_time = database_now().await - 1_000_000_000;
    for holder in &mut initial.holders {
        holder.time_ns = historic_time;
    }
    initial.recompute();
    inject_token(&ns, &["/cas".into()], &initial).await;
    let mut replacement = initial.clone();
    replacement.holders[0].kind = PathLockKind::Tree;
    replacement.holders[1].time_ns = u128::MAX;
    replacement.add_holder("third", PathLockKind::Exact, 0, 0, None);
    let mut wrong = initial.clone();
    wrong.time_ns += 1;
    assert!(!p
        .compare_and_write_token("/cas", &wrong, &replacement)
        .await
        .unwrap());
    assert_eq!(p.read_token("/cas").await.unwrap(), Some(initial.clone()));
    let before = database_now().await;
    assert!(p
        .compare_and_write_token("/cas", &initial, &replacement)
        .await
        .unwrap());
    let after = database_now().await;
    let stored = p.read_token("/cas").await.unwrap().unwrap();
    assert_eq!(stored.holders[0].time_ns, historic_time);
    assert_eq!(stored.holders[0].kind, PathLockKind::Tree);
    assert!((before..=after).contains(&stored.holders[1].time_ns));
    assert_eq!(stored.holders[1].time_ns, stored.holders[2].time_ns);
    assert_eq!(stored.time_ns, stored.holders[1].time_ns);
    assert!(!p
        .compare_and_remove_token("/cas", &replacement)
        .await
        .unwrap());

    // Reusing holder IDs under a new owner starts new DB-clock lifetimes.
    let mut transferred = stored.clone();
    transferred.owner_id = "new-owner".into();
    let before = database_now().await;
    assert!(p
        .compare_and_write_token("/cas", &stored, &transferred)
        .await
        .unwrap());
    let after = database_now().await;
    let stored = p.read_token("/cas").await.unwrap().unwrap();
    assert!((before..=after).contains(&stored.time_ns));
    assert!(stored.holders.iter().all(|h| h.time_ns == stored.time_ns));
    assert_eq!(stored.owner_id, "new-owner");
    cleanup(&p, &["/cas"]).await;
}

#[tokio::test]
#[ignore = "requires OV_TEST_PG_DSN"]
async fn raw_legacy_cas_preserves_kind_changes_and_rebases_renewals() {
    let p = provider(&namespace()).await;
    let supplied = LockToken {
        owner_id: "owner".into(),
        time_ns: u128::MAX,
        lock_type: PathLockKind::Exact,
        holders: Vec::new(),
    };
    p.try_create_token("/legacy", &supplied).await.unwrap();
    let initial = p.read_token("/legacy").await.unwrap().unwrap();
    let mut changed = initial.clone();
    changed.lock_type = PathLockKind::Tree;
    assert!(p
        .compare_and_write_token("/legacy", &initial, &changed)
        .await
        .unwrap());
    assert_eq!(
        p.read_token("/legacy").await.unwrap(),
        Some(changed.clone())
    );
    for client_time in [0, 1, u128::MAX] {
        let initial = p.read_token("/legacy").await.unwrap().unwrap();
        changed = initial.clone();
        changed.time_ns = client_time;
        let before = database_now().await;
        assert!(p
            .compare_and_write_token("/legacy", &initial, &changed)
            .await
            .unwrap());
        let after = database_now().await;
        let stored = p.read_token("/legacy").await.unwrap().unwrap();
        assert!((before..=after).contains(&stored.time_ns));
        assert_eq!(stored.lock_type, PathLockKind::Tree);
    }
    let initial = p.read_token("/legacy").await.unwrap().unwrap();
    let mut converted = initial.clone();
    converted.add_holder("new", PathLockKind::Exact, u128::MAX, 0, None);
    let before = database_now().await;
    assert!(p
        .compare_and_write_token("/legacy", &initial, &converted)
        .await
        .unwrap());
    let after = database_now().await;
    let stored = p.read_token("/legacy").await.unwrap().unwrap();
    assert_eq!(stored.holders[0].id, "legacy");
    assert_eq!(stored.holders[0].time_ns, initial.time_ns);
    assert!((before..=after).contains(&stored.holders[1].time_ns));
    cleanup(&p, &["/legacy"]).await;
}

#[tokio::test]
#[ignore = "requires OV_TEST_PG_DSN"]
async fn database_clock_expiry_takeover_and_ordered_renewal() {
    let ns = namespace();
    let p = provider(&ns).await;
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
    inject_token(&ns, &["/a".into()], &expired).await;
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
    let ns = namespace();
    let p = provider(&ns).await;
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
    let mut expected = p.read_token("/rollback").await.unwrap().unwrap();
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
    inject_token(&ns, &["/rollback".into()], &expired).await;
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
