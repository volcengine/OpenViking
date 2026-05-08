//! LocalFS plugin - Local file system mount
//!
//! This plugin mounts a local directory into RAGFS virtual file system,
//! providing direct access to local files and directories.

use async_trait::async_trait;
use std::fs;
use std::path::{Path, PathBuf};

use std::io::{BufRead, BufReader};
use std::process::{Command, Stdio};

use grep_regex::RegexMatcher;
use grep_searcher::sinks::UTF8;
use grep_searcher::{BinaryDetection, SearcherBuilder};
use ignore::WalkBuilder;
use serde::Deserialize;

use crate::core::errors::{Error, Result};
use crate::core::filesystem::FileSystem;
use crate::core::plugin::ServicePlugin;
use crate::core::types::{ConfigParameter, FileInfo, GrepResult, PluginConfig, WriteFlag};

/// LocalFS - Local file system implementation
pub struct LocalFileSystem {
    /// Base path of the mounted directory
    base_path: PathBuf,
    /// Whether external `rg` is available in current process PATH.
    has_rg: bool,
}

impl LocalFileSystem {
    /// Create a new LocalFileSystem
    ///
    /// # Arguments
    /// * `base_path` - The local directory path to mount
    ///
    /// # Errors
    /// Returns an error if the base path doesn't exist or is not a directory
    pub fn new(base_path: &str) -> Result<Self> {
        let path = PathBuf::from(base_path);

        // Check if path exists
        if !path.exists() {
            return Err(Error::plugin(format!(
                "base path does not exist: {}",
                base_path
            )));
        }

        // Check if it's a directory
        if !path.is_dir() {
            return Err(Error::plugin(format!(
                "base path is not a directory: {}",
                base_path
            )));
        }

        // Canonicalize to stabilize prefix stripping and make rg JSON path mapping more reliable.
        let canonical = fs::canonicalize(&path)
            .map_err(|e| Error::plugin(format!("failed to canonicalize base path: {}", e)))?;

        let has_rg = Command::new("rg")
            .arg("--version")
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .map(|s| s.success())
            .unwrap_or(false);

        Ok(Self {
            base_path: canonical,
            has_rg,
        })
    }

    /// Resolve a virtual path to actual local path
    fn resolve_path(&self, path: &str) -> PathBuf {
        // Remove leading slash to make it relative
        let relative = path.strip_prefix('/').unwrap_or(path);

        // Join with base path
        if relative.is_empty() {
            self.base_path.clone()
        } else {
            self.base_path.join(relative)
        }
    }

    /// Run blocking grep work on a dedicated thread and normalize join errors.
    async fn run_blocking_grep<T, F>(job: F) -> Result<T>
    where
        T: Send + 'static,
        F: FnOnce() -> Result<T> + Send + 'static,
    {
        tokio::task::spawn_blocking(job)
            .await
            .map_err(|e| Error::Internal(format!("spawn_blocking failed: {}", e)))?
    }

    /// Convert an exclude path (plugin-relative "virtual mount" form, often starts with "/")
    /// into a query-root-relative path.
    ///
    /// Contract:
    /// - Returns `Some(".")` when the exclude path covers the entire query root (exclude all).
    /// - Returns `Some(rel)` when the exclude path is under the query root (so it can be
    ///   compared with `file_virtual`, which is always query-root-relative).
    /// - Returns `None` when the exclude path is outside the query root (no effect).
    fn exclude_to_query_root_relative_path(
        base_path: &Path,
        query_root_local: &Path,
        exclude_path: &str,
    ) -> Option<String> {
        let p = exclude_path.trim_end_matches('/');
        if p.is_empty() {
            return Some(".".to_string());
        }

        let exclude_local = Self::resolve_virtual_path(base_path, p);

        // If the exclude prefix includes the query root itself, exclude everything under the query root.
        if query_root_local == exclude_local
            || query_root_local.strip_prefix(&exclude_local).is_ok()
        {
            return Some(".".to_string());
        }

        // If exclude is under query root, convert it to query-root-relative.
        let rel = exclude_local.strip_prefix(query_root_local).ok()?;
        let s = rel.to_string_lossy();
        if s.is_empty() {
            Some(".".to_string())
        } else {
            Some(s.to_string())
        }
    }

    /// Check whether a query-root-relative `file_virtual` is excluded by `exclude_rel`.
    fn is_excluded_virtual(file_virtual: &str, exclude_rel: &str) -> bool {
        if exclude_rel == "." {
            return true;
        }
        if file_virtual == exclude_rel || exclude_rel.is_empty() {
            return file_virtual == exclude_rel;
        }

        file_virtual
            .strip_prefix(exclude_rel)
            .is_some_and(|suffix| suffix.starts_with('/'))
    }

