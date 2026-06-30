//! Account-level `.ovgitignore` parsing and matching.
//!
//! The syntax is a documented OpenViking subset of root `.gitignore` rules.
//! It intentionally rejects negation so callers do not assume full Git index
//! semantics.

use std::path::Path;

use ignore::gitignore::{Gitignore, GitignoreBuilder};

use crate::git::error::GitError;

/// Account-relative tree path of the control file.
pub const OVGITIGNORE_PATH: &str = ".ovgitignore";

/// Hard size cap on the ignore file, in bytes.
pub const OVGITIGNORE_MAX_BYTES: usize = 64 * 1024;

/// Compiled, account-level `.ovgitignore` rule set.
///
/// Holds `None` when the file is absent or contains no effective rules, in
/// which case [`IgnoreMatcher::is_ignored`] always returns `false`.
#[derive(Debug, Clone)]
pub struct IgnoreMatcher {
    inner: Option<Gitignore>,
}

impl Default for IgnoreMatcher {
    fn default() -> Self {
        Self::empty()
    }
}

impl IgnoreMatcher {
    /// Construct a matcher that ignores nothing.
    pub fn empty() -> Self {
        Self { inner: None }
    }

    /// Parse `.ovgitignore` bytes into a matcher.
    ///
    /// Enforces the documented subset: UTF-8 text within
    /// [`OVGITIGNORE_MAX_BYTES`], no `!` negation, and no Git-style escaping
    /// (a backslash is rejected rather than silently reinterpreted by the
    /// underlying matcher). Comments (`#`) and blank lines are skipped;
    /// leading/trailing whitespace is trimmed per the spec.
    pub fn parse(bytes: &[u8]) -> Result<Self, GitError> {
        if bytes.len() > OVGITIGNORE_MAX_BYTES {
            return Err(GitError::IgnoreFileTooLarge {
                path: OVGITIGNORE_PATH.to_string(),
                size: bytes.len() as u64,
                max: OVGITIGNORE_MAX_BYTES as u64,
            });
        }

        let text = std::str::from_utf8(bytes).map_err(|e| GitError::InvalidIgnoreFile {
            path: OVGITIGNORE_PATH.to_string(),
            reason: format!("must be UTF-8: {e}"),
        })?;

        let mut builder = GitignoreBuilder::new(Path::new(""));
        let mut added = false;
        for (idx, raw) in text.lines().enumerate() {
            let line = raw.trim();
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            if line.starts_with('!') {
                return Err(GitError::InvalidIgnoreFile {
                    path: OVGITIGNORE_PATH.to_string(),
                    reason: format!(
                        "line {} uses unsupported negation: {}",
                        idx + 1,
                        line
                    ),
                });
            }
            // Git-style escaping is unsupported by this subset. Reject it up
            // front with a clear error rather than letting the underlying
            // matcher silently reinterpret `\x` (e.g. `foo\bar` -> `foobar`)
            // or fail on an incomplete trailing escape (`foo\`).
            if line.contains('\\') {
                return Err(GitError::InvalidIgnoreFile {
                    path: OVGITIGNORE_PATH.to_string(),
                    reason: format!(
                        "line {} uses unsupported escaping: {}",
                        idx + 1,
                        line
                    ),
                });
            }
            builder.add_line(Some(OVGITIGNORE_PATH.into()), line).map_err(|e| {
                GitError::InvalidIgnoreFile {
                    path: OVGITIGNORE_PATH.to_string(),
                    reason: format!("line {} is invalid: {e}", idx + 1),
                }
            })?;
            added = true;
        }

        if !added {
            return Ok(Self::empty());
        }

        let inner = builder.build().map_err(|e| GitError::InvalidIgnoreFile {
            path: OVGITIGNORE_PATH.to_string(),
            reason: e.to_string(),
        })?;
        Ok(Self { inner: Some(inner) })
    }

    /// Whether `rel_path` (account-relative, `/`-separated) is excluded.
    ///
    /// `is_dir` must reflect whether `rel_path` is a directory: directory-only
    /// patterns such as `build/` only match a path that is itself a directory.
    /// Pass `false` for file paths (the common commit case, where enumeration
    /// only yields files). The `.ovgitignore` file itself is never ignored.
    pub fn is_ignored(&self, rel_path: &str, is_dir: bool) -> bool {
        let Some(inner) = &self.inner else {
            return false;
        };
        let cleaned = rel_path.trim_matches('/');
        if cleaned.is_empty() || cleaned == OVGITIGNORE_PATH {
            return false;
        }
        inner
            .matched_path_or_any_parents(Path::new(cleaned), is_dir)
            .is_ignore()
    }
}

