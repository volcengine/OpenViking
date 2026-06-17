//! VFS enumeration with pruning rules from design §4.2.
//!
//! Walks an account's VFS subtree at `/local/{account}` and returns
//! account-relative paths of files that must be included in a Git snapshot.
//!
//! Pruning rules (verbatim from design §4.2):
//!
//! | Skip if account-relative path matches | Reason |
//! |---|---|
//! | First path segment ∈ {`_system`, `tasks`, `temp`, `queue`, `upload`} | Internal scopes + runtime locks |
//! | Any segment equals `.path.ovlock` OR starts with `.path.ovlock` | Runtime lock |
//! | File extension is `.faiss` or `.index`, OR path contains an `embedding_cache/` segment | Vector index — derived data |
//!
//! L0/L1 derived files (`.abstract.md`, `.overview.md`, `.relations.json`)
//! are intentionally KEPT — design §4.2 says they belong in snapshots.

use std::sync::Arc;

use crate::core::filesystem::FileSystem;
use crate::git::error::GitError;

/// First-segment internal scope names that are pruned.
///
/// Combines `INTERNAL_SCOPES` (system/tasks/temp/queue/upload) and
/// `VikingFS._INTERNAL_NAMES` from the design.
const INTERNAL_FIRST_SEGMENTS: &[&str] = &["_system", "tasks", "temp", "queue", "upload"];

/// Returns true if this account-relative path must be excluded from commits.
///
/// `rel` is the path relative to the account root (no leading "/", no
/// "/local/{account}/" prefix). Examples of valid input:
///   - "resources/a.md"
///   - "_system/lock"
///   - ".path.ovlock"
pub fn prune_path(rel: &str) -> bool {
    if rel.is_empty() {
        return true;
    }

    let segments: Vec<&str> = rel.split('/').filter(|s| !s.is_empty()).collect();
    if segments.is_empty() {
        return true;
    }

    // Rule 1: first path segment in INTERNAL_FIRST_SEGMENTS
    if INTERNAL_FIRST_SEGMENTS.contains(&segments[0]) {
        return true;
    }

    // Rule 2: any segment equals or starts with ".path.ovlock"
    for seg in &segments {
        if seg.starts_with(".path.ovlock") {
            return true;
        }
    }

    // Rule 3a: any intermediate segment named exactly "embedding_cache"
    // (i.e. path contains an `embedding_cache/` segment).
    for seg in &segments[..segments.len().saturating_sub(1)] {
        if *seg == "embedding_cache" {
            return true;
        }
    }

    // Rule 3b: extension is .faiss or .index
    if let Some(last) = segments.last() {
        if last.ends_with(".faiss") || last.ends_with(".index") {
            return true;
        }
    }

    false
}

/// Enumerate every versionable file in an account's VFS subtree.
///
/// Returns paths relative to the account root (no leading "/", no
/// "/local/{account}/" prefix). Directories are filtered out; only file
/// entries that survive pruning are returned.
pub async fn collect_all(
    vfs: &Arc<dyn FileSystem>,
    account: &str,
) -> Result<Vec<String>, GitError> {
    let root = format!("/local/{}", account);
    let prefix = format!("{}/", root);

    let entries = vfs.tree_directory(&root, true, None, None).await?;

    let mut survivors = Vec::new();
    for entry in entries {
        if entry.info.is_dir {
            continue;
        }

        // Strip "/local/{account}/" prefix. If path doesn't start with the
        // prefix, skip it (defensive — shouldn't happen for a well-formed VFS).
        let rel = match entry.path.strip_prefix(&prefix) {
            Some(r) => r,
            None => continue,
        };

        if prune_path(rel) {
            continue;
        }

        survivors.push(rel.to_string());
    }

    Ok(survivors)
}

#[cfg(test)]
mod tests {
    use super::*;
    use async_trait::async_trait;
    use std::collections::HashMap;
    use std::sync::Arc;

    use crate::core::errors::Result;
    use crate::core::filesystem::FileSystem;
    use crate::core::types::{FileInfo, TreeEntry, WriteFlag};

    /// In-test mock that directly overrides `tree_directory` to return a
    /// precomputed list of (path, is_dir). All other trait methods are
    /// unimplemented because `collect_all` only calls `tree_directory`.
    struct MockFS {
        /// Map: root path -> list of (absolute path, is_dir)
        entries_by_root: HashMap<String, Vec<(String, bool)>>,
    }

    impl MockFS {
        fn new(root: &str, entries: Vec<(&str, bool)>) -> Self {
            let mut map = HashMap::new();
            map.insert(
                root.to_string(),
                entries
                    .into_iter()
                    .map(|(p, d)| (p.to_string(), d))
                    .collect(),
            );
            Self {
                entries_by_root: map,
            }
        }
    }

    #[async_trait]
    impl FileSystem for MockFS {
        async fn create(&self, _path: &str) -> Result<()> {
            unimplemented!()
        }
        async fn mkdir(&self, _path: &str, _mode: u32) -> Result<()> {
            unimplemented!()
        }
        async fn remove(&self, _path: &str) -> Result<()> {
            unimplemented!()
        }
        async fn remove_all(&self, _path: &str) -> Result<()> {
            unimplemented!()
        }
        async fn read(&self, _path: &str, _offset: u64, _size: u64) -> Result<Vec<u8>> {
            unimplemented!()
        }
        async fn write(
            &self,
            _path: &str,
            _data: &[u8],
            _offset: u64,
            _flags: WriteFlag,
        ) -> Result<u64> {
            unimplemented!()
        }
        async fn read_dir(&self, _path: &str) -> Result<Vec<FileInfo>> {
            unimplemented!()
        }
        async fn stat(&self, _path: &str) -> Result<FileInfo> {
            unimplemented!()
        }
        async fn rename(&self, _old_path: &str, _new_path: &str) -> Result<()> {
            unimplemented!()
        }
        async fn chmod(&self, _path: &str, _mode: u32) -> Result<()> {
            unimplemented!()
        }

