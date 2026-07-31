//! PathLockWrappedFS — automatic lock acquisition for simple file operations.
//!
//! This wrapper sits between `StatsWrappedFS` and `MountableFS` in the stack.
//! It auto-acquires locks for `create`, `write`, `truncate`, `remove`, `remove_all`,
//! `rename`, and `replace`, then delegates to the inner filesystem.
//!
//! It is NOT a lock protocol implementation — all lock logic is delegated to
//! `PathLockManager`.

use std::sync::Arc;
use async_trait::async_trait;

use crate::core::filesystem::FileSystem;
use crate::core::internal_names::is_hidden_runtime_lock_name;
use crate::core::types::{FileInfo, GlobPage, GrepResult, TreeEntry, WriteFlag};
use crate::core::MountableFS;

use super::manager::{AutoPathLockAction, PathLockManager};
use super::types::{PathLockKind, PathLockRequest};

/// A `FileSystem` wrapper that auto-acquires path locks for mutating operations.
pub struct PathLockWrappedFS {
    /// The lock manager (single source of truth).
    manager: Arc<PathLockManager>,
    /// The inner filesystem.
    inner: Arc<dyn FileSystem>,
}

impl PathLockWrappedFS {
    /// Create a new PathLockWrappedFS.
    pub fn new(manager: Arc<PathLockManager>, inner: Arc<dyn FileSystem>) -> Self {
        Self { manager, inner }
    }

    /// Return the inner filesystem.
    pub fn inner_fs(&self) -> &Arc<dyn FileSystem> {
        &self.inner
    }

    /// Return true for virtual control paths and lock metadata that must not be auto-locked.
    fn should_bypass_auto_lock(path: &str) -> bool {
        path == "/queue"
            || path.starts_with("/queue/")
            || path == "/serverinfo"
            || path.starts_with("/serverinfo/")
            || path
                .rsplit_once('/')
                .map(|(_, name)| is_hidden_runtime_lock_name(name))
                .unwrap_or_else(|| is_hidden_runtime_lock_name(path))
    }

    /// Check if auto-locking should be skipped for the current context.
    ///
    /// Returns `true` when auto-locking is disabled or an active lease covers the operation.
    async fn should_skip_auto_lock(
        &self,
        requests: &[PathLockRequest],
    ) -> crate::core::Result<bool> {
        match self.manager.resolve_auto_pathlock_action(requests).await {
            Ok(AutoPathLockAction::Disabled | AutoPathLockAction::Covered(_)) => Ok(true),
            Ok(AutoPathLockAction::Acquire) => Ok(false),
            Err(error) => Err(crate::core::Error::internal(format!(
                "lock lease error: {error}"
            ))),
        }
    }

    /// Merge an operation result with its lock-release result.
    fn merge_operation_and_release<T>(
        operation: crate::core::Result<T>,
        release: super::types::PathLockResult<()>,
    ) -> crate::core::Result<T> {
        match (operation, release) {
            (Err(error), _) => Err(error),
            (Ok(value), Ok(())) => Ok(value),
            (Ok(_), Err(error)) => Err(crate::core::Error::internal(format!(
                "lock release error: {error}"
            ))),
        }
    }

    /// Return whether the routed encryption wrapper owns locking for this write.
    async fn encryption_handles_pathlock(&self, path: &str) -> bool {
        let any = self.inner.as_ref() as &dyn std::any::Any;
        match any.downcast_ref::<MountableFS>() {
            Some(mountable) => mountable.encryption_handles_pathlock(path).await,
            None => false,
        }
    }
}

#[async_trait]
impl FileSystem for PathLockWrappedFS {
    async fn create(&self, path: &str) -> crate::core::Result<()> {
        if Self::should_bypass_auto_lock(path) || self.encryption_handles_pathlock(path).await {
            return self.inner.create(path).await;
        }
        let requests = [PathLockRequest {
            path: path.to_string(),
            kind: PathLockKind::Exact,
        }];
        if self.should_skip_auto_lock(&requests).await? {
            return self.inner.create(path).await;
        }
        let lease = self
            .manager
            .acquire_exact(path, self.manager.default_lock_timeout(), None)
            .await
            .map_err(|e| crate::core::Error::internal(format!("lock error: {e}")))?;
        let result = self.inner.create(path).await;
        let release = self.manager.release(&lease).await;
        Self::merge_operation_and_release(result, release)
    }

