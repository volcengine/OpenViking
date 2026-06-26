//! Local filesystem backend for Git object and ref storage

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use async_trait::async_trait;
use bytes::Bytes;
use dashmap::DashMap;
use gix_hash::ObjectId;
use tokio::sync::Mutex;

use crate::git::error::{ObjectStoreError, RefStoreError};
use crate::git::index_store::{
    decode_index, encode_index, systemtime_to_ns, CommitIndex, IndexStore, IndexStoreError,
};
use crate::git::object_store::ObjectStore;
use crate::git::ref_store::RefStore;
use crate::git::util::validate_ref_name;

/// Per-process counter used to give each in-flight object write a unique temp
/// filename, preventing concurrent `put` calls from sharing one `.tmp`.
static TMP_SEQ: AtomicU64 = AtomicU64::new(0);

/// Local filesystem implementation of ObjectStore.
///
/// Stores objects in Git's standard loose object format:
/// `{base_dir}/{account}/objects/{aa}/{bb...}`
/// where `aa` is the first 2 hex chars of the oid, and `bb...` is the rest.
pub struct LocalObjectStore {
    base_dir: PathBuf,
}

impl LocalObjectStore {
    /// Create a new LocalObjectStore with the given base directory.
    pub fn new(base_dir: impl Into<PathBuf>) -> Self {
        Self {
            base_dir: base_dir.into(),
        }
    }

    /// Get the filesystem path for an object.
    fn object_path(&self, account: &str, oid: &ObjectId) -> PathBuf {
        let hex = oid.to_hex().to_string();
        self.base_dir
            .join(account)
            .join("objects")
            .join(&hex[..2])
            .join(&hex[2..])
    }
}

#[async_trait]
impl ObjectStore for LocalObjectStore {
    async fn put(
        &self,
        account: &str,
        oid: &ObjectId,
        zlib_body: Bytes,
    ) -> Result<(), ObjectStoreError> {
        let path = self.object_path(account, oid);

        // Idempotent: if object already exists, do nothing
        if tokio::fs::try_exists(&path).await? {
            return Ok(());
        }

        // Ensure parent directory exists
        if let Some(parent) = path.parent() {
            tokio::fs::create_dir_all(parent).await?;
        }

        // Write to a unique temp file first, then rename for atomicity. A
        // per-process counter keeps the temp name unique so concurrent `put`
        // calls for the same not-yet-existing object don't share one `.tmp`
        // and clobber each other mid-write.
        let seq = TMP_SEQ.fetch_add(1, Ordering::Relaxed);
        let tmp_path = path.with_extension(format!("tmp.{}.{}", std::process::id(), seq));
        tokio::fs::write(&tmp_path, &zlib_body).await?;
        match tokio::fs::rename(&tmp_path, &path).await {
            Ok(()) => Ok(()),
            Err(e) => {
                // A racing `put` for the same object may have already produced
                // it. Idempotency holds as long as the object exists, so treat
                // that as success. Clean up our orphaned temp file regardless.
                let _ = tokio::fs::remove_file(&tmp_path).await;
                if tokio::fs::try_exists(&path).await.unwrap_or(false) {
                    Ok(())
                } else {
                    Err(e.into())
                }
            }
        }
    }

    async fn get(&self, account: &str, oid: &ObjectId) -> Result<Bytes, ObjectStoreError> {
        let path = self.object_path(account, oid);
        match tokio::fs::read(&path).await {
            Ok(bytes) => Ok(Bytes::from(bytes)),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                Err(ObjectStoreError::NotFound(*oid))
            }
            Err(e) => Err(e.into()),
        }
    }

    async fn exists(&self, account: &str, oid: &ObjectId) -> Result<bool, ObjectStoreError> {
        let path = self.object_path(account, oid);
        tokio::fs::try_exists(&path)
            .await
            .map_err(ObjectStoreError::Io)
    }
}

/// Local filesystem implementation of RefStore.
///
/// Stores refs as plain text files with hex oid content, and uses:
/// - In-memory locks per (account, ref_name) for process-level serialization
/// - Atomic rename for filesystem-level atomicity
pub struct LocalRefStore {
    base_dir: PathBuf,
    locks: DashMap<(String, String), Arc<Mutex<()>>>,
}

impl LocalRefStore {
    /// Create a new LocalRefStore with the given base directory.
    pub fn new(base_dir: impl Into<PathBuf>) -> Self {
        Self {
            base_dir: base_dir.into(),
            locks: DashMap::new(),
        }
    }

