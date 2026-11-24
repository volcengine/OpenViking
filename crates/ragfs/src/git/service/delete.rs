//! Plan against a pinned overlay. No object or derived index changes are
//! published until all target commits and their changed paths are known.
use super::*;
use crate::git::{
    overlay::{commit_overlay_edit, OverlayEdit, OVERLAY_REF},
    snapshot_log::fanout_changed_paths,
    tree_builder::diff_trees,
    DeletePlanEntry, DeleteRequest, DeleteResponse,
};
use std::{collections::BTreeMap, time::SystemTime};

const WALK_LIMIT: usize = 50_000;

#[derive(Default)]
struct Location {
    // Every target must be reachable. A target may also be on a shared prefix.
    branches: HashMap<ObjectId, String>,
    // Surviving children whose immediate parent is a target.
    children: BTreeMap<ObjectId, String>,
}

/// Only the immutable tree and effective edges participate in planning.
#[derive(PartialEq, Eq)]
struct Node {
    tree: ObjectId,
    parents: Vec<ObjectId>,
}

struct Planner<'a> {
    svc: &'a GitService,
    account: &'a str,
    overlay: &'a OverlayView,
    targets: HashSet<ObjectId>,
    index_corrupt: bool,
    // Cache immutable commit metadata, including each side of a tree diff.
    commits: HashMap<ObjectId, Node>,
}

