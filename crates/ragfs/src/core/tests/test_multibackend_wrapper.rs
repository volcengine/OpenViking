use super::*;
use crate::lock::{MemoryPathLockProvider, PathLockConfig, PathLockManager};
use crate::multibackend::provider::FilesystemProvider;
use crate::plugins::memfs::MemFileSystem;

/// Verifies startup skips one account whose V2 partition directory is missing.
#[tokio::test]
async fn build_skips_account_with_missing_partition_directory() {
    let primary: Arc<dyn FileSystem> = Arc::new(MemFileSystem::new());
    let manager = Arc::new(PathLockManager::new(
        primary.clone(),
        Arc::new(MemoryPathLockProvider::new()),
        PathLockConfig::default(),
    ));
    let store = Arc::new(MetadataStore::new(primary.clone(), manager, "/local").unwrap());
    store.initialize_account("account", 16, &[]).await.unwrap();
    let partitions_dir = store.paths().account_partitions("account").unwrap().0;
    store.remove_all(&format!("{partitions_dir}/0")).await.unwrap();
    let provider = Arc::new(FilesystemProvider::new(store.clone()));

    let result = MultiWriteWrappedFS::builder(primary)
        .with_metadata_store(store)
        .with_metadata_provider(provider)
        .initial_partitions(16)
        .build_inactive()
        .await;

    assert!(result.is_ok(), "build failed: {:?}", result.err());
}
