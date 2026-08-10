//! PathLockProvider trait and built-in implementations.
//!
//! The provider is a pure storage abstraction — it does not implement lock semantics
//! (conflict detection, waiting, etc.). Those belong to `PathLockManager`.

use std::collections::HashMap;
use std::sync::Arc;

use async_trait::async_trait;
use tokio::sync::RwLock;

use crate::crypto;
use crate::core::internal_names::is_hidden_runtime_lock_name;
use crate::core::FileSystem;

use super::codec::LockTokenCodec;
use super::types::{LockToken, PathLockError, PathLockResult};

/// Storage abstraction for lock tokens. Only `PathLockManager` should call these methods.
#[async_trait]
pub trait PathLockProvider: Send + Sync {
    /// Human-readable provider name for config/logging/metrics.
    fn name(&self) -> &'static str;

    /// Read the token at `lock_path`, returning `None` if no token exists.
    async fn read_token(&self, lock_path: &str) -> PathLockResult<Option<LockToken>>;

    /// Atomically create a token at `lock_path`. Must fail if a token already exists
    /// (unless it is stale and can be cleaned up by the manager).
    async fn try_create_token(&self, lock_path: &str, token: &LockToken) -> PathLockResult<()>;

    /// Atomically replace one exact token value.
    async fn compare_and_write_token(
        &self,
        lock_path: &str,
        expected: &LockToken,
        replacement: &LockToken,
    ) -> PathLockResult<bool>;

    /// Refresh the timestamp of an existing token owned by `owner_id`.
    /// Returns `true` if the token was refreshed, `false` if not found or wrong owner.
    async fn refresh_token(
        &self,
        lock_path: &str,
        owner_id: &str,
        time_ns: u128,
    ) -> PathLockResult<bool>;

    /// Remove the token at `lock_path` if owned by `owner_id`.
    /// Returns `true` if removed, `false` if not found or wrong owner.
    async fn remove_token(&self, lock_path: &str, owner_id: &str) -> PathLockResult<bool>;

    /// Scan all descendant lock paths under `root`.
    async fn scan_descendant_locks(&self, root: &str) -> PathLockResult<Vec<String>>;
}

// ── Memory provider ──

/// In-process lock table backed by a `HashMap`. Single-process only.
pub struct MemoryPathLockProvider {
    tokens: RwLock<HashMap<String, LockToken>>,
}

impl MemoryPathLockProvider {
    /// Create an empty in-memory provider.
    pub fn new() -> Self {
        Self {
            tokens: RwLock::new(HashMap::new()),
        }
    }
}

impl Default for MemoryPathLockProvider {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl PathLockProvider for MemoryPathLockProvider {
    fn name(&self) -> &'static str {
        "memory"
    }

    async fn read_token(&self, lock_path: &str) -> PathLockResult<Option<LockToken>> {
        Ok(self.tokens.read().await.get(lock_path).cloned())
    }

    async fn try_create_token(&self, lock_path: &str, token: &LockToken) -> PathLockResult<()> {
        let mut tokens = self.tokens.write().await;
        if tokens.contains_key(lock_path) {
            return Err(PathLockError::Conflict {
                lock_path: lock_path.to_string(),
                owner: tokens[lock_path].owner_id.clone(),
                kind: tokens[lock_path].lock_type,
            });
        }
        tokens.insert(lock_path.to_string(), token.clone());
        Ok(())
    }

    async fn compare_and_write_token(
        &self,
        lock_path: &str,
        expected: &LockToken,
        replacement: &LockToken,
    ) -> PathLockResult<bool> {
        let mut tokens = self.tokens.write().await;
        match tokens.get(lock_path) {
            Some(token) if token == expected => {
                tokens.insert(lock_path.to_string(), replacement.clone());
                Ok(true)
            }
            _ => Ok(false),
        }
    }

    async fn refresh_token(
        &self,
        lock_path: &str,
        owner_id: &str,
        time_ns: u128,
    ) -> PathLockResult<bool> {
        let mut tokens = self.tokens.write().await;
        match tokens.get_mut(lock_path) {
            Some(token) if token.owner_id == owner_id => {
                token.time_ns = time_ns;
                Ok(true)
            }
            _ => Ok(false),
        }
    }

    async fn remove_token(&self, lock_path: &str, owner_id: &str) -> PathLockResult<bool> {
        let mut tokens = self.tokens.write().await;
        match tokens.get(lock_path) {
            Some(token) if token.owner_id == owner_id => {
                tokens.remove(lock_path);
                Ok(true)
            }
            _ => Ok(false),
        }
    }

    async fn scan_descendant_locks(&self, root: &str) -> PathLockResult<Vec<String>> {
        let prefix = if root.ends_with('/') {
            root.to_string()
        } else {
            format!("{}/", root)
        };
        Ok(self
            .tokens
            .read()
            .await
            .keys()
            .filter(|k| k.starts_with(&prefix))
            .cloned()
            .collect())
    }
}