impl Planner<'_> {
    async fn meta(&mut self, id: ObjectId) -> Result<&Node, GitError> {
        if !self.commits.contains_key(&id) {
            let meta = load_commit_meta(
                self.svc.object_store.as_ref(),
                self.overlay,
                self.account,
                &id,
            )
            .await?;
            self.commits.insert(
                id,
                Node {
                    tree: meta.tree,
                    parents: meta.parents,
                },
            );
        }
        Ok(&self.commits[&id])
    }

    // A peer's SQLite row can outlive both the commit's visibility and its loose object.
    async fn candidate_meta(&mut self, id: ObjectId) -> Result<Option<&Node>, GitError> {
        match self.meta(id).await {
            Ok(node) => Ok(Some(node)),
            Err(
                GitError::CommitNotFound(missing)
                | GitError::ObjectStore(ObjectStoreError::NotFound(missing)),
            ) if missing == id => Ok(None),
            Err(err) => Err(err),
        }
    }

    fn index_read<T>(&mut self, result: Result<T, crate::git::SnapshotLogError>) -> Option<T> {
        match result {
            Ok(value) => Some(value),
            Err(crate::git::SnapshotLogError::UnindexableRow(_)) => {
                self.index_corrupt = true;
                None
            }
            Err(_) => None,
        }
    }

    /// Check the index frontier against every branch. A tag or a shared
    /// branch prefix not fully covered by the index requires a graph walk.
    async fn indexed(&mut self, refs: &[(String, ObjectId)]) -> Result<Option<Location>, GitError> {
        let Some(store) = self.svc.commit_index.clone() else {
            return Ok(None);
        };
        let mut branches = HashSet::new();
        for (name, tip) in refs {
            let Some(branch) = name.strip_prefix("refs/heads/") else {
                continue;
            };
            let Some(latest) = self.index_read(store.latest_commit(self.account, branch).await)
            else {
                return Ok(None);
            };
            let Some(latest) = latest else {
                self.svc.trigger_commit_index_backfill(self.account, branch);
                return Ok(None);
            };
            if latest.commit_oid != tip.to_string() {
                self.svc.trigger_commit_index_backfill(self.account, branch);
                // A concurrently appended suffix does not invalidate the
                // indexed prefix. Verify its connection to the actual head.
                let Ok(index_tip) = latest.commit_oid.parse::<ObjectId>() else {
                    return Ok(None);
                };
                let mut cursor = *tip;
                while cursor != index_tip {
                    if self.commits.len() >= WALK_LIMIT {
                        return Ok(None);
                    }
                    let [parent] = self.meta(cursor).await?.parents.as_slice() else {
                        return Ok(None);
                    };
                    cursor = *parent;
                }
            }
            if refs.len() > 1 {
                let Some(Some(first)) =
                    self.index_read(store.next_live(self.account, branch, 0).await)
                else {
                    return Ok(None);
                };
                let Ok(root) = ObjectId::from_hex(first.commit_oid.as_bytes()) else {
                    return Ok(None);
                };
                let Some(root_node) = self.candidate_meta(root).await? else {
                    self.svc.invalidate_commit_index(self.account, branch).await;
                    return Ok(None);
                };
                if !root_node.parents.is_empty() {
                    return Ok(None);
                }
            }
            branches.insert(branch);
        }
        for (name, tip) in refs {
            if !name.starts_with("refs/heads/") {
                let Some(Some(row)) =
                    self.index_read(store.get_commit(self.account, &tip.to_string()).await)
                else {
                    return Ok(None);
                };
                if !branches.contains(row.branch.as_str()) || row.deleted_at.is_some() {
                    return Ok(None);
                }
            }
        }
        // Keep SQL-derived nodes separate until every run has been verified.
        // Falling back must never reuse a partially verified plan.
        let mut nodes = HashMap::new();
        let mut loc = Location::default();
        for id in self.targets.clone() {
            let Some(Some(row)) =
                self.index_read(store.get_commit(self.account, &id.to_string()).await)
            else {
                return Ok(None);
            };
            let branch = row.branch.as_str();
            if !branches.contains(branch) || row.deleted_at.is_some() {
                return Ok(None);
            }
            let Some(previous) =
                self.index_read(store.prev_live(self.account, branch, row.seq).await)
            else {
                return Ok(None);
            };
            let Some(Some(next)) =
                self.index_read(store.next_live(self.account, branch, row.seq).await)
            else {
                return Ok(None);
            };
            let Some(child) = next.commit_oid.parse::<ObjectId>().ok() else {
                return Ok(None);
            };
            let Some(node) = self.index_node(id, &row).await? else {
                return Ok(None);
            };
            let previous_id = previous
                .as_ref()
                .map(|p| p.commit_oid.parse::<ObjectId>())
                .transpose();
            let Ok(previous_id) = previous_id else {
                return Ok(None);
            };
            let first_in_run = previous_id.is_none_or(|p| !self.targets.contains(&p));
            let last_in_run = !self.targets.contains(&child);
            if node.parents.as_slice() != previous_id.as_slice()
                || (first_in_run && self.candidate_meta(id).await? != Some(&node))
                || (last_in_run
                    && self
                        .candidate_meta(child)
                        .await?
                        .is_none_or(|n| n.parents.as_slice() != [id]))
            {
                self.svc.invalidate_commit_index(self.account, branch).await;
                return Ok(None);
            }
            nodes.insert(id, node);
            if let (Some(parent), Some(row)) = (previous_id, previous) {
                let Some(node) = self.index_node(parent, &row).await? else {
                    return Ok(None);
                };
                nodes.insert(parent, node);
            }
            loc.branches.insert(id, branch.to_owned());
            if last_in_run {
                loc.children.insert(child, branch.to_owned());
            }
        }
        self.commits.extend(nodes);
        Ok(Some(loc))
    }

    async fn index_node(
        &self,
        id: ObjectId,
        row: &crate::git::CommitIndexRow,
    ) -> Result<Option<Node>, GitError> {
        // The provider verifies this row's immutable fields against the digest
        // written with the original commit. The overlay alone changes ancestry.
        let Ok(tree) = row.tree_oid.parse() else {
            return Ok(None);
        };
        let Ok(raw_parent) = row
            .parent_oid
            .as_deref()
            .map(str::parse::<ObjectId>)
            .transpose()
        else {
            return Ok(None);
        };
        let parents = self
            .overlay
            .graft(&id)
            .await?
            .unwrap_or_else(|| raw_parent.into_iter().collect());
        Ok(Some(Node { tree, parents }))
    }

    async fn walk(&mut self, refs: &[(String, ObjectId)]) -> Result<Location, GitError> {
        let mut loc = Location::default();
        let mut visited = HashSet::new();
        for (name, tip) in refs {
            let branch = name.strip_prefix("refs/heads/").unwrap_or(name);
            let mut stack = vec![*tip];
            while let Some(id) = stack.pop() {
                if !visited.insert(id) {
                    continue;
                }
                if visited.len() > WALK_LIMIT {
                    for (r, _) in refs {
                        if let Some(b) = r.strip_prefix("refs/heads/") {
                            self.svc.trigger_commit_index_backfill(self.account, b);
                        }
                    }
                    return Err(GitError::CommitIndexUnavailable {
                        scanned: WALK_LIMIT,
                    });
                }
                let parents = match self.meta(id).await {
                    Ok(meta) => meta.parents.clone(),
                    Err(GitError::Other(_)) if id == *tip && !name.starts_with("refs/heads/") => {
                        continue
                    }
                    Err(e) => return Err(e),
                };
                if self.targets.contains(&id) {
                    loc.branches.insert(id, branch.to_owned());
                } else if parents.iter().any(|p| self.targets.contains(p)) {
                    loc.children.insert(id, branch.to_owned());
                }
                if refs.len() == 1 && loc.branches.len() == self.targets.len() {
                    break;
                }
                stack.extend(parents);
            }
        }
        for id in &self.targets {
            if !loc.branches.contains_key(id) {
                return Err(GitError::CommitNotFound(*id));
            }
        }
        Ok(loc)
    }

    async fn paths(
        &mut self,
        id: ObjectId,
        parent: Option<ObjectId>,
    ) -> Result<Vec<String>, GitError> {
        let tree = self.meta(id).await?.tree;
        let base = match parent {
            Some(p) => Some(self.meta(p).await?.tree),
            None => None,
        };
        let mut paths: Vec<_> = diff_trees(
            self.svc.object_store.as_ref(),
            self.account,
            base,
            Some(tree),
        )
        .await?
        .into_iter()
        .map(|(p, _)| p)
        .collect();
        paths.sort();
        Ok(paths)
    }

    async fn execute(
        mut self,
        req: &DeleteRequest,
        ids: &[ObjectId],
    ) -> Result<DeleteResponse, GitError> {
        let mut refs = self.svc.ref_store.list(self.account, "refs/").await?;
        refs.retain(|(r, _)| r != OVERLAY_REF);
        refs.sort_by_key(|(r, _)| (!r.starts_with("refs/heads/"), r.clone()));
        for (name, tip) in &refs {
            if self.targets.contains(tip) {
                return Err(GitError::CommitIsRefTip {
                    oid: *tip,
                    ref_name: name.clone(),
                });
            }
        }
        for id in ids {
            if self.overlay.deleted_at(id).await?.is_some() {
                return Err(GitError::CommitNotFound(*id));
            }
        }
        let loc = match self.indexed(&refs).await? {
            Some(loc) => loc,
            None => {
                if self.index_corrupt {
                    for (name, _) in &refs {
                        if let Some(branch) = name.strip_prefix("refs/heads/") {
                            self.svc.invalidate_commit_index(self.account, branch).await;
                        }
                    }
                }
                self.walk(&refs).await?
            }
        };
        let mut plan = Vec::new();
        for id in ids {
            let parent = self.meta(*id).await?.parents.first().copied();
            plan.push(DeletePlanEntry {
                commit: *id,
                branch: loc.branches[id].clone(),
                changed_paths: self.paths(*id, parent).await?,
            });
        }
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs() as i64;
        let mut repairs: BTreeMap<String, Vec<(Vec<ObjectId>, ObjectId, Vec<String>)>> =
            BTreeMap::new();
        let mut edit = OverlayEdit {
            remove_grafts: ids.to_vec(),
            add_tombstones: ids.iter().map(|id| (*id, now)).collect(),
            ..Default::default()
        };
        for (child, branch) in loc.children {
            let mut parents = Vec::new();
            let mut removed = Vec::new();
            for mut parent in self.meta(child).await?.parents.clone() {
                loop {
                    if !self.targets.contains(&parent) {
                        if !parents.contains(&parent) {
                            parents.push(parent);
                        }
                        break;
                    }
                    removed.push(parent);
                    match self.meta(parent).await?.parents.first() {
                        Some(p) => parent = *p,
                        None => break,
                    }
                }
            }
            if !req.dry_run && self.svc.snapshot_log_store.is_some() {
                let paths = self.paths(child, parents.first().copied()).await?;
                repairs.entry(branch).or_default().push((
                    removed,
                    child,
                    fanout_changed_paths(&paths),
                ));
            }
            edit.set_grafts.push((child, parents));
        }
        let mut root = self.overlay.root_oid;
        if !req.dry_run {
            root = commit_overlay_edit(
                self.svc.write_store().as_ref(),
                self.svc.ref_store.as_ref(),
                self.account,
                self.overlay,
                &edit,
            )
            .await?;
            if let Some(store) = &self.svc.commit_index {
                let strings = ids.iter().map(ToString::to_string).collect::<Vec<_>>();
                if let Err(err) = store.mark_deleted(self.account, &strings, now).await {
                    warn!(%err,"failed to mark deleted commits in index");
                }
            }
            for (branch, runs) in repairs {
                self.svc
                    .fix_snapshot_log_after_delete(
                        self.account,
                        &branch,
                        self.overlay.path_history_root,
                        root.unwrap(),
                        &runs,
                    )
                    .await;
            }
            self.svc.schedule_gc(self.account);
        }
        Ok(DeleteResponse {
            dry_run: req.dry_run,
            deleted: ids.to_vec(),
            reparented: edit.set_grafts,
            plan,
            overlay_root: root,
            deleted_at: now,
            reclaim_after: now
                .saturating_add(i64::try_from(self.svc.gc_grace.as_secs()).unwrap_or(i64::MAX)),
        })
    }
}