/// Whether `rel_path` should be included in a commit snapshot.
///
/// Combines the hardcoded system pruning ([`crate::git::enumerate::prune_path`])
/// with the account `.ovgitignore` rules. `rel_path` is treated as a file
/// (`is_dir = false`), which matches the commit flow — enumeration only
/// yields file paths. The `.ovgitignore` file is always tracked.
pub fn should_track_path(rel_path: &str, matcher: &IgnoreMatcher) -> bool {
    if rel_path == OVGITIGNORE_PATH {
        return true;
    }
    !crate::git::enumerate::prune_path(rel_path) && !matcher.is_ignored(rel_path, false)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn matcher(src: &str) -> IgnoreMatcher {
        IgnoreMatcher::parse(src.as_bytes()).expect("ignore file parses")
    }

    #[test]
    fn empty_comments_and_blank_lines_match_nothing() {
        let m = matcher("\n  \n# comment\n   # indented comment\n");
        assert!(!m.is_ignored("resources/a.log", false));
        assert!(should_track_path("resources/a.log", &m));
    }

    #[test]
    fn basename_glob_matches_at_any_depth() {
        let m = matcher("*.log\n");
        assert!(m.is_ignored("resources/a.log", false));
        assert!(m.is_ignored("resources/proj/nested/a.log", false));
        assert!(!m.is_ignored("resources/a.md", false));
    }

    #[test]
    fn double_star_glob_matches_nested_paths() {
        let m = matcher("**/*.bak\n");
        assert!(m.is_ignored("a.bak", false));
        assert!(m.is_ignored("resources/proj/a.bak", false));
        assert!(!m.is_ignored("resources/proj/a.md", false));
    }

    #[test]
    fn root_relative_patterns_match_from_account_root() {
        let m = matcher("resources/tmp/**\n/resources/cache/**\n");
        assert!(m.is_ignored("resources/tmp/a.txt", false));
        assert!(m.is_ignored("resources/tmp/nested/a.txt", false));
        assert!(m.is_ignored("resources/cache/a.txt", false));
        assert!(!m.is_ignored("user/default/resources/tmp/a.txt", false));
    }

    #[test]
    fn directory_patterns_match_directory_contents() {
        let m = matcher("tmp/\n/cache/\n");
        assert!(m.is_ignored("resources/tmp/a.txt", false));
        assert!(m.is_ignored("tmp/a.txt", false));
        assert!(m.is_ignored("cache/a.txt", false));
        assert!(!m.is_ignored("resources/cache/a.txt", false));
    }

    #[test]
    fn ovgitignore_is_always_tracked() {
        let m = matcher("*\n.ovgitignore\n");
        assert!(m.is_ignored("resources/a.md", false));
        assert!(should_track_path(OVGITIGNORE_PATH, &m));
    }

    #[test]
    fn system_prune_still_wins() {
        let m = IgnoreMatcher::empty();
        assert!(!should_track_path("_system/state.json", &m));
        assert!(!should_track_path("resources/index.faiss", &m));
        assert!(!should_track_path("resources/embedding_cache/a.bin", &m));
    }

    #[test]
    fn negation_is_rejected() {
        let err = IgnoreMatcher::parse(b"!keep.log\n").unwrap_err();
        assert!(matches!(err, GitError::InvalidIgnoreFile { .. }));
        assert!(err.to_string().contains("negation"));
    }

    #[test]
    fn non_utf8_is_rejected() {
        let err = IgnoreMatcher::parse(&[0xff, 0xfe]).unwrap_err();
        assert!(matches!(err, GitError::InvalidIgnoreFile { .. }));
        assert!(err.to_string().contains("UTF-8"));
    }

    #[test]
    fn oversized_file_is_rejected() {
        let bytes = vec![b'a'; OVGITIGNORE_MAX_BYTES + 1];
        let err = IgnoreMatcher::parse(&bytes).unwrap_err();
        assert!(matches!(err, GitError::IgnoreFileTooLarge { .. }));
    }

    #[test]
    fn git_style_escaping_is_rejected() {
        // `\` is unsupported by the documented subset; reject up front rather
        // than letting the matcher silently reinterpret it.
        for src in [b"foo\\bar\n".as_slice(), b"keep\\\n"] {
            let err = IgnoreMatcher::parse(src).unwrap_err();
            assert!(matches!(err, GitError::InvalidIgnoreFile { .. }));
            assert!(err.to_string().contains("escaping"), "src={:?}", src);
        }
    }

    #[test]
    fn directory_pattern_needs_is_dir_to_match_the_dir_itself() {
        // `build/` is a directory-only pattern: it matches files *inside*
        // `build/` regardless of is_dir, but the directory path `build`
        // itself only matches when is_dir is true.
        let m = matcher("build/\n");
        assert!(m.is_ignored("build/output.o", false));
        assert!(!m.is_ignored("build", false));
        assert!(m.is_ignored("build", true));
    }
}