    /// Compute depth of a query-root-relative path.
    fn virtual_depth(file_virtual: &str) -> usize {
        if file_virtual.is_empty() || file_virtual == "." {
            0
        } else {
            file_virtual.split('/').filter(|p| !p.is_empty()).count()
        }
    }

    /// Execute grep via external `rg` (ripgrep) and return `GrepResult`.
    ///
    /// Returns `Ok(None)` when `rg` is unavailable or cannot be executed (e.g. not in PATH),
    /// so the caller can fall back to the in-process implementation.
    fn grep_via_rg(
        base_path: &Path,
        target_path: &Path,
        pattern: &str,
        recursive: bool,
        case_insensitive: bool,
        node_limit: Option<usize>,
        exclude_path: Option<&str>,
        level_limit: Option<usize>,
    ) -> Result<Option<GrepResult>> {
        if node_limit == Some(0) {
            return Ok(Some(GrepResult::new()));
        }

        let mut cmd = Command::new("rg");
        cmd.arg("--json");
        cmd.arg("--no-messages"); // Avoid interleaving permission/IO warnings with JSON output
        cmd.arg("--no-ignore-parent"); // Match fallback `.parents(false)` semantics.

        if case_insensitive {
            cmd.arg("-i");
        }

        // Non-recursive directory search: only scan the current directory (no descent).
        if !recursive && target_path.is_dir() {
            cmd.arg("--max-depth").arg("1");
        }

        // NOTE: rg's --max-count is a per-file limit; we enforce a global limit by terminating early while parsing.
        if let Some(limit) = node_limit {
            cmd.arg("--max-count").arg(limit.to_string());
        }

        // Run rg from base_path so we can interpret relative paths in JSON output reliably.
        let current_dir = if target_path.is_dir() {
            target_path
        } else {
            target_path.parent().unwrap_or(target_path)
        };
        cmd.current_dir(current_dir);

        cmd.arg(pattern);
        // Search relative to the query root so returned paths can be interpreted as query-root relative.
        if target_path.is_dir() {
            cmd.arg(".");
        } else {
            cmd.arg(
                target_path
                    .file_name()
                    .ok_or_else(|| Error::InvalidPath(target_path.display().to_string()))?,
            );
        }
        cmd.stdout(Stdio::piped());
        cmd.stderr(Stdio::piped());

        let mut child = match cmd.spawn() {
            Ok(c) => c,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(e) => {
                return Err(Error::InvalidOperation(format!("spawn rg failed: {}", e)));
            }
        };

        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| Error::Internal("rg stdout missing".to_string()))?;
        let mut stderr_pipe = child.stderr.take();
        let reader = BufReader::new(stdout);

        let mut out = GrepResult::new();
        let mut killed_for_limit = false;
        let exclude_rel = exclude_path
            .and_then(|p| Self::exclude_to_query_root_relative_path(base_path, target_path, p));

        #[derive(Debug, Deserialize)]
        struct RgEvent {
            #[serde(rename = "type")]
            kind: String,
            #[serde(default)]
            data: Option<RgMatchData>,
        }

        #[derive(Debug, Deserialize)]
        struct RgMatchData {
            path: RgTextField,
            #[serde(default)]
            line_number: u64,
            #[serde(default)]
            lines: RgTextField,
        }

        #[derive(Debug, Deserialize, Default)]
        struct RgTextField {
            #[serde(default)]
            text: String,
        }

        for line in reader.lines() {
            let line = match line {
                Ok(s) => s,
                Err(_) => break,
            };
            if line.is_empty() {
                continue;
            }

            let ev: RgEvent = match serde_json::from_str(&line) {
                Ok(v) => v,
                Err(_) => continue,
            };

            // Only handle "match" events.
            if ev.kind != "match" {
                continue;
            }

            let data = match ev.data {
                Some(d) => d,
                None => continue,
            };

            let file_text = data.path.text;
            if file_text.is_empty() {
                continue;
            }

            // `rg --json` may output relative paths depending on invocation. Convert to absolute using base_path.
            let file_local = {
                let p = Path::new(&file_text);
                if p.is_absolute() {
                    p.to_path_buf()
                } else {
                    current_dir.join(p)
                }
            };

            let file_virtual = match Self::local_to_query_relative_path(target_path, &file_local) {
                Some(p) => p,
                None => continue,
            };

            if let Some(excl_rel) = exclude_rel.as_deref() {
                if Self::is_excluded_virtual(&file_virtual, excl_rel) {
                    continue;
                }
            }

            if let Some(limit) = level_limit {
                if Self::virtual_depth(&file_virtual) > limit {
                    continue;
                }
            }

            let line_no = data.line_number;

            // `lines.text` usually contains the full line, possibly including trailing newline.
            let content = data
                .lines
                .text
                .trim_end_matches(&['\r', '\n'][..])
                .to_string();

            out.add_match(file_virtual, line_no, content);

            if let Some(limit) = node_limit {
                if out.count >= limit {
                    // Reached global limit: terminate the rg process to avoid further scanning.
                    let _ = child.kill();
                    killed_for_limit = true;
                    break;
                }
            }
        }

