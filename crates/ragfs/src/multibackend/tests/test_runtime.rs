use super::*;
use crate::core::{FileSystem, WriteFlag};
use crate::lock::{MemoryPathLockProvider, PathLockConfig, PathLockManager};
use crate::multibackend::provider::FilesystemProvider;
use crate::plugins::memfs::MemFileSystem;

/// Verifies deleted registry entries are excluded while null deletions stay active.
#[tokio::test]
async fn active_accounts_exclude_completed_deletions() {
    let primary = MemFileSystem::new();
    let registry = serde_json::json!({
        "accounts": {
            "active": {},
            "pending": {"deletion": null},
            "deleted": {"deletion": {"deleted_at_ns": 1}}
        }
    });
    primary
        .ensure_parent_dirs("/_system/accounts.json", 0o755)
        .await
        .unwrap();
    primary
        .write(
            "/_system/accounts.json",
            &serde_json::to_vec(&registry).unwrap(),
            0,
            WriteFlag::Create,
        )
        .await
        .unwrap();

    let accounts = read_active_accounts(&primary).await.unwrap();
    assert_eq!(
        accounts,
        HashSet::from(["active".to_string(), "pending".to_string()])
    );
}

/// Verifies delete events are persisted in the system scope and remove backup data.
#[tokio::test]
async fn delete_account_event_removes_backup_scope() {
    let primary: Arc<dyn FileSystem> = Arc::new(MemFileSystem::new());
    let backup: Arc<dyn FileSystem> = Arc::new(MemFileSystem::new());
    primary
        .ensure_parent_dirs("/_system/accounts.json", 0o755)
        .await
        .unwrap();
    primary
        .write(
            "/_system/accounts.json",
            br#"{"accounts":{"account":{}}}"#,
            0,
            WriteFlag::Create,
        )
        .await
        .unwrap();
    backup.mkdir("/account", 0o755).await.unwrap();

    let manager = Arc::new(PathLockManager::new(
        primary.clone(),
        Arc::new(MemoryPathLockProvider::new()),
        PathLockConfig::default(),
    ));
    let store = Arc::new(MetadataStore::new(primary.clone(), manager, "/local").unwrap());
    let provider = Arc::new(FilesystemProvider::new(store.clone()));
    let metrics = Arc::new(MultiWriteMetricsSnapshot::default());
    let catch_up = Arc::new(CatchUpWorker::new(
        "backup".to_string(),
        primary.clone(),
        backup.clone(),
        store.clone(),
        provider.clone(),
        metrics.clone(),
    ));
    let mut worker = FlushWorker::new(
        store,
        provider,
        1,
        vec!["backup".to_string()],
        Some(primary),
        vec![Arc::new(StdMutex::new(HashSet::<ScopeKey>::new()))],
        metrics,
    );

    let retry = worker
        .flush_batch(vec![PendingEvent {
            operation_id: uuid::Uuid::from_u128(1),
            account_id: "account".to_string(),
            kind: PendingEventKind::DeleteAccount,
            path: "/local/account".to_string(),
            destination_path: None,
            route_hint: None,
        }])
        .await;
    assert!(retry.is_empty());
    catch_up.run_once().await.unwrap();

    assert!(matches!(
        backup.stat("/account").await.unwrap_err(),
        Error::NotFound(_)
    ));
}

/// Verifies account initialization contention preserves pending events until retry succeeds.
#[tokio::test]
async fn flush_worker_retries_account_initialization_after_tree_lock_release() {
    let primary: Arc<dyn FileSystem> = Arc::new(MemFileSystem::new());
    primary
        .ensure_parent_dirs("/_system/accounts.json", 0o755)
        .await
        .unwrap();
    primary
        .write(
            "/_system/accounts.json",
            br#"{"accounts":{"account":{}}}"#,
            0,
            WriteFlag::Create,
        )
        .await
        .unwrap();

    let manager = Arc::new(PathLockManager::new(
        primary.clone(),
        Arc::new(MemoryPathLockProvider::new()),
        PathLockConfig::default(),
    ));
    let tree_lease = manager
        .acquire_tree("/local/account", Duration::ZERO, None)
        .await
        .unwrap();
    let store = Arc::new(MetadataStore::new(primary.clone(), manager.clone(), "/local").unwrap());
    let provider = Arc::new(FilesystemProvider::new(store.clone()));
    let mut worker = FlushWorker::new(
        store.clone(),
        provider,
        1,
        Vec::new(),
        Some(primary.clone()),
        Vec::new(),
        Arc::new(MultiWriteMetricsSnapshot::default()),
    );
    let retry = worker
        .flush_batch(vec![PendingEvent {
            operation_id: uuid::Uuid::from_u128(2),
            account_id: "account".to_string(),
            kind: PendingEventKind::Data(SegmentEventType::Remove),
            path: "/local/account/file.md".to_string(),
            destination_path: None,
            route_hint: None,
        }])
        .await;
    assert_eq!(retry.len(), 1);
    assert_eq!(store.worker_error_count(MultiWriteWorker::Flush), 0);

    let (sender, receiver) = mpsc::unbounded_channel();
    let pending = Arc::new(AtomicUsize::new(1));
    sender.send(retry.into_iter().next().unwrap()).unwrap();
    drop(sender);
    assert_eq!(pending.load(Ordering::Relaxed), 1);

    manager.release(&tree_lease).await.unwrap();
    let worker_task = tokio::spawn(worker.run(receiver, pending.clone()));
    tokio::time::timeout(Duration::from_secs(3), worker_task)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(pending.load(Ordering::Relaxed), 0);
    let manifest = store.paths().account_manifest("account").unwrap().1;
    assert!(primary.stat(&manifest).await.is_ok());
}

