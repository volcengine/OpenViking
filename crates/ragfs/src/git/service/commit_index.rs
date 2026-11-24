use super::*;
use crate::git::CommitIndexStore;

impl GitService {
    /// Attach a rebuildable commit-history index, independently of the working-tree stat cache.
    pub fn with_commit_index(mut self, store: Option<Arc<dyn CommitIndexStore>>) -> Self {
        self.commit_index = store;
        self
    }

    pub(super) async fn try_append_commit_index(
        &self,
        account: &str,
        branch: &str,
        parent: Option<ObjectId>,
        commit: ObjectId,
        tree: ObjectId,
        committed_at: i64,
    ) {
        let Some(store) = &self.commit_index else {
            return;
        };
        let parent = parent.map(|oid| oid.to_string());
        if let Err(err) = store
            .append_commit_if_latest(
                account,
                branch,
                parent.as_deref(),
                &commit.to_string(),
                &tree.to_string(),
                committed_at,
            )
            .await
        {
            warn!("commit index append failed for {account}/{branch}: {err}");
            self.trigger_commit_index_backfill(account, branch);
        }
    }

    pub(crate) async fn invalidate_commit_index(&self, account: &str, branch: &str) {
        let Some(store) = &self.commit_index else {
            return;
        };
        if let Err(err) = store.clear_branch(account, branch).await {
            warn!("commit index invalidation failed for {account}/{branch}: {err}");
            return;
        }
        self.trigger_commit_index_backfill(account, branch);
    }

    pub(super) fn trigger_commit_index_backfill(&self, account: &str, branch: &str) {
        let Some(store) = self.commit_index.clone() else {
            return;
        };
        let key = (account.to_owned(), branch.to_owned());
        {
            let mut active = self.commit_index_backfills.lock().unwrap();
            if let Some(pending) = active.get_mut(&key) {
                // A new commit or invalidation arrived while the worker was using an older tip.
                *pending = true;
                return;
            }
            active.insert(key.clone(), false);
        }
        let service = self.clone();
        tokio::spawn(async move {
            loop {
                if let Err(err) = service
                    .backfill_commit_index(&key.0, &key.1, store.as_ref())
                    .await
                {
                    warn!(
                        "commit index backfill failed for {}/{}: {err}",
                        key.0, key.1
                    );
                }
                let mut active = service.commit_index_backfills.lock().unwrap();
                if active.get(&key) == Some(&true) {
                    active.insert(key.clone(), false);
                } else {
                    active.remove(&key);
                    break;
                }
            }
        });
    }

    async fn backfill_commit_index(
        &self,
        account: &str,
        branch: &str,
        store: &dyn CommitIndexStore,
    ) -> anyhow::Result<()> {
        let overlay = self.overlay(account).await?;
        let head = self
            .ref_store
            .read(account, &format!("refs/heads/{branch}"))
            .await?;
        let latest = store
            .latest_commit(account, branch)
            .await?
            .map(|row| row.commit_oid);
        let mut cursor = Some(head);
        let mut pending = Vec::new();
        while let Some(oid) = cursor {
            if Some(oid.to_string()) == latest {
                break;
            }
            let meta =
                load_commit_meta(self.object_store.as_ref(), &overlay, account, &oid).await?;
            cursor = meta.parents.first().copied();
            pending.push((
                oid,
                cursor,
                meta.raw_parents.first().copied(),
                meta.tree,
                meta.committer.time_seconds,
            ));
        }
        if cursor.is_none() && latest.is_some() {
            store.clear_branch(account, branch).await?;
        }
        for (oid, parent, raw_parent, tree, time) in pending.into_iter().rev() {
            store
                .append_with_raw_parent(
                    account,
                    branch,
                    parent.map(|p| p.to_string()).as_deref(),
                    raw_parent.map(|p| p.to_string()).as_deref(),
                    &oid.to_string(),
                    &tree.to_string(),
                    time,
                )
                .await?;
        }
        Ok(())
    }
}
