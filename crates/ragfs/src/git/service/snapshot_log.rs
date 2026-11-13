use super::*;
use crate::git::snapshot_log::{
    can_index_account_branch, can_index_paths, fanout_changed_paths, SnapshotLogError,
    SnapshotLogRow, SnapshotLogStore,
};

impl GitService {
    /// Best-effort append of SnapshotLog rows after a branch ref advances.
    pub(super) async fn try_append_snapshot_log(
        &self,
        account: &str,
        branch: &str,
        parent_commit_oid: Option<ObjectId>,
        commit_oid: ObjectId,
        changed_paths: &[String],
    ) {
        let Some(store) = self.snapshot_log_store.clone() else {
            return;
        };
        let limits = store.limits();
        if !can_index_account_branch(limits, account, branch) {
            return;
        }
        let rows = match snapshot_log_rows_for_paths(limits, commit_oid, changed_paths) {
            Ok(rows) => rows,
            Err(err) => {
                warn!("snapshot log row build skipped for {account}/{branch}: {err}");
                return;
            }
        };
        let parent_hex = parent_commit_oid.map(|oid| oid.to_hex().to_string());
        match store
            .append_rows_if_latest(account, branch, parent_hex.as_deref(), &rows)
            .await
        {
            Ok(()) => {}
            Err(err) => {
                warn!("snapshot log append failed for {account}/{branch}: {err}");
                self.trigger_snapshot_log_backfill(account, branch);
            }
        }
    }

    /// Queue one background SnapshotLog backfill for `account+branch`.
    pub(super) fn trigger_snapshot_log_backfill(&self, account: &str, branch: &str) {
        let Some(store) = self.snapshot_log_store.clone() else {
            return;
        };
        if !can_index_account_branch(store.limits(), account, branch) {
            return;
        }
        let key = (account.to_string(), branch.to_string());
        {
            let mut active = self.snapshot_log_backfills.lock().unwrap();
            if !active.insert(key.clone()) {
                return;
            }
        }

        let object_store = self.object_store.clone();
        let ref_store = self.ref_store.clone();
        let active = self.snapshot_log_backfills.clone();
        tokio::spawn(async move {
            if let Err(err) =
                run_snapshot_log_backfill(object_store, ref_store, store, key.0.clone(), key.1.clone())
                    .await
            {
                warn!("snapshot log backfill failed for {}/{}: {}", key.0, key.1, err);
            }
            active.lock().unwrap().remove(&key);
        });
    }
}

/// Convert restore's project-relative diff paths to account-relative paths.
pub(super) fn restore_snapshot_log_paths(
    project_dir: Option<&str>,
    diff: &crate::git::types::RestoreDiff,
) -> Vec<String> {
    let mut out = Vec::with_capacity(diff.to_write.len() + diff.to_delete.len());
    for (path, _) in &diff.to_write {
        out.push(prefix_restore_path(project_dir, path));
    }
    for path in &diff.to_delete {
        out.push(prefix_restore_path(project_dir, path));
    }
    out
}

fn prefix_restore_path(project_dir: Option<&str>, path: &str) -> String {
    match project_dir {
        Some(project_dir) => format!("{project_dir}/{path}"),
        None => path.to_string(),
    }
}

fn snapshot_log_rows_for_paths(
    limits: crate::git::snapshot_log::SnapshotLogLimits,
    commit_oid: ObjectId,
    changed_paths: &[String],
) -> Result<Vec<SnapshotLogRow>, SnapshotLogError> {
    let paths = fanout_changed_paths(changed_paths);
    if paths.len() > limits.max_rows_per_commit {
        return Err(SnapshotLogError::TooManyRows {
            count: paths.len(),
            limit: limits.max_rows_per_commit,
        });
    }
    if !can_index_paths(limits, &paths) {
        return Err(SnapshotLogError::UnindexableRow(
            "fan-out path exceeds provider limit".to_string(),
        ));
    }
    let commit_hex = commit_oid.to_hex().to_string();
    Ok(paths
        .into_iter()
        .map(|path| SnapshotLogRow {
            path,
            commit_oid: commit_hex.clone(),
        })
        .collect())
}

async fn run_snapshot_log_backfill(
    object_store: Arc<dyn ObjectStore>,
    ref_store: Arc<dyn RefStore>,
    store: Arc<dyn SnapshotLogStore>,
    account: String,
    branch: String,
) -> Result<(), String> {
    let ref_name = format!("refs/heads/{branch}");
    let head = match ref_store.read(&account, &ref_name).await {
        Ok(head) => head,
        Err(RefStoreError::NotFound(_)) => return Ok(()),
        Err(err) => return Err(err.to_string()),
    };
    let latest = store
        .load_latest_indexed_commit(&account, &branch)
        .await
        .map_err(|err| err.to_string())?;
    let latest_oid = latest.as_ref().map(|(_, oid)| oid.clone());

    let mut chain = Vec::new();
    let mut found_latest = latest_oid.is_none();
    let mut next = Some(head);
    while let Some(commit_oid) = next {
        let meta = load_commit_meta(object_store.as_ref(), &account, &commit_oid)
            .await
            .map_err(|err| err.to_string())?;
        if latest_oid.as_deref() == Some(commit_oid.to_hex().to_string().as_str()) {
            found_latest = true;
            chain.push((commit_oid, meta));
            break;
        }
        next = meta.parents.first().copied();
        chain.push((commit_oid, meta));
    }

    if !found_latest {
        warn!("snapshot log latest is not on branch chain for {account}/{branch}");
        return Ok(());
    }

    chain.reverse();
    let mut parent_tree = if latest_oid.is_some() && !chain.is_empty() {
        Some(chain.remove(0).1.tree)
    } else {
        None
    };

    for (commit_oid, meta) in chain {
        let parent_oid = meta.parents.first().copied();
        let changed_paths: Vec<String> =
            crate::git::tree_builder::diff_trees(
                object_store.as_ref(),
                &account,
                parent_tree,
                Some(meta.tree),
            )
            .await
            .map_err(|err| err.to_string())?
            .into_iter()
            .map(|(path, _)| path)
            .collect();
        let rows = match snapshot_log_rows_for_paths(store.limits(), commit_oid, &changed_paths) {
            Ok(rows) => rows,
            Err(err) => {
                warn!("snapshot log backfill stopped for {account}/{branch}: {err}");
                return Ok(());
            }
        };
        let parent_hex = parent_oid.map(|oid| oid.to_hex().to_string());
        match store
            .append_rows_if_latest(&account, &branch, parent_hex.as_deref(), &rows)
            .await
        {
            Ok(()) => {}
            Err(err) if err.is_stale_latest() => return Ok(()),
            Err(err) => return Err(err.to_string()),
        }
        parent_tree = Some(meta.tree);
    }

    Ok(())
}
