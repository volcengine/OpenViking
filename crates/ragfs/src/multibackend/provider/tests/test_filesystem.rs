use super::*;
use crate::core::FileSystem;
use crate::lock::{MemoryPathLockProvider, PathLockConfig, PathLockManager};
use crate::plugins::memfs::MemFileSystem;

/// Verifies flush publishes a contiguous range that committed reads can recover.
#[tokio::test]
async fn flush_publishes_a_complete_committed_range() {
    let raw_primary: Arc<dyn FileSystem> = Arc::new(MemFileSystem::new());
    let pathlock_manager = Arc::new(PathLockManager::new(
        raw_primary.clone(),
        Arc::new(MemoryPathLockProvider::new()),
        PathLockConfig::default(),
    ));
    let store = Arc::new(MetadataStore::new(raw_primary, pathlock_manager, "/local").unwrap());
    let partitions = store.initialize_account("account", 1, &[]).await.unwrap();
    let provider = FilesystemProvider::new(store);
    let scope = ScopeKey {
        account_id: "account".to_string(),
        partition_id: 0,
        epoch: partitions.epoch,
    };

    let result = provider
        .flush(
            None,
            vec![
                PendingEvent {
                    operation_id: uuid::Uuid::from_u128(1),
                    account_id: "account".to_string(),
                    kind: PendingEventKind::Data(SegmentEventType::Write),
                    path: "/local/account/first.txt".to_string(),
                    destination_path: None,
                    route_hint: None,
                },
                PendingEvent {
                    operation_id: uuid::Uuid::from_u128(2),
                    account_id: "account".to_string(),
                    kind: PendingEventKind::Data(SegmentEventType::Remove),
                    path: "/local/account/second.txt".to_string(),
                    destination_path: None,
                    route_hint: None,
                },
            ],
        )
        .await
        .unwrap();

    assert_eq!(result.scope, scope);
    assert_eq!((result.first_seq, result.last_seq), (1, 2));
    let records = provider.read_committed_range(&scope, 1, 3).await.unwrap();
    assert_eq!(
        records,
        vec![
            SegmentRecord {
                seq: 1,
                event_type: SegmentEventType::Write,
                path: "/local/account/first.txt".to_string(),
                op_id: None,
                destination_path: None,
            },
            SegmentRecord {
                seq: 2,
                event_type: SegmentEventType::Remove,
                path: "/local/account/second.txt".to_string(),
                op_id: None,
                destination_path: None,
            },
        ]
    );
    assert!(provider.read_committed_range(&scope, 2, 4).await.is_err());
}
