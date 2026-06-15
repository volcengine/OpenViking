use std::sync::Arc;

use serde_json::{json, Value};

use super::*;
use crate::multibackend::meta::{current_required_ctx, file_name, parent_dir};

impl Inner {
    /// Keep write-side call sites explicit even though read routing is uncached.
    pub(super) async fn invalidate_read_route(&self, _path: &str) {}

    /// Record read-route counters in one place so hot paths stay explicit.
    fn record_read_route(&self, source: ReadRouteSource) {
        match source {
            ReadRouteSource::Backup => {
                self.read_backup_hits.fetch_add(1, Ordering::Relaxed);
            }
            ReadRouteSource::Primary => {
                self.read_primary_hits.fetch_add(1, Ordering::Relaxed);
            }
            ReadRouteSource::Redirect => {
                self.read_redirect_hits.fetch_add(1, Ordering::Relaxed);
            }
            ReadRouteSource::Miss => {
                self.read_misses.fetch_add(1, Ordering::Relaxed);
            }
        }
    }

    /// Export read-route metrics for operational introspection.
    pub(crate) fn read_route_metrics(&self) -> Value {
        json!({
            "cache_hits": 0,
            "backup_hits": self.read_backup_hits.load(Ordering::Relaxed),
            "primary_hits": self.read_primary_hits.load(Ordering::Relaxed),
            "redirect_hits": self.read_redirect_hits.load(Ordering::Relaxed),
            "misses": self.read_misses.load(Ordering::Relaxed),
        })
    }

    /// Stat the first reachable redirect target and return user-visible metadata.
    pub(super) async fn redirect_file_info(
        &self,
        path: &str,
        name: &str,
        redirect_entry: &RedirectEntry,
    ) -> FileInfo {
        for target_name in &redirect_entry.targets {
            if let Some(be) = self.backup_by_name(target_name) {
                if let Ok(mut info) = be.backend.stat(path).await {
                    info.name = name.to_string();
                    return info;
                }
            }
        }
        FileInfo::new_file(name.to_string(), 0, 0o644)
    }

    /// Resolve the read backend for a path using the fallback chain.
    pub(super) async fn resolve_read_backend(&self, path: &str) -> Option<Arc<dyn FileSystem>> {
        let normalized = normalize_prefix_path(path);
        let read_backups = self.read_backups_sorted();
        let backup_exists = futures::future::join_all(read_backups.iter().map(|backup| async {
            (
                backup.name.clone(),
                backup.backend.clone(),
                backup.backend.exists(&normalized).await,
            )
        }))
        .await;
        for (_name, backend, exists) in backup_exists {
            if exists {
                self.record_read_route(ReadRouteSource::Backup);
                return Some(backend);
            }
        }

        if self.primary().backend.exists(&normalized).await {
            self.record_read_route(ReadRouteSource::Primary);
            return Some(self.primary().backend.clone());
        }

        let dir = parent_dir(&normalized);
        let name = file_name(&normalized).to_string();
        let ctx = current_required_ctx()
            .or_else(|_| self.meta_store.ctx_resolver().resolve(&dir))
            .ok()?;
        if let Ok(redirect_meta) = self.meta_store.get_redirect_meta(&dir, &ctx).await {
            if let Some(entry) = redirect_meta.entries.get(&name) {
                let redirect_targets: Vec<(String, Arc<dyn FileSystem>)> = entry
                    .targets
                    .iter()
                    .filter_map(|target_name| {
                        self.backup_by_name(target_name)
                            .map(|be| (be.name.clone(), be.backend.clone()))
                    })
                    .collect();
                let redirect_exists = futures::future::join_all(redirect_targets.iter().map(
                    |(target_name, backend)| async {
                        (
                            target_name.clone(),
                            backend.clone(),
                            backend.exists(&normalized).await,
                        )
                    },
                ))
                .await;
                for (_target_name, backend, exists) in redirect_exists {
                    if exists {
                        self.record_read_route(ReadRouteSource::Redirect);
                        return Some(backend);
                    }
                }
            }
        }

        self.record_read_route(ReadRouteSource::Miss);
        None
    }
}
