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

/// Verifies delete events do not override the active account registry.
#[tokio::test]
async fn delete_account_event_keeps_active_backup_account() {
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
    let catch_up = Arc::new(CatchUpWorker::new(
        "backup".to_string(),
        primary.clone(),
        backup.clone(),
        store.clone(),
        provider.clone(),
    ));
    let mut worker = FlushWorker::new(
        store,
        provider,
        1,
        vec!["backup".to_string()],
        Some(primary),
    );

    worker
        .flush_batch(vec![PendingEvent {
            operation_id: uuid::Uuid::from_u128(1),
            account_id: "account".to_string(),
            kind: PendingEventKind::DeleteAccount,
            path: "/local/account".to_string(),
            destination_path: None,
            route_hint: None,
        }])
        .await;
    catch_up.run_once().await.unwrap();

    assert!(backup.stat("/account").await.unwrap().is_dir);
}
