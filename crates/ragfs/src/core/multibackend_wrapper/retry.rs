use std::time::{Duration, Instant};

use rand::Rng;
use tracing::warn;

use super::*;

impl Inner {
    /// Mark one directory as pending retry work.
    pub(super) async fn mark_pending_dir(&self, dir: &str) {
        self.pending_dirs.lock().await.insert(dir.to_string());
    }

    /// Remove one directory from the retry set.
    async fn clear_pending_dir(&self, dir: &str) {
        self.pending_dirs.lock().await.remove(dir);
    }

    /// Snapshot the current set of pending retry directories.
    pub(crate) async fn pending_dirs_snapshot(&self) -> Vec<String> {
        self.pending_dirs.lock().await.iter().cloned().collect()
    }

    /// Recompute whether one directory still has pending retry work.
    pub(super) async fn refresh_pending_dir(&self, dir: &str, ctx: &FsContext) -> Result<()> {
        if self.sync_work_in_dir(dir, ctx).await?.iter().any(|work| {
            work.entry.is_primary_committed()
                && work.targets.iter().any(|target| {
                    !work.entry.is_in_sync(target) && !work.entry.is_quarantined(target)
                })
        }) {
            self.mark_pending_dir(dir).await;
        } else {
            self.clear_pending_dir(dir).await;
        }
        Ok(())
    }

    /// Collect sync work entries for one metadata directory.
    pub(crate) async fn sync_work_in_dir(
        &self,
        dir: &str,
        ctx: &FsContext,
    ) -> Result<Vec<SyncWorkEntry>> {
        let sync_log = self.meta_store.get_sync_log_meta(dir, ctx).await?;
        if sync_log.entries.is_empty() {
            return Ok(Vec::new());
        }

        let redirect_meta = self.meta_store.get_redirect_meta(dir, ctx).await?;
        Ok(sync_log
            .entries
            .into_iter()
            .map(|(file_name, entry)| {
                let file_path = if dir == "/" {
                    format!("/{}", file_name)
                } else {
                    format!("{}/{}", dir, file_name)
                };
                let targets =
                    self.target_backend_names(&redirect_meta, &file_name, &file_path, &entry);
                SyncWorkEntry {
                    file_path,
                    entry,
                    targets,
                }
            })
            .collect())
    }

    /// Record a started background task.
    pub(super) fn background_task_started(&self) {
        self.background_tasks.fetch_add(1, Ordering::SeqCst);
    }

    /// Record a finished background task and wake idle waiters if needed.
    pub(super) fn background_task_finished(&self) {
        if self.background_tasks.fetch_sub(1, Ordering::SeqCst) == 1 {
            self.idle_notify.notify_waiters();
        }
    }

    /// Wait for all background tasks to drain.
    pub(super) async fn wait_idle(&self, timeout: Duration) -> Result<()> {
        let deadline = Instant::now() + timeout;
        loop {
            if self.background_tasks.load(Ordering::SeqCst) == 0 {
                return Ok(());
            }
            let now = Instant::now();
            if now >= deadline {
                return Err(Error::timeout(
                    "timed out while waiting multi-write background tasks to drain",
                ));
            }
            let wait = deadline.saturating_duration_since(now);
            tokio::time::timeout(wait, self.idle_notify.notified())
                .await
                .map_err(|_| {
                    Error::timeout("timed out while waiting multi-write background tasks to drain")
                })?;
        }
    }

    /// Update the acked_seq for a backup in the sync log.
    pub(super) async fn update_backup_acked_seq(
        &self,
        path: &str,
        backup_name: &str,
        ctx: &FsContext,
    ) -> Result<()> {
        self.update_backend_state(path, backup_name, ctx, |state, latest_seq| {
            state.mark_acked(latest_seq);
        })
        .await?;
        self.refresh_pending_dir(&parent_dir(path), ctx).await
    }

    /// Record a replay failure and quarantine the target after repeated failures.
    pub(crate) async fn record_backup_retry_failure(
        &self,
        path: &str,
        backup_name: &str,
        ctx: &FsContext,
    ) -> Result<()> {
        self.update_backend_state(path, backup_name, ctx, |state, _latest_seq| {
            state.mark_retry_failed(self.quarantine_after_failures);
        })
        .await?;
        self.refresh_pending_dir(&parent_dir(path), ctx).await
    }

