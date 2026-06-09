//! Encryption wrapper — a horizontal cross-cutting `FileSystem` layer.
//!
//! Wraps any `FileSystem` and transparently encrypts content on write / decrypts on read using
//! three-layer envelope encryption (see [`crate::crypto`]). It is only inserted into the stack
//! when an encryption config (root_key) is present (see `builder::build_default_stack`); when
//! present, this layer *always* encrypts.
//!
//! Only `read`/`write`/`grep` carry crypto logic; every other trait method is delegated verbatim
//! to `self.inner` so plugin-native behavior/optimizations are preserved.

use std::collections::HashMap;
use std::sync::{Arc, RwLock};

use async_trait::async_trait;

use crate::crypto;
use crate::shape::SHAPE_MANIFEST_PATH;

use super::context::FsContextView;
use super::errors::{Error, Result};
use super::filesystem::{compile_grep_regex, normalize_prefix_path, FileSystem};
use super::types::{FileInfo, GrepResult, TreeEntry, WriteFlag};

const SYSTEM_ACCOUNT_ID: &str = "_system";

/// A `FileSystem` wrapper that applies envelope encryption to file content.
pub struct EncryptionWrappedFS {
    /// Wrapped lower layer (typically `MountableFS`).
    inner: Arc<dyn FileSystem>,
    /// Root key, fixed at construction (immutable).
    root_key: [u8; 32],
    /// Provider marker written into the envelope header (immutable).
    provider_type: u8,
    /// Lazily-derived Account Key cache, keyed by account_id.
    account_keys: RwLock<HashMap<String, [u8; 32]>>,
}

impl EncryptionWrappedFS {
    /// Get the wrapped filesystem for specialized delegation.
    pub(crate) fn inner_fs(&self) -> &Arc<dyn FileSystem> {
        &self.inner
    }

    /// Construct an encryption layer over `inner`. Built only when a root key is configured.
    pub fn new(inner: Arc<dyn FileSystem>, root_key: [u8; 32], provider_type: u8) -> Self {
        Self {
            inner,
            root_key,
            provider_type,
            account_keys: RwLock::new(HashMap::new()),
        }
    }

    /// Derive (and cache) the Account Key for `account_id` via HKDF (L2).
    fn get_account_key(&self, account_id: &str) -> [u8; 32] {
        if let Some(k) = self.account_keys.read().unwrap().get(account_id) {
            return *k;
        }
        let k = crypto::hkdf_sha256(&self.root_key, account_id.as_bytes());
        self.account_keys
            .write()
            .unwrap()
            .insert(account_id.to_string(), k);
        k
    }

    /// Read the tenant `account_id` from the task-local context, erroring if absent
    /// (never silently use a wrong/default tenant key).
    fn require_account_id(&self) -> Result<String> {
        FsContextView::current()
            .account_id()
            .map(str::to_string)
            .ok_or_else(|| Error::internal("account_id missing in FsContext"))
    }

    /// Decrypt a full envelope blob into plaintext using the given account key.
    fn decrypt_envelope(&self, account_id: &str, data: &[u8]) -> Result<Vec<u8>> {
        let account_key = self.get_account_key(account_id);
        let (_p, enc_key, key_iv, data_iv, ct) = crypto::parse_envelope(data)?;
        let key_iv: &[u8; 12] = key_iv
            .try_into()
            .map_err(|_| Error::internal("invalid key_iv length in envelope"))?;
        let data_iv: &[u8; 12] = data_iv
            .try_into()
            .map_err(|_| Error::internal("invalid data_iv length in envelope"))?;
        let file_key_bytes = crypto::aes_gcm_decrypt(&account_key, key_iv, enc_key)?;
        let file_key: [u8; 32] = file_key_bytes
            .as_slice()
            .try_into()
            .map_err(|_| Error::internal("invalid file key length"))?;
        crypto::aes_gcm_decrypt(&file_key, data_iv, ct)
    }

