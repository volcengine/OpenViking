//! FileSystem trait definition
//!
//! This module defines the core FileSystem trait that all filesystem implementations
//! must implement. This provides a unified interface for file operations across
//! different storage backends.

use async_trait::async_trait;
use regex::Regex;

use super::errors::{Error, Result};
use super::types::{FileInfo, GrepResult, TreeEntry, WriteFlag};

/// Normalize a path for prefix comparisons.
///
/// - Keeps "/" as-is.
/// - Strips trailing slashes for non-root paths (so "/a" and "/a/" behave the same).
pub(crate) fn normalize_prefix_path(path: &str) -> String {
    if path == "/" {
        "/".to_string()
    } else {
        path.trim_end_matches('/').to_string()
    }
}

/// Check whether `path` is under `exclude_path` (including itself).
fn is_excluded_path(path: &str, exclude_path: &str) -> bool {
    if exclude_path == "/" {
        return true;
    }

    path == exclude_path
        || path
            .strip_prefix(exclude_path)
            .is_some_and(|suffix| suffix.starts_with('/'))
}

/// Convert an absolute/plugin path to a query-root-relative grep match path.
///
/// Contract:
/// - "." means query root itself.
/// - otherwise returns a relative path without leading "/".
pub(crate) fn relative_match_file(query_root: &str, path: &str) -> String {
    let base = normalize_prefix_path(query_root);
    if path == base {
        return ".".to_string();
    }

    if base == "/" {
        return path.trim_start_matches('/').to_string();
    }

    match path.strip_prefix(&base) {
        Some(rest) => {
            let rel = rest.trim_start_matches('/');
            if rel.is_empty() {
                ".".to_string()
            } else {
                rel.to_string()
            }
        }
        None => path.trim_start_matches('/').to_string(),
    }
}

/// Compute depth from a query-root-relative path.
///
/// - "." and "" => 0
/// - "a/b" => 2
pub(crate) fn relative_depth(rel: &str) -> usize {
    if rel.is_empty() || rel == "." {
        0
    } else {
        rel.split('/').filter(|p| !p.is_empty()).count()
    }
}

/// Compile a grep pattern into a `Regex`, applying the case-insensitive `(?i)` prefix.
///
/// Shared by the trait's default `grep` and by `EncryptionWrappedFS::grep`, so the regex setup
/// lives in exactly one place (no copy-paste drift between the two call sites).
pub(crate) fn compile_grep_regex(pattern: &str, case_insensitive: bool) -> Result<Regex> {
    let regex_pattern = if case_insensitive {
        format!("(?i){}", pattern)
    } else {
        pattern.to_string()
    };
    Regex::new(&regex_pattern)
        .map_err(|e| Error::invalid_operation(format!("Invalid regex pattern: {}", e)))
}

/// Core filesystem abstraction trait
///
/// All filesystem plugins must implement this trait to provide file operations.
/// All methods are async to support I/O-bound operations efficiently.
#[async_trait]
pub trait FileSystem: Send + Sync {
    /// Create an empty file at the specified path
    ///
    /// # Arguments
    /// * `path` - The path where the file should be created
    ///
    /// # Errors
    /// * `Error::AlreadyExists` - If a file already exists at the path
    /// * `Error::NotFound` - If the parent directory doesn't exist
    /// * `Error::PermissionDenied` - If permission is denied
    async fn create(&self, path: &str) -> Result<()>;

    /// Create a directory at the specified path
    ///
    /// # Arguments
    /// * `path` - The path where the directory should be created
    /// * `mode` - Unix-style permissions (e.g., 0o755)
    ///
    /// # Errors
    /// * `Error::AlreadyExists` - If a directory already exists at the path
    /// * `Error::NotFound` - If the parent directory doesn't exist
    async fn mkdir(&self, path: &str, mode: u32) -> Result<()>;