/// Verifies one account's initialization retry does not block another account.
#[tokio::test]
async fn flush_worker_processes_other_accounts_while_one_account_retries() {
    let primary: Arc<dyn FileSystem> = Arc::new(MemFileSystem::new());
    primary
        .ensure_parent_dirs("/_system/accounts.json", 0o755)
        .await
        .unwrap();
    primary
        .write(
            "/_system/accounts.json",
            br#"{"accounts":{"blocked":{},"ready":{}}}"#,
            0,
            WriteFlag::Create,
        )
        .await
        .unwrap();

    let manager = Arc::new(PathLockManager::new(
        primary.clone(),
        Arc::new(MemoryPathLockProvider::new()),
        PathLockConfig::default(),
    ));
    let tree_lease = manager
        .acquire_tree("/local/blocked", Duration::ZERO, None)
        .await
        .unwrap();
    let store = Arc::new(MetadataStore::new(primary.clone(), manager.clone(), "/local").unwrap());
    let provider = Arc::new(FilesystemProvider::new(store.clone()));
    let mut worker = FlushWorker::new(
        store.clone(),
        provider,
        1,
        Vec::new(),
        Some(primary.clone()),
        Vec::new(),
        Arc::new(MultiWriteMetricsSnapshot::default()),
    );
    let blocked_event = PendingEvent {
        operation_id: uuid::Uuid::from_u128(3),
        account_id: "blocked".to_string(),
        kind: PendingEventKind::Data(SegmentEventType::Remove),
        path: "/local/blocked/file.md".to_string(),
        destination_path: None,
        route_hint: None,
    };
    let started_at = Instant::now();
    let mut retries = HashMap::from([(
        "blocked".to_string(),
        FlushRetryState {
            events: vec![blocked_event],
            attempts: 1,
            started_at,
            retry_at: started_at + FLUSH_RETRY_INITIAL_DELAY,
        },
    )]);
    let pending = AtomicUsize::new(2);
    worker
        .flush_fresh_events(
            vec![PendingEvent {
                operation_id: uuid::Uuid::from_u128(4),
                account_id: "ready".to_string(),
                kind: PendingEventKind::Data(SegmentEventType::Remove),
                path: "/local/ready/file.md".to_string(),
                destination_path: None,
                route_hint: None,
            }],
            &mut retries,
            &pending,
        )
        .await;

    let ready_manifest = store.paths().account_manifest("ready").unwrap().1;
    assert!(primary.stat(&ready_manifest).await.is_ok());
    assert_eq!(pending.load(Ordering::Relaxed), 1);
    assert_eq!(retries["blocked"].events.len(), 1);
    let blocked_manifest = store.paths().account_manifest("blocked").unwrap().1;
    assert!(matches!(
        primary.stat(&blocked_manifest).await.unwrap_err(),
        Error::NotFound(_)
    ));

    manager.release(&tree_lease).await.unwrap();
    let state = retries.remove("blocked").unwrap();
    worker
        .flush_retry("blocked", state, &mut retries, &pending)
        .await;
    assert!(retries.is_empty());
    assert_eq!(pending.load(Ordering::Relaxed), 0);
    assert!(primary.stat(&blocked_manifest).await.is_ok());
}

#[test]
fn only_transient_pathlock_errors_are_retryable() {
    assert!(FlushWorker::is_retryable_pathlock_error(&Error::PathLock(
        PathLockError::Conflict {
            lock_path: "/local/account".into(),
            owner: "owner".into(),
            kind: crate::lock::types::PathLockKind::Tree,
        }
    )));
    assert!(FlushWorker::is_retryable_pathlock_error(&Error::PathLock(
        PathLockError::Timeout { elapsed_ms: 1 }
    )));
    assert!(FlushWorker::is_retryable_pathlock_error(&Error::PathLock(
        PathLockError::Busy {
            lock_path: "/local/account".into(),
            operation: "acquire".into(),
        }
    )));
    assert!(!FlushWorker::is_retryable_pathlock_error(
        &Error::Serialization("invalid manifest".into())
    ));
}

#[test]
fn flush_retry_backoff_is_exponential_and_bounded() {
    assert_eq!(
        FlushWorker::retry_delay(1, Duration::ZERO),
        Duration::from_millis(100)
    );
    assert_eq!(
        FlushWorker::retry_delay(2, Duration::ZERO),
        Duration::from_millis(200)
    );
    assert_eq!(
        FlushWorker::retry_delay(3, Duration::ZERO),
        Duration::from_millis(400)
    );
    assert_eq!(
        FlushWorker::retry_delay(7, Duration::ZERO),
        Duration::from_millis(6400)
    );
    assert_eq!(
        FlushWorker::retry_delay(8, Duration::ZERO),
        Duration::from_secs(10)
    );
    assert_eq!(
        FlushWorker::retry_delay(32, Duration::ZERO),
        Duration::from_secs(10)
    );
    assert_eq!(
        FlushWorker::retry_delay(32, Duration::from_millis(179_950)),
        Duration::from_millis(50)
    );

    assert!(!FlushWorker::retry_exhausted(
        FLUSH_RETRY_MAX_ATTEMPTS - 1,
        FLUSH_RETRY_MAX_ELAPSED - Duration::from_millis(1),
    ));
    assert!(FlushWorker::retry_exhausted(
        FLUSH_RETRY_MAX_ATTEMPTS,
        Duration::ZERO,
    ));
    assert!(FlushWorker::retry_exhausted(0, FLUSH_RETRY_MAX_ELAPSED,));
}