    /// Return true for virtual plugin control paths whose read/write payload is protocol data, not
    /// user file content. Those operations must preserve plugin semantics and bypass encryption.
    fn should_passthrough_content(path: &str) -> bool {
        path == "/queue"
            || path.starts_with("/queue/")
            || path == "/serverinfo"
            || path.starts_with("/serverinfo/")
    }

    /// Return true when one path points at the backend-shape manifest.
    fn is_shape_manifest_path(path: &str) -> bool {
        path == SHAPE_MANIFEST_PATH || path.ends_with(SHAPE_MANIFEST_PATH)
    }

    /// Derive the encryption account domain from a filesystem path.
    ///
    /// `/local/{account_id}/...` paths are tenant-scoped and use that account id. Control or
    /// non-local paths do not encode a tenant and therefore use the reserved system account.
    fn encryption_account_domain(path: &str) -> &str {
        let trimmed = path.trim_start_matches('/');
        let mut parts = trimmed.split('/');
        match (parts.next(), parts.next()) {
            (Some("local"), Some(account_id)) if !account_id.is_empty() => account_id,
            _ => SYSTEM_ACCOUNT_ID,
        }
    }

    /// Reject raw path moves across different encryption account domains.
    fn ensure_same_encryption_domain(old_path: &str, new_path: &str) -> Result<()> {
        let old_account = Self::encryption_account_domain(old_path);
        let new_account = Self::encryption_account_domain(new_path);
        if old_account != new_account {
            return Err(Error::internal(format!(
                "cross-account rename is not supported for encrypted blobs: {:?} -> {:?}",
                old_account, new_account
            )));
        }
        Ok(())
    }
}

/// Slice `data` by `(offset, size)` with `size == 0` meaning "to end", matching plugin read semantics.
fn slice_bytes(data: Vec<u8>, offset: u64, size: u64) -> Vec<u8> {
    let len = data.len() as u64;
    if offset >= len {
        return Vec::new();
    }
    let start = offset as usize;
    let end = if size == 0 {
        data.len()
    } else {
        ((offset + size).min(len)) as usize
    };
    data[start..end].to_vec()
}

#[async_trait]
impl FileSystem for EncryptionWrappedFS {
    // ── content methods: encryption logic ──

    async fn read(&self, path: &str, offset: u64, size: u64) -> Result<Vec<u8>> {
        if Self::should_passthrough_content(path) {
            return self.inner.read(path, offset, size).await;
        }

        // The envelope is an indivisible whole, so always read the full blob, decrypt, then slice
        // in memory (matches the legacy Python encryptor behavior). Single-form is guaranteed by
        // the startup probe, so this layer always expects ciphertext.
        let data = self.inner.read(path, 0, 0).await?;
        let account_id = self.require_account_id()?;
        let plaintext = self.decrypt_envelope(&account_id, &data)?;
        Ok(slice_bytes(plaintext, offset, size))
    }

    async fn write(&self, path: &str, data: &[u8], offset: u64, flags: WriteFlag) -> Result<u64> {
        if Self::should_passthrough_content(path) {
            return self.inner.write(path, data, offset, flags).await;
        }

        // The envelope is indivisible: a partial write (offset > 0) would splice a fragment into
        // the middle of ciphertext and corrupt it. Binding writes are always whole-file (offset=0).
        if offset != 0 {
            return Err(Error::internal(
                "encrypted write only supports whole-file write (offset must be 0)",
            ));
        }
        if !matches!(flags, WriteFlag::Create | WriteFlag::Truncate) {
            return Err(Error::internal(
                "encrypted write requires whole-blob replacement (flags must be Create or Truncate)",
            ));
        }
        let account_id = self.require_account_id()?;
        let account_key = self.get_account_key(&account_id);

        let file_key: [u8; 32] = rand::random();
        let data_iv: [u8; 12] = rand::random();
        let key_iv: [u8; 12] = rand::random();

        let ct = crypto::aes_gcm_encrypt(&file_key, &data_iv, data)?;
        let enc_key = crypto::aes_gcm_encrypt(&account_key, &key_iv, &file_key)?;
        let envelope = crypto::build_envelope(self.provider_type, &enc_key, &key_iv, &data_iv, &ct);
        self.inner.write(path, &envelope, 0, flags).await
    }

