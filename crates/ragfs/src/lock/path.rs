//! Shared lexical path rules for lock identity and lease coverage.

use super::types::{PathLockError, PathLockResult};

/// Validate a logical path and preserve compatibility with a single trailing slash.
///
/// Reject aliases instead of rewriting them: the filesystem still receives the
/// original path, and resolving `..` could change the operation's scope.
pub(super) fn normalize_logical_path(path: &str) -> PathLockResult<&str> {
    if !path.starts_with('/')
        || path.contains("//")
        || path.contains('\0')
        || path.split('/').any(|part| matches!(part, "." | ".."))
    {
        return Err(PathLockError::InvalidRequest(format!(
            "PathLock requires an absolute logical path without repeated '/', \
             '.' or '..' components, or NUL: {path:?}"
        )));
    }
    Ok(if path == "/" {
        path
    } else {
        path.strip_suffix('/').unwrap_or(path)
    })
}

/// Provider token paths must already be normalized by the Manager.
pub(super) fn validate_normalized_logical_path(path: &str) -> PathLockResult<()> {
    if normalize_logical_path(path)? != path {
        return Err(PathLockError::InvalidRequest(format!(
            "PathLock requires a normalized absolute logical path: {path:?}"
        )));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::{FileSystem, FsContextInner, PathLockContext, FS_CTX};
    use crate::lock::types::PathLockKind;
    use crate::lock::{MemoryPathLockProvider, PathLockConfig, PathLockManager, PathLockRequest};
    use crate::plugins::memfs::MemFileSystem;
    use std::sync::Arc;
    use std::time::Duration;

    #[test]
    fn logical_paths_reject_aliases_and_preserve_valid_names() {
        for path in [
            "", "relative", "//", "/a//b", "/a//", "/a/./b", "/./a", "/a/../b", "/a/.", "/a/..",
            "/a\0b",
        ] {
            assert!(normalize_logical_path(path).is_err(), "{path:?}");
            assert!(validate_normalized_logical_path(path).is_err(), "{path:?}");
        }
        for path in [
            "/",
            "/a",
            "/a/.hidden",
            "/a/file..txt",
            "/中文/文件",
            "/a/file name",
        ] {
            assert_eq!(normalize_logical_path(path).unwrap(), path);
            validate_normalized_logical_path(path).unwrap();
        }
        assert_eq!(normalize_logical_path("/a/").unwrap(), "/a");
        assert!(validate_normalized_logical_path("/a/").is_err());
    }

    /// Invalid aliases cannot acquire locks, query locks, or borrow Tree coverage.
    #[tokio::test]
    async fn path_aliases_are_rejected_before_acquisition_and_coverage() {
        let fs = Arc::new(MemFileSystem::new());
        fs.mkdir("/data", 0o755).await.unwrap();
        let mgr = PathLockManager::new(
            fs.clone(),
            Arc::new(MemoryPathLockProvider::new()),
            PathLockConfig::default(),
        );
        let tree = mgr
            .acquire_tree("/data", Duration::ZERO, None)
            .await
            .unwrap();
        let context = Arc::new(FsContextInner::with_pathlock(
            "account",
            PathLockContext {
                lease_ref: Some(tree.lease.lease_ref.clone()),
                disable_auto_pathlock: false,
            },
        ));
        for path in [
            "",
            "relative",
            "//",
            "/data//file",
            "/data/./file",
            "/data/../other/file",
            "/data/file//",
            "/data/file\0",
        ] {
            let request = PathLockRequest {
                path: path.to_string(),
                kind: PathLockKind::Exact,
            };
            for kind in [PathLockKind::Exact, PathLockKind::Tree] {
                assert!(
                    matches!(
                        mgr.acquire_batch(
                            &[
                                PathLockRequest {
                                    path: "/unrelated/file".into(),
                                    kind: PathLockKind::Exact
                                },
                                PathLockRequest {
                                    path: path.into(),
                                    kind
                                },
                            ],
                            Duration::ZERO,
                            None
                        )
                        .await,
                        Err(PathLockError::InvalidRequest(_))
                    ),
                    "Manager accepted {path:?}"
                );
            }
            assert!(
                matches!(
                    mgr.is_locked(path, true).await,
                    Err(PathLockError::InvalidRequest(_))
                ),
                "is_locked accepted {path:?}"
            );
            assert!(
                !tree.lease.covers(&request),
                "Tree lease covered alias {path:?}"
            );
            assert!(matches!(
                mgr.require_covered_lease_ref(&tree.lease.lease_ref, &[request.clone()])
                    .await,
                Err(PathLockError::InvalidRequest(_))
            ));
            assert!(matches!(
                mgr.resolve_auto_pathlock_action(&[request.clone()]).await,
                Err(PathLockError::InvalidRequest(_))
            ));
            assert!(matches!(
                FS_CTX
                    .scope(context.clone(), async {
                        mgr.resolve_auto_pathlock_action(&[request.clone()]).await
                    })
                    .await,
                Err(PathLockError::InvalidRequest(_))
            ));
            let mut handoff = mgr.to_handoff(&tree);
            handoff.covered_paths = vec![request];
            assert!(matches!(
                mgr.adopt(&handoff).await,
                Err(PathLockError::InvalidRequest(_))
            ));
        }
        assert!(matches!(
            fs.stat("/unrelated").await,
            Err(crate::core::Error::NotFound(_))
        ));
        assert_eq!(mgr.metrics_snapshot().await.active_lock_count, 1);
        mgr.release(&tree).await.unwrap();
    }

    /// A single trailing slash retains the existing canonical lease identity.
    #[tokio::test]
    async fn trailing_slash_uses_the_same_lock_path() {
        let fs = Arc::new(MemFileSystem::new());
        fs.mkdir("/data", 0o755).await.unwrap();
        let mgr = PathLockManager::new(
            fs,
            Arc::new(MemoryPathLockProvider::new()),
            PathLockConfig::default(),
        );
        let lease = mgr
            .acquire_exact("/data/file/", Duration::ZERO, None)
            .await
            .unwrap();
        assert_eq!(lease.lease.covered_paths[0].path, "/data/file");
        assert!(mgr.is_locked("/data/file/", true).await.unwrap());
        assert!(lease.lease.covers(&PathLockRequest {
            path: "/data/file/".into(),
            kind: PathLockKind::Exact,
        }));
        assert!(matches!(
            mgr.acquire_exact("/data/file", Duration::ZERO, None).await,
            Err(PathLockError::Timeout { .. })
        ));
        mgr.release(&lease).await.unwrap();
    }
}
