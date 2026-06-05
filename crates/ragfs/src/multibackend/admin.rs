use std::collections::HashSet;
use std::time::Duration;

use serde_json::{json, Value};

use crate::core::errors::Result;
use crate::core::filesystem::{normalize_prefix_path, FileSystem};
use crate::core::multibackend_wrapper::MultiWriteWrappedFS;
use crate::core::SyncLogEntry;
use crate::multibackend::meta::{current_required_ctx, parent_dir};

impl MultiWriteWrappedFS {
    /// Collect effective sync work entries under a path using the current request context.
    async fn collect_sync_work(
        &self,
        path: &str,
    ) -> Result<Vec<(String, SyncLogEntry, Vec<String>)>> {
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
        } else {
            add_dir(
                &mut dirs,
                &mut seen_dirs,
                normalize_prefix_path(&parent_dir(&normalized)),
            );
        }

        let mut work = Vec::new();
        for dir in dirs {
            let sync_log = inner.meta_store.get_sync_log_meta(&dir, &ctx).await?;
            if sync_log.entries.is_empty() {
                continue;
            }
            let redirect_meta = inner
                .meta_store
                .get_redirect_meta(&dir, &ctx)
                .await
                .unwrap_or_default();

            for (name, sync_entry) in sync_log.entries {
                let file_path = if dir == "/" {
                    format!("/{}", name)
                } else {
                    format!("{}/{}", dir, name)
                };
                if !path_info.is_dir && file_path != normalized {
                    continue;
                }
                let target_backend_names =
                    inner.target_backend_names(&redirect_meta, &name, &file_path, &sync_entry);
                work.push((file_path, sync_entry, target_backend_names));
            }
        }

        Ok(work)
    }

    /// Query effective multi-write sync status under a file or directory path.
    pub async fn system_sync_status(&self, path: &str) -> Result<Value> {
        let work = self.collect_sync_work(path).await?;
        let mut entries = Vec::new();
        let mut pending_target_count = 0usize;

        for (file_path, sync_entry, target_backend_names) in work {
            let mut targets = Vec::new();
            let mut all_synced = true;

            for backend_name in target_backend_names {
                let acked_seq = sync_entry.acked_seq(&backend_name);
                let in_sync = sync_entry.is_in_sync(&backend_name);
                if !in_sync {
                    pending_target_count += 1;
                    all_synced = false;
                }
                let state = sync_entry.backend_state(&backend_name);
                targets.push(json!({
                    "name": backend_name,
                    "acked_seq": acked_seq,
                    "retry_failures": state.map(|state| state.retry_failures).unwrap_or(0),
                    "quarantined": state.map(|state| state.quarantined).unwrap_or(false),
                    "in_sync": in_sync,
                }));
            }

            entries.push(json!({
                "path": file_path,
                "latest_seq": sync_entry.latest_seq,
                "op": serde_json::to_value(&sync_entry.op)?,
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

        for (file_path, sync_entry, target_backend_names) in work {
            for backend_name in target_backend_names {
                let acked_seq = sync_entry.acked_seq(&backend_name);
                let was_quarantined = sync_entry.is_quarantined(&backend_name);
                if sync_entry.is_in_sync(&backend_name) {
                    skipped += 1;
                    results.push(json!({
                        "path": file_path,
                        "target": backend_name,
                        "status": "skipped",
                        "latest_seq": sync_entry.latest_seq,
                        "acked_seq": acked_seq,
                    }));
                    continue;
                }

                let mut last_error = None;
                let mut success = false;
                for _attempt in 0..self.inner.max_retry_per_round {
                    match self
                        .inner
                        .replay_operation(&file_path, &backend_name, &ctx)
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
                        "path": file_path,
                        "target": backend_name,
                        "status": "retried",
                        "latest_seq": sync_entry.latest_seq,
                        "acked_seq": sync_entry.latest_seq,
                    }));
                } else {
                    self.inner
                        .record_backup_retry_failure(&file_path, &backend_name, &ctx)
                        .await?;
                    failed += 1;
                    results.push(json!({
                        "path": file_path,
                        "target": backend_name,
                        "status": "failed",
                        "latest_seq": sync_entry.latest_seq,
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