    async fn grep(
        &self,
        path: &str,
        pattern: &str,
        recursive: bool,
        case_insensitive: bool,
        node_limit: Option<usize>,
        exclude_path: Option<&str>,
        level_limit: Option<usize>,
    ) -> Result<GrepResult> {
        if Self::should_passthrough_content(path) {
            return self
                .inner
                .grep(
                    path,
                    pattern,
                    recursive,
                    case_insensitive,
                    node_limit,
                    exclude_path,
                    level_limit,
                )
                .await;
        }

        // This layer only exists when encryption is on, so always traverse via the trait default
        // grep_internal -> grep_file -> self.read (which decrypts). Never delegate to inner.grep:
        // the plugin override would run the regex against raw ciphertext and miss every file.
        let re = compile_grep_regex(pattern, case_insensitive)?;
        let base = normalize_prefix_path(path);
        let excl = exclude_path.map(normalize_prefix_path);
        let mut result = GrepResult::new();
        self.grep_internal(
            &base,
            &base,
            &re,
            recursive,
            node_limit,
            excl.as_deref(),
            level_limit,
            &mut result,
        )
        .await?;
        Ok(result)
    }

    // ── non-content methods: explicit verbatim delegation (preserve plugin behavior) ──

    async fn create(&self, path: &str) -> Result<()> {
        if Self::should_passthrough_content(path) {
            return self.inner.create(path).await;
        }

        // Empty files still have content shape: in encrypted mode they must be stored as a valid
        // envelope so later reads do not see a plaintext zero-byte blob.
        self.write(path, b"", 0, WriteFlag::Create).await?;
        Ok(())
    }

    async fn mkdir(&self, path: &str, mode: u32) -> Result<()> {
        self.inner.mkdir(path, mode).await
    }

    async fn remove(&self, path: &str) -> Result<()> {
        self.inner.remove(path).await
    }

    async fn remove_all(&self, path: &str) -> Result<()> {
        self.inner.remove_all(path).await
    }

    async fn read_dir(&self, path: &str) -> Result<Vec<FileInfo>> {
        let entries = self.inner.read_dir(path).await?;
        Ok(entries
            .into_iter()
            .filter(|entry| entry.name != SHAPE_MANIFEST_PATH.trim_start_matches('/'))
            .collect())
    }

    async fn stat(&self, path: &str) -> Result<FileInfo> {
        self.inner.stat(path).await
    }

    async fn rename(&self, old_path: &str, new_path: &str) -> Result<()> {
        // Pure path operation is only safe within the same encryption account domain. Crossing
        // domains would move ciphertext under a different derived account key and make it unreadable.
        Self::ensure_same_encryption_domain(old_path, new_path)?;
        self.inner.rename(old_path, new_path).await
    }

    async fn chmod(&self, path: &str, mode: u32) -> Result<()> {
        self.inner.chmod(path, mode).await
    }

    async fn tree_directory(
        &self,
        path: &str,
        show_hidden: bool,
        node_limit: Option<usize>,
        level_limit: Option<usize>,
    ) -> Result<Vec<TreeEntry>> {
        // Metadata only — no content read, so delegate to preserve plugin-native tree optimizations.
        let entries = self
            .inner
            .tree_directory(path, show_hidden, node_limit, level_limit)
            .await?;
        Ok(entries
            .into_iter()
            .filter(|entry| !Self::is_shape_manifest_path(&entry.path))
            .collect())
    }

    async fn ensure_parent_dirs(&self, path: &str, mode: u32) -> Result<()> {
        self.inner.ensure_parent_dirs(path, mode).await
    }

    // `truncate` and `exists` are intentionally NOT overridden:
    // - truncate default = self.read -> resize -> self.write, which goes through this wrapper and
    //   yields "decrypt whole -> truncate -> re-encrypt whole" (correct; no plugin truncate to lose).
    // - exists default = self.stat(..).is_ok(), unrelated to encryption.
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::context::{FsContextInner, FS_CTX};
    use crate::core::MountableFS;
    use crate::core::PluginConfig;
    use crate::plugins::MemFSPlugin;