        let status = child
            .wait()
            .map_err(|e| Error::InvalidOperation(format!("wait rg failed: {}", e)))?;

        // rg exit code: 0=matches, 1=no matches, 2=error
        // If we killed it due to node_limit, ignore the exit code.
        if !killed_for_limit && status.code() == Some(2) {
            let mut stderr = String::new();
            if let Some(mut err) = stderr_pipe.take() {
                use std::io::Read;
                let _ = err.read_to_string(&mut stderr);
            }
            return Err(Error::InvalidOperation(format!("rg failed: {}", stderr)));
        }

        Ok(Some(out))
    }

    /// In-process grep fallback using Rust crates, aiming to match rg-like defaults:
    /// - respect .gitignore/.ignore
    /// - skip hidden files/dirs by default
    /// - skip binary files by default
    fn grep_via_libs(
        base_path: &Path,
        virtual_path: &str,
        pattern: &str,
        recursive: bool,
        case_insensitive: bool,
        node_limit: Option<usize>,
        exclude_path: Option<&str>,
        level_limit: Option<usize>,
    ) -> Result<GrepResult> {
        let local_root = Self::resolve_virtual_path(base_path, virtual_path);
        if !local_root.exists() {
            return Err(Error::NotFound(virtual_path.to_string()));
        }

        if node_limit == Some(0) {
            return Ok(GrepResult::new());
        }

        // Build regex for fallback (uses Rust `regex` semantics).
        let regex_pattern = if case_insensitive {
            format!("(?i){}", pattern)
        } else {
            pattern.to_string()
        };

        let matcher = RegexMatcher::new_line_matcher(&regex_pattern)
            .map_err(|e| Error::InvalidOperation(format!("Invalid regex pattern: {}", e)))?;

        let mut searcher = SearcherBuilder::new()
            .line_number(true)
            .binary_detection(BinaryDetection::quit(b'\x00'))
            .build();

        let mut out = GrepResult::new();
        let limit = node_limit.unwrap_or(usize::MAX);
        let exclude_rel = exclude_path
            .and_then(|p| Self::exclude_to_query_root_relative_path(base_path, &local_root, p));

        // Single file: search directly.
        if local_root.is_file() {
            let file_virtual = Self::local_to_query_relative_path(&local_root, &local_root)
                .ok_or_else(|| Error::InvalidPath(virtual_path.to_string()))?;

            let mut remaining = limit.saturating_sub(out.count);
            let sink = UTF8(|lnum, line| {
                if remaining == 0 {
                    return Ok(false);
                }
                if let Some(excl_rel) = exclude_rel.as_deref() {
                    if Self::is_excluded_virtual(&file_virtual, excl_rel) {
                        return Ok(true);
                    }
                }
                if let Some(max_depth) = level_limit {
                    if Self::virtual_depth(&file_virtual) > max_depth {
                        return Ok(true);
                    }
                }
                out.add_match(
                    file_virtual.clone(),
                    lnum as u64,
                    line.trim_end().to_string(),
                );
                remaining -= 1;
                Ok(remaining > 0)
            });

            // Similar to rg's --no-messages: file-level errors are not fatal here.
            let _ = searcher.search_path(&matcher, &local_root, sink);
            return Ok(out);
        }

        // Directory traversal: use ignore::WalkBuilder and explicitly set rg-like defaults.
        //
        // NOTE: We intentionally do NOT inherit ignore rules from parent directories.
        // In OpenViking, a localfs mount may live under a git repo whose root `.gitignore`
        // ignores the storage directory (e.g. `data/`). Inheriting parent ignore rules would
        // cause grep to return empty results unexpectedly.
        let mut builder = WalkBuilder::new(&local_root);
        builder
            // In ignore crate, hidden(true) means "ignore hidden" (enabled by default).
            // We set it explicitly to match rg default behavior.
            .hidden(true)
            .parents(false)
            .ignore(true)
            .git_ignore(true)
            .git_global(true)
            .git_exclude(true);
        // Apply git-related ignore rules even if the target directory isn't inside a git repo.
        builder.require_git(false);
        if !recursive && local_root.is_dir() {
            builder.max_depth(Some(1));
        }

        for dent in builder.build() {
            if out.count >= limit {
                break;
            }

            let dent = match dent {
                Ok(d) => d,
                Err(_) => continue,
            };

            let ft_is_file = dent.file_type().map(|t| t.is_file()).unwrap_or(false);
            if !ft_is_file {
                continue;
            }

            let file_path = dent.path();
            let file_virtual = match Self::local_to_query_relative_path(&local_root, file_path) {
                Some(p) => p,
                None => continue,
            };

            if let Some(excl_rel) = exclude_rel.as_deref() {
                if Self::is_excluded_virtual(&file_virtual, excl_rel) {
                    continue;
                }
            }

            if let Some(max_depth) = level_limit {
                if Self::virtual_depth(&file_virtual) > max_depth {
                    continue;
                }
            }

            let mut remaining = limit.saturating_sub(out.count);
            let sink = UTF8(|lnum, line| {
                if remaining == 0 {
                    return Ok(false);
                }
                out.add_match(
                    file_virtual.clone(),
                    lnum as u64,
                    line.trim_end().to_string(),
                );
                remaining -= 1;
                Ok(remaining > 0)
            });

            // Skip file read/permission errors to better match rg default behavior.
            let _ = searcher.search_path(&matcher, file_path, sink);
        }

        Ok(out)
    }
    fn local_to_query_relative_path(query_root: &Path, local: &Path) -> Option<String> {
        let rel = local.strip_prefix(query_root).ok()?;
        let s = rel.to_string_lossy();
        if s.is_empty() {
            Some(".".to_string())
        } else {
            Some(s.to_string())
        }
    }

    fn resolve_virtual_path(base_path: &Path, path: &str) -> PathBuf {
        let relative = path.strip_prefix('/').unwrap_or(path);
        if relative.is_empty() {
            base_path.to_path_buf()
        } else {
            base_path.join(relative)
        }
    }
}