    /// Get the filesystem path for a ref.
    fn ref_path(&self, account: &str, ref_name: &str) -> PathBuf {
        self.base_dir.join(account).join(ref_name)
    }

    /// Get or create a lock for the given (account, ref_name).
    fn get_lock(&self, account: &str, ref_name: &str) -> Arc<Mutex<()>> {
        self.locks
            .entry((account.to_string(), ref_name.to_string()))
            .or_insert_with(|| Arc::new(Mutex::new(())))
            .clone()
    }

    /// Read a ref from disk, returns None if not found.
    async fn read_ref_opt(path: &Path) -> Result<Option<ObjectId>, RefStoreError> {
        match tokio::fs::read_to_string(path).await {
            Ok(content) => {
                let trimmed = content.trim();
                let oid = trimmed.parse::<ObjectId>().map_err(|_| {
                    RefStoreError::Backend(format!("invalid oid in ref file: {trimmed}"))
                })?;
                Ok(Some(oid))
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
            Err(e) => Err(e.into()),
        }
    }
}

#[async_trait]
impl RefStore for LocalRefStore {
    async fn read(&self, account: &str, ref_name: &str) -> Result<ObjectId, RefStoreError> {
        // Validate ref name
        validate_ref_name(ref_name)?;

        let path = self.ref_path(account, ref_name);
        Self::read_ref_opt(&path)
            .await?
            .ok_or_else(|| RefStoreError::NotFound(ref_name.to_string()))
    }

    async fn cas_update(
        &self,
        account: &str,
        ref_name: &str,
        expected: Option<ObjectId>,
        new: ObjectId,
    ) -> Result<(), RefStoreError> {
        // Validate ref name first
        validate_ref_name(ref_name)?;

        // Acquire per-ref lock to serialize concurrent updates
        let lock = self.get_lock(account, ref_name);
        let _guard = lock.lock().await;

        let path = self.ref_path(account, ref_name);

        // Check current value matches expected
        let actual = Self::read_ref_opt(&path).await?;
        if actual != expected {
            return Err(RefStoreError::Conflict { expected, actual });
        }

        // Ensure parent directory exists
        if let Some(parent) = path.parent() {
            tokio::fs::create_dir_all(parent).await?;
        }

        // Write to temp file then rename for atomicity
        let tmp_path = path.with_extension("tmp");
        tokio::fs::write(&tmp_path, format!("{}\n", new.to_hex())).await?;
        tokio::fs::rename(&tmp_path, &path).await?;

        Ok(())
    }

    async fn list(
        &self,
        account: &str,
        prefix: &str,
    ) -> Result<Vec<(String, ObjectId)>, RefStoreError> {
        let dir_path = self.base_dir.join(account).join(prefix);
        let mut result = Vec::new();

        // Walk the directory recursively
        let mut stack = vec![dir_path];

        while let Some(current_path) = stack.pop() {
            match tokio::fs::read_dir(&current_path).await {
                Ok(mut entries) => {
                    while let Some(entry) = entries.next_entry().await? {
                        let entry_path = entry.path();
                        if entry_path.is_dir() {
                            stack.push(entry_path);
                        } else {
                            // It's a file - parse as ref
                            if let Ok(ref_name) = entry_path.strip_prefix(self.base_dir.join(account))
                            {
                                if let Some(ref_name_str) = ref_name.to_str() {
                                    if let Ok(Some(oid)) = Self::read_ref_opt(&entry_path).await {
                                        result.push((ref_name_str.to_string(), oid));
                                    }
                                }
                            }
                        }
                    }
                }
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                    // Directory doesn't exist - return empty list
                    break;
                }
                Err(e) => return Err(e.into()),
            }
        }

        Ok(result)
    }
}

/// Local filesystem implementation of [`IndexStore`].
///
/// Persists each `(account, branch)` snapshot at
/// `{base_dir}/{account}/index/{branch}.json`. The branch component is
/// `validate_ref_name`-checked before any path is constructed to keep crafted
/// names from escaping the per-account directory.
///
/// All errors degrade to `Ok(None)` on `load`: missing file, decode failure,
/// version skew. Save uses tempfile + rename for atomicity, so a crash mid-
/// write leaves the previous snapshot intact.
pub struct LocalIndexStore {
    base_dir: PathBuf,
}