    async fn mkdir(&self, path: &str, mode: u32) -> crate::core::Result<()> {
        self.inner.mkdir(path, mode).await
    }

    async fn remove(&self, path: &str) -> crate::core::Result<()> {
        if Self::should_bypass_auto_lock(path) {
            return self.inner.remove(path).await;
        }
        let requests = [PathLockRequest {
            path: path.to_string(),
            kind: PathLockKind::Exact,
        }];
        if self.should_skip_auto_lock(&requests).await? {
            return self.inner.remove(path).await;
        }
        let lease = self
            .manager
            .acquire_exact(path, self.manager.default_lock_timeout(), None)
            .await
            .map_err(|e| crate::core::Error::internal(format!("lock error: {e}")))?;
        let result = self.inner.remove(path).await;
        let release = self.manager.release(&lease).await;
        Self::merge_operation_and_release(result, release)
    }

    async fn remove_all(&self, path: &str) -> crate::core::Result<()> {
        if Self::should_bypass_auto_lock(path) {
            return self.inner.remove_all(path).await;
        }
        let requests = [PathLockRequest {
            path: path.to_string(),
            kind: PathLockKind::Tree,
        }];
        if self.should_skip_auto_lock(&requests).await? {
            return self.inner.remove_all(path).await;
        }
        let lease = self
            .manager
            .acquire_tree(path, self.manager.default_lock_timeout(), None)
            .await
            .map_err(|e| crate::core::Error::internal(format!("lock error: {e}")))?;
        let result = self.inner.remove_all(path).await;
        let release = self.manager.release(&lease).await;
        Self::merge_operation_and_release(result, release)
    }

    async fn read(&self, path: &str, offset: u64, size: u64) -> crate::core::Result<Vec<u8>> {
        self.inner.read(path, offset, size).await
    }

    async fn write(
        &self,
        path: &str,
        data: &[u8],
        offset: u64,
        flags: WriteFlag,
    ) -> crate::core::Result<u64> {
        if Self::should_bypass_auto_lock(path) || self.encryption_handles_pathlock(path).await {
            return self.inner.write(path, data, offset, flags).await;
        }
        let requests = [PathLockRequest {
            path: path.to_string(),
            kind: PathLockKind::Exact,
        }];
        if self.should_skip_auto_lock(&requests).await? {
            return self.inner.write(path, data, offset, flags).await;
        }
        let lease = self
            .manager
            .acquire_exact(path, self.manager.default_lock_timeout(), None)
            .await
            .map_err(|e| crate::core::Error::internal(format!("lock error: {e}")))?;
        let result = self.inner.write(path, data, offset, flags).await;
        let release = self.manager.release(&lease).await;
        Self::merge_operation_and_release(result, release)
    }

    async fn read_dir(&self, path: &str) -> crate::core::Result<Vec<FileInfo>> {
        self.inner.read_dir(path).await
    }

    async fn stat(&self, path: &str) -> crate::core::Result<FileInfo> {
        self.inner.stat(path).await
    }

    async fn rename(&self, old_path: &str, new_path: &str) -> crate::core::Result<()> {
        if Self::should_bypass_auto_lock(old_path) || Self::should_bypass_auto_lock(new_path) {
            return self.inner.rename(old_path, new_path).await;
        }

        // Determine if source is a directory.
        let src_is_dir = self
            .inner
            .stat(old_path)
            .await
            .map(|info| info.is_dir)
            .unwrap_or(false);

        let requests: Vec<PathLockRequest> = if src_is_dir {
            vec![
                PathLockRequest {
                    path: old_path.to_string(),
                    kind: PathLockKind::Tree,
                },
                PathLockRequest {
                    path: new_path.to_string(),
                    kind: PathLockKind::Exact,
                },
            ]
        } else {
            vec![
                PathLockRequest {
                    path: old_path.to_string(),
                    kind: PathLockKind::Exact,
                },
                PathLockRequest {
                    path: new_path.to_string(),
                    kind: PathLockKind::Exact,
                },
            ]
        };

        if self.should_skip_auto_lock(&requests).await? {
            return self.inner.rename(old_path, new_path).await;
        }

        let lease = self
            .manager
            .acquire_batch(&requests, self.manager.default_lock_timeout(), None)
            .await
            .map_err(|e| crate::core::Error::internal(format!("lock error: {e}")))?;
        let result = self.inner.rename(old_path, new_path).await;
        let release = self.manager.release(&lease).await;
        Self::merge_operation_and_release(result, release)
    }

