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

/// Computes lock-file paths for exact and tree locks.
pub struct LockPathResolver {
    /// Filesystem used for `stat` calls to determine is_dir.
    pub(crate) fs: Arc<dyn FileSystem>,
}

impl LockPathResolver {
    /// Create a new resolver backed by `fs`.
    pub fn new(fs: Arc<dyn FileSystem>) -> Self {
        Self { fs }
    }

    /// Compute exact lock paths for `path`.
    ///
    /// - Existing directory → `{path}/.path.ovlock`
    /// - File or non-existent path → `{parent}/.exact.ovlock.<safe_name>.<sha1-prefix>`
    pub async fn resolve_exact_lock_paths(&self, path: &str) -> PathLockResult<Vec<String>> {
        Ok(vec![self.resolve_exact_lock_path(path).await?])
    }

    /// Compute every lock-file path that can represent an exact lock for conflict checks.
    pub async fn resolve_exact_conflict_paths(
        &self,
        path: &str,
    ) -> PathLockResult<Vec<String>> {
        Ok(self.resolve_exact_paths(path).await?.conflict_paths)
    }

    /// Compute the primary lock-file path used when acquiring one exact lock.
    pub async fn resolve_exact_lock_path(&self, path: &str) -> PathLockResult<String> {
        Ok(self.resolve_exact_paths(path).await?.acquire_lock_path)
    }

    /// Resolve the exact acquire path and its conservative conflict set using one `stat`.
    pub(crate) async fn resolve_exact_paths(&self, path: &str) -> PathLockResult<ResolvedExactPaths> {
        let stat_result = self.fs.stat(path).await;
        let acquire_lock_path = match stat_result {
            Ok(info) if info.is_dir => path_lock_path(path),
            _ => prefixed_exact_lock_path(path),
        };
        let conflict_paths = if acquire_lock_path == path_lock_path(path) {
            vec![acquire_lock_path.clone(), prefixed_exact_lock_path(path)]
        } else {
            vec![acquire_lock_path.clone(), path_lock_path(path)]
        };
        Ok(ResolvedExactPaths {
            acquire_lock_path,
            conflict_paths,
        })
    }

    /// Compute the tree lock path for `path`.
    ///
    /// - Regular directory or missing path → `{path}/.path.ovlock`
    /// - Existing file path → parent directory sidecar
    pub async fn resolve_tree_lock_path(&self, path: &str) -> PathLockResult<String> {
        let stat_result = self.fs.stat(path).await;
        match stat_result {
            Ok(info) if !info.is_dir => {
                // Existing file: use parent sidecar to avoid `{file}/.path.ovlock`.
                Ok(prefixed_exact_lock_path(path))
            }
            _ => Ok(path_lock_path(path)),
        }
    }
}

/// Return the `.path.ovlock` path for one target.
fn path_lock_path(path: &str) -> String {
    format!("{}/{}", path.trim_end_matches('/'), PATH_LOCK_FILE)
}

/// Return the legacy-compatible prefixed exact sidecar path.
fn prefixed_exact_lock_path(path: &str) -> String {
    let (parent, name) = split_parent_name(path);
    format!(
        "{}/{}{}.{}",
        parent,
        EXACT_LOCK_FILE_PREFIX,
        safe_name(name),
        hash_prefix(path)
    )
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
    digest[..8].iter().map(|b| format!("{:02x}", b)).collect::<String>()
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
    async fn resolver_uses_path_lock_for_directories_and_sidecars_for_files() {
        let fs = memfs().await;
        fs.mkdir("/data/dir", 0o755).await.unwrap();
        fs.write("/data/file.txt", b"", 0, WriteFlag::Create)
            .await
            .unwrap();
        let resolver = LockPathResolver::new(fs);

        assert_eq!(
            resolver.resolve_exact_lock_paths("/data/dir").await.unwrap(),
            vec!["/data/dir/.path.ovlock".to_string()]
        );
        assert_eq!(
            resolver.resolve_tree_lock_path("/data/dir").await.unwrap(),
            "/data/dir/.path.ovlock"
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
