//! Standard RAGFS stack builder.
//!
//! Centralizes "register all built-in plugins + assemble the wrapper stack" so the binding has a
//! single construction path. Encryption is now applied per-backend inside `MountableFS::mount()`
//! (single backend) or `MountableFS::build_multi_write_fs()` (multi-write), so the global
//! `EncryptionWrappedFS` wrapper is removed from this builder.
//!
//! The top layer is always `StatsWrappedFS` so end-to-end timing (including crypto) is captured.
//! When pathlock is enabled, `PathLockWrappedFS` sits between `StatsWrappedFS` and `MountableFS`.

use std::sync::Arc;

use super::filesystem::FileSystem;
use super::mountable::MountableFS;
use super::stats_wrapper::StatsWrappedFS;

use crate::lock::{
    FilesystemPathLockProvider, MemoryPathLockProvider, PathLockConfig, PathLockManager,
    PathLockProvider, PathLockWrappedFS,
};

#[cfg(feature = "s3")]
use crate::plugins::S3FSPlugin;
use crate::plugins::{
    KVFSPlugin, LocalFSPlugin, MemFSPlugin, QueueFSPlugin, SQLFSPlugin, ServerInfoFSPlugin,
};

/// Sectioned binding configuration (mirrors the ov.conf sectioned layout).
///
/// New capabilities are added as new optional sections here, without changing
/// `build_default_stack`'s signature.
pub struct RagfsConfig {
    /// Encryption section: `None` → plaintext stack; `Some` → per-backend encryption wrapping.
    pub encryption: Option<EncryptionConfig>,
    /// PathLock configuration. OpenViking always requires PathLock to be present.
    pub pathlock: PathLockConfig,
}

impl Default for RagfsConfig {
    fn default() -> Self {
        Self {
            encryption: None,
            pathlock: PathLockConfig::default(),
        }
    }
}

/// Encryption section: root key fixed and immutable at construction time.
pub struct EncryptionConfig {
    /// 32-byte root key (L1).
    pub root_key: [u8; 32],
    /// Provider marker written into envelope headers.
    pub provider_type: u8,
}

/// The assembled stack handles returned by the builder.
pub struct RagfsStack {
    /// Mount manager (mount/unmount/list/stats/register_plugin live here).
    pub mountable: Arc<MountableFS>,
    /// Data entry point: `Stats(PathLock(Mountable))`.
    pub top: Arc<dyn FileSystem>,
    /// PathLock manager. OpenViking always constructs the stack with PathLock enabled.
    pub pathlock_manager: Arc<PathLockManager>,
}

/// Build the standard RAGFS stack.
///
/// Encryption config is forwarded to `MountableFS` so it can wrap individual backends
/// with `EncryptionWrappedFS` during `mount()`. The top is always `StatsWrappedFS`.
/// PathLockWrappedFS is always inserted between stats and mountable.
pub async fn build_default_stack(config: RagfsConfig) -> RagfsStack {
    let mountable = Arc::new(MountableFS::new());
    build_stack_with_mountable(config, mountable).await
}

/// Build the standard wrapper stack around an existing mount manager.
///
/// This is used by the binding's cache-aware mountable so cache and non-cache
/// construction share identical encryption and pathlock assembly.
pub async fn build_stack_with_mountable(
    config: RagfsConfig,
    mountable: Arc<MountableFS>,
) -> RagfsStack {
    register_builtin_plugins(&mountable).await;

    // Forward encryption config to MountableFS for per-backend wrapping.
    if let Some(enc) = &config.encryption {
        mountable
            .set_encryption_config(Some(enc.root_key), Some(enc.provider_type))
            .await;
    }

    let provider: Arc<dyn PathLockProvider> = match config.pathlock.provider.as_str() {
        "memory" => Arc::new(MemoryPathLockProvider::new()),
        _ => Arc::new(FilesystemPathLockProvider::new(
            mountable.clone() as Arc<dyn FileSystem>,
        )),
    };
    let pathlock_manager = Arc::new(PathLockManager::new(
        mountable.clone() as Arc<dyn FileSystem>,
        provider,
        config.pathlock.clone(),
    ));
    // Inject into MountableFS so EncryptionWrappedFS can use it for dual-path exact lock.
    mountable
        .set_pathlock_manager(pathlock_manager.clone())
        .await;
    let inner: Arc<dyn FileSystem> = Arc::new(PathLockWrappedFS::new(
        pathlock_manager.clone(),
        mountable.clone() as Arc<dyn FileSystem>,
    ));

    let top: Arc<dyn FileSystem> = Arc::new(StatsWrappedFS::with_arc(inner));
    RagfsStack { mountable, top, pathlock_manager }
}