    /// Build a memfs plugin config for encryption wrapper tests.
    fn memfs_config(mount_path: &str) -> PluginConfig {
        PluginConfig::single_backend("memfs", mount_path, HashMap::new())
    }

    /// Build a memfs-backed stack mounted at the default path.
    async fn memfs_stack() -> Arc<MountableFS> {
        memfs_stack_at("/mem").await
    }

    /// Build a memfs-backed stack mounted at the provided path for path-sensitive tests.
    async fn memfs_stack_at(mount_path: &str) -> Arc<MountableFS> {
        let m = Arc::new(MountableFS::new());
        m.register_plugin(MemFSPlugin).await;
        m.mount(memfs_config(mount_path)).await.unwrap();
        m
    }

    fn ctx(account: &str) -> Arc<FsContextInner> {
        Arc::new(FsContextInner::new(account))
    }

    #[tokio::test]
    async fn write_then_read_roundtrip_under_ctx() {
        let inner = memfs_stack().await;
        let enc = EncryptionWrappedFS::new(inner, [9u8; 32], crypto::PROVIDER_LOCAL);

        FS_CTX
            .scope(ctx("tenant-1"), async {
                enc.write("/mem/a.txt", b"super secret", 0, WriteFlag::Create)
                    .await
                    .unwrap();
                let out = enc.read("/mem/a.txt", 0, 0).await.unwrap();
                assert_eq!(out, b"super secret");
            })
            .await;
    }

    #[tokio::test]
    async fn on_disk_bytes_are_ciphertext_with_envelope() {
        let inner = memfs_stack().await;
        let enc = EncryptionWrappedFS::new(inner.clone(), [9u8; 32], crypto::PROVIDER_LOCAL);

        FS_CTX
            .scope(ctx("tenant-1"), async {
                enc.write("/mem/a.txt", b"plttext", 0, WriteFlag::Create)
                    .await
                    .unwrap();
            })
            .await;

        // Raw read through inner (no encryption layer) yields the envelope bytes.
        let raw = inner.read("/mem/a.txt", 0, 0).await.unwrap();
        assert!(crypto::is_encrypted(&raw), "on-disk must be enveloped");
        assert_ne!(raw, b"plttext");
    }

    #[tokio::test]
    async fn read_without_account_id_errors() {
        let inner = memfs_stack().await;
        let enc = EncryptionWrappedFS::new(inner.clone(), [9u8; 32], crypto::PROVIDER_LOCAL);
        // Seed an envelope first.
        FS_CTX
            .scope(ctx("tenant-1"), async {
                enc.write("/mem/a.txt", b"x", 0, WriteFlag::Create)
                    .await
                    .unwrap();
            })
            .await;
        // Read with no FS_CTX scope -> account_id missing -> error (not silent ciphertext).
        let err = enc.read("/mem/a.txt", 0, 0).await;
        assert!(err.is_err());
    }

    #[tokio::test]
    async fn read_offset_size_slicing() {
        let inner = memfs_stack().await;
        let enc = EncryptionWrappedFS::new(inner, [1u8; 32], crypto::PROVIDER_LOCAL);
        FS_CTX
            .scope(ctx("t"), async {
                enc.write("/mem/a.txt", b"0123456789", 0, WriteFlag::Create)
                    .await
                    .unwrap();
                assert_eq!(enc.read("/mem/a.txt", 2, 3).await.unwrap(), b"234");
                assert_eq!(enc.read("/mem/a.txt", 8, 0).await.unwrap(), b"89");
                assert_eq!(enc.read("/mem/a.txt", 100, 0).await.unwrap(), b"");
            })
            .await;
    }

    #[tokio::test]
    async fn grep_matches_encrypted_files() {
        let inner = memfs_stack().await;
        let enc = EncryptionWrappedFS::new(inner, [2u8; 32], crypto::PROVIDER_LOCAL);
        FS_CTX
            .scope(ctx("t"), async {
                enc.write(
                    "/mem/a.txt",
                    b"alpha\nNEEDLE here\nbeta",
                    0,
                    WriteFlag::Create,
                )
                .await
                .unwrap();
                let res = enc
                    .grep("/mem", "NEEDLE", true, false, None, None, None)
                    .await
                    .unwrap();
                assert_eq!(res.count, 1);
                assert_eq!(res.matches[0].content, "NEEDLE here");
            })
            .await;
    }