        /// Override tree_directory to return precomputed entries, bypassing
        /// the default read_dir/stat-based recursion.
        async fn tree_directory(
            &self,
            path: &str,
            _show_hidden: bool,
            _node_limit: Option<usize>,
            _level_limit: Option<usize>,
        ) -> Result<Vec<TreeEntry>> {
            let raw = self
                .entries_by_root
                .get(path)
                .cloned()
                .unwrap_or_default();

            let prefix = if path == "/" {
                "/".to_string()
            } else {
                format!("{}/", path)
            };

            let mut out = Vec::new();
            for (full_path, is_dir) in raw {
                let rel_path = full_path
                    .strip_prefix(&prefix)
                    .unwrap_or(&full_path)
                    .to_string();
                let name = full_path
                    .rsplit('/')
                    .next()
                    .unwrap_or(&full_path)
                    .to_string();
                let info = if is_dir {
                    FileInfo::new_dir(name, 0o755)
                } else {
                    FileInfo::new_file(name, 0, 0o644)
                };
                out.push(TreeEntry {
                    path: full_path,
                    rel_path,
                    info,
                    extra: HashMap::new(),
                });
            }
            Ok(out)
        }
    }

    #[tokio::test]
    async fn test_collect_all_prunes_internal_scopes_and_vector_indexes() {
        let mock = MockFS::new(
            "/local/acct",
            vec![
                ("/local/acct/resources/a.md", false),
                ("/local/acct/agent/b.py", false),
                ("/local/acct/_system/lock", false),
                ("/local/acct/tasks/job.json", false),
                ("/local/acct/temp/upload.bin", false),
                ("/local/acct/queue/x", false),
                ("/local/acct/upload/y", false),
                ("/local/acct/resources/x.faiss", false),
                ("/local/acct/resources/x.index", false),
                ("/local/acct/resources/embedding_cache/v.bin", false),
            ],
        );
        let fs: Arc<dyn FileSystem> = Arc::new(mock);

        let mut got = collect_all(&fs, "acct").await.unwrap();
        got.sort();

        let mut expected = vec!["agent/b.py".to_string(), "resources/a.md".to_string()];
        expected.sort();

        assert_eq!(got, expected);
    }

    #[tokio::test]
    async fn test_collect_all_keeps_derived_l0_l1_files() {
        let mock = MockFS::new(
            "/local/acct",
            vec![
                ("/local/acct/resources/x.md", false),
                ("/local/acct/resources/x.md.abstract.md", false),
                ("/local/acct/resources/x.md.overview.md", false),
                ("/local/acct/resources/x.md.relations.json", false),
            ],
        );
        let fs: Arc<dyn FileSystem> = Arc::new(mock);

        let mut got = collect_all(&fs, "acct").await.unwrap();
        got.sort();

        let mut expected = vec![
            "resources/x.md".to_string(),
            "resources/x.md.abstract.md".to_string(),
            "resources/x.md.overview.md".to_string(),
            "resources/x.md.relations.json".to_string(),
        ];
        expected.sort();

        assert_eq!(got, expected);
    }

    #[tokio::test]
    async fn test_collect_all_returns_account_relative_paths() {
        let mock = MockFS::new(
            "/local/acct",
            vec![("/local/acct/resources/a.md", false)],
        );
        let fs: Arc<dyn FileSystem> = Arc::new(mock);

        let got = collect_all(&fs, "acct").await.unwrap();
        assert_eq!(got, vec!["resources/a.md".to_string()]);
        // Defensive: ensure no absolute leakage.
        for p in &got {
            assert!(!p.starts_with('/'), "path should not be absolute: {}", p);
            assert!(
                !p.contains("/local/acct"),
                "path should not contain account prefix: {}",
                p
            );
        }
    }

    #[test]
    fn test_prune_path_table() {
        // Pruned
        assert!(prune_path("_system/lock"));
        assert!(prune_path("tasks/job.json"));
        assert!(prune_path("temp/x"));
        assert!(prune_path("queue/x"));
        assert!(prune_path("upload/x"));
        assert!(prune_path("resources/.path.ovlock"));
        assert!(prune_path(".path.ovlock"));
        assert!(prune_path("resources/x.faiss"));
        assert!(prune_path("resources/x.index"));
        assert!(prune_path("resources/embedding_cache/v.bin"));
        assert!(prune_path("agent/embedding_cache/something"));

        // Survivors
        assert!(!prune_path("resources/a.md"));
        assert!(!prune_path("agent/skills/b.py"));
        assert!(!prune_path("resources/x.md.abstract.md"));
        assert!(!prune_path("resources/x.md.overview.md"));
        assert!(!prune_path("resources/x.md.relations.json"));
        // "_systemfoo" is NOT "_system", must survive.
        assert!(!prune_path("_systemfoo/x"));
        // Per "any segment starting with .path.ovlock", ".path.ovlocking"
        // is pruned even though it isn't exactly ".path.ovlock".
        assert!(prune_path("resources/.path.ovlocking"));
    }
}