#[async_trait]
impl FileSystem for LocalFileSystem {
    async fn create(&self, path: &str) -> Result<()> {
        let local_path = self.resolve_path(path);

        // Check if file already exists
        if local_path.exists() {
            return Err(Error::AlreadyExists(path.to_string()));
        }

        // Check if parent directory exists
        if let Some(parent) = local_path.parent() {
            if !parent.exists() {
                return Err(Error::NotFound(parent.to_string_lossy().to_string()));
            }
        }

        // Create empty file
        fs::File::create(&local_path)
            .map_err(|e| Error::plugin(format!("failed to create file: {}", e)))?;

        Ok(())
    }

    async fn mkdir(&self, path: &str, _mode: u32) -> Result<()> {
        let local_path = self.resolve_path(path);

        // Check if directory already exists
        if local_path.exists() {
            return Err(Error::AlreadyExists(path.to_string()));
        }

        // Check if parent directory exists
        if let Some(parent) = local_path.parent() {
            if !parent.exists() {
                return Err(Error::NotFound(parent.to_string_lossy().to_string()));
            }
        }

        // Create directory
        fs::create_dir(&local_path)
            .map_err(|e| Error::plugin(format!("failed to create directory: {}", e)))?;

        Ok(())
    }

    async fn remove(&self, path: &str) -> Result<()> {
        let local_path = self.resolve_path(path);

        // Check if exists
        if !local_path.exists() {
            return Err(Error::NotFound(path.to_string()));
        }

        // If directory, check if empty
        if local_path.is_dir() {
            let entries = fs::read_dir(&local_path)
                .map_err(|e| Error::plugin(format!("failed to read directory: {}", e)))?;

            if entries.count() > 0 {
                return Err(Error::plugin(format!("directory not empty: {}", path)));
            }
        }

        // Remove file or empty directory
        fs::remove_file(&local_path)
            .or_else(|_| fs::remove_dir(&local_path))
            .map_err(|e| Error::plugin(format!("failed to remove: {}", e)))?;

        Ok(())
    }

    async fn remove_all(&self, path: &str) -> Result<()> {
        let local_path = self.resolve_path(path);

        // Check if exists
        if !local_path.exists() {
            return Err(Error::NotFound(path.to_string()));
        }

        // Remove recursively
        fs::remove_dir_all(&local_path)
            .map_err(|e| Error::plugin(format!("failed to remove: {}", e)))?;

        Ok(())
    }

    async fn read(&self, path: &str, offset: u64, size: u64) -> Result<Vec<u8>> {
        let local_path = self.resolve_path(path);

        // Check if exists and is not a directory
        let metadata = fs::metadata(&local_path).map_err(|_| Error::NotFound(path.to_string()))?;

        if metadata.is_dir() {
            return Err(Error::plugin(format!("is a directory: {}", path)));
        }

        // Read file
        let data = fs::read(&local_path)
            .map_err(|e| Error::plugin(format!("failed to read file: {}", e)))?;

        // Apply offset and size
        let file_size = data.len() as u64;
        let start = offset.min(file_size) as usize;
        let end = if size == 0 {
            data.len()
        } else {
            (offset + size).min(file_size) as usize
        };

        if start >= data.len() {
            Ok(vec![])
        } else {
            Ok(data[start..end].to_vec())
        }
    }