    /// Remove a file at the specified path
    ///
    /// # Arguments
    /// * `path` - The path of the file to remove
    ///
    /// # Errors
    /// * `Error::NotFound` - If the file doesn't exist
    /// * `Error::IsADirectory` - If the path points to a directory
    async fn remove(&self, path: &str) -> Result<()>;

    /// Recursively remove a file or directory
    ///
    /// # Arguments
    /// * `path` - The path to remove
    ///
    /// # Errors
    /// * `Error::NotFound` - If the path doesn't exist
    async fn remove_all(&self, path: &str) -> Result<()>;

    /// Read file contents
    ///
    /// # Arguments
    /// * `path` - The path of the file to read
    /// * `offset` - Byte offset to start reading from
    /// * `size` - Number of bytes to read (0 means read all)
    ///
    /// # Returns
    /// The file contents as a byte vector
    ///
    /// # Errors
    /// * `Error::NotFound` - If the file doesn't exist
    /// * `Error::IsADirectory` - If the path points to a directory
    async fn read(&self, path: &str, offset: u64, size: u64) -> Result<Vec<u8>>;

    /// Write data to a file
    ///
    /// # Arguments
    /// * `path` - The path of the file to write
    /// * `data` - The data to write
    /// * `offset` - Byte offset to start writing at
    /// * `flags` - Write flags (create, append, truncate, etc.)
    ///
    /// # Returns
    /// The number of bytes written
    ///
    /// # Errors
    /// * `Error::NotFound` - If the file doesn't exist and Create flag not set
    /// * `Error::IsADirectory` - If the path points to a directory
    async fn write(&self, path: &str, data: &[u8], offset: u64, flags: WriteFlag) -> Result<u64>;

    /// List directory contents
    ///
    /// # Arguments
    /// * `path` - The path of the directory to list
    ///
    /// # Returns
    /// A vector of FileInfo for each entry in the directory
    ///
    /// # Errors
    /// * `Error::NotFound` - If the directory doesn't exist
    /// * `Error::NotADirectory` - If the path is not a directory
    async fn read_dir(&self, path: &str) -> Result<Vec<FileInfo>>;

    /// Get file or directory metadata
    ///
    /// # Arguments
    /// * `path` - The path to get metadata for
    ///
    /// # Returns
    /// FileInfo containing metadata
    ///
    /// # Errors
    /// * `Error::NotFound` - If the path doesn't exist
    async fn stat(&self, path: &str) -> Result<FileInfo>;

    /// Rename/move a file or directory
    ///
    /// # Arguments
    /// * `old_path` - The current path
    /// * `new_path` - The new path
    ///
    /// # Errors
    /// * `Error::NotFound` - If old_path doesn't exist
    /// * `Error::AlreadyExists` - If new_path already exists
    async fn rename(&self, old_path: &str, new_path: &str) -> Result<()>;

    /// Change file permissions
    ///
    /// # Arguments
    /// * `path` - The path of the file
    /// * `mode` - New Unix-style permissions
    ///
    /// # Errors
    /// * `Error::NotFound` - If the path doesn't exist
    async fn chmod(&self, path: &str, mode: u32) -> Result<()>;

    /// Truncate a file to a specified size
    ///
    /// # Arguments
    /// * `path` - The path of the file
    /// * `size` - The new size in bytes
    ///
    /// # Errors
    /// * `Error::NotFound` - If the file doesn't exist
    /// * `Error::IsADirectory` - If the path points to a directory
    async fn truncate(&self, path: &str, size: u64) -> Result<()> {
        // Default implementation: read, resize, write back
        let mut data = self.read(path, 0, 0).await?;
        data.resize(size as usize, 0);
        self.write(path, &data, 0, WriteFlag::Truncate).await?;
        Ok(())
    }

    /// Check if a path exists
    ///
    /// # Arguments
    /// * `path` - The path to check
    ///
    /// # Returns
    /// true if the path exists, false otherwise
    async fn exists(&self, path: &str) -> bool {
        self.stat(path).await.is_ok()
    }

