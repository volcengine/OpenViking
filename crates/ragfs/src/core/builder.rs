//! Standard RAGFS stack builder.
//!
//! Centralizes "register all built-in plugins + assemble the wrapper stack" so the binding has a
//! single construction path. Encryption is now applied per-backend inside `MountableFS::mount()`
//! (single backend) or `MountableFS::build_multi_write_fs()` (multi-write), so the global
//! `EncryptionWrappedFS` wrapper is removed from this builder.
//!
//! The top layer is always `StatsWrappedFS` so end-to-end timing (including crypto) is captured.

use std::sync::Arc;

use super::filesystem::FileSystem;
use super::mountable::MountableFS;
use super::stats_wrapper::StatsWrappedFS;

#[cfg(feature = "s3")]
use crate::plugins::S3FSPlugin;
use crate::plugins::{
    KVFSPlugin, LocalFSPlugin, MemFSPlugin, QueueFSPlugin, SQLFSPlugin, ServerInfoFSPlugin,
};

/// Sectioned binding configuration (mirrors the ov.conf sectioned layout).
///
/// New capabilities are added as new optional sections here, without changing
/// `build_default_stack`'s signature.
#[derive(Default)]
pub struct RagfsConfig {
    /// Encryption section: `None` → plaintext stack; `Some` → per-backend encryption wrapping.
    pub encryption: Option<EncryptionConfig>,
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
    /// Data entry point: `Stats(Mountable)` — encryption is per-backend inside mount.
    pub top: Arc<dyn FileSystem>,
}

/// Build the standard RAGFS stack.
///
/// Encryption config is forwarded to `MountableFS` so it can wrap individual backends
/// with `EncryptionWrappedFS` during `mount()`. The top is always `StatsWrappedFS`.
pub async fn build_default_stack(config: RagfsConfig) -> RagfsStack {
    let mountable = Arc::new(MountableFS::new());
    register_builtin_plugins(&mountable).await;

    // Forward encryption config to MountableFS for per-backend wrapping.
    if let Some(enc) = &config.encryption {
        mountable
            .set_encryption_config(Some(enc.root_key), Some(enc.provider_type))
            .await;
    }

    // MountableFS is the data entry point; encryption is applied inside mount().
    let top: Arc<dyn FileSystem> = Arc::new(StatsWrappedFS::with_arc(
        mountable.clone() as Arc<dyn FileSystem>
    ));
    RagfsStack { mountable, top }
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
    use crate::core::context::{FsContextInner, FS_CTX};
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
}