    #[tokio::test]
    async fn write_rejects_nonzero_offset() {
        let inner = memfs_stack().await;
        let enc = EncryptionWrappedFS::new(inner, [2u8; 32], crypto::PROVIDER_LOCAL);
        FS_CTX
            .scope(ctx("t"), async {
                let r = enc.write("/mem/a.txt", b"x", 5, WriteFlag::Create).await;
                assert!(r.is_err());
            })
            .await;
    }

    #[tokio::test]
    async fn create_writes_encrypted_empty_file() {
        let inner = memfs_stack().await;
        let enc = EncryptionWrappedFS::new(inner.clone(), [2u8; 32], crypto::PROVIDER_LOCAL);
        FS_CTX
            .scope(ctx("t"), async {
                enc.create("/mem/empty.txt").await.unwrap();
                let plaintext = enc.read("/mem/empty.txt", 0, 0).await.unwrap();
                assert_eq!(plaintext, b"");
            })
            .await;

        let raw = inner.read("/mem/empty.txt", 0, 0).await.unwrap();
        assert!(crypto::is_encrypted(&raw));
    }

    #[tokio::test]
    async fn cross_account_cannot_decrypt() {
        let inner = memfs_stack().await;
        let enc = EncryptionWrappedFS::new(inner, [3u8; 32], crypto::PROVIDER_LOCAL);
        FS_CTX
            .scope(ctx("acct-A"), async {
                enc.write("/mem/a.txt", b"owned by A", 0, WriteFlag::Create)
                    .await
                    .unwrap();
            })
            .await;
        // Different account -> different account key -> file-key unwrap fails.
        let bad = FS_CTX
            .scope(ctx("acct-B"), async { enc.read("/mem/a.txt", 0, 0).await })
            .await;
        assert!(bad.is_err());
    }

    #[tokio::test]
    async fn write_rejects_append_flag() {
        let inner = memfs_stack().await;
        let enc = EncryptionWrappedFS::new(inner, [4u8; 32], crypto::PROVIDER_LOCAL);
        FS_CTX
            .scope(ctx("t"), async {
                enc.write("/mem/a.txt", b"first", 0, WriteFlag::Create)
                    .await
                    .unwrap();
                let appended = enc
                    .write("/mem/a.txt", b"second", 0, WriteFlag::Append)
                    .await;
                assert!(appended.is_err());
            })
            .await;
    }

    #[tokio::test]
    async fn write_rejects_non_replacing_none_flag() {
        let inner = memfs_stack().await;
        let enc = EncryptionWrappedFS::new(inner, [4u8; 32], crypto::PROVIDER_LOCAL);
        FS_CTX
            .scope(ctx("t"), async {
                enc.write("/mem/a.txt", b"first", 0, WriteFlag::Create)
                    .await
                    .unwrap();
                let rewritten = enc.write("/mem/a.txt", b"second", 0, WriteFlag::None).await;
                assert!(rewritten.is_err());
            })
            .await;
    }

    #[tokio::test]
    async fn rename_rejects_cross_account_paths() {
        let inner = memfs_stack_at("/local").await;
        let enc = EncryptionWrappedFS::new(inner, [5u8; 32], crypto::PROVIDER_LOCAL);
        let result = FS_CTX
            .scope(ctx("acct-a"), async {
                enc.mkdir("/local/acct-a", 0).await.unwrap();
                enc.mkdir("/local/acct-b", 0).await.unwrap();
                enc.write("/local/acct-a/file.txt", b"owned", 0, WriteFlag::Create)
                    .await
                    .unwrap();
                enc.rename("/local/acct-a/file.txt", "/local/acct-b/file.txt")
                    .await
            })
            .await;
        assert!(result.is_err());
    }
}
