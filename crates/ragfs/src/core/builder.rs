//! Standard RAGFS stack builder.
//!
//! Centralizes "register all built-in plugins + assemble the wrapper stack" so the binding has a
//! single construction path. Whether the encryption layer is present is decided *here, at
//! construction time*, by whether `RagfsConfig::encryption` is set (see §2.4/§2.6 of the design).

use std::sync::Arc;

use super::encryption_wrapper::EncryptionWrappedFS;
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
    /// Encryption section: `None` → plaintext stack; `Some` → wrap an `EncryptionWrappedFS`.
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
    /// Data entry point: `Stats(Encryption(Mountable))` or `Stats(Mountable)`.
    pub top: Arc<dyn FileSystem>,
}

/// Build the standard RAGFS stack.
///
/// `config.encryption == None` → no encryption layer (plaintext); `Some` → insert
/// `EncryptionWrappedFS` in the middle. The top is always `StatsWrappedFS` so end-to-end timing
/// (including crypto) is captured.
pub async fn build_default_stack(config: RagfsConfig) -> RagfsStack {
    let mountable = Arc::new(MountableFS::new());
    register_builtin_plugins(&mountable).await;

    let data_fs: Arc<dyn FileSystem> = match config.encryption {
        Some(enc) => Arc::new(EncryptionWrappedFS::new(
            mountable.clone() as Arc<dyn FileSystem>,
            enc.root_key,
            enc.provider_type,
        )),
        None => mountable.clone() as Arc<dyn FileSystem>,
    };

    let top: Arc<dyn FileSystem> = Arc::new(StatsWrappedFS::with_arc(data_fs));
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
    use crate::core::{PluginConfig, WriteFlag};
    use crate::crypto;
    use std::collections::HashMap;

    fn enc_config() -> RagfsConfig {
        RagfsConfig {
            encryption: Some(EncryptionConfig {
                root_key: [4u8; 32],
                provider_type: crypto::PROVIDER_LOCAL,
            }),
        }
    }

    async fn mount_mem(stack: &RagfsStack) {
        stack
            .mountable
            .mount(PluginConfig {
                name: "memfs".to_string(),
                mount_path: "/mem".to_string(),
                params: HashMap::new(),
            })
            .await
            .unwrap();
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

        // Underlying mountable holds ciphertext.
        let raw = stack.mountable.read("/mem/f", 0, 0).await.unwrap();
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
        let raw = stack.mountable.read("/mem/f", 0, 0).await.unwrap();
        assert_eq!(raw, b"hello", "plaintext stack stores raw bytes");
    }

    #[tokio::test]
    async fn encrypted_stack_preserves_queuefs_control_semantics() {
        let stack = build_default_stack(enc_config()).await;
        stack
            .mountable
            .mount(PluginConfig {
                name: "queuefs".to_string(),
                mount_path: "/queue".to_string(),
                params: HashMap::new(),
            })
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
}