    async fn write(&self, path: &str, data: &[u8], offset: u64, flags: WriteFlag) -> Result<u64> {
        let local_path = self.resolve_path(path);

        // Check if it's a directory
        if local_path.exists() && local_path.is_dir() {
            return Err(Error::plugin(format!("is a directory: {}", path)));
        }

        // Check if parent directory exists
        if let Some(parent) = local_path.parent() {
            if !parent.exists() {
                return Err(Error::NotFound(parent.to_string_lossy().to_string()));
            }
        }

        // Determine if we should truncate based on flags
        let should_truncate = matches!(flags, WriteFlag::Create | WriteFlag::Truncate);

        // Open or create file with truncate support
        let mut file = fs::OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(should_truncate)
            .open(&local_path)
            .map_err(|e| Error::plugin(format!("failed to open file: {}", e)))?;

        // Write data
        use std::io::{Seek, SeekFrom, Write};

        if offset > 0 {
            file.seek(SeekFrom::Start(offset))
                .map_err(|e| Error::plugin(format!("failed to seek: {}", e)))?;
        }

        let written = file
            .write(data)
            .map_err(|e| Error::plugin(format!("failed to write: {}", e)))?;

        Ok(written as u64)
    }

    async fn read_dir(&self, path: &str) -> Result<Vec<FileInfo>> {
        let local_path = self.resolve_path(path);

        // Check if directory exists
        if !local_path.exists() {
            return Err(Error::NotFound(path.to_string()));
        }

        if !local_path.is_dir() {
            return Err(Error::plugin(format!("not a directory: {}", path)));
        }

        // Read directory
        let entries = fs::read_dir(&local_path)
            .map_err(|e| Error::plugin(format!("failed to read directory: {}", e)))?;

        let mut files = Vec::new();
        for entry in entries {
            let entry = entry.map_err(|e| Error::plugin(format!("failed to read entry: {}", e)))?;
            let metadata = entry
                .metadata()
                .map_err(|e| Error::plugin(format!("failed to get metadata: {}", e)))?;

            let name = entry.file_name().to_string_lossy().to_string();
            let mode = if metadata.is_dir() { 0o755 } else { 0o644 };
            let mod_time = metadata
                .modified()
                .unwrap_or(std::time::SystemTime::UNIX_EPOCH);

            files.push(FileInfo::new(
                name,
                metadata.len(),
                mode,
                mod_time,
                metadata.is_dir(),
            ));
        }

        Ok(files)
    }

    async fn stat(&self, path: &str) -> Result<FileInfo> {
        let local_path = self.resolve_path(path);

        // Get file metadata
        let metadata = fs::metadata(&local_path).map_err(|_| Error::NotFound(path.to_string()))?;

        let name = Path::new(path)
            .file_name()
            .unwrap_or(path.as_ref())
            .to_string_lossy()
            .to_string();
        let mode = if metadata.is_dir() { 0o755 } else { 0o644 };
        let mod_time = metadata
            .modified()
            .unwrap_or(std::time::SystemTime::UNIX_EPOCH);

        Ok(FileInfo::new(
            name,
            metadata.len(),
            mode,
            mod_time,
            metadata.is_dir(),
        ))
    }

    async fn rename(&self, old_path: &str, new_path: &str) -> Result<()> {
        let old_local = self.resolve_path(old_path);
        let new_local = self.resolve_path(new_path);

        // Check if old path exists
        if !old_local.exists() {
            return Err(Error::NotFound(old_path.to_string()));
        }

        // Check if new path parent directory exists
        if let Some(parent) = new_local.parent() {
            if !parent.exists() {
                return Err(Error::NotFound(parent.to_string_lossy().to_string()));
            }
        }

        // Rename/move
        fs::rename(&old_local, &new_local)
            .map_err(|e| Error::plugin(format!("failed to rename: {}", e)))?;

        Ok(())
    }