/// The single built-in plugin registration sequence (eliminates drift across call sites).
pub async fn register_builtin_plugins(fs: &MountableFS) {
    fs.register_plugin(MemFSPlugin).await;
    fs.register_plugin(KVFSPlugin).await;
    fs.register_plugin(QueueFSPlugin::new()).await;
    fs.register_plugin(SQLFSPlugin::new()).await;
    fs.register_plugin(LocalFSPlugin::new()).await;
    fs.register_plugin(ServerInfoFSPlugin::new()).await;
    #[cfg(feature = "s3")]
    fs.register_plugin(S3FSPlugin::new()).await;
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::context::{FsContextInner, PathLockContext, FS_CTX};
    use crate::core::{ConfigValue, PluginConfig, Result, WriteFlag};
    use crate::crypto;
    use std::collections::HashMap;
    use std::fs;
    use tempfile::TempDir;

    fn enc_config() -> RagfsConfig {
        RagfsConfig {
            encryption: Some(EncryptionConfig {
                root_key: [4u8; 32],
                provider_type: crypto::PROVIDER_LOCAL,
            }),
            ..RagfsConfig::default()
        }
    }

    fn enc_pathlock_config() -> RagfsConfig {
        RagfsConfig {
            encryption: Some(EncryptionConfig {
                root_key: [4u8; 32],
                provider_type: crypto::PROVIDER_LOCAL,
            }),
            pathlock: PathLockConfig {
                provider: "filesystem".to_string(),
                lock_timeout_secs: 0.0,
                lock_expire_secs: 1800.0,
            },
        }
    }

    /// Create a plugin config for stack builder tests.
    fn plugin_config(
        name: &str,
        mount_path: &str,
        params: HashMap<String, ConfigValue>,
    ) -> PluginConfig {
        PluginConfig::single_backend(name, mount_path, params)
    }

    async fn mount_mem(stack: &RagfsStack) {
        stack
            .mountable
            .mount(plugin_config("memfs", "/mem", HashMap::new()))
            .await
            .unwrap();
    }

    /// Mount a LocalFS backend backed by a temporary directory.
    async fn mount_local(stack: &RagfsStack, mount_path: &str, local_dir: &str) -> Result<()> {
        let mut params = HashMap::new();
        params.insert(
            "local_dir".to_string(),
            ConfigValue::String(local_dir.to_string()),
        );
        stack
            .mountable
            .mount(plugin_config("localfs", mount_path, params))
            .await
    }

    #[tokio::test]
    async fn encrypted_stack_encrypts_on_disk() {
        let stack = build_default_stack(enc_config()).await;
        mount_mem(&stack).await;

        let ctx = Arc::new(FsContextInner::new("tenant"));
        let top = stack.top.clone();
        FS_CTX
            .scope(ctx, async {
                top.write("/mem/f", b"hello", 0, WriteFlag::Create)
                    .await
                    .unwrap();
                assert_eq!(top.read("/mem/f", 0, 0).await.unwrap(), b"hello");
            })
            .await;

        // Read raw bytes from mountable (bypasses encryption layer) to verify ciphertext.
        let raw = stack.mountable.read_raw("/mem/f", 0, 0).await.unwrap();
        assert!(crypto::is_encrypted(&raw));
    }

    #[tokio::test]
    async fn encrypted_stack_with_pathlock_does_not_reacquire_final_lock() {
        let stack = build_default_stack(enc_pathlock_config()).await;
        mount_mem(&stack).await;

        let write = FS_CTX.scope(Arc::new(FsContextInner::new("tenant")), async {
            stack
                .top
                .write("/mem/f", b"hello", 0, WriteFlag::Create)
                .await
        });
        tokio::time::timeout(std::time::Duration::from_millis(200), write)
            .await
            .expect("encrypted write blocked on its own final-path lock")
            .unwrap();

        let rewrite = FS_CTX.scope(Arc::new(FsContextInner::new("tenant")), async {
            stack
                .top
                .write("/mem/f", b"updated", 0, WriteFlag::Create)
                .await
        });
        tokio::time::timeout(std::time::Duration::from_millis(200), rewrite)
            .await
            .expect("encrypted rewrite blocked on an unreleased lock token")
            .unwrap();
    }

    #[tokio::test]
    async fn encrypted_write_extends_outer_lease() {
        let stack = build_default_stack(enc_pathlock_config()).await;
        mount_mem(&stack).await;
        let manager = &stack.pathlock_manager;
        let outer = manager
            .acquire_exact("/mem/f", std::time::Duration::ZERO, None)
            .await
            .unwrap();
        let ctx = Arc::new(FsContextInner::with_pathlock(
            "tenant",
            PathLockContext {
                lease_ref: Some(outer.lease.lease_ref.clone()),
                disable_auto_pathlock: false,
            },
        ));

        let write = FS_CTX.scope(ctx, async {
            stack
                .top
                .write("/mem/f", b"hello", 0, WriteFlag::Create)
                .await
        });
        tokio::time::timeout(std::time::Duration::from_millis(200), write)
            .await
            .expect("encrypted write blocked on its outer final-path lease")
            .unwrap();

        manager.release(&outer).await.unwrap();
    }

    #[tokio::test]
    async fn encrypted_write_rejects_uncovered_outer_lease() {
        let stack = build_default_stack(enc_pathlock_config()).await;
        mount_mem(&stack).await;
        let manager = &stack.pathlock_manager;
        let outer = manager
            .acquire_exact("/mem/other", std::time::Duration::ZERO, None)
            .await
            .unwrap();
        let ctx = Arc::new(FsContextInner::with_pathlock(
            "tenant",
            PathLockContext {
                lease_ref: Some(outer.lease.lease_ref.clone()),
                disable_auto_pathlock: false,
            },
        ));

        let error = FS_CTX
            .scope(ctx, async {
                stack
                    .top
                    .write("/mem/f", b"hello", 0, WriteFlag::Create)
                    .await
            })
            .await
            .unwrap_err();

        assert!(
            error
                .to_string()
                .contains("does not cover the requested operation")
        );
        manager.release(&outer).await.unwrap();
    }

    #[tokio::test]
    async fn default_stack_initializes_pathlock_manager() {
        let stack = build_default_stack(RagfsConfig::default()).await;
        let snapshot = stack.pathlock_manager.observe().await;
        assert_eq!(snapshot.active_locks, 0);
    }

    #[tokio::test]
    async fn plaintext_stack_has_no_encryption_layer() {
        let stack = build_default_stack(RagfsConfig::default()).await;
        mount_mem(&stack).await;

        // No FS_CTX scope needed; bytes are stored verbatim.
        stack
            .top
            .write("/mem/f", b"hello", 0, WriteFlag::Create)
            .await
            .unwrap();
        let raw = stack.mountable.read_raw("/mem/f", 0, 0).await.unwrap();
        assert_eq!(raw, b"hello", "plaintext stack stores raw bytes");
    }

    #[tokio::test]
    async fn encrypted_stack_preserves_queuefs_control_semantics() {
        let stack = build_default_stack(enc_config()).await;
        stack
            .mountable
            .mount(plugin_config("queuefs", "/queue", HashMap::new()))
            .await
            .unwrap();

        let ctx = Arc::new(FsContextInner::new("_system"));
        let top = stack.top.clone();
        FS_CTX
            .scope(ctx, async {
                top.mkdir("/queue/semantic", 0o755).await.unwrap();
                top.write("/queue/semantic/enqueue", b"payload", 0, WriteFlag::None)
                    .await
                    .unwrap();

                let size = top.read("/queue/semantic/size", 0, 0).await.unwrap();
                assert_eq!(String::from_utf8(size).unwrap(), "1");

                let msg = top.read("/queue/semantic/dequeue", 0, 0).await.unwrap();
                assert!(!crypto::is_encrypted(&msg));
            })
            .await;
    }

    #[tokio::test]
    async fn encrypted_mount_writes_backend_shape_guard() {
        let dir = TempDir::new().unwrap();
        let stack = build_default_stack(enc_config()).await;

        mount_local(&stack, "/local", dir.path().to_str().unwrap())
            .await
            .unwrap();

        let manifest = fs::read(dir.path().join("backend_meta.json")).unwrap();
        assert!(crypto::is_encrypted(&manifest));
        let account_key = crypto::hkdf_sha256(&[4u8; 32], b"_system");
        let (_provider, enc_key, key_iv, data_iv, ciphertext) =
            crypto::parse_envelope(&manifest).unwrap();
        let file_key =
            crypto::aes_gcm_decrypt(&account_key, key_iv.try_into().unwrap(), enc_key).unwrap();
        let file_key: [u8; 32] = file_key.as_slice().try_into().unwrap();
        let plaintext =
            crypto::aes_gcm_decrypt(&file_key, data_iv.try_into().unwrap(), ciphertext).unwrap();
        assert!(plaintext.is_empty());
    }

    #[tokio::test]
    async fn encrypted_mount_rejects_legacy_plaintext_backend_without_manifest() {
        let dir = TempDir::new().unwrap();
        fs::write(dir.path().join("legacy.txt"), b"plaintext").unwrap();

        let stack = build_default_stack(enc_config()).await;
        let err = mount_local(&stack, "/local", dir.path().to_str().unwrap())
            .await
            .unwrap_err();

        match err {
            crate::core::errors::Error::Config(message) => {
                assert!(message.contains("backend storage shape mismatch"));
            }
            other => panic!("unexpected error: {other:?}"),
        }
    }

    #[tokio::test]
    async fn encrypted_mount_ignores_legacy_plaintext_task_records() {
        let dir = TempDir::new().unwrap();
        let account_task_dir = dir.path().join("default/_system/tasks/default");
        let system_task_dir = dir.path().join("_system/tasks/root");
        fs::create_dir_all(&account_task_dir).unwrap();
        fs::create_dir_all(&system_task_dir).unwrap();
        fs::write(
            account_task_dir.join("00f676a8-b8cf-4b1a-a0a9-fe46ca3324d3.json"),
            br#"{"task_id":"account-task"}"#,
        )
        .unwrap();
        fs::write(
            system_task_dir.join("00f676a8-b8cf-4b1a-a0a9-fe46ca3324d4.json"),
            br#"{"task_id":"system-task"}"#,
        )
        .unwrap();

        let stack = build_default_stack(enc_config()).await;
        mount_local(&stack, "/local", dir.path().to_str().unwrap())
            .await
            .unwrap();

        let manifest = fs::read(dir.path().join("backend_meta.json")).unwrap();
        assert!(crypto::is_encrypted(&manifest));
    }
}