impl LocalIndexStore {
    /// Create a new `LocalIndexStore` rooted at `base_dir`. Per-account
    /// subdirectories are created lazily on first save.
    pub fn new(base_dir: impl Into<PathBuf>) -> Self {
        Self {
            base_dir: base_dir.into(),
        }
    }

    fn index_path(&self, account: &str, branch: &str) -> PathBuf {
        self.base_dir
            .join(account)
            .join("index")
            .join(format!("{branch}.json"))
    }
}

#[async_trait]
impl IndexStore for LocalIndexStore {
    async fn load(
        &self,
        account: &str,
        branch: &str,
    ) -> Result<Option<CommitIndex>, IndexStoreError> {
        validate_ref_name(branch)
            .map_err(|_| IndexStoreError::InvalidBranch(branch.to_string()))?;

        let path = self.index_path(account, branch);
        match tokio::fs::read(&path).await {
            Ok(bytes) => match decode_index(&bytes) {
                Ok(Some(mut idx)) => {
                    // Stamp the index with its own on-disk mtime so the commit
                    // path can apply the racy-clean guard. Same clock + same
                    // granularity as the working-tree file mtimes it's compared
                    // against. A stat failure leaves `saved_at_ns = None`
                    // (conservative: every entry is then treated as racy).
                    idx.saved_at_ns = tokio::fs::metadata(&path)
                        .await
                        .ok()
                        .and_then(|m| m.modified().ok())
                        .and_then(systemtime_to_ns);
                    Ok(Some(idx))
                }
                Ok(None) => Ok(None),
                Err(_) => Ok(None),
            },
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
            Err(e) => Err(e.into()),
        }
    }

