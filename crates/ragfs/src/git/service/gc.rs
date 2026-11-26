use std::collections::{HashMap, HashSet};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use futures::{stream, StreamExt, TryStreamExt};
use gix_hash::ObjectId;
use gix_object::Kind;

use super::{load_commit_meta, validate_account_id, GitService};
use crate::git::{
    overlay::{commit_overlay_edit, OverlayEdit, OverlayView, OVERLAY_REF},
    util::{parse_object_header, read_object},
    GcReport, GitError, ObjectStore,
};

type Refs = HashMap<String, ObjectId>;

impl GitService {
    /// Reclaim expired, unreachable loose objects belonging to one account.
    pub async fn gc(&self, account: &str) -> Result<GcReport, GitError> {
        self.gc_at(account, SystemTime::now(), self.gc_grace).await
    }

    pub(crate) async fn gc_at(
        &self,
        account: &str,
        now: SystemTime,
        grace: Duration,
    ) -> Result<GcReport, GitError> {
        validate_account_id(account)?;
        let mut report = GcReport::default();
        if !self.gc_enabled {
            report.skipped = Some("disabled".into());
            return Ok(report);
        }
        let started = Instant::now();
        let cutoff = now.checked_sub(grace).unwrap_or(UNIX_EPOCH);
        let cutoff_s = cutoff
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs() as i64;
        let (refs, overlay) = self.gc_roots(account).await?;
        let mut live = HashSet::new();
        self.mark_roots(account, &overlay, &refs, cutoff_s, &mut live, &mut report)
            .await?;
        let objects = self.object_store.list_objects(account).await?;
        report.scanned = objects.len();

        #[cfg(test)]
        if let Some(hook) = &self.before_sweep_hook {
            hook().await;
        }

        let (latest, latest_overlay) = self.gc_roots(account).await?;
        if latest != refs {
            report.remarked = true;
            // An overlay change can change ancestry without moving a head.
            if overlay.root_oid != latest_overlay.root_oid {
                let mut revised = HashSet::new();
                self.mark_roots(
                    account,
                    &latest_overlay,
                    &latest,
                    cutoff_s,
                    &mut revised,
                    &mut report,
                )
                .await?;
                live.extend(revised);
            } else {
                let changes = latest
                    .into_iter()
                    .filter(|(name, oid)| refs.get(name) != Some(oid))
                    .collect();
                self.mark_roots(
                    account,
                    &latest_overlay,
                    &changes,
                    cutoff_s,
                    &mut live,
                    &mut report,
                )
                .await?;
            }
        }
        let candidates: Vec<_> = objects
            .into_iter()
            .filter(|m| m.modified < cutoff && !live.contains(&m.oid))
            .collect();
        for chunk in candidates.chunks(1000) {
            let removed: HashSet<_> = self
                .object_store
                .sweep_objects(account, chunk, cutoff)
                .await?
                .into_iter()
                .collect();
            report.deleted += removed.len();
            report.bytes_freed += chunk
                .iter()
                .filter(|m| removed.contains(&m.oid))
                .map(|m| m.size)
                .sum::<u64>();
        }
        self.object_store
            .cleanup_temp_files(account, cutoff)
            .await?;
        report.tombstones_purged = self.purge_tombstones(account, cutoff_s).await?;
        report.duration_ms = started.elapsed().as_millis() as u64;
        Ok(report)
    }

    async fn gc_roots(&self, account: &str) -> Result<(Refs, OverlayView), GitError> {
        for _ in 0..3 {
            let refs: Refs = self
                .ref_store
                .list(account, "refs/")
                .await?
                .into_iter()
                .collect();
            let overlay = self.overlay(account).await?;
            if overlay.root_oid == refs.get(OVERLAY_REF).copied() {
                return Ok((refs, overlay));
            }
        }
        Err(GitError::OverlayConflict)
    }

    async fn mark_roots(
        &self,
        account: &str,
        overlay: &OverlayView,
        refs: &Refs,
        cutoff: i64,
        live: &mut HashSet<ObjectId>,
        report: &mut GcReport,
    ) -> Result<(), GitError> {
        let mut pending = Vec::new();
        live.extend(overlay.blob_oids());
        for (oid, deleted_at) in overlay.all_tombstones().await? {
            if deleted_at >= cutoff {
                pending.push((oid, false)); // protect its tree, never its raw ancestors
            }
        }
        for (name, oid) in refs {
            if name == OVERLAY_REF {
                continue;
            }
            if let Some(branch) = name.strip_prefix("refs/heads/") {
                if self.commit_index.is_some() {
                    if let Some(chain) = self.gc_index_chain(account, branch, *oid, overlay).await?
                    {
                        report.used_commit_index = true;
                        for (commit, tree) in chain {
                            live.insert(commit);
                            pending.push((tree, true));
                        }
                        continue;
                    }
                    self.invalidate_commit_index(account, branch).await;
                }
            }
            pending.push((*oid, true));
        }

        // Each tree is decoded once, even when shared by thousands of commits.
        while !pending.is_empty() {
            let batch: Vec<_> = std::mem::take(&mut pending)
                .into_iter()
                .filter(|(oid, _)| live.insert(*oid))
                .collect();
            let mut reads = stream::iter(batch)
                .map(|(oid, parents)| async move {
                    object_edges(self.object_store.as_ref(), account, overlay, oid, parents).await
                })
                .buffer_unordered(32);
            while let Some((objects, leaves)) = reads.try_next().await? {
                pending.extend(objects.into_iter().map(|oid| (oid, true)));
                live.extend(leaves);
            }
        }
        Ok(())
    }