    /// Search for a pattern in files using regular expressions
    ///
    /// This is the default implementation that recursively searches files
    /// and matches lines against the provided pattern. Plugins can override
    /// this method to provide more efficient implementations.
    ///
    /// # Arguments
    /// * `path` - The path to search (file or directory)
    /// * `pattern` - The regular expression pattern to search for
    /// * `recursive` - Whether to search recursively in subdirectories
    /// * `case_insensitive` - Whether to perform case-insensitive matching
    /// * `node_limit` - Maximum number of matches to return (None means no limit)
    /// * `exclude_path` - Optional path prefix to exclude from search
    /// * `level_limit` - Optional maximum depth relative to query root
    ///
    /// # Returns
    /// A GrepResult containing all matches found
    ///
    /// # Errors
    /// * `Error::NotFound` - If the path doesn't exist
    /// * `Error::Regex` - If the pattern is invalid
    async fn grep(
        &self,
        path: &str,
        pattern: &str,
        recursive: bool,
        case_insensitive: bool,
        node_limit: Option<usize>,
        exclude_path: Option<&str>,
        level_limit: Option<usize>,
    ) -> Result<GrepResult> {
        let re = compile_grep_regex(pattern, case_insensitive)?;

        let mut result = GrepResult::new();
        let normalized_path = normalize_prefix_path(path);
        let normalized_exclude = exclude_path.map(normalize_prefix_path);

        self.grep_internal(
            normalized_path.as_str(),
            normalized_path.as_str(),
            &re,
            recursive,
            node_limit,
            normalized_exclude.as_deref(),
            level_limit,
            &mut result,
        )
        .await?;

        Ok(result)
    }

    /// Internal recursive grep helper
    async fn grep_internal(
        &self,
        base_path: &str,
        current_path: &str,
        re: &Regex,
        recursive: bool,
        node_limit: Option<usize>,
        exclude_path: Option<&str>,
        level_limit: Option<usize>,
        result: &mut GrepResult,
    ) -> Result<()> {
        if node_limit.is_some_and(|limit| result.count >= limit) {
            return Ok(());
        }

        if let Some(exclude) = exclude_path {
            if is_excluded_path(current_path, exclude) {
                return Ok(());
            }
        }

        let stat = self.stat(current_path).await?;

        if stat.is_dir {
            if !recursive && current_path != base_path {
                return Ok(());
            }

            if let Some(limit) = level_limit {
                let rel = relative_match_file(base_path, current_path);
                let depth = relative_depth(&rel);
                // Directories at depth >= limit cannot contain files within the limit.
                if depth >= limit {
                    return Ok(());
                }
            }

            let entries = self.read_dir(current_path).await?;

            for entry in entries {
                if node_limit.is_some_and(|limit| result.count >= limit) {
                    break;
                }

                let entry_path = if current_path == "/" {
                    format!("/{}", entry.name)
                } else {
                    format!("{}/{}", current_path, entry.name)
                };

                self.grep_internal(
                    base_path,
                    &entry_path,
                    re,
                    recursive,
                    node_limit,
                    exclude_path,
                    level_limit,
                    result,
                )
                .await?;
            }
        } else {
            if let Some(limit) = level_limit {
                let rel = relative_match_file(base_path, current_path);
                let depth = relative_depth(&rel);
                if depth > limit {
                    return Ok(());
                }
            }

            self.grep_file(base_path, current_path, re, node_limit, result)
                .await?;
        }

        Ok(())
    }

    /// Grep a single file
    async fn grep_file(
        &self,
        base_path: &str,
        path: &str,
        re: &Regex,
        node_limit: Option<usize>,
        result: &mut GrepResult,
    ) -> Result<()> {
        if node_limit.is_some_and(|limit| result.count >= limit) {
            return Ok(());
        }

        let content = self.read(path, 0, 0).await?;

        let content_str = String::from_utf8_lossy(&content);

        let rel_file = relative_match_file(base_path, path);

        for (line_num, line) in content_str.lines().enumerate() {
            if node_limit.is_some_and(|limit| result.count >= limit) {
                break;
            }

            if re.is_match(line) {
                result.add_match(rel_file.clone(), (line_num + 1) as u64, line.to_string());
            }
        }

        Ok(())
    }