impl GitService {
    /// Delete non-tip commits with one compare-and-swap on the account history.
    pub async fn delete_commits(&self, req: DeleteRequest) -> Result<DeleteResponse, GitError> {
        validate_account_id(&req.account)?;
        let mut targets = HashSet::new();
        let mut ids = Vec::new();
        for text in &req.commit_ids {
            let id = ObjectId::from_hex(text.as_bytes())
                .map_err(|_| GitError::InvalidCommitId(text.clone()))?;
            if targets.insert(id) {
                ids.push(id);
                if ids.len() > 100 {
                    return Err(GitError::InvalidCommitId(
                        "at most 100 distinct commit IDs are allowed".into(),
                    ));
                }
            }
        }
        if ids.is_empty() {
            return Err(GitError::InvalidCommitId("no commit ids".into()));
        }
        for _ in 0..3 {
            let overlay = self.overlay(&req.account).await?;
            if req
                .expected_overlay_root
                .is_some_and(|r| r != overlay.root_oid)
            {
                return Err(GitError::OverlayConflict);
            }
            let planner = Planner {
                svc: self,
                account: &req.account,
                overlay: &overlay,
                targets: targets.clone(),
                index_corrupt: false,
                commits: HashMap::new(),
            };
            match planner.execute(&req, &ids).await {
                Err(GitError::OverlayConflict) if req.expected_overlay_root.is_none() => continue,
                result => return result,
            }
        }
        Err(GitError::OverlayConflict)
    }
}