    async fn replace(&self, src_path: &str, dst_path: &str) -> crate::core::Result<()> {
        if Self::should_bypass_auto_lock(src_path) || Self::should_bypass_auto_lock(dst_path) {
            return self.inner.replace(src_path, dst_path).await;
        }

        let requests = vec![
            PathLockRequest {
                path: src_path.to_string(),
                kind: PathLockKind::Exact,
            },
            PathLockRequest {
                path: dst_path.to_string(),
                kind: PathLockKind::Exact,
            },
        ];
        if self.should_skip_auto_lock(&requests).await? {
            return self.inner.replace(src_path, dst_path).await;
        }
        let lease = self
            .manager
            .acquire_batch(&requests, self.manager.default_lock_timeout(), None)
            .await
            .map_err(|e| crate::core::Error::internal(format!("lock error: {e}")))?;
        let result = self.inner.replace(src_path, dst_path).await;
        let release = self.manager.release(&lease).await;
        Self::merge_operation_and_release(result, release)
    }

    async fn chmod(&self, path: &str, mode: u32) -> crate::core::Result<()> {
        self.inner.chmod(path, mode).await
    }

    async fn truncate(&self, path: &str, size: u64) -> crate::core::Result<()> {
        if Self::should_bypass_auto_lock(path) || self.encryption_handles_pathlock(path).await {
            return self.inner.truncate(path, size).await;
        }
        let requests = [PathLockRequest {
            path: path.to_string(),
            kind: PathLockKind::Exact,
        }];
        if self.should_skip_auto_lock(&requests).await? {
            return self.inner.truncate(path, size).await;
        }
        let lease = self
            .manager
            .acquire_exact(path, self.manager.default_lock_timeout(), None)
            .await
            .map_err(|e| crate::core::Error::internal(format!("lock error: {e}")))?;
        let result = self.inner.truncate(path, size).await;
        let release = self.manager.release(&lease).await;
        Self::merge_operation_and_release(result, release)
    }

    async fn grep(
        &self,
        path: &str,
        pattern: &str,
        recursive: bool,
        case_insensitive: bool,
        node_limit: Option<usize>,
        exclude_path: Option<&str>,
        level_limit: Option<usize>,
    ) -> crate::core::Result<GrepResult> {
        self.inner
            .grep(
                path,
                pattern,
                recursive,
                case_insensitive,
                node_limit,
                exclude_path,
                level_limit,
            )
            .await
    }

    async fn tree_directory(
        &self,
        path: &str,
        show_hidden: bool,
        node_limit: Option<usize>,
        level_limit: Option<usize>,
    ) -> crate::core::Result<Vec<TreeEntry>> {
        self.inner
            .tree_directory(path, show_hidden, node_limit, level_limit)
            .await
    }

    async fn glob_directory(
        &self,
        path: &str,
        pattern: &str,
        show_hidden: bool,
        page_size: Option<usize>,
        level_limit: Option<usize>,
        continuation_token: Option<String>,
    ) -> crate::core::Result<GlobPage> {
        self.inner
            .glob_directory(
                path,
                pattern,
                show_hidden,
                page_size,
                level_limit,
                continuation_token,
            )
            .await
    }

    async fn ensure_parent_dirs(&self, path: &str, mode: u32) -> crate::core::Result<()> {
        self.inner.ensure_parent_dirs(path, mode).await
    }
}
