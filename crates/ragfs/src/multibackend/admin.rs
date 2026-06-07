use std::collections::HashSet;
use std::time::Duration;

use serde_json::{json, Value};

use crate::core::errors::Result;
use crate::core::filesystem::{normalize_prefix_path, FileSystem};
use crate::core::multibackend_wrapper::{MultiWriteWrappedFS, SyncWorkEntry};
use crate::multibackend::meta::{current_required_ctx, parent_dir};

impl MultiWriteWrappedFS {
    /// Collect effective sync work entries under a path using the current request context.
    async fn collect_sync_work(&self, path: &str) -> Result<Vec<SyncWorkEntry>> {
        let ctx = current_required_ctx()?;
        let inner = &self.inner;
        let normalized = normalize_prefix_path(path);
        let path_info = <Self as FileSystem>::stat(self, &normalized).await?;
        let mut dirs = Vec::new();
        let mut seen_dirs = HashSet::new();

        let add_dir = |dirs: &mut Vec<String>, seen_dirs: &mut HashSet<String>, dir: String| {
            if seen_dirs.insert(dir.clone()) {
                dirs.push(dir);
            }
        };

        if path_info.is_dir {
            add_dir(&mut dirs, &mut seen_dirs, normalized.clone());
            for dir in inner.pending_dirs_snapshot().await {
                if dir == normalized
                    || dir
                        .strip_prefix(&normalized)
                        .is_some_and(|suffix| suffix.starts_with('/'))
                {
                    add_dir(&mut dirs, &mut seen_dirs, dir);
                }
            }
            if dirs.len() == 1 {
                for entry in inner
                    .primary()
                    .backend
                    .tree_directory(&normalized, true, None, None)
                    .await?
                {
                    if entry.info.is_dir {
                        add_dir(
                            &mut dirs,
                            &mut seen_dirs,
                            normalize_prefix_path(&entry.path),
                        );
                    }
                }
            }
        } else {
            add_dir(
                &mut dirs,
                &mut seen_dirs,
                normalize_prefix_path(&parent_dir(&normalized)),
            );
        }

        let mut work = Vec::new();
        for dir in dirs {
            work.extend(
                inner
                    .sync_work_in_dir(&dir, &ctx)
                    .await?
                    .into_iter()
                    .filter(|work| path_info.is_dir || work.file_path == normalized),
            );
        }

        Ok(work)
    }

    /// Query effective multi-write sync status under a file or directory path.
    pub async fn system_sync_status(&self, path: &str) -> Result<Value> {
        let work = self.collect_sync_work(path).await?;
        let mut entries = Vec::new();
        let mut pending_target_count = 0usize;

        for work in work {
            let mut targets = Vec::new();
            let primary_committed = work.entry.is_primary_committed();
            let mut all_synced = primary_committed;

            for backend_name in work.targets {
                let acked_seq = if primary_committed {
                    work.entry.acked_seq(&backend_name)
                } else {
                    0
                };
                let in_sync = primary_committed && work.entry.is_in_sync(&backend_name);
                if primary_committed && !in_sync {
                    pending_target_count += 1;
                    all_synced = false;
                }
                let state = work.entry.backend_state(&backend_name);
                targets.push(json!({
                    "name": backend_name,
                    "acked_seq": acked_seq,
                    "retry_failures": state.map(|state| state.retry_failures).unwrap_or(0),
                    "quarantined": state.map(|state| state.quarantined).unwrap_or(false),
                    "primary_committed": primary_committed,
                    "in_sync": in_sync,
                }));
            }

            entries.push(json!({
                "path": work.file_path,
                "latest_seq": work.entry.latest_seq,
                "primary_committed": primary_committed,
                "op": serde_json::to_value(&work.entry.op)?,
                "all_synced": all_synced,
                "targets": targets,
            }));
        }

        entries.sort_by(|a, b| {
            let ap = a.get("path").and_then(Value::as_str).unwrap_or_default();
            let bp = b.get("path").and_then(Value::as_str).unwrap_or_default();
            ap.cmp(bp)
        });

        Ok(json!({
            "path": normalize_prefix_path(path),
            "entry_count": entries.len(),
            "pending_target_count": pending_target_count,
            "capabilities": {
                "multi_instance_safe": false
            },
            "read_route_metrics": self.inner.read_route_metrics(),
            "entries": entries,
        }))
    }

    /// Manually retry lagging multi-write targets under a file or directory path.
    pub async fn system_sync_retry(&self, path: &str) -> Result<Value> {
        let ctx = current_required_ctx()?;
        let work = self.collect_sync_work(path).await?;
        let mut results = Vec::new();
        let mut retried = 0usize;
        let mut failed = 0usize;
        let mut skipped = 0usize;

        for work in work {
            if !work.entry.is_primary_committed() {
                skipped += work.targets.len();
                for backend_name in work.targets {
                    results.push(json!({
                        "path": work.file_path,
                        "target": backend_name,
                        "status": "awaiting_primary_commit",
                        "latest_seq": work.entry.latest_seq,
                        "primary_committed": false,
                        "acked_seq": 0,
                    }));
                }
                continue;
            }

            for backend_name in work.targets {
                let acked_seq = work.entry.acked_seq(&backend_name);
                let was_quarantined = work.entry.is_quarantined(&backend_name);
                if work.entry.is_in_sync(&backend_name) {
                    skipped += 1;
                    results.push(json!({
                        "path": work.file_path,
                        "target": backend_name,
                        "status": "skipped",
                        "latest_seq": work.entry.latest_seq,
                        "acked_seq": acked_seq,
                    }));
                    continue;
                }

                let mut last_error = None;
                let mut success = false;
                for _attempt in 0..self.inner.max_retry_per_round {
                    match self
                        .inner
                        .replay_operation(&work.file_path, &backend_name, &ctx)
                        .await
                    {
                        Ok(()) => {
                            success = true;
                            break;
                        }
                        Err(err) => {
                            last_error = Some(err.to_string());
                            tokio::time::sleep(Duration::from_millis(
                                self.inner.retry_backoff_base_ms,
                            ))
                            .await;
                        }
                    }
                }

                if success {
                    retried += 1;
                    results.push(json!({
                        "path": work.file_path,
                        "target": backend_name,
                        "status": "retried",
                        "latest_seq": work.entry.latest_seq,
                        "acked_seq": work.entry.latest_seq,
                    }));
                } else {
                    self.inner
                        .record_backup_retry_failure(&work.file_path, &backend_name, &ctx)
                        .await?;
                    failed += 1;
                    results.push(json!({
                        "path": work.file_path,
                        "target": backend_name,
                        "status": "failed",
                        "latest_seq": work.entry.latest_seq,
                        "acked_seq": acked_seq,
                        "was_quarantined": was_quarantined,
                        "error": last_error.unwrap_or_else(|| "unknown replay error".to_string()),
                    }));
                }
            }
        }

        Ok(json!({
            "path": normalize_prefix_path(path),
            "retried": retried,
            "failed": failed,
            "skipped": skipped,
            "results": results,
        }))
    }
}
