//! Lock-path resolver — computes `.path.ovlock` and `.exact.ovlock.*` paths.

use std::sync::Arc;

use sha1::{Digest, Sha1};

use crate::core::internal_names::{EXACT_LOCK_FILE_PREFIX, PATH_LOCK_FILE};
use crate::core::FileSystem;

use super::types::PathLockResult;

/// Exact-lock paths resolved from one real path classification.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ResolvedExactPaths {
    /// Lock path used for the actual exact token creation.
    pub acquire_lock_path: String,
    /// Conservative conflict set checked before and after token creation.
    pub conflict_paths: Vec<String>,
}

/// Tree-lock paths resolved for new writes plus legacy reads.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ResolvedTreePaths {
    /// Lock path used for the actual tree token creation.
    pub acquire_lock_path: String,
    /// Paths that may contain a tree token for the same logical path.
    pub conflict_paths: Vec<String>,
}

/// Computes lock-file paths for exact and tree locks.
pub struct LockPathResolver {
    /// Filesystem retained for lock-directory creation and compatibility checks.
    pub(crate) fs: Arc<dyn FileSystem>,
}

impl LockPathResolver {
    /// Create a new resolver backed by `fs`.
    pub fn new(fs: Arc<dyn FileSystem>) -> Self {
        Self { fs }
    }

    /// Compute exact lock paths for `path`.
    ///
    /// All paths use a parent sidecar so deleting a directory cannot delete its own token.
    pub async fn resolve_exact_lock_paths(&self, path: &str) -> PathLockResult<Vec<String>> {
        Ok(vec![self.resolve_exact_lock_path(path).await?])
    }

    /// Compute every lock-file path that can represent an exact lock for conflict checks.
    pub async fn resolve_exact_conflict_paths(&self, path: &str) -> PathLockResult<Vec<String>> {
        Ok(self.resolve_exact_paths(path).await?.conflict_paths)
    }

    /// Compute the primary lock-file path used when acquiring one exact lock.
    pub async fn resolve_exact_lock_path(&self, path: &str) -> PathLockResult<String> {
        Ok(self.resolve_exact_paths(path).await?.acquire_lock_path)
    }

    /// Resolve the sidecar acquire path and include the legacy in-directory token for conflicts.
    pub(crate) async fn resolve_exact_paths(
        &self,
        path: &str,
    ) -> PathLockResult<ResolvedExactPaths> {
        let acquire_lock_path = prefixed_exact_lock_path(path);
        let conflict_paths = vec![acquire_lock_path.clone(), path_lock_path(path)];
        Ok(ResolvedExactPaths {
            acquire_lock_path,
            conflict_paths,
        })
    }

    /// Compute every lock-file path that can represent a tree lock.
    pub async fn resolve_tree_conflict_paths(&self, path: &str) -> PathLockResult<Vec<String>> {
        Ok(self.resolve_tree_paths(path).await?.conflict_paths)
    }

    /// Compute the primary lock-file path used when acquiring one tree lock.
    pub async fn resolve_tree_lock_path(&self, path: &str) -> PathLockResult<String> {
        Ok(self.resolve_tree_paths(path).await?.acquire_lock_path)
    }

    /// Resolve the shared sidecar acquire path and include the legacy tree token for reads.
    pub(crate) async fn resolve_tree_paths(&self, path: &str) -> PathLockResult<ResolvedTreePaths> {
        let acquire_lock_path = prefixed_exact_lock_path(path);
        let conflict_paths = vec![acquire_lock_path.clone(), path_lock_path(path)];
        Ok(ResolvedTreePaths {
            acquire_lock_path,
            conflict_paths,
        })
    }

    /// Return every ancestor path from nearest parent to root.
    pub(crate) fn ancestor_paths(path: &str) -> Vec<String> {
        let mut current = path.to_string();
        let mut ancestors = Vec::new();

        loop {
            let parent = match current.rsplit_once('/') {
                Some((p, _)) if !p.is_empty() => p.to_string(),
                Some(_) if current != "/" => "/".to_string(),
                _ => break,
            };
            ancestors.push(parent.clone());
            current = parent;
        }
        ancestors
    }
}

/// Return the `.path.ovlock` path for one target.
fn path_lock_path(path: &str) -> String {
    format!("{}/{}", path.trim_end_matches('/'), PATH_LOCK_FILE)
}

/// Return the legacy-compatible prefixed exact sidecar path.
fn prefixed_exact_lock_path(path: &str) -> String {
    let (parent, name) = split_parent_name(path);
    let file_name = format!(
        "{}{}.{}",
        EXACT_LOCK_FILE_PREFIX,
        safe_name(name),
        hash_prefix(path)
    );
    if parent == "/" {
        format!("/{file_name}")
    } else {
        format!("{parent}/{file_name}")
    }
}