    /// Update per-backend sync state for a file entry.
    async fn update_backend_state<F>(
        &self,
        path: &str,
        backup_name: &str,
        ctx: &FsContext,
        update: F,
    ) -> Result<()>
    where
        F: FnOnce(&mut BackendSyncState, u64) + Send,
    {
        let dir = parent_dir(path);
        let backup_name = backup_name.to_string();
        let name = file_name(path).to_string();
        self.meta_store
            .update_dir_meta(&dir, ctx, move |_redirect, sync_log| {
                if let Some(entry) = sync_log.entries.get_mut(&name) {
                    let state = entry.backends.entry(backup_name).or_default();
                    update(state, entry.latest_seq);
                }
                Ok(())
            })
            .await
    }

    /// Replay a single operation on a lagging backup.
    pub(crate) async fn replay_operation(
        &self,
        file_path: &str,
        backup_name: &str,
        ctx: &FsContext,
    ) -> Result<()> {
        let queue_key = Self::backup_queue_key(file_path, backup_name);
        self.path_queues
            .with_path_lock(&queue_key, || async {
                let dir = parent_dir(file_path);
                let name = file_name(file_path).to_string();
                let sync_log = self.meta_store.get_sync_log_meta(&dir, ctx).await?;
                let entry = sync_log
                    .entries
                    .get(&name)
                    .ok_or_else(|| Error::not_found(file_path))?
                    .clone();
                let backup = match self.backup_by_name(backup_name) {
                    Some(b) => b,
                    None => {
                        return Err(Error::internal(format!(
                            "backup '{}' not found",
                            backup_name
                        )))
                    }
                };

                entry
                    .op
                    .replay(
                        self.primary().backend.clone(),
                        backup.backend.clone(),
                        file_path,
                        ctx,
                    )
                    .await?;

                self.update_backup_acked_seq(file_path, backup_name, ctx)
                    .await?;

                Ok(())
            })
            .await
    }

    /// Retry all pending backup work for one directory and keep metadata errors visible.
    pub(crate) async fn retry_pending_dir(&self, dir: &str) -> Result<()> {
        let ctx = self.meta_store.ctx_resolver().resolve(dir)?;

        for work in self.sync_work_in_dir(dir, &ctx).await? {
            if !work.entry.is_primary_committed() {
                continue;
            }

            for backup_name in &work.targets {
                if work.entry.is_in_sync(backup_name) || work.entry.is_quarantined(backup_name) {
                    continue;
                }

                let mut success = false;
                for attempt in 0..self.max_retry_per_round {
                    if self.retry_cancelled.load(Ordering::SeqCst) {
                        return Ok(());
                    }
                    if self
                        .replay_operation(&work.file_path, backup_name, &ctx)
                        .await
                        .is_ok()
                    {
                        success = true;
                        break;
                    }
                    let base_ms = self.retry_backoff_base_ms.saturating_mul(1u64 << attempt);
                    let jitter_ms = rand::thread_rng().gen_range(0..=50);
                    tokio::time::sleep(Duration::from_millis(base_ms + jitter_ms)).await;
                }

                if !success {
                    self.record_backup_retry_failure(&work.file_path, backup_name, &ctx)
                        .await?;
                }
            }
        }

        self.refresh_pending_dir(dir, &ctx).await
    }

    /// Background retry loop: periodically scans sync_log for lagging backups and replays.
    pub(super) async fn retry_loop(inner: Arc<Inner>) {
        loop {
            if inner.retry_cancelled.load(Ordering::SeqCst) {
                break;
            }

            tokio::select! {
                _ = tokio::time::sleep(inner.retry_interval) => {}
                _ = inner.retry_shutdown.notified() => break,
            }
            let dirs = inner.pending_dirs_snapshot().await;
            for dir in dirs {
                if let Err(err) = inner.retry_pending_dir(&dir).await {
                    warn!(
                        dir = %dir,
                        error = %err,
                        "multi-write retry skipped one pending directory due to metadata or replay error"
                    );
                }
            }
        }
        inner.background_task_finished();
    }
}
