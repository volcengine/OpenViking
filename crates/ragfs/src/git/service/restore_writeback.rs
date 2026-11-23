use super::restore::read_blob_payload;
use super::*;
use crate::core::errors::Error;
use crate::git::tree_builder::{lookup_cached, TreeLookupCache};
use crate::git::types::{RestoreDiff, RestoreWritebackPartial};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

#[derive(Deserialize, Serialize)]
pub(super) struct PendingRestore {
    pub parent: String,
    pub commit: String,
    pub unchanged: usize,
    writes: Vec<(String, String)>,
    deletes: Vec<String>,
}

pub(super) fn writeback_ref(branch: &str, project: &str, source: ObjectId) -> String {
    let key = format!("{branch}\0{project}\0{}", source.to_hex());
    // Keep the marker out of refs/ so snapshot lookups never enumerate it.
    format!("restore-writeback/{:x}", Sha256::digest(key.as_bytes()))
}

pub(super) fn parse_oid(value: &str) -> Result<ObjectId, GitError> {
    ObjectId::from_hex(value.as_bytes())
        .map_err(|e| GitError::CorruptedObject(format!("invalid restore writeback OID: {e}")))
}

impl PendingRestore {
    pub fn from_failure(
        project: &str,
        diff: &RestoreDiff,
        failure: &RestoreWritebackPartial,
    ) -> Self {
        let prefix = format!("{project}/");
        let failed: HashSet<&str> = failure
            .failed_writes
            .iter()
            .chain(&failure.failed_deletes)
            .filter_map(|(path, _)| path.strip_prefix(&prefix))
            .collect();
        Self {
            parent: failure.parent_commit.to_hex().to_string(),
            commit: failure.new_commit_oid.to_hex().to_string(),
            unchanged: failure.unchanged,
            writes: diff
                .to_write
                .iter()
                .filter(|(path, _)| failed.contains(path.as_str()))
                .map(|(path, oid)| (path.clone(), oid.to_hex().to_string()))
                .collect(),
            deletes: diff
                .to_delete
                .iter()
                .filter(|path| failed.contains(path.as_str()))
                .cloned()
                .collect(),
        }
    }

    pub fn diff(&self) -> Result<RestoreDiff, GitError> {
        Ok(RestoreDiff {
            to_write: self
                .writes
                .iter()
                .map(|(path, oid)| Ok((path.clone(), parse_oid(oid)?)))
                .collect::<Result<_, GitError>>()?,
            to_delete: self.deletes.clone(),
            unchanged: Vec::new(),
        })
    }
}

impl GitService {
    /// Check every pending path before mutating any of them. Content matching the
    /// parent snapshot can be safely replaced. Content at the target is already
    /// applied and can be reported for indexing without another mutation.
    pub(super) async fn check_restore_workspace(
        &self,
        account: &str,
        project: &str,
        parent: ObjectId,
        diff: RestoreDiff,
    ) -> Result<(RestoreDiff, Vec<String>, Vec<String>), GitError> {
        let store = self.object_store.as_ref();
        let parent_tree = load_commit_meta(store, account, &parent).await?.tree;
        let mut cache = TreeLookupCache::new();
        let mut remaining = RestoreDiff {
            to_write: Vec::new(),
            to_delete: Vec::new(),
            unchanged: Vec::new(),
        };
        let mut written = Vec::new();
        let mut deleted = Vec::new();
        let writes = diff
            .to_write
            .into_iter()
            .map(|(path, oid)| (path, Some(oid)));
        let deletes = diff.to_delete.into_iter().map(|path| (path, None));
        for (path, target) in writes.chain(deletes) {
            let account_path = format!("{project}/{path}");
            let actual = match self
                .vfs
                .read(&format!("/local/{account}/{account_path}"), 0, 0)
                .await
            {
                Ok(content) => Some(gix_object::compute_hash(
                    gix_hash::Kind::Sha1,
                    gix_object::Kind::Blob,
                    &content,
                )),
                Err(Error::NotFound(_)) => None,
                Err(_) => return Err(GitError::RestoreWritebackConflict),
            };
            if actual == target {
                match target {
                    Some(_) => written.push(account_path),
                    None => deleted.push(account_path),
                }
                continue;
            }
            let expected = lookup_cached(store, account, parent_tree, &account_path, &mut cache)
                .await?
                .map(|(oid, _)| oid);
            if actual != expected {
                return Err(GitError::RestoreWritebackConflict);
            }
            match target {
                Some(oid) => remaining.to_write.push((path, oid)),
                None => remaining.to_delete.push(path),
            }
        }
        Ok((remaining, written, deleted))
    }

    pub(super) async fn read_restore_writeback(
        &self,
        account: &str,
        key: &str,
    ) -> Result<(Option<ObjectId>, Option<PendingRestore>), GitError> {
        match self.ref_store.read(account, key).await {
            Err(RefStoreError::NotFound(_)) => Ok((None, None)),
            Ok(oid) if oid.is_null() => Ok((Some(oid), None)),
            Ok(oid) => {
                let data = read_blob_payload(self.object_store.as_ref(), account, &oid).await?;
                let record = serde_json::from_slice(&data).map_err(|e| {
                    GitError::CorruptedObject(format!("invalid restore writeback record: {e}"))
                })?;
                Ok((Some(oid), Some(record)))
            }
            Err(e) => Err(e.into()),
        }
    }

    pub(super) async fn save_restore_writeback(
        &self,
        account: &str,
        key: &str,
        previous: Option<ObjectId>,
        pending: Option<&PendingRestore>,
    ) -> Result<(), GitError> {
        let oid = if let Some(record) = pending {
            let bytes = serde_json::to_vec(record).map_err(|e| {
                ObjectStoreError::Backend(format!("encode restore writeback record: {e}"))
            })?;
            crate::git::util::write_object(
                self.object_store.as_ref(),
                account,
                gix_object::Kind::Blob,
                &bytes,
            )
            .await?
        } else {
            ObjectId::null(gix_hash::Kind::Sha1)
        };
        self.ref_store
            .cas_update(account, key, previous, oid)
            .await?;
        Ok(())
    }
}