/// Split a path into (parent_dir, basename).
fn split_parent_name(path: &str) -> (String, &str) {
    let trimmed = path.trim_end_matches('/');
    match trimmed.rsplit_once('/') {
        Some((parent, name)) if !parent.is_empty() => (parent.to_string(), name),
        Some((_, name)) => ("/".to_string(), name),
        None => ("/".to_string(), trimmed),
    }
}

/// Sanitize a filename exactly like the legacy Python pathlock protocol.
fn safe_name(name: &str) -> String {
    let mut sanitized = String::new();
    let mut replacing = false;
    for c in name.chars() {
        if c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-') {
            sanitized.push(c);
            replacing = false;
        } else if !replacing {
            sanitized.push('_');
            replacing = true;
        }
    }
    let sanitized = sanitized.trim_matches(['.', '_']);
    let sanitized = if sanitized.is_empty() {
        "path"
    } else {
        sanitized
    };
    sanitized.chars().take(80).collect()
}

/// First 16 hex chars of SHA-1 of the full backend path.
fn hash_prefix(path: &str) -> String {
    let digest = Sha1::digest(path.as_bytes());
    digest[..8]
        .iter()
        .map(|b| format!("{:02x}", b))
        .collect::<String>()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::WriteFlag;
    use crate::plugins::memfs::MemFileSystem;

    /// Build the real in-memory filesystem used by resolver tests.
    async fn memfs() -> Arc<MemFileSystem> {
        let fs = Arc::new(MemFileSystem::new());
        fs.mkdir("/data", 0o755).await.unwrap();
        fs
    }

    #[tokio::test]
    async fn resolver_uses_parent_sidecars_for_all_paths() {
        let fs = memfs().await;
        fs.mkdir("/data/dir", 0o755).await.unwrap();
        fs.write("/data/file.txt", b"", 0, WriteFlag::Create)
            .await
            .unwrap();
        let resolver = LockPathResolver::new(fs);

        let exact_dir = resolver
            .resolve_exact_lock_paths("/data/dir")
            .await
            .unwrap();
        assert!(exact_dir[0].starts_with("/data/.exact.ovlock.dir."));
        assert_eq!(
            resolver.resolve_tree_lock_path("/data/dir").await.unwrap(),
            exact_dir[0]
        );
        assert_eq!(
            resolver
                .resolve_tree_conflict_paths("/data/dir")
                .await
                .unwrap(),
            vec![exact_dir[0].clone(), "/data/dir/.path.ovlock".to_string()]
        );
        assert_eq!(
            resolver
                .resolve_exact_conflict_paths("/data/dir")
                .await
                .unwrap(),
            vec![exact_dir[0].clone(), "/data/dir/.path.ovlock".to_string()]
        );

        let exact_file = resolver
            .resolve_exact_lock_paths("/data/file.txt")
            .await
            .unwrap();
        assert!(exact_file[0].starts_with("/data/.exact.ovlock.file.txt."));
        assert!(resolver
            .resolve_tree_lock_path("/data/file.txt")
            .await
            .unwrap()
            .starts_with("/data/.exact.ovlock.file.txt."));
        assert!(resolver
            .resolve_exact_lock_paths("/data/new.txt")
            .await
            .unwrap()[0]
            .starts_with("/data/.exact.ovlock.new.txt."));
        assert!(resolver
            .resolve_tree_lock_path("/top-level")
            .await
            .unwrap()
            .starts_with("/.exact.ovlock.top-level."));
        assert!(!resolver
            .resolve_tree_lock_path("/top-level")
            .await
            .unwrap()
            .starts_with("//"));
        assert!(resolver
            .resolve_tree_lock_path("/")
            .await
            .unwrap()
            .starts_with("/.exact.ovlock.path."));
    }

    #[test]
    fn ancestor_paths_run_from_nearest_parent_to_root() {
        assert_eq!(
            LockPathResolver::ancestor_paths("/data/a/b"),
            vec!["/data/a", "/data", "/"]
        );
        assert_eq!(LockPathResolver::ancestor_paths("/data"), vec!["/"]);
        assert!(LockPathResolver::ancestor_paths("/").is_empty());
    }

    #[test]
    fn sidecar_name_matches_legacy_python_protocol() {
        assert_eq!(safe_name("hello world"), "hello_world");
        assert_eq!(safe_name("a/b"), "a_b");
        assert_eq!(safe_name("ok.txt"), "ok.txt");
        assert_eq!(safe_name("中文名"), "path");
        assert_eq!(safe_name(""), "path");
        assert_eq!(
            prefixed_exact_lock_path("/data/中文 name..txt"),
            "/data/.exact.ovlock.name..txt.591847f8876e86f7"
        );
    }
}
