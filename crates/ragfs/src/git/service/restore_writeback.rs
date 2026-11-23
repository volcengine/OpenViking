use super::restore::read_blob_payload;
use super::*;
use crate::git::types::RestoreDiff;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

#[derive(Deserialize, Serialize)]
pub(super) struct PendingRestore {
    pub parent: String,
    pub commit: String,
    pub unchanged: usize,
    writes: Vec<(String, String)>,
    deletes: Vec<String>,
    before: HashMap<String, WorkingFile>,
}

#[derive(Deserialize, Serialize, PartialEq, Eq)]
enum WorkingFile {
    Missing,
    File { digest: String, modified_ns: u128 },
    Unknown,
}

impl WorkingFile {
    async fn read(vfs: &dyn FileSystem, path: &str) -> Self {
        use crate::core::errors::Error;

        match vfs.stat(path).await {
            Err(Error::NotFound(_)) => Self::Missing,
            Ok(info) if !info.is_dir => {
                match (
                    info.mod_time.duration_since(UNIX_EPOCH),
                    vfs.read(path, 0, 0).await,
                ) {
                    (Ok(modified), Ok(bytes)) => Self::File {
                        digest: fingerprint(&bytes),
                        modified_ns: modified.as_nanos(),
                    },
                    _ => Self::Unknown,
                }
            }
            _ => Self::Unknown,
        }
    }

    fn matches_before(&self, before: Option<&Self>) -> bool {
        !matches!(self, Self::Unknown) && Some(self) == before
    }
}

fn fingerprint(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

pub(super) fn writeback_ref(branch: &str, project: &str, source: ObjectId) -> String {
    // Branch and project names cannot contain a NUL.
    let key = format!("{branch}\0{project}\0{}", source.to_hex());
    format!("refs/ov-restore/{}", fingerprint(key.as_bytes()))
}

pub(super) fn parse_oid(value: &str) -> Result<ObjectId, GitError> {
    ObjectId::from_hex(value.as_bytes())
        .map_err(|e| GitError::CorruptedObject(format!("invalid restore writeback OID: {e}")))
}

impl PendingRestore {
    pub async fn prepare(
        vfs: &dyn FileSystem,
        prefix: &str,
        parent: ObjectId,
        commit: ObjectId,
        diff: &RestoreDiff,
    ) -> Self {
        let mut before = HashMap::new();
        for path in diff
            .to_write
            .iter()
            .map(|(path, _)| path)
            .chain(&diff.to_delete)
        {
            before.insert(
                path.clone(),
                WorkingFile::read(vfs, &format!("{prefix}/{path}")).await,
            );
        }
        Self {
            parent: parent.to_hex().to_string(),
            commit: commit.to_hex().to_string(),
            unchanged: diff.unchanged.len(),
            writes: diff
                .to_write
                .iter()
                .map(|(path, oid)| (path.clone(), oid.to_hex().to_string()))
                .collect(),
            deletes: diff.to_delete.clone(),
            before,
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

    /// Check all paths before applying any pending mutation. Returning already
    /// landed but unconfirmed changes lets the caller still arrange indexing.
    pub async fn check_workspace(
        &self,
        service: &GitService,
        account: &str,
        project: &str,
        diff: RestoreDiff,
    ) -> Result<(RestoreDiff, Vec<String>, Vec<String>), GitError> {
        let mut writes = Vec::new();
        let mut deletes = Vec::new();
        let mut landed_writes = Vec::new();
        let mut landed_deletes = Vec::new();
        for (path, oid) in diff.to_write {
            let account_path = format!("{project}/{path}");
            let actual = WorkingFile::read(
                service.vfs.as_ref(),
                &format!("/local/{account}/{account_path}"),
            )
            .await;
            if actual.matches_before(self.before.get(&path)) {
                writes.push((path, oid));
            } else if let WorkingFile::File { digest, .. } = actual {
                let desired =
                    read_blob_payload(service.object_store.as_ref(), account, &oid).await?;
                if digest != fingerprint(&desired) {
                    return Err(GitError::RestoreWritebackConflict);
                }
                landed_writes.push(account_path);
            } else {
                return Err(GitError::RestoreWritebackConflict);
            }
        }
        for path in diff.to_delete {
            let account_path = format!("{project}/{path}");
            let actual = WorkingFile::read(
                service.vfs.as_ref(),
                &format!("/local/{account}/{account_path}"),
            )
            .await;
            if matches!(actual, WorkingFile::Missing) {
                landed_deletes.push(account_path);
            } else if actual.matches_before(self.before.get(&path)) {
                deletes.push(path);
            } else {
                return Err(GitError::RestoreWritebackConflict);
            }
        }
        Ok((
            RestoreDiff {
                to_write: writes,
                to_delete: deletes,
                unchanged: diff.unchanged,
            },
            landed_writes,
            landed_deletes,
        ))
    }

    pub fn retain_failures(
        &mut self,
        project: &str,
        failure: &crate::git::types::RestoreWritebackPartial,
    ) {
        let prefix = format!("{project}/");
        let failed: HashSet<&str> = failure
            .failed_writes
            .iter()
            .chain(&failure.failed_deletes)
            .filter_map(|(path, _)| path.strip_prefix(&prefix))
            .collect();
        self.writes
            .retain(|(path, _)| failed.contains(path.as_str()));
        self.deletes.retain(|path| failed.contains(path.as_str()));
        self.before.retain(|path, _| failed.contains(path.as_str()));
    }
}

impl GitService {
    pub(super) async fn read_restore_writeback(
        &self,
        account: &str,
        key: &str,
    ) -> Result<(Option<ObjectId>, Option<PendingRestore>), GitError> {
        match self.ref_store.read(account, key).await {
            Err(RefStoreError::NotFound(_)) => Ok((None, None)),
            Ok(oid) if oid.is_null() => Ok((Some(oid), None)),
            Ok(oid) => {
                let bytes = read_blob_payload(self.object_store.as_ref(), account, &oid).await?;
                let record = serde_json::from_slice(&bytes).map_err(|e| {
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
    ) -> Result<ObjectId, GitError> {
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
        Ok(oid)
    }
}