    /// Recursively traverse a directory tree, returning a flat list of TreeEntry nodes.
    ///
    /// This is the public entry point. The default implementation uses recursive
    /// `read_dir()` calls. Plugins (e.g. s3fs) may override this for better performance.
    ///
    /// # Arguments
    /// * `path` - The root path of the traversal
    /// * `show_hidden` - Whether to include hidden files (names starting with `.`)
    /// * `node_limit` - Maximum number of nodes to return (None means no limit)
    /// * `level_limit` - Maximum depth relative to query root (None means no limit)
    ///
    /// # Returns
    /// A flat `Vec<TreeEntry>` in DFS order (directories before their children).
    async fn tree_directory(
        &self,
        path: &str,
        show_hidden: bool,
        node_limit: Option<usize>,
        level_limit: Option<usize>,
    ) -> Result<Vec<TreeEntry>> {
        let normalized_path = normalize_prefix_path(path);
        let mut result = Vec::new();

        self.tree_directory_internal(
            normalized_path.as_str(),
            normalized_path.as_str(),
            show_hidden,
            node_limit,
            level_limit,
            &mut result,
        )
        .await?;

        Ok(result)
    }

    /// Internal recursive helper for tree_directory.
    ///
    /// # Arguments
    /// * `base_path` - The original query root (used for rel_path computation)
    /// * `current_path` - The current directory being traversed
    /// * `show_hidden` - Whether to include hidden files
    /// * `node_limit` - Maximum number of nodes to return
    /// * `level_limit` - Maximum depth relative to base_path
    /// * `result` - Accumulator for the flat result list
    async fn tree_directory_internal(
        &self,
        base_path: &str,
        current_path: &str,
        show_hidden: bool,
        node_limit: Option<usize>,
        level_limit: Option<usize>,
        result: &mut Vec<TreeEntry>,
    ) -> Result<()> {
        if node_limit.is_some_and(|limit| result.len() >= limit) {
            return Ok(());
        }

        if let Some(limit) = level_limit {
            let current_rel = relative_match_file(base_path, current_path);
            let current_depth = relative_depth(&current_rel);
            // `current_depth >= limit` is a pre-expansion guard: stop before
            // reading the directory.  Equivalent to S3FS's post-generation
            // `depth > limit` filter — both ensure entries at depth > limit
            // are excluded.
            if current_depth >= limit {
                return Ok(());
            }
        }

        let entries = self.read_dir(current_path).await?;

        for entry in entries {
            if node_limit.is_some_and(|limit| result.len() >= limit) {
                break;
            }

            let is_hidden_file = !entry.is_dir && entry.name.starts_with('.');
            if is_hidden_file && !show_hidden {
                continue;
            }

            let entry_path = if current_path == "/" {
                format!("/{}", entry.name)
            } else {
                format!("{}/{}", current_path, entry.name)
            };

            let rel_path = relative_match_file(base_path, &entry_path);

            result.push(TreeEntry {
                path: entry_path.clone(),
                rel_path,
                info: entry.clone(),
                extra: std::collections::HashMap::new(),
            });

            if entry.is_dir {
                self.tree_directory_internal(
                    base_path,
                    &entry_path,
                    show_hidden,
                    node_limit,
                    level_limit,
                    result,
                )
                .await?;
            }
        }

        Ok(())
    }

