use super::*;

impl GitService {
    /// Restore a subtree at `project_dir` to the state it had in `source_commit`,
    /// producing a new commit whose parent is the **current HEAD** (not
    /// `source_commit`). HEAD always moves forward.
    ///
    /// See design §8.2 for the full algorithm and `RestoreResponse` for the
    /// three possible outcomes (`Applied` / `Noop` / `DryRun`).
    ///
    /// Errors:
    /// - `GitError::InvalidProjectDir` — `project_dir` is empty / malformed.
    /// - `GitError::RefStore(NotFound)` — branch HEAD or source_commit ref missing.
    /// - `GitError::SubtreeNotFoundInCommit` — `project_dir` does not resolve
    ///   to a subtree in `source_commit`'s tree.
    /// - `GitError::ConcurrentCommit` — branch ref changed between our read
    ///   and the CAS swap.
    pub async fn restore(&self, req: RestoreRequest) -> Result<RestoreResponse, GitError> {
        validate_account_id(&req.account)?;
        if let Some(project_dir) = &req.project_dir {
            validate_project_dir(project_dir)?;
        }

        // Encrypted writeback must reuse the caller's tree lock rather than
        // acquire a conflicting lock. The request remains the tenant authority.
        let ctx = Arc::new(FsContextInner::with_pathlock(
            req.account.clone(),
            PathLockContext {
                lease_ref: FsContextView::current()
                    .pathlock_lease_ref()
                    .map(str::to_owned),
                ..Default::default()
            },
        ));
        FS_CTX.scope(ctx, self.restore_impl(req)).await
    }

