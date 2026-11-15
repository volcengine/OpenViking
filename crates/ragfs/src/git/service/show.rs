use super::*;

impl GitService {
    /// Read a commit's metadata, or a single blob's bytes from inside a commit's tree.
    ///
    /// `target_ref` resolution: 40-hex OID / "main" / "refs/heads/main".
    ///
    /// - `path = None`  → returns `ShowResponse::Commit { oid, tree, parents, author, committer, message }`.
    /// - `path = Some(p)` → returns `ShowResponse::Blob { oid, size, bytes }` for the path inside
    ///   the commit's tree. Missing path → `GitError::PathNotFound(p)`. Path that resolves to
    ///   a tree (not a blob) → `GitError::PathIsDirectory(p)` — distinct from missing so callers
    ///   can tell apart "no such path" from "path exists but is a directory, not a file".
    ///
    /// Missing ref → `GitError::RefStore(RefStoreError::NotFound)`.
    /// Missing commit object → `GitError::ObjectStore(ObjectStoreError::NotFound)`.
    pub async fn show(&self, req: ShowRequest) -> Result<ShowResponse, GitError> {
        self.show_with_limit(req, None).await
    }

    /// Same as [`GitService::show`], with an optional maximum blob payload size.
    pub async fn show_with_limit(
        &self,
        req: ShowRequest,
        max_blob_bytes: Option<u64>,
    ) -> Result<ShowResponse, GitError> {
        let ShowRequest {
            account,
            target_ref,
            path,
        } = req;

        validate_account_id(&account)?;
        if let Some(p) = &path {
            validate_relative_path(p)?;
        }

        let commit_oid = resolve_ref(
            self.ref_store.as_ref(),
            self.object_store.as_ref(),
            &account,
            &target_ref,
        )
        .await?;
        let meta = load_commit_meta(self.object_store.as_ref(), &account, &commit_oid).await?;

        match path {
            None => Ok(ShowResponse::Commit {
                oid: commit_oid,
                tree: meta.tree,
                parents: meta.parents,
                author: meta.author,
                committer: meta.committer,
                message: meta.message,
            }),
            Some(p) => {
                let entry = crate::git::tree_builder::lookup(
                    self.object_store.as_ref(),
                    &account,
                    meta.tree,
                    &p,
                )
                .await?;
                let (blob_oid, mode) = entry.ok_or_else(|| GitError::PathNotFound(p.clone()))?;
                // Reject trees masquerading as paths: callers asked for blob bytes.
                if mode.is_tree() {
                    return Err(GitError::PathIsDirectory(p));
                }
                let raw = match max_blob_bytes {
                    Some(limit) => crate::git::util::read_object_limited(
                        self.object_store.as_ref(),
                        &account,
                        &blob_oid,
                        limit,
                    )
                    .await
                    .map_err(|err| match err {
                        ObjectStoreError::ReadLimitExceeded { size, .. } => {
                            GitError::BlobTooLarge { size, limit }
                        }
                        other => GitError::ObjectStore(other),
                    })?,
                    None => crate::git::util::read_object(
                        self.object_store.as_ref(),
                        &account,
                        &blob_oid,
                    )
                    .await?,
                };
                let (kind, payload_size, hdr) = crate::git::util::parse_object_header(&raw)?;
                if kind != gix_object::Kind::Blob {
                    return Err(GitError::CorruptedObject(format!(
                        "expected blob at {p}, got {kind:?}"
                    )));
                }
                if let Some(limit) = max_blob_bytes {
                    if payload_size > limit {
                        return Err(GitError::BlobTooLarge {
                            size: payload_size,
                            limit,
                        });
                    }
                }
                // `raw` is already a `Bytes`; `slice` is O(1) and shares the
                // backing buffer instead of allocating a fresh payload copy.
                let bytes = raw.slice(hdr..);
                Ok(ShowResponse::Blob {
                    oid: blob_oid,
                    size: payload_size,
                    bytes,
                })
            }
        }
    }
}