    /// Ensure all parent directories exist for the given path
    ///
    /// # Arguments
    /// * `path` - The path for which to ensure parent directories exist
    /// * `mode` - Unix-style permissions for created directories (default: 0o755)
    ///
    /// # Returns
    /// Ok(()) if successful
    async fn ensure_parent_dirs(&self, path: &str, mode: u32) -> Result<()> {
        // Skip if path is root or has no parent
        if path == "/" || !path.contains('/') {
            return Ok(());
        }

        // Get parent path by removing the last component
        let parts: Vec<&str> = path.trim_start_matches('/').split('/').collect();
        if parts.len() <= 1 {
            return Ok(());
        }

        let parent_parts = &parts[..parts.len() - 1];
        let mut current = String::new();

        for part in parent_parts {
            current.push('/');
            current.push_str(part);

            // Try to create directory, ignore if already exists
            match self.mkdir(&current, mode).await {
                Ok(_) => {}
                Err(Error::AlreadyExists(_)) => {}
                Err(e) => return Err(e),
            }
        }

        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    // Mock filesystem for testing
    struct MockFS;

    #[async_trait]
    impl FileSystem for MockFS {
        async fn create(&self, _path: &str) -> Result<()> {
            Ok(())
        }

        async fn mkdir(&self, _path: &str, _mode: u32) -> Result<()> {
            Ok(())
        }

        async fn remove(&self, _path: &str) -> Result<()> {
            Ok(())
        }

        async fn remove_all(&self, _path: &str) -> Result<()> {
            Ok(())
        }

        async fn read(&self, _path: &str, _offset: u64, _size: u64) -> Result<Vec<u8>> {
            Ok(vec![])
        }

        async fn write(
            &self,
            _path: &str,
            _data: &[u8],
            _offset: u64,
            _flags: WriteFlag,
        ) -> Result<u64> {
            Ok(_data.len() as u64)
        }

        async fn read_dir(&self, _path: &str) -> Result<Vec<FileInfo>> {
            Ok(vec![])
        }

        async fn stat(&self, _path: &str) -> Result<FileInfo> {
            Ok(FileInfo::new_file("test".to_string(), 0, 0o644))
        }

        async fn rename(&self, _old_path: &str, _new_path: &str) -> Result<()> {
            Ok(())
        }

        async fn chmod(&self, _path: &str, _mode: u32) -> Result<()> {
            Ok(())
        }
    }

    #[tokio::test]
    async fn test_filesystem_trait() {
        let fs = MockFS;
        assert!(fs.exists("/test").await);
    }

    #[derive(Default)]
    struct TreeFS {
        /// Map: directory path -> entries (name, is_dir)
        dirs: HashMap<String, Vec<(String, bool)>>,
        /// Map: file path -> utf-8 content
        files: HashMap<String, String>,
    }

    impl TreeFS {
        /// Add/update a file and its content.
        fn with_file(mut self, path: &str, content: &str) -> Self {
            self.files.insert(path.to_string(), content.to_string());
            self
        }

        /// Define directory entries for a directory.
        fn with_dir_entries(mut self, dir: &str, entries: Vec<(&str, bool)>) -> Self {
            self.dirs.insert(
                dir.to_string(),
                entries
                    .into_iter()
                    .map(|(n, is_dir)| (n.to_string(), is_dir))
                    .collect(),
            );
            self
        }

        /// Helper to build a FileInfo in tests.
        fn file_info(name: String, is_dir: bool) -> FileInfo {
            if is_dir {
                FileInfo::new_dir(name, 0o755)
            } else {
                FileInfo::new_file(name, 0, 0o644)
            }
        }
    }

    #[async_trait]
    impl FileSystem for TreeFS {
        async fn create(&self, _path: &str) -> Result<()> {
            Ok(())
        }
        async fn mkdir(&self, _path: &str, _mode: u32) -> Result<()> {
            Ok(())
        }
        async fn remove(&self, _path: &str) -> Result<()> {
            Ok(())
        }
        async fn remove_all(&self, _path: &str) -> Result<()> {
            Ok(())
        }

        async fn read(&self, path: &str, _offset: u64, _size: u64) -> Result<Vec<u8>> {
            let s = self.files.get(path).cloned().unwrap_or_default();
            Ok(s.into_bytes())
        }

        async fn write(
            &self,
            _path: &str,
            data: &[u8],
            _offset: u64,
            _flags: WriteFlag,
        ) -> Result<u64> {
            Ok(data.len() as u64)
        }

        async fn read_dir(&self, path: &str) -> Result<Vec<FileInfo>> {
            let entries = self.dirs.get(path).cloned().unwrap_or_default();
            Ok(entries
                .into_iter()
                .map(|(name, is_dir)| TreeFS::file_info(name, is_dir))
                .collect())
        }

        async fn stat(&self, path: &str) -> Result<FileInfo> {
            if self.dirs.contains_key(path) {
                return Ok(TreeFS::file_info(path.to_string(), true));
            }
            if self.files.contains_key(path) {
                return Ok(TreeFS::file_info(path.to_string(), false));
            }
            Ok(TreeFS::file_info(path.to_string(), false))
        }

        async fn rename(&self, _old_path: &str, _new_path: &str) -> Result<()> {
            Ok(())
        }
        async fn chmod(&self, _path: &str, _mode: u32) -> Result<()> {
            Ok(())
        }
    }

    #[tokio::test]
    async fn test_default_grep_match_file_is_query_root_relative() {
        let fs = TreeFS::default()
            .with_dir_entries("/root", vec![("a.txt", false), ("sub", true)])
            .with_dir_entries("/root/sub", vec![("b.txt", false)])
            .with_file("/root/a.txt", "hello\n")
            .with_file("/root/sub/b.txt", "hello\n");

        let out = fs
            .grep("/root", "hello", true, false, None, None, None)
            .await
            .unwrap();

        let files: Vec<String> = out.matches.into_iter().map(|m| m.file).collect();
        assert!(files.contains(&"a.txt".to_string()));
        assert!(files.contains(&"sub/b.txt".to_string()));
    }

    #[tokio::test]
    async fn test_default_grep_exclude_path_applies_before_node_limit() {
        let fs = TreeFS::default()
            .with_dir_entries("/root", vec![("excluded", true), ("ok", true)])
            .with_dir_entries("/root/excluded", vec![("x.txt", false)])
            .with_dir_entries("/root/ok", vec![("y.txt", false)])
            .with_file("/root/excluded/x.txt", "hit\n")
            .with_file("/root/ok/y.txt", "hit\n");

        let out = fs
            .grep(
                "/root",
                "hit",
                true,
                false,
                Some(1),
                Some("/root/excluded"),
                None,
            )
            .await
            .unwrap();

        assert_eq!(out.count, 1);
        assert_eq!(out.matches[0].file, "ok/y.txt");
    }

    #[tokio::test]
    async fn test_default_grep_level_limit_applies_before_node_limit() {
        let fs = TreeFS::default()
            .with_dir_entries("/root", vec![("a.txt", false), ("deep", true)])
            .with_dir_entries("/root/deep", vec![("b.txt", false)])
            .with_file("/root/a.txt", "hit\n")
            .with_file("/root/deep/b.txt", "hit\n");

        let out = fs
            .grep("/root", "hit", true, false, Some(1), None, Some(1))
            .await
            .unwrap();

        assert_eq!(out.count, 1);
        assert_eq!(out.matches[0].file, "a.txt");
    }

    // ── tree_directory default implementation tests ──

    fn tree_names(entries: &[TreeEntry]) -> Vec<String> {
        entries.iter().map(|e| e.info.name.clone()).collect()
    }

    fn tree_paths(entries: &[TreeEntry]) -> Vec<String> {
        entries.iter().map(|e| e.path.clone()).collect()
    }

    fn tree_rel_paths(entries: &[TreeEntry]) -> Vec<String> {
        entries.iter().map(|e| e.rel_path.clone()).collect()
    }

    #[tokio::test]
    async fn test_tree_empty_dir() {
        let fs = TreeFS::default().with_dir_entries("/root", vec![]);

        let entries = fs.tree_directory("/root", false, None, None).await.unwrap();

        assert!(entries.is_empty(), "empty dir returns empty vec");
    }

    #[tokio::test]
    async fn test_tree_single_layer_files() {
        let fs = TreeFS::default().with_dir_entries(
            "/root",
            vec![("a.txt", false), ("b.txt", false), ("c.txt", false)],
        );

        let entries = fs.tree_directory("/root", false, None, None).await.unwrap();

        assert_eq!(entries.len(), 3);
        assert_eq!(tree_names(&entries), vec!["a.txt", "b.txt", "c.txt"]);
        assert_eq!(tree_rel_paths(&entries), vec!["a.txt", "b.txt", "c.txt"]);
    }

    #[tokio::test]
    async fn test_tree_nested_dfs_order() {
        let fs = TreeFS::default()
            .with_dir_entries("/root", vec![("a.txt", false), ("sub", true)])
            .with_dir_entries("/root/sub", vec![("b.txt", false)]);

        let entries = fs.tree_directory("/root", false, None, None).await.unwrap();

        assert_eq!(entries.len(), 3);
        assert_eq!(tree_names(&entries), vec!["a.txt", "sub", "b.txt"]);
        assert_eq!(tree_rel_paths(&entries), vec!["a.txt", "sub", "sub/b.txt"]);
        assert_eq!(
            tree_paths(&entries),
            vec!["/root/a.txt", "/root/sub", "/root/sub/b.txt"]
        );
        assert_eq!(entries[0].info.mode, 0o644);
        assert!(!entries[0].info.is_dir);
        assert_eq!(entries[1].info.mode, 0o755);
        assert!(entries[1].info.is_dir);
        assert!(entries.iter().all(|entry| entry.extra.is_empty()));
    }

    #[tokio::test]
    async fn test_tree_node_limit() {
        let fs = TreeFS::default().with_dir_entries(
            "/root",
            vec![
                ("a.txt", false),
                ("b.txt", false),
                ("c.txt", false),
                ("d.txt", false),
                ("e.txt", false),
            ],
        );

        let entries = fs
            .tree_directory("/root", false, Some(3), None)
            .await
            .unwrap();

        assert_eq!(entries.len(), 3);
        assert_eq!(tree_names(&entries), vec!["a.txt", "b.txt", "c.txt"]);
    }

    #[tokio::test]
    async fn test_tree_node_limit_zero() {
        let fs = TreeFS::default().with_dir_entries("/root", vec![("a.txt", false)]);

        let entries = fs
            .tree_directory("/root", false, Some(0), None)
            .await
            .unwrap();

        assert!(entries.is_empty(), "node_limit=0 returns empty");
    }

    #[tokio::test]
    async fn test_tree_level_limit_zero() {
        let fs = TreeFS::default()
            .with_dir_entries("/root", vec![("a.txt", false), ("sub", true)])
            .with_dir_entries("/root/sub", vec![("b.txt", false)]);

        let entries = fs
            .tree_directory("/root", false, None, Some(0))
            .await
            .unwrap();

        assert!(
            entries.is_empty(),
            "level_limit=0 returns empty (root not expanded)"
        );
    }

    #[tokio::test]
    async fn test_tree_level_limit_one() {
        let fs = TreeFS::default()
            .with_dir_entries("/root", vec![("a.txt", false), ("sub", true)])
            .with_dir_entries("/root/sub", vec![("b.txt", false)]);

        let entries = fs
            .tree_directory("/root", false, None, Some(1))
            .await
            .unwrap();

        assert_eq!(entries.len(), 2);
        assert_eq!(tree_names(&entries), vec!["a.txt", "sub"]);
    }

    #[tokio::test]
    async fn test_tree_level_limit_none() {
        let fs = TreeFS::default()
            .with_dir_entries("/root", vec![("a.txt", false), ("sub", true)])
            .with_dir_entries("/root/sub", vec![("b.txt", false), ("deep", true)])
            .with_dir_entries("/root/sub/deep", vec![("c.txt", false)]);

        let entries = fs.tree_directory("/root", false, None, None).await.unwrap();

        assert_eq!(entries.len(), 5);
        assert!(tree_paths(&entries).contains(&"/root/sub/deep/c.txt".to_string()));
    }

    #[tokio::test]
    async fn test_tree_show_hidden_false() {
        let fs = TreeFS::default().with_dir_entries(
            "/root",
            vec![("a.txt", false), (".hidden", false), ("visible", true)],
        );

        let entries = fs.tree_directory("/root", false, None, None).await.unwrap();

        let names = tree_names(&entries);
        assert!(names.contains(&"a.txt".to_string()));
        assert!(names.contains(&"visible".to_string()));
        assert!(!names.contains(&".hidden".to_string()));
    }

    #[tokio::test]
    async fn test_tree_show_hidden_false_dir_preserved() {
        let fs = TreeFS::default()
            .with_dir_entries("/root", vec![(".hidden_dir", true)])
            .with_dir_entries("/root/.hidden_dir", vec![("secret.txt", false)]);

        let entries = fs.tree_directory("/root", false, None, None).await.unwrap();

        assert_eq!(entries.len(), 2);
        assert_eq!(tree_names(&entries), vec![".hidden_dir", "secret.txt"]);
    }

    #[tokio::test]
    async fn test_tree_show_hidden_true() {
        let fs = TreeFS::default().with_dir_entries(
            "/root",
            vec![("a.txt", false), (".hidden", false), (".hidden_dir", true)],
        );

        let entries = fs.tree_directory("/root", true, None, None).await.unwrap();

        let names = tree_names(&entries);
        assert!(names.contains(&"a.txt".to_string()));
        assert!(names.contains(&".hidden".to_string()));
        assert!(names.contains(&".hidden_dir".to_string()));
    }

    #[tokio::test]
    async fn test_tree_root_path() {
        let fs = TreeFS::default().with_dir_entries("/", vec![("a.txt", false), ("sub", true)]);

        let entries = fs.tree_directory("/", false, None, None).await.unwrap();

        assert_eq!(entries.len(), 2);
        assert_eq!(entries[0].path, "/a.txt");
        assert_eq!(entries[0].rel_path, "a.txt");
    }

    #[tokio::test]
    async fn test_tree_deep_nesting() {
        let fs = TreeFS::default()
            .with_dir_entries("/a", vec![("b", true)])
            .with_dir_entries("/a/b", vec![("c", true)])
            .with_dir_entries("/a/b/c", vec![("d", true)])
            .with_dir_entries("/a/b/c/d", vec![("e", true)])
            .with_dir_entries("/a/b/c/d/e", vec![("f.txt", false)]);

        let entries = fs.tree_directory("/a", false, None, None).await.unwrap();

        assert_eq!(entries.len(), 5);
        assert_eq!(
            tree_paths(&entries),
            vec![
                "/a/b",
                "/a/b/c",
                "/a/b/c/d",
                "/a/b/c/d/e",
                "/a/b/c/d/e/f.txt"
            ]
        );
    }

    #[tokio::test]
    async fn test_tree_mixed_scenario() {
        let fs = TreeFS::default()
            .with_dir_entries(
                "/root",
                vec![
                    ("a.txt", false),
                    (".hidden_file", false),
                    ("sub", true),
                    (".hidden_dir", true),
                ],
            )
            .with_dir_entries("/root/sub", vec![("b.txt", false)])
            .with_dir_entries("/root/.hidden_dir", vec![("secret.txt", false)]);

        let entries = fs.tree_directory("/root", false, None, None).await.unwrap();

        assert_eq!(entries.len(), 5);
        let names = tree_names(&entries);
        assert!(names.contains(&"a.txt".to_string()));
        assert!(names.contains(&"sub".to_string()));
        assert!(names.contains(&"b.txt".to_string()));
        assert!(names.contains(&".hidden_dir".to_string()));
        assert!(names.contains(&"secret.txt".to_string()));
        assert!(!names.contains(&".hidden_file".to_string()));
    }
}
