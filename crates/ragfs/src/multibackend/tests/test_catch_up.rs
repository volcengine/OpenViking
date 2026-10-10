use super::*;

use std::collections::HashMap;
use std::sync::Arc;

use crate::core::context::{FsContextInner, FS_CTX};
use crate::core::encryption_wrapper::EncryptionWrappedFS;
use crate::core::{MountableFS, PluginConfig};
use crate::crypto;
use crate::lock::{MemoryPathLockProvider, PathLockConfig, PathLockManager};
use crate::plugins::memfs::MemFileSystem;
use crate::plugins::MemFSPlugin;

/// Build one encrypted MemFS backup and expose its raw mounted storage.
async fn encrypted_backup() -> (Arc<MountableFS>, EncryptionWrappedFS) {
    let raw = Arc::new(MountableFS::new());
    raw.register_plugin(MemFSPlugin).await;
    raw.mount(PluginConfig::single_backend(
        "memfs",
        "/mem",
        HashMap::new(),
    ))
    .await
    .unwrap();
    let manager = Arc::new(PathLockManager::new(
        raw.clone() as Arc<dyn FileSystem>,
        Arc::new(MemoryPathLockProvider::new()),
        PathLockConfig::default(),
    ));
    raw.set_pathlock_manager(manager.clone()).await;
    let encrypted = EncryptionWrappedFS::new(
        raw.clone(),
        [7_u8; 32],
        crypto::PROVIDER_LOCAL,
        manager,
        "/mem".to_string(),
    );
    (raw, encrypted)
}

/// Verify encrypted writes preserve their historical physical byte count.
#[tokio::test]
async fn encrypted_write_returns_physical_length() {
    let (raw, encrypted) = encrypted_backup().await;
    let data = b"plain";
    let written = FS_CTX
        .scope(
            Arc::new(FsContextInner::new("acct")),
            encrypted.write("/mem/file.txt", data, 0, WriteFlag::Create),
        )
        .await
        .unwrap();
    let stored = raw.read("/mem/file.txt", 0, 0).await.unwrap();

    assert_eq!(written, stored.len() as u64);
    assert!(written > data.len() as u64);
}

/// Verify catch-up accepts an encrypted backend's physical byte count.
#[tokio::test]
async fn reconcile_file_accepts_encrypted_write_length() {
    let primary = MemFileSystem::new();
    primary
        .ensure_parent_dirs("/mem/file.txt", 0o755)
        .await
        .unwrap();
    primary
        .write("/mem/file.txt", b"plain", 0, WriteFlag::Create)
        .await
        .unwrap();
    let (_, encrypted) = encrypted_backup().await;

    FS_CTX
        .scope(
            Arc::new(FsContextInner::new("acct")),
            reconcile_file(&primary, &encrypted, "/mem/file.txt"),
        )
        .await
        .unwrap();

    let copied = FS_CTX
        .scope(
            Arc::new(FsContextInner::new("acct")),
            encrypted.read("/mem/file.txt", 0, 0),
        )
        .await
        .unwrap();
    assert_eq!(copied, b"plain");
}