    async fn save(
        &self,
        account: &str,
        branch: &str,
        index: &CommitIndex,
    ) -> Result<(), IndexStoreError> {
        validate_ref_name(branch)
            .map_err(|_| IndexStoreError::InvalidBranch(branch.to_string()))?;

        let bytes = encode_index(index)?;
        let path = self.index_path(account, branch);
        if let Some(parent) = path.parent() {
            tokio::fs::create_dir_all(parent).await?;
        }
        let tmp_path = path.with_extension("tmp");
        tokio::fs::write(&tmp_path, &bytes).await?;
        tokio::fs::rename(&tmp_path, &path).await?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[tokio::test]
    async fn test_local_object_store_put_get_exists() {
        let dir = tempdir().unwrap();
        let store = LocalObjectStore::new(dir.path());

        let account = "test-account";
        let oid = "0123456789abcdef0123456789abcdef01234567".parse::<ObjectId>().unwrap();
        let body = Bytes::from("test content");

        // Initially doesn't exist
        assert!(!store.exists(account, &oid).await.unwrap());

        // Put object
        store.put(account, &oid, body.clone()).await.unwrap();

        // Now exists
        assert!(store.exists(account, &oid).await.unwrap());

        // Get object and verify
        let retrieved = store.get(account, &oid).await.unwrap();
        assert_eq!(retrieved, body);

        // Put again is idempotent
        store.put(account, &oid, body).await.unwrap();
    }

    #[tokio::test]
    async fn test_local_object_store_concurrent_put_same_oid() {
        let dir = tempdir().unwrap();
        let store = Arc::new(LocalObjectStore::new(dir.path()));

        let account = "test-account";
        let oid = "0123456789abcdef0123456789abcdef01234567".parse::<ObjectId>().unwrap();
        let body = Bytes::from("test content");

        // Many concurrent puts for the same not-yet-existing object must all
        // succeed: the unique temp name + rename-failure recheck preserves
        // idempotency under the race.
        let mut handles = Vec::new();
        for _ in 0..32 {
            let store = store.clone();
            let body = body.clone();
            handles.push(tokio::spawn(async move {
                store.put(account, &oid, body).await
            }));
        }
        for handle in handles {
            handle.await.unwrap().unwrap();
        }

        assert_eq!(store.get(account, &oid).await.unwrap(), body);
    }

    #[tokio::test]
    async fn test_local_ref_store_read_cas_list() {
        let dir = tempdir().unwrap();
        let store = LocalRefStore::new(dir.path());

        let account = "test-account";
        let ref_name = "refs/heads/main";
        let oid1 = "0123456789abcdef0123456789abcdef01234567".parse::<ObjectId>().unwrap();
        let oid2 = "fedcba9876543210fedcba9876543210fedcba98".parse::<ObjectId>().unwrap();

        // Initially not found
        assert!(matches!(
            store.read(account, ref_name).await,
            Err(RefStoreError::NotFound(_))
        ));

        // CAS from None should work
        store.cas_update(account, ref_name, None, oid1).await.unwrap();

        // Read should return oid1
        assert_eq!(store.read(account, ref_name).await.unwrap(), oid1);

        // CAS from oid1 to oid2 should work
        store.cas_update(account, ref_name, Some(oid1), oid2).await.unwrap();
        assert_eq!(store.read(account, ref_name).await.unwrap(), oid2);

        // CAS with wrong expected should fail
        let result = store.cas_update(account, ref_name, Some(oid1), oid1).await;
        assert!(matches!(result, Err(RefStoreError::Conflict { .. })));

        // List refs
        let refs = store.list(account, "refs/heads/").await.unwrap();
        assert_eq!(refs.len(), 1);
        assert_eq!(refs[0].0, "refs/heads/main");
        assert_eq!(refs[0].1, oid2);
    }

    #[tokio::test]
    async fn test_local_ref_store_concurrent_cas() {
        let dir = tempdir().unwrap();
        let store = Arc::new(LocalRefStore::new(dir.path()));

        let account = "test-account";
        let ref_name = "refs/heads/main";

        // Spawn multiple concurrent cas_update tasks
        let mut handles = Vec::new();
        for i in 0..10 {
            let store = store.clone();
            let oid = format!("{:040}", i).parse::<ObjectId>().unwrap();
            handles.push(tokio::spawn(async move {
                // Each task will try to CAS in a loop until it succeeds
                let mut attempts = 0;
                while attempts < 100 {
                    let current = store.read(account, ref_name).await.ok();
                    match store.cas_update(account, ref_name, current, oid).await {
                        Ok(_) => return true,
                        Err(RefStoreError::Conflict { .. }) => attempts += 1,
                        Err(e) => panic!("unexpected error: {e}"),
                    }
                }
                false
            }));
        }

        // All should succeed eventually
        for handle in handles {
            assert!(handle.await.unwrap());
        }
    }

    fn idx_oid(b: u8) -> ObjectId {
        let mut bytes = [0u8; 20];
        bytes.fill(b);
        ObjectId::from_bytes_or_panic(&bytes)
    }

    #[tokio::test]
    async fn local_index_store_round_trip() {
        let dir = tempdir().unwrap();
        let store = LocalIndexStore::new(dir.path());

        // Missing → None
        let loaded = store.load("acct", "main").await.unwrap();
        assert!(loaded.is_none());

        let mut entries = std::collections::HashMap::new();
        entries.insert(
            "resources/a.md".into(),
            crate::git::types::IndexEntry {
                size: 11,
                mtime_ns: 1_700_000_000_000_000_000,
                oid: idx_oid(0xAA),
            },
        );
        let idx = crate::git::index_store::CommitIndex {
            parent_oid: idx_oid(0xCC),
            entries,
            saved_at_ns: None,
        };

        store.save("acct", "main", &idx).await.unwrap();
        let loaded = store.load("acct", "main").await.unwrap().unwrap();
        // parent_oid + entries round-trip through the wire format unchanged.
        assert_eq!(loaded.parent_oid, idx.parent_oid);
        assert_eq!(loaded.entries, idx.entries);
        // saved_at_ns is NOT in the wire format — it is stamped from the index
        // file's own mtime on load, so a freshly-saved index loads with Some.
        assert!(
            loaded.saved_at_ns.is_some(),
            "load must stamp saved_at_ns from the index file mtime",
        );
    }

    #[tokio::test]
    async fn local_index_store_corruption_is_soft_miss() {
        let dir = tempdir().unwrap();
        let store = LocalIndexStore::new(dir.path());

        // Manually drop a malformed file at the expected path
        let path = dir.path().join("acct").join("index").join("main.json");
        tokio::fs::create_dir_all(path.parent().unwrap()).await.unwrap();
        tokio::fs::write(&path, b"definitely not json").await.unwrap();

        // Should be Ok(None), not Err
        assert!(store.load("acct", "main").await.unwrap().is_none());
    }

    #[tokio::test]
    async fn local_index_store_rejects_invalid_branch() {
        let dir = tempdir().unwrap();
        let store = LocalIndexStore::new(dir.path());

        // Path-traversal style branch name → InvalidBranch error
        let result = store.load("acct", "../escape").await;
        assert!(matches!(
            result,
            Err(crate::git::index_store::IndexStoreError::InvalidBranch(_))
        ));
    }
}