    async fn restore_impl(&self, req: RestoreRequest) -> Result<RestoreResponse, GitError> {
        let RestoreRequest {
            account,
            branch,
            project_dir,
            source_commit,
            dry_run,
            message: _,
            author_name: _,
            author_email: _,
        } = &req;
        let ref_name = format!("refs/heads/{branch}");

        // 1. Resolve both commits.
        let source_oid = resolve_ref(
            self.ref_store.as_ref(),
            self.object_store.as_ref(),
            account,
            source_commit,
        )
        .await?;
        let head_oid = self.ref_store.read(account, &ref_name).await?;
        let source_meta =
            load_commit_meta(self.object_store.as_ref(), account, &source_oid).await?;
        let head_meta = load_commit_meta(self.object_store.as_ref(), account, &head_oid).await?;

        // 2. Extract subtree from each (or use full tree if project_dir is None).
        //    Source missing → error (if project_dir is Some).
        //    Head missing → treat as empty (every file is a fresh write).
        let (source_tree_to_flatten, head_tree_to_flatten) = match project_dir {
            Some(project_dir) => {
                let source_subtree = match crate::git::tree_builder::lookup(
                    self.object_store.as_ref(),
                    account,
                    source_meta.tree,
                    project_dir,
                )
                .await?
                {
                    Some((oid, mode)) if mode.is_tree() => oid,
                    _ => {
                        return Err(GitError::SubtreeNotFoundInCommit {
                            project_dir: project_dir.clone(),
                            commit: source_oid,
                        });
                    }
                };
                let head_subtree = match crate::git::tree_builder::lookup(
                    self.object_store.as_ref(),
                    account,
                    head_meta.tree,
                    project_dir,
                )
                .await?
                {
                    Some((oid, mode)) if mode.is_tree() => Some(oid),
                    _ => None,
                };
                (source_subtree, head_subtree)
            }
            None => (source_meta.tree, Some(head_meta.tree)),
        };

        // 3. Flatten and diff (paths in the result are subtree-relative, or account-relative if full tree).
        let source_entries = crate::git::tree_builder::flatten(
            self.object_store.as_ref(),
            account,
            source_tree_to_flatten,
            &None,
        )
        .await?;
        let head_entries = match head_tree_to_flatten {
            Some(oid) => {
                crate::git::tree_builder::flatten(self.object_store.as_ref(), account, oid, &None)
                    .await?
            }
            None => Vec::new(),
        };
        let diff = compute_subtree_diff(&source_entries, &head_entries);

        // 4. dry_run short-circuits BEFORE any writes.
        if *dry_run {
            return Ok(RestoreResponse::DryRun {
                diff,
                head: head_oid,
                source: source_oid,
            });
        }

        // 5. Source == head → noop.
        if diff.to_write.is_empty() && diff.to_delete.is_empty() {
            return Ok(RestoreResponse::Noop {
                head: head_oid,
                source: source_oid,
            });
        }
        #[cfg(feature = "snapshot_log")]
        let snapshot_log_paths =
            super::snapshot_log::restore_snapshot_log_paths(project_dir.as_deref(), &diff);

        // 6. Prepare writeback metadata. Paths in the diff are relative to
        //    project_dir — prefix here. NOTE: the VFS is NOT mutated yet. The
        //    ref-consistency protocol (steps 7–9) must complete first so that a
        //    losing CAS race leaves the working tree untouched; the actual
        //    writeback happens in step 10 only after the ref swap succeeds.
        use futures::stream::{self, StreamExt};

        let abs_prefix = match project_dir {
            Some(project_dir) => format!("/local/{}/{}", account, project_dir),
            None => format!("/local/{}", account),
        };
        let unchanged_count = diff.unchanged.len();

        // 7. Build the new tree: load head.tree into an editor and splice
        //    source_subtree at project_dir, or use source tree directly if full restore.
        let new_tree_oid = match project_dir {
            Some(project_dir) => {
                let mut editor = crate::git::tree_builder::TreeEditor::from_tree(
                    self.object_store.as_ref(),
                    account,
                    head_meta.tree,
                )
                .await?;
                editor
                    .upsert_subtree(
                        self.object_store.as_ref(),
                        account,
                        project_dir,
                        source_tree_to_flatten,
                    )
                    .await?;
                editor.write(self.object_store.as_ref(), account).await?
            }
            None => source_meta.tree,
        };

        // 8. Construct the new commit. parent = head_oid (NOT source_oid).
        let msg = req.message.clone().unwrap_or_else(|| {
            let short = &source_oid.to_hex().to_string()[..12.min(40)];
            match &req.project_dir {
                Some(project_dir) => format!("restore {} from {}", project_dir, short),
                None => format!("restore full tree from {}", short),
            }
        });
        let new_commit_oid = crate::git::commit::write_commit(
            self.object_store.as_ref(),
            account,
            new_tree_oid,
            vec![head_oid],
            &req.author_name,
            &req.author_email,
            &msg,
        )
        .await?;

        // 9. CAS-swap the branch ref. Map Conflict → ConcurrentCommit.
        //    This MUST happen before any VFS writeback: if another commit
        //    advanced the branch between our HEAD read and now, the CAS fails
        //    and we return early with the working tree still matching HEAD,
        //    leaving caller-driven reindex and on-disk state consistent.
        match self
            .ref_store
            .cas_update(account, &ref_name, Some(head_oid), new_commit_oid)
            .await
        {
            Ok(()) => {}
            Err(crate::git::error::RefStoreError::Conflict { expected, actual }) => {
                return Err(GitError::ConcurrentCommit {
                    ref_name,
                    expected,
                    actual,
                });
            }
            Err(other) => return Err(other.into()),
        }

        #[cfg(feature = "snapshot_log")]
        self.try_append_snapshot_log(
            account,
            branch,
            Some(head_oid),
            new_commit_oid,
            &snapshot_log_paths,
        )
        .await;

        // 10. The ref swap committed our new state. Now write back through the
        //     VFS so the working tree reflects the restored content. The ref
        //     has already advanced, so a per-path failure here can NOT be
        //     rolled back — instead the streams below collect every failure
        //     and we surface them as `GitError::RestoreWritebackPartial`. The
        //     caller (Python) then schedules reindex for the paths that *did*
        //     reach the VFS so the vector index does not stay stale.
        let object_store_ref = self.object_store.clone();
        let vfs_ref = self.vfs.clone();
        let account_owned = account.clone();
        let abs_prefix_for_writes = abs_prefix.clone();
        let project_dir_for_writes = project_dir.clone();

        let write_results: Vec<(String, Result<(), GitError>)> =
            stream::iter(diff.to_write.clone().into_iter())
                .map(|(rel, blob_oid)| {
                    let object_store = object_store_ref.clone();
                    let vfs = vfs_ref.clone();
                    let account = account_owned.clone();
                    let abs_prefix = abs_prefix_for_writes.clone();
                    let project_dir = project_dir_for_writes.clone();
                    async move {
                        let account_rel = match &project_dir {
                            Some(pd) => format!("{}/{}", pd, rel),
                            None => rel.clone(),
                        };
                        let r = async {
                            let bytes =
                                read_blob_payload(object_store.as_ref(), &account, &blob_oid)
                                    .await?;
                            let abs = format!("{}/{}", abs_prefix, rel);
                            // The target's parent directory may have been removed out of
                            // band (e.g. an `rm -r` that a later commit recorded as a
                            // deletion), so the restore must recreate the directory
                            // chain before writing the blob back.
                            crate::core::filesystem::FileSystem::ensure_parent_dirs(
                                vfs.as_ref(),
                                &abs,
                                0o755,
                            )
                            .await?;
                            crate::core::filesystem::FileSystem::write(
                                vfs.as_ref(),
                                &abs,
                                &bytes,
                                0,
                                crate::core::types::WriteFlag::Create,
                            )
                            .await?;
                            Ok::<(), GitError>(())
                        }
                        .await;
                        (account_rel, r)
                    }
                })
                .buffer_unordered(32)
                .collect()
                .await;

        let abs_prefix_for_deletes = abs_prefix.clone();
        let vfs_for_deletes = self.vfs.clone();
        let project_dir_for_deletes = project_dir.clone();
        let delete_results: Vec<(String, Result<(), GitError>)> =
            stream::iter(diff.to_delete.clone().into_iter())
                .map(|rel| {
                    let vfs = vfs_for_deletes.clone();
                    let abs_prefix = abs_prefix_for_deletes.clone();
                    let project_dir = project_dir_for_deletes.clone();
                    async move {
                        let account_rel = match &project_dir {
                            Some(pd) => format!("{}/{}", pd, rel),
                            None => rel.clone(),
                        };
                        let abs = format!("{}/{}", abs_prefix, rel);
                        // Restore is idempotent: a path the diff wants to delete may
                        // already be absent from the VFS (e.g. derived files like
                        // `.abstract.md` that were removed or regenerated out of band).
                        // Treat NotFound as success rather than counting it as a failure.
                        let r =
                            match crate::core::filesystem::FileSystem::remove(vfs.as_ref(), &abs)
                                .await
                            {
                                Ok(_) => Ok::<(), GitError>(()),
                                Err(crate::core::errors::Error::NotFound(_)) => Ok(()),
                                Err(e) => Err(e.into()),
                            };
                        (account_rel, r)
                    }
                })
                .buffer_unordered(32)
                .collect()
                .await;

        // 10b. Prune directories left empty by the deletes above. Git does not
        //      track directories, so `to_delete` only ever lists files; removing
        //      the last file in a directory would otherwise leave an empty husk
        //      in the VFS. Walk each deleted file's ancestor directories (within
        //      project_dir, deepest first) and drop any that are now empty.
        //      Best-effort: a directory that still holds entries, or has already
        //      vanished, is simply skipped — pruning never aborts the restore.
        use std::collections::BTreeSet;
        // (depth, rel_dir): BTreeSet iterates ascending, so reversing yields the
        // deepest directories first — children are pruned before their parents,
        // letting a parent that held only pruned subdirs be removed in turn.
        let mut prune_candidates: BTreeSet<(usize, String)> = BTreeSet::new();
        for rel in &diff.to_delete {
            let mut dir = rel.as_str();
            while let Some(idx) = dir.rfind('/') {
                dir = &dir[..idx];
                prune_candidates.insert((dir.split('/').count(), dir.to_string()));
            }
        }
        for (_depth, rel_dir) in prune_candidates.into_iter().rev() {
            let abs = format!("{}/{}", abs_prefix, rel_dir);
            let is_empty = match crate::core::filesystem::FileSystem::read_dir(
                self.vfs.as_ref(),
                &abs,
                None,
                None,
                None,
                None,
            )
            .await
            {
                Ok(entries) => entries.is_empty(),
                // Missing or not a directory → nothing to prune.
                Err(_) => false,
            };
            if is_empty {
                // Ignore failures: a concurrent writer may have repopulated the
                // directory, or it may already be gone. Either way the restore
                // itself has succeeded.
                let _ = crate::core::filesystem::FileSystem::remove(self.vfs.as_ref(), &abs).await;
            }
        }

        // 10c. Partition the per-path results into success / failure buckets.
        //      `written_paths` / `deleted_paths` here only carry the paths
        //      that actually reached the VFS — callers use these lists to
        //      drive reindex, and a path whose write failed must not be
        //      reindexed (the file's blob never landed).
        let mut written_paths: Vec<String> = Vec::with_capacity(write_results.len());
        let mut failed_writes: Vec<(String, String)> = Vec::new();
        for (path, r) in write_results {
            match r {
                Ok(()) => written_paths.push(path),
                Err(e) => failed_writes.push((path, e.to_string())),
            }
        }
        let mut deleted_paths: Vec<String> = Vec::with_capacity(delete_results.len());
        let mut failed_deletes: Vec<(String, String)> = Vec::new();
        for (path, r) in delete_results {
            match r {
                Ok(()) => deleted_paths.push(path),
                Err(e) => failed_deletes.push((path, e.to_string())),
            }
        }
        written_paths.sort();
        deleted_paths.sort();

        let written_actual = written_paths.len();
        let deleted_actual = deleted_paths.len();

        // 11. Partial failure path. The ref has already advanced, so we
        //     cannot rollback — surface a structured error carrying enough
        //     payload for the caller to schedule reindex for the paths that
        //     *did* succeed and to report the failures upward.
        if !failed_writes.is_empty() || !failed_deletes.is_empty() {
            return Err(GitError::RestoreWritebackPartial(Box::new(
                crate::git::types::RestoreWritebackPartial {
                    new_commit_oid,
                    source_commit: source_oid,
                    parent_commit: head_oid,
                    written: written_actual,
                    deleted: deleted_actual,
                    unchanged: unchanged_count,
                    written_paths,
                    deleted_paths,
                    failed_writes,
                    failed_deletes,
                },
            )));
        }

        Ok(RestoreResponse::Applied {
            new_commit_oid,
            source_commit: source_oid,
            parent_commit: head_oid,
            written: written_actual,
            deleted: deleted_actual,
            unchanged: unchanged_count,
            written_paths,
            deleted_paths,
        })
    }
}


/// Load a blob object and return only its payload bytes (header stripped).
///
/// Errors out with `CorruptedObject` if the loaded object is not a blob —
/// this should not happen on a well-formed store but is cheap to verify.
async fn read_blob_payload(
    store: &dyn ObjectStore,
    account: &str,
    blob_oid: &gix_hash::ObjectId,
) -> Result<bytes::Bytes, GitError> {
    let raw = crate::git::util::read_object(store, account, blob_oid).await?;
    let (kind, _, hdr) = crate::git::util::parse_object_header(&raw)?;
    if kind != gix_object::Kind::Blob {
        return Err(GitError::CorruptedObject(format!(
            "expected blob, got {kind:?}"
        )));
    }
    Ok(raw.slice(hdr..))
}
