use super::*;
use crate::lock::{MemoryPathLockProvider, PathLockConfig, PathLockManager};
use crate::multibackend::provider::FilesystemProvider;
use crate::plugins::memfs::MemFileSystem;

/// Verifies startup skips one account whose V2 partition metadata is incomplete.
#[tokio::test]
async fn build_skips_account_with_missing_segment_manifest() {
    let primary: Arc<dyn FileSystem> = Arc::new(MemFileSystem::new());
    let manager = Arc::new(PathLockManager::new(
        primary.clone(),
        Arc::new(MemoryPathLockProvider::new()),
        PathLockConfig::default(),
    ));
    let store = Arc::new(MetadataStore::new(primary.clone(), manager, "/local").unwrap());
    store.initialize_account("account", 2, &[]).await.unwrap();
    let missing = store.paths().segment_manifest("account", 0).unwrap().0;
    store.remove_file(&missing).await.unwrap();
    let provider = Arc::new(FilesystemProvider::new(store.clone()));

    let result = MultiWriteWrappedFS::builder(primary)
        .with_metadata_store(store)
        .with_metadata_provider(provider)
        .initial_partitions(2)
        .build_inactive()
        .await;

    assert!(result.is_ok(), "build failed: {:?}", result.err());
}