// ── Filesystem provider ──

/// Lock provider that stores tokens as files on the underlying filesystem.
pub struct FilesystemPathLockProvider {
    fs: Arc<dyn FileSystem>,
}

impl FilesystemPathLockProvider {
    /// Create a filesystem-backed provider.
    pub fn new(fs: Arc<dyn FileSystem>) -> Self {
        Self { fs }
    }

    /// Read raw token bytes from `lock_path`, returning `None` if no token exists.
    async fn read_token_raw(&self, lock_path: &str) -> PathLockResult<Option<Vec<u8>>> {
        match self.fs.read(lock_path, 0, 0).await {
            Ok(data) => Ok(Some(data)),
            Err(e) => {
                if matches!(
                    e,
                    crate::core::Error::NotFound(_) | crate::core::Error::MountPointNotFound(_)
                ) {
                    Ok(None)
                } else {
                    Err(PathLockError::Io(format!(
                        "failed to read lock token at '{lock_path}': {e}"
                    )))
                }
            }
        }
    }

    /// Map filesystem CAS failures into pathlock busy vs. fatal I/O outcomes.
    fn map_cas_error(operation: &str, lock_path: &str, error: crate::core::Error) -> PathLockError {
        match error {
            crate::core::Error::WouldBlock(message) => PathLockError::Busy {
                lock_path: lock_path.to_string(),
                operation: format!("{operation} CAS ({message})"),
            },
            other => PathLockError::Io(format!("{operation} CAS failed: {other}")),
        }
    }
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;

    use crate::core::{Error, FileSystem, WriteFlag};
    use crate::plugins::memfs::MemFileSystem;

    use super::*;

    /// Verify legacy encrypted lock files are treated as removable upgrade leftovers.
    #[tokio::test]
    async fn read_token_cleans_up_legacy_encrypted_lock() {
        let fs = Arc::new(MemFileSystem::new());
        fs.mkdir("/data", 0o755).await.unwrap();
        fs.write("/data/.path.ovlock", b"OVE1legacy-ciphertext", 0, WriteFlag::Create)
            .await
            .unwrap();
        let provider = FilesystemPathLockProvider::new(fs.clone());

        let token = provider.read_token("/data/.path.ovlock").await.unwrap();

        assert!(token.is_none());
        assert!(matches!(
            fs.read("/data/.path.ovlock", 0, 0).await,
            Err(Error::NotFound(_))
        ));
    }

    /// Verify malformed non-legacy lock files still surface as invalid tokens.
    #[tokio::test]
    async fn read_token_rejects_non_legacy_invalid_lock() {
        let fs = Arc::new(MemFileSystem::new());
        fs.mkdir("/data", 0o755).await.unwrap();
        fs.write("/data/.path.ovlock", b"not-a-token", 0, WriteFlag::Create)
            .await
            .unwrap();
        let provider = FilesystemPathLockProvider::new(fs.clone());

        let err = provider.read_token("/data/.path.ovlock").await.unwrap_err();

        assert!(matches!(err, PathLockError::InvalidToken(_)));
        assert_eq!(
            fs.read("/data/.path.ovlock", 0, 0).await.unwrap(),
            b"not-a-token"
        );
    }

    /// Verify valid plaintext lock files continue to decode unchanged.
    #[tokio::test]
    async fn read_token_accepts_plaintext_lock() {
        let fs = Arc::new(MemFileSystem::new());
        fs.mkdir("/data", 0o755).await.unwrap();
        fs.write("/data/.path.ovlock", b"owner:123:E", 0, WriteFlag::Create)
            .await
            .unwrap();
        let provider = FilesystemPathLockProvider::new(fs);

        let token = provider.read_token("/data/.path.ovlock").await.unwrap().unwrap();

        assert_eq!(token.owner_id, "owner");
        assert_eq!(token.time_ns, 123);
        assert_eq!(token.lock_type, crate::lock::PathLockKind::Exact);
    }

    #[test]
    fn compare_and_remove_would_block_maps_to_busy() {
        let err = FilesystemPathLockProvider::map_cas_error(
            "remove",
            "/data/.path.ovlock",
            Error::WouldBlock("lock would block".to_string()),
        );
        assert!(matches!(err, PathLockError::Busy { .. }));
    }
}

