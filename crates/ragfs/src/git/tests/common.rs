    use super::*;
    use async_trait::async_trait;
    use std::collections::{HashMap, HashSet};
    use std::sync::{Arc, Mutex};

    use crate::core::errors::{Error, Result};
    use crate::core::filesystem::FileSystem;
    use crate::core::types::{FileInfo, TreeEntry, WriteFlag};
    use crate::git::backends::local::{LocalObjectStore, LocalRefStore};
    use crate::git::error::ObjectStoreError;
    use crate::git::error::RefStoreError;
    use crate::git::ignore::OVGITIGNORE_PATH;
    use crate::git::tree_builder::{flatten, lookup};

    /// In-memory VFS mock that owns a map from absolute path to bytes.
    /// Root for the account is always `/local/{account}` — paths inserted
    /// must be the absolute path including this prefix.
    struct MockVfs {
        account: String,
        files: Arc<Mutex<HashMap<String, Vec<u8>>>>,
        /// When true, `remove` returns NotFound for absent paths (like the real
        /// VFS) instead of silently succeeding. Used to exercise the idempotent
        /// delete path in restore.
        strict_remove: bool,
        /// Absolute paths whose `write` call should fail with an I/O error.
        /// Used by restore-partial tests to force a per-path writeback failure
        /// without otherwise breaking the mock VFS.
        fail_writes: Arc<Mutex<HashSet<String>>>,
        /// Absolute paths whose `remove` call should fail with an I/O error
        /// (NotFound-style errors are still produced by the existing
        /// `strict_remove` flag, not via this set — that mirrors the real
        /// "idempotent delete" semantics in service.rs).
        fail_removes: Arc<Mutex<HashSet<String>>>,
    }

    impl MockVfs {
        fn new(account: &str) -> Arc<Self> {
            Arc::new(Self {
                account: account.to_string(),
                files: Arc::new(Mutex::new(HashMap::new())),
                strict_remove: false,
                fail_writes: Arc::new(Mutex::new(HashSet::new())),
                fail_removes: Arc::new(Mutex::new(HashSet::new())),
            })
        }

        fn new_strict_remove(account: &str) -> Arc<Self> {
            Arc::new(Self {
                account: account.to_string(),
                files: Arc::new(Mutex::new(HashMap::new())),
                strict_remove: true,
                fail_writes: Arc::new(Mutex::new(HashSet::new())),
                fail_removes: Arc::new(Mutex::new(HashSet::new())),
            })
        }

        /// Insert/update file content. `rel` is account-relative.
        fn put(&self, rel: &str, data: &[u8]) {
            let abs = format!("/local/{}/{}", self.account, rel);
            self.files.lock().unwrap().insert(abs, data.to_vec());
        }

        /// Delete a file by account-relative path.
        fn delete(&self, rel: &str) {
            let abs = format!("/local/{}/{}", self.account, rel);
            self.files.lock().unwrap().remove(&abs);
        }

        /// Cause subsequent `write` calls targeting `rel` (account-relative)
        /// to return an I/O error.
        fn fail_write(&self, rel: &str) {
            let abs = format!("/local/{}/{}", self.account, rel);
            self.fail_writes.lock().unwrap().insert(abs);
        }

        /// Cause subsequent `remove` calls targeting `rel` (account-relative)
        /// to return an I/O error.
        fn fail_remove(&self, rel: &str) {
            let abs = format!("/local/{}/{}", self.account, rel);
            self.fail_removes.lock().unwrap().insert(abs);
        }
    }

    #[async_trait]
    impl FileSystem for MockVfs {
        async fn create(&self, _path: &str) -> Result<()> {
            unimplemented!()
        }
        async fn mkdir(&self, _path: &str, _mode: u32) -> Result<()> {
            // Directories are implicit in this flat file map, so creating one is
            // a no-op. Defined (rather than unimplemented!) so the default
            // `ensure_parent_dirs` used by restore's writeback succeeds.
            Ok(())
        }
        async fn remove(&self, path: &str) -> Result<()> {
            if self.fail_removes.lock().unwrap().contains(path) {
                return Err(Error::Internal(format!("forced remove failure: {path}")));
            }
            let existed = self.files.lock().unwrap().remove(path).is_some();
            if self.strict_remove && !existed {
                return Err(Error::not_found(path));
            }
            Ok(())
        }
        async fn remove_all(&self, _path: &str) -> Result<()> {
            unimplemented!()
        }

        async fn read(&self, path: &str, _offset: u64, _size: u64) -> Result<Vec<u8>> {
            let g = self.files.lock().unwrap();
            match g.get(path) {
                Some(bytes) => Ok(bytes.clone()),
                None => Err(Error::not_found(path)),
            }
        }

        async fn write(
            &self,
            path: &str,
            data: &[u8],
            _offset: u64,
            _flags: WriteFlag,
        ) -> Result<u64> {
            if self.fail_writes.lock().unwrap().contains(path) {
                return Err(Error::Internal(format!("forced write failure: {path}")));
            }
            self.files
                .lock()
                .unwrap()
                .insert(path.to_string(), data.to_vec());
            Ok(data.len() as u64)
        }
        async fn read_dir(
            &self,
            _path: &str,
            _offset: Option<usize>,
            _limit: Option<usize>,
            _sort_by: Option<crate::core::ListSortBy>,
            _sort_order: Option<crate::core::SortOrder>,
        ) -> Result<Vec<FileInfo>> {
            unimplemented!()
        }

        async fn stat(&self, path: &str) -> Result<FileInfo> {
            let g = self.files.lock().unwrap();
            if let Some(bytes) = g.get(path) {
                let name = path.rsplit('/').next().unwrap_or(path).to_string();
                return Ok(FileInfo::new_file(name, bytes.len() as u64, 0o644));
            }
            Err(Error::not_found(path))
        }

        async fn rename(&self, _old_path: &str, _new_path: &str) -> Result<()> {
            unimplemented!()
        }
        async fn chmod(&self, _path: &str, _mode: u32) -> Result<()> {
            unimplemented!()
        }

        async fn tree_directory(
            &self,
            path: &str,
            _show_hidden: bool,
            _node_limit: Option<usize>,
            _level_limit: Option<usize>,
            _offset: Option<usize>,
            _sort_by: Option<crate::core::ListSortBy>,
            _sort_order: Option<crate::core::SortOrder>,
            _directories_only: bool,
        ) -> Result<Vec<TreeEntry>> {
            let prefix = if path == "/" {
                "/".to_string()
            } else {
                format!("{}/", path)
            };
            let g = self.files.lock().unwrap();
            let mut out = Vec::new();
            for (full_path, _bytes) in g.iter() {
                if !full_path.starts_with(&prefix) {
                    continue;
                }
                let rel = full_path
                    .strip_prefix(&prefix)
                    .unwrap_or(full_path)
                    .to_string();
                let name = full_path
                    .rsplit('/')
                    .next()
                    .unwrap_or(full_path)
                    .to_string();
                let info = FileInfo::new_file(name, 0, 0o644);
                out.push(TreeEntry {
                    path: full_path.clone(),
                    rel_path: rel,
                    info,
                    extra: HashMap::new(),
                });
            }
            Ok(out)
        }
    }

    /// Helper: build a fresh GitService backed by a temp dir + a fresh
    /// in-memory VFS for the given account.
    fn make_service(
        account: &str,
    ) -> (
        tempfile::TempDir,
        Arc<MockVfs>,
        Arc<LocalObjectStore>,
        Arc<LocalRefStore>,
        GitService,
    ) {
        let dir = tempfile::tempdir().unwrap();
        let object_store = Arc::new(LocalObjectStore::new(dir.path()));
        let ref_store = Arc::new(LocalRefStore::new(dir.path()));
        let vfs = MockVfs::new(account);
        let svc = GitService::new(
            vfs.clone() as Arc<dyn FileSystem>,
            object_store.clone() as Arc<dyn ObjectStore>,
            ref_store.clone() as Arc<dyn RefStore>,
        );
        (dir, vfs, object_store, ref_store, svc)
    }

    fn req(
        account: &str,
        branch: &str,
        message: &str,
        paths: Option<Vec<String>>,
    ) -> CommitRequest {
        CommitRequest {
            account: account.to_string(),
            branch: branch.to_string(),
            message: message.to_string(),
            paths,
            author_name: "tester".to_string(),
            author_email: "tester@example.com".to_string(),
        }
    }

    /// Load a commit's parent OIDs from the object store.
    async fn commit_parents(
        store: &dyn ObjectStore,
        account: &str,
        commit_oid: ObjectId,
    ) -> Vec<ObjectId> {
        let raw = crate::git::util::read_object(store, account, &commit_oid)
            .await
            .unwrap();
        let (_, _, hdr) = crate::git::util::parse_object_header(&raw).unwrap();
        let parsed = gix_object::CommitRef::from_bytes(&raw[hdr..]).unwrap();
        parsed.parents().collect()
    }

    async fn commit_tree(store: &dyn ObjectStore, account: &str, commit_oid: ObjectId) -> ObjectId {
        load_commit_meta(store, account, &commit_oid)
            .await
            .unwrap()
            .tree
    }

    /// Make a commit and return its OID.
    async fn make_commit(svc: &GitService, account: &str, branch: &str, msg: &str) -> ObjectId {
        match svc.commit(req(account, branch, msg, None)).await.unwrap() {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        }
    }

    // ── prev_tree lookup cache: each ancestor tree loaded once per commit ──
    /// ObjectStore wrapper that records every `get` oid so we can prove the
    /// commit loop re-fetches each prev_tree subtree at most once.
    struct GetSpyObjectStore {
        inner: LocalObjectStore,
        gets: std::sync::Mutex<Vec<ObjectId>>,
    }

    impl GetSpyObjectStore {
        fn count_gets(&self, oid: &ObjectId) -> usize {
            self.gets
                .lock()
                .unwrap()
                .iter()
                .filter(|o| *o == oid)
                .count()
        }
        fn reset(&self) {
            self.gets.lock().unwrap().clear();
        }
    }

    #[async_trait]
    impl ObjectStore for GetSpyObjectStore {
        async fn put(
            &self,
            account: &str,
            oid: &ObjectId,
            zlib_body: bytes::Bytes,
        ) -> std::result::Result<(), ObjectStoreError> {
            self.inner.put(account, oid, zlib_body).await
        }
        async fn get(
            &self,
            account: &str,
            oid: &ObjectId,
        ) -> std::result::Result<bytes::Bytes, ObjectStoreError> {
            self.gets.lock().unwrap().push(*oid);
            self.inner.get(account, oid).await
        }
        async fn exists(
            &self,
            account: &str,
            oid: &ObjectId,
        ) -> std::result::Result<bool, ObjectStoreError> {
            self.inner.exists(account, oid).await
        }
    }