    async fn gc_index_chain(
        &self,
        account: &str,
        branch: &str,
        tip: ObjectId,
        overlay: &OverlayView,
    ) -> Result<Option<Vec<(ObjectId, ObjectId)>>, GitError> {
        let Some(index) = &self.commit_index else {
            return Ok(None);
        };
        let Ok(rows) = index.list_branch(account, branch).await else {
            return Ok(None);
        };
        let mut chain = Vec::new();
        let mut previous = None;
        for row in rows {
            let (Ok(oid), Ok(tree)) = (
                row.commit_oid.parse::<ObjectId>(),
                row.tree_oid.parse::<ObjectId>(),
            ) else {
                return Ok(None);
            };
            if overlay.deleted_at(&oid).await?.is_some() {
                continue;
            }
            let Ok(raw_parent) = row
                .parent_oid
                .as_deref()
                .map(str::parse::<ObjectId>)
                .transpose()
            else {
                return Ok(None);
            };
            let parents = overlay
                .graft(&oid)
                .await?
                .unwrap_or_else(|| raw_parent.into_iter().collect());
            if parents.as_slice() != previous.as_slice() {
                return Ok(None);
            }
            chain.push((oid, tree));
            previous = Some(oid);
        }
        let Some(index_tip) = previous else {
            return Ok(None);
        };
        let mut current = tip;
        while current != index_tip {
            let meta =
                load_commit_meta(self.object_store.as_ref(), overlay, account, &current).await?;
            chain.push((current, meta.tree));
            let [parent] = meta.parents.as_slice() else {
                return Ok(None);
            };
            current = *parent;
        }
        Ok(Some(chain))
    }

    async fn purge_tombstones(&self, account: &str, cutoff: i64) -> Result<usize, GitError> {
        for _ in 0..3 {
            let base = self.overlay(account).await?;
            let expired: Vec<_> = base
                .all_tombstones()
                .await?
                .into_iter()
                .filter_map(|(oid, time)| (time < cutoff).then_some(oid))
                .collect();
            let mut purge = Vec::new();
            let mut checks = stream::iter(expired)
                .map(|oid| async move {
                    self.object_store
                        .exists(account, &oid)
                        .await
                        .map(|exists| (!exists).then_some(oid))
                })
                .buffer_unordered(32);
            while let Some(result) = checks.try_next().await? {
                purge.extend(result);
            }
            if purge.is_empty() {
                return Ok(0);
            }
            let edit = OverlayEdit {
                remove_tombstones: purge.clone(),
                ..Default::default()
            };
            match commit_overlay_edit(
                self.write_store().as_ref(),
                self.ref_store.as_ref(),
                account,
                &base,
                &edit,
            )
            .await
            {
                Err(GitError::OverlayConflict) => continue,
                result => {
                    result?;
                }
            }
            if let Some(index) = &self.commit_index {
                let ids: Vec<_> = purge.iter().map(ToString::to_string).collect();
                if let Err(err) = index.purge_commits(account, &ids).await {
                    tracing::warn!(%err, "failed to purge snapshot commit index rows");
                }
            }
            return Ok(purge.len());
        }
        Err(GitError::OverlayConflict)
    }
}

// The second vector contains terminal leaves: their contents need not be read.
async fn object_edges(
    store: &dyn ObjectStore,
    account: &str,
    overlay: &OverlayView,
    oid: ObjectId,
    parents: bool,
) -> Result<(Vec<ObjectId>, Vec<ObjectId>), GitError> {
    let raw = read_object(store, account, &oid).await?;
    let (kind, size, hdr) = parse_object_header(&raw)?;
    let data = &raw[hdr..];
    if size != data.len() as u64
        || gix_object::compute_hash(gix_hash::Kind::Sha1, kind, data) != oid
    {
        return Err(GitError::CorruptedObject(format!(
            "snapshot GC found corrupt object {oid}"
        )));
    }
    let decode = |e: gix_object::decode::Error| GitError::Other(format!("snapshot GC: {e}"));
    let mut edges = Vec::new();
    let mut leaves = Vec::new();
    match kind {
        Kind::Commit => {
            let commit = gix_object::CommitRef::from_bytes(data).map_err(decode)?;
            edges.push(commit.tree());
            if parents {
                edges.extend(
                    overlay
                        .graft(&oid)
                        .await?
                        .unwrap_or_else(|| commit.parents().collect()),
                );
            }
        }
        Kind::Tree => {
            for entry in gix_object::TreeRef::from_bytes(data)
                .map_err(decode)?
                .entries
            {
                if entry.mode.is_tree() {
                    edges.push(entry.oid.to_owned());
                } else {
                    leaves.push(entry.oid.to_owned());
                }
            }
        }
        Kind::Tag => edges.push(
            gix_object::TagRef::from_bytes(data)
                .map_err(decode)?
                .target(),
        ),
        Kind::Blob => (),
    }
    Ok((edges, leaves))
}