#[async_trait]
impl PathLockProvider for FilesystemPathLockProvider {
    fn name(&self) -> &'static str {
        "filesystem"
    }

    async fn read_token(&self, lock_path: &str) -> PathLockResult<Option<LockToken>> {
        let mut current = self.read_token_raw(lock_path).await?;
        loop {
            match current {
                Some(data) => {
                    let raw = String::from_utf8_lossy(&data).trim().to_string();
                    if raw.is_empty() {
                        return Ok(None);
                    }
                    match LockTokenCodec::decode(&raw) {
                        Ok(token) => return Ok(Some(token)),
                        Err(error) if crypto::is_encrypted(&data) => {
                            if self
                                .fs
                                .compare_and_remove(lock_path, &data)
                                .await
                                .map_err(|e| Self::map_cas_error("legacy encrypted lock cleanup", lock_path, e))?
                            {
                                return Ok(None);
                            }
                            current = self.read_token_raw(lock_path).await?;
                        }
                        Err(error) => return Err(error),
                    }
                }
                None => return Ok(None),
            }
        }
    }

    async fn try_create_token(&self, lock_path: &str, token: &LockToken) -> PathLockResult<()> {
        use crate::core::WriteFlag;

        let encoded = LockTokenCodec::encode(token);
        match self
            .fs
            .write(lock_path, encoded.as_bytes(), 0, WriteFlag::CreateNew)
            .await
        {
            Ok(_) => Ok(()),
            Err(_) => {
                // Already exists — read and check if stale.
                let existing = self.read_token(lock_path).await?;
                match existing {
                    Some(t) => Err(PathLockError::Conflict {
                        lock_path: lock_path.to_string(),
                        owner: t.owner_id,
                        kind: t.lock_type,
                    }),
                    None => Err(PathLockError::Io(format!(
                        "failed to create lock token at {lock_path}"
                    ))),
                }
            }
        }
    }

    async fn compare_and_write_token(
        &self,
        lock_path: &str,
        expected: &LockToken,
        replacement: &LockToken,
    ) -> PathLockResult<bool> {
        let expected = LockTokenCodec::encode(expected);
        let replacement = LockTokenCodec::encode(replacement);
        self.fs
            .compare_and_write(lock_path, expected.as_bytes(), replacement.as_bytes())
            .await
            .map_err(|e| Self::map_cas_error("compare_and_write", lock_path, e))
    }

    async fn refresh_token(
        &self,
        lock_path: &str,
        owner_id: &str,
        time_ns: u128,
    ) -> PathLockResult<bool> {
        let Some(raw) = self.read_token_raw(lock_path).await? else {
            return Ok(false);
        };
        let token = LockTokenCodec::decode(&String::from_utf8_lossy(&raw).trim())?;
        if token.owner_id != owner_id {
            return Ok(false);
        }
        let new_token = LockToken {
            owner_id: owner_id.to_string(),
            time_ns,
            lock_type: token.lock_type,
        };
        let encoded = LockTokenCodec::encode(&new_token);
        self.fs
            .compare_and_write(lock_path, &raw, encoded.as_bytes())
            .await
            .map_err(|e| Self::map_cas_error("refresh", lock_path, e))
    }

    async fn remove_token(&self, lock_path: &str, owner_id: &str) -> PathLockResult<bool> {
        let Some(raw) = self.read_token_raw(lock_path).await? else {
            return Ok(false);
        };
        let token = LockTokenCodec::decode(&String::from_utf8_lossy(&raw).trim())?;
        if token.owner_id != owner_id {
            return Ok(false);
        }
        self.fs
            .compare_and_remove(lock_path, &raw)
            .await
            .map_err(|e| Self::map_cas_error("remove", lock_path, e))
    }

    async fn scan_descendant_locks(&self, root: &str) -> PathLockResult<Vec<String>> {
        let mut result = Vec::new();
        self.scan_recursive(root, &mut result).await?;
        Ok(result)
    }
}

impl FilesystemPathLockProvider {
    /// Recursively scan a directory for `.path.ovlock` and `.exact.ovlock.*` files.
    fn is_lock_file(name: &str) -> bool {
        is_hidden_runtime_lock_name(name)
    }

    async fn scan_recursive(
        &self,
        dir: &str,
        result: &mut Vec<String>,
    ) -> PathLockResult<()> {
        let entries = match self.fs.read_internal_dir(dir).await {
            Ok(entries) => entries,
            Err(crate::core::Error::NotFound(_) | crate::core::Error::NotADirectory(_)) => {
                return Ok(());
            }
            Err(error) => {
                return Err(PathLockError::Io(format!(
                    "failed to scan lock directory '{dir}': {error}"
                )));
            }
        };

        for entry in entries {
            let child_path = if dir.ends_with('/') {
                format!("{}{}", dir, entry.name)
            } else {
                format!("{}/{}", dir, entry.name)
            };

            if Self::is_lock_file(&entry.name) {
                result.push(child_path);
            } else if entry.is_dir {
                Box::pin(self.scan_recursive(&child_path, result)).await?;
            }
        }
        Ok(())
    }
}