    async fn chmod(&self, path: &str, _mode: u32) -> Result<()> {
        let local_path = self.resolve_path(path);

        // Check if exists
        if !local_path.exists() {
            return Err(Error::NotFound(path.to_string()));
        }

        // Note: chmod is not fully implemented on all platforms
        // For now, just return success
        Ok(())
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
    ) -> Result<GrepResult> {
        // Fast-path: requesting 0 matches should return immediately without touching disk or spawning processes.
        if node_limit == Some(0) {
            return Ok(GrepResult::new());
        }

        let local = self.resolve_path(path);
        if !local.exists() {
            return Err(Error::NotFound(path.to_string()));
        }
        let pattern_owned = pattern.to_string();
        let path_owned = path.to_string();
        let base_path = self.base_path.clone();
        let exclude_owned = exclude_path.map(|s| s.to_string());

        if self.has_rg {
            // External rg path: run in a blocking thread to avoid blocking the async runtime.
            let rg_pattern = pattern_owned.clone();
            let rg_base_path = base_path.clone();
            let rg_exclude = exclude_owned.clone();
            let rg_res = Self::run_blocking_grep(move || {
                LocalFileSystem::grep_via_rg(
                    rg_base_path.as_path(),
                    local.as_path(),
                    rg_pattern.as_str(),
                    recursive,
                    case_insensitive,
                    node_limit,
                    rg_exclude.as_deref(),
                    level_limit,
                )
            })
            .await?;

            if let Some(out) = rg_res {
                return Ok(out);
            }
        }

        // Fallback: also run in a blocking thread (dir walking + file reads are blocking IO).
        Self::run_blocking_grep(move || {
            LocalFileSystem::grep_via_libs(
                base_path.as_path(),
                &path_owned,
                &pattern_owned,
                recursive,
                case_insensitive,
                node_limit,
                exclude_owned.as_deref(),
                level_limit,
            )
        })
        .await
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct EnvVarGuard {
        key: &'static str,
        old: Option<std::ffi::OsString>,
    }

    impl EnvVarGuard {
        fn set(key: &'static str, val: &std::ffi::OsStr) -> Self {
            let old = std::env::var_os(key);
            std::env::set_var(key, val);
            Self { key, old }
        }
    }

    impl Drop for EnvVarGuard {
        fn drop(&mut self) {
            if let Some(v) = self.old.take() {
                std::env::set_var(self.key, v);
            } else {
                std::env::remove_var(self.key);
            }
        }
    }

    #[tokio::test]
    async fn test_localfs_grep_fallback_respects_gitignore_and_hidden() {
        use tempfile::TempDir;

        let dir = TempDir::new().unwrap();
        std::fs::write(dir.path().join(".gitignore"), "ignored.txt\n").unwrap();
        std::fs::write(dir.path().join("a.txt"), "hello\n").unwrap();
        std::fs::write(dir.path().join("ignored.txt"), "hello\n").unwrap();
        std::fs::create_dir_all(dir.path().join(".hidden")).unwrap();
        std::fs::write(dir.path().join(".hidden/secret.txt"), "hello\n").unwrap();

        let mut fs = LocalFileSystem::new(dir.path().to_str().unwrap()).unwrap();
        fs.has_rg = false;

        let result = fs
            .grep("/", "hello", true, false, None, None, None)
            .await
            .unwrap();

        assert_eq!(result.count, 1);
        assert_eq!(result.matches[0].file, "a.txt");
    }

    #[tokio::test]
    async fn test_localfs_grep_case_insensitive() {
        use tempfile::TempDir;

        let dir = TempDir::new().unwrap();
        std::fs::write(dir.path().join("a.txt"), "HELLO\n").unwrap();

        let mut fs = LocalFileSystem::new(dir.path().to_str().unwrap()).unwrap();
        fs.has_rg = false;

        let result = fs
            .grep("/", "hello", true, true, None, None, None)
            .await
            .unwrap();

        assert_eq!(result.count, 1);
    }

    #[tokio::test]
    async fn test_localfs_grep_missing_path_returns_not_found() {
        use tempfile::TempDir;

        let dir = TempDir::new().unwrap();
        let mut fs = LocalFileSystem::new(dir.path().to_str().unwrap()).unwrap();
        fs.has_rg = false;

        let err = fs
            .grep("/does-not-exist", "hello", true, false, None, None, None)
            .await
            .unwrap_err();
        assert!(matches!(err, Error::NotFound(_)));
    }

    #[tokio::test]
    async fn test_localfs_grep_does_not_inherit_parent_gitignore() {
        use tempfile::TempDir;

        let dir = TempDir::new().unwrap();
        let mount_dir = dir.path().join("mount");
        std::fs::create_dir_all(&mount_dir).unwrap();

        // Parent .gitignore ignores the mounted directory entirely.
        std::fs::write(dir.path().join(".gitignore"), "mount/\n").unwrap();
        std::fs::write(mount_dir.join("a.txt"), "hello\n").unwrap();

        let mut fs = LocalFileSystem::new(mount_dir.to_str().unwrap()).unwrap();
        fs.has_rg = false;

        let result = fs
            .grep("/", "hello", true, false, None, None, None)
            .await
            .unwrap();
        assert_eq!(result.count, 1);
        assert_eq!(result.matches[0].file, "a.txt");
    }

    #[tokio::test]
    async fn test_localfs_grep_returns_query_root_relative_paths() {
        use tempfile::TempDir;

        let dir = TempDir::new().unwrap();
        std::fs::create_dir_all(dir.path().join("sub")).unwrap();
        std::fs::write(dir.path().join("sub/a.txt"), "hello\n").unwrap();

        let mut fs = LocalFileSystem::new(dir.path().to_str().unwrap()).unwrap();
        fs.has_rg = false;

        let result = fs
            .grep("/sub", "hello", true, false, None, None, None)
            .await
            .unwrap();
        assert_eq!(result.count, 1);
        assert_eq!(result.matches[0].file, "a.txt");

        let single_file = fs
            .grep("/sub/a.txt", "hello", true, false, None, None, None)
            .await
            .unwrap();
        assert_eq!(single_file.count, 1);
        assert_eq!(single_file.matches[0].file, ".");
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn test_localfs_grep_node_limit_zero_does_not_invoke_rg() {
        use tempfile::TempDir;

        let dir = TempDir::new().unwrap();
        std::fs::write(dir.path().join("a.txt"), "hello\n").unwrap();

        // Create a fake `rg` that fails if invoked. The expected behavior is to short-circuit
        // and return empty result when node_limit=0.
        let bin_dir = dir.path().join("bin");
        std::fs::create_dir_all(&bin_dir).unwrap();
        let rg_path = bin_dir.join("rg");
        std::fs::write(&rg_path, "#!/bin/sh\nexit 2\n").unwrap();
        use std::os::unix::fs::PermissionsExt;
        let mut perms = std::fs::metadata(&rg_path).unwrap().permissions();
        perms.set_mode(0o755);
        std::fs::set_permissions(&rg_path, perms).unwrap();

        // Prepend fake rg to PATH.
        let old_path = std::env::var_os("PATH").unwrap_or_default();
        let mut new_path = std::ffi::OsString::new();
        new_path.push(bin_dir.as_os_str());
        new_path.push(std::ffi::OsStr::new(":"));
        new_path.push(old_path);
        let _path_guard = EnvVarGuard::set("PATH", &new_path);

        let fs = LocalFileSystem::new(dir.path().to_str().unwrap()).unwrap();
        let result = fs
            .grep("/", "hello", true, false, Some(0), None, None)
            .await
            .unwrap();
        assert_eq!(result.count, 0);
        assert!(result.matches.is_empty());
    }

    #[tokio::test]
    async fn test_localfs_grep_exclude_path_applies_before_node_limit() {
        use tempfile::TempDir;

        let dir = TempDir::new().unwrap();
        std::fs::create_dir_all(dir.path().join("excluded")).unwrap();
        std::fs::create_dir_all(dir.path().join("ok")).unwrap();
        std::fs::write(dir.path().join("excluded/x.txt"), "hit\n").unwrap();
        std::fs::write(dir.path().join("ok/y.txt"), "hit\n").unwrap();

        let mut fs = LocalFileSystem::new(dir.path().to_str().unwrap()).unwrap();
        fs.has_rg = false;

        let out = fs
            .grep("/", "hit", true, false, Some(1), Some("/excluded"), None)
            .await
            .unwrap();

        assert_eq!(out.count, 1);
        assert_eq!(out.matches.len(), 1);
        assert_eq!(out.matches[0].file, "ok/y.txt");
    }

    #[tokio::test]
    async fn test_localfs_grep_exclude_path_is_query_root_relative_for_nested_query_root() {
        use tempfile::TempDir;

        let dir = TempDir::new().unwrap();
        let nested_root = dir.path().join("acct/resources/docs");
        std::fs::create_dir_all(nested_root.join("excluded")).unwrap();
        std::fs::create_dir_all(nested_root.join("ok")).unwrap();
        std::fs::write(nested_root.join("excluded/x.txt"), "hit\n").unwrap();
        std::fs::write(nested_root.join("ok/y.txt"), "hit\n").unwrap();

        let mut fs = LocalFileSystem::new(dir.path().to_str().unwrap()).unwrap();
        fs.has_rg = false;

        // Simulate MountableFS -> LocalFS: query root and exclude path are plugin-relative and nested.
        let out = fs
            .grep(
                "/acct/resources/docs",
                "hit",
                true,
                false,
                Some(1),
                Some("/acct/resources/docs/excluded"),
                None,
            )
            .await
            .unwrap();

        assert_eq!(out.count, 1);
        assert_eq!(out.matches.len(), 1);
        assert_eq!(out.matches[0].file, "ok/y.txt");
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn test_localfs_grep_rg_fast_path_disables_parent_ignore_inheritance() {
        use tempfile::TempDir;

        let dir = TempDir::new().unwrap();
        std::fs::write(dir.path().join(".gitignore"), "mount/\n").unwrap();

        let mount_dir = dir.path().join("mount");
        std::fs::create_dir_all(&mount_dir).unwrap();
        std::fs::write(mount_dir.join("a.txt"), "hello\n").unwrap();

        // Fake `rg` verifies `--no-ignore-parent` is present, then emits one JSON match.
        let bin_dir = dir.path().join("bin");
        std::fs::create_dir_all(&bin_dir).unwrap();
        let rg_path = bin_dir.join("rg");
        std::fs::write(
            &rg_path,
            format!(
                "#!/bin/sh\nfor arg in \"$@\"; do\n  if [ \"$arg\" = \"--no-ignore-parent\" ]; then\n    printf '%s\\n' '{{\"type\":\"match\",\"data\":{{\"path\":{{\"text\":\"a.txt\"}},\"line_number\":1,\"lines\":{{\"text\":\"hello\\\\n\"}}}}}}'\n    exit 0\n  fi\ndone\necho missing --no-ignore-parent 1>&2\nexit 2\n"
            ),
        )
        .unwrap();
        use std::os::unix::fs::PermissionsExt;
        let mut perms = std::fs::metadata(&rg_path).unwrap().permissions();
        perms.set_mode(0o755);
        std::fs::set_permissions(&rg_path, perms).unwrap();

        let old_path = std::env::var_os("PATH").unwrap_or_default();
        let mut new_path = std::ffi::OsString::new();
        new_path.push(bin_dir.as_os_str());
        new_path.push(std::ffi::OsStr::new(":"));
        new_path.push(old_path);
        let _path_guard = EnvVarGuard::set("PATH", &new_path);

        let fs = LocalFileSystem::new(mount_dir.to_str().unwrap()).unwrap();
        let result = fs
            .grep("/", "hello", true, false, None, None, None)
            .await
            .unwrap();

        assert_eq!(result.count, 1);
        assert_eq!(result.matches.len(), 1);
        assert_eq!(result.matches[0].file, "a.txt");
        assert_eq!(result.matches[0].content, "hello");
    }
}

/// LocalFS plugin
pub struct LocalFSPlugin {
    config_params: Vec<ConfigParameter>,
}

impl LocalFSPlugin {
    /// Create a new LocalFS plugin
    pub fn new() -> Self {
        Self {
            config_params: vec![ConfigParameter {
                name: "local_dir".to_string(),
                param_type: "string".to_string(),
                required: true,
                default: None,
                description: "Local directory path to expose (must exist)".to_string(),
            }],
        }
    }
}

#[async_trait]
impl ServicePlugin for LocalFSPlugin {
    fn name(&self) -> &str {
        "localfs"
    }

    fn readme(&self) -> &str {
        r#"LocalFS Plugin - Local File System Mount

This plugin mounts a local directory into RAGFS virtual file system.

FEATURES:
  - Mount any local directory into RAGFS
  - Full POSIX file system operations
  - Direct access to local files and directories
  - Preserves file permissions and timestamps
  - Efficient file operations (no copying)

CONFIGURATION:

  Basic configuration:
  [plugins.localfs]
  enabled = true
  path = "/local"

    [plugins.localfs.config]
    local_dir = "/path/to/local/directory"

  Multiple local mounts:
  [plugins.localfs_home]
  enabled = true
  path = "/home"

    [plugins.localfs_home.config]
    local_dir = "/Users/username"

USAGE:

  List directory:
    agfs ls /local

  Read a file:
    agfs cat /local/file.txt

  Write to a file:
    agfs write /local/file.txt "Hello, World!"

  Create a directory:
    agfs mkdir /local/newdir

  Remove a file:
    agfs rm /local/file.txt

NOTES:
  - Changes are directly applied to local file system
  - File permissions are preserved and can be modified
  - Be careful with rm -r as it permanently deletes files

VERSION: 1.0.0
"#
    }

    async fn validate(&self, config: &PluginConfig) -> Result<()> {
        // Validate local_dir parameter
        let local_dir = config
            .params
            .get("local_dir")
            .and_then(|v| match v {
                crate::core::types::ConfigValue::String(s) => Some(s),
                _ => None,
            })
            .ok_or_else(|| Error::plugin("local_dir is required in configuration".to_string()))?;

        // Check if path exists
        let path = Path::new(local_dir);
        if !path.exists() {
            return Err(Error::plugin(format!(
                "base path does not exist: {}",
                local_dir
            )));
        }

        // Verify it's a directory
        if !path.is_dir() {
            return Err(Error::plugin(format!(
                "base path is not a directory: {}",
                local_dir
            )));
        }

        Ok(())
    }

    async fn initialize(&self, config: PluginConfig) -> Result<Box<dyn FileSystem>> {
        // Parse configuration
        let local_dir = config
            .params
            .get("local_dir")
            .and_then(|v| match v {
                crate::core::types::ConfigValue::String(s) => Some(s),
                _ => None,
            })
            .ok_or_else(|| Error::plugin("local_dir is required".to_string()))?;

        let fs = LocalFileSystem::new(local_dir)?;
        Ok(Box::new(fs))
    }

    fn config_params(&self) -> &[ConfigParameter] {
        &self.config_params
    }
}
