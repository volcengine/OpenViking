//! Glob helpers shared by the default trait implementation and backend
//! overrides.

use std::cmp::Ordering;

use globset::{GlobBuilder, GlobMatcher};
use sha2::{Digest, Sha256};

use crate::core::{Error, Result};

/// Precompiled standard glob matcher for query-root-relative paths.
pub struct PreparedGlob {
    matcher: Option<GlobMatcher>,
}

impl PreparedGlob {
    /// Compile a glob pattern once for repeated full relative-path checks.
    pub fn new(pattern: &str) -> Result<Self> {
        validate_pattern(pattern)?;
        let normalized = normalize_rel_path(pattern);
        let matcher = if normalized.is_empty() {
            None
        } else {
            Some(
                GlobBuilder::new(&normalized)
                    .literal_separator(true)
                    .build()
                    .map_err(|err| {
                        Error::invalid_operation(format!("invalid glob pattern: {err}"))
                    })?
                    .compile_matcher(),
            )
        };
        Ok(Self { matcher })
    }

    /// Match one path relative to the query root using standard glob semantics.
    pub fn is_match(&self, rel_path: &str) -> bool {
        self.matcher
            .as_ref()
            .is_some_and(|matcher| matcher.is_match(normalize_rel_path(rel_path)))
    }
}

#[allow(dead_code)]
pub fn standard_glob_match(rel_path: &str, pattern: &str) -> Result<bool> {
    Ok(PreparedGlob::new(pattern)?.is_match(rel_path))
}

/// Decode the opaque offset token used by the default implementations.
pub fn decode_offset_token(
    token: Option<&str>,
    path: &str,
    pattern: &str,
    show_hidden: bool,
    level_limit: Option<usize>,
) -> Result<usize> {
    match token {
        None => Ok(0),
        Some(raw) if raw.is_empty() => Err(Error::invalid_operation("empty continuation token")),
        Some(raw) => {
            let Some((scope, offset)) = raw.split_once(':') else {
                return Err(Error::invalid_operation("invalid continuation token"));
            };
            if scope != token_scope(path, pattern, show_hidden, level_limit) {
                return Err(Error::invalid_operation(
                    "continuation token scope mismatch",
                ));
            }
            offset
                .parse::<usize>()
                .map_err(|_| Error::invalid_operation("invalid continuation token"))
        }
    }
}

/// Encode the opaque offset token used by the default implementations.
pub fn encode_offset_token(
    offset: usize,
    path: &str,
    pattern: &str,
    show_hidden: bool,
    level_limit: Option<usize>,
) -> String {
    format!(
        "{}:{offset}",
        token_scope(path, pattern, show_hidden, level_limit)
    )
}

/// Validate whether a glob pattern is acceptable for matching.
///
/// Separator-only patterns such as `"/"` are valid inputs, but they do not
/// match any relative path.
pub fn validate_pattern(pattern: &str) -> Result<()> {
    if pattern.is_empty() {
        return Err(Error::invalid_operation("empty glob pattern"));
    }

    let mut saw_segment = false;
    for segment in pattern.split('/') {
        if segment.is_empty() {
            continue;
        }
        saw_segment = true;
        if segment != "." {
            return Ok(());
        }
    }

    if saw_segment {
        return Err(Error::invalid_operation("empty glob pattern"));
    }

    Ok(())
}

/// Compare two relative paths lexicographically by path component, ignoring
/// empty segments and `.` segments.
pub fn compare_rel_paths(left: &str, right: &str) -> Ordering {
    split_segments(left).cmp(&split_segments(right))
}

fn split_segments(value: &str) -> Vec<String> {
    value
        .split('/')
        .filter(|segment| !segment.is_empty() && *segment != ".")
        .map(ToString::to_string)
        .collect()
}

fn normalize_rel_path(value: &str) -> String {
    split_segments(value).join("/")
}

fn token_scope(path: &str, pattern: &str, show_hidden: bool, level_limit: Option<usize>) -> String {
    let mut hasher = Sha256::new();
    hasher.update(path.as_bytes());
    hasher.update([0]);
    hasher.update(pattern.as_bytes());
    hasher.update([0, show_hidden as u8, 0]);
    hasher.update(level_limit.unwrap_or(usize::MAX).to_string().as_bytes());
    format!("{:x}", hasher.finalize())
}

#[cfg(test)]
mod tests {
    use std::cmp::Ordering;

    use super::{
        compare_rel_paths, decode_offset_token, encode_offset_token, standard_glob_match,
        validate_pattern, PreparedGlob,
    };

    #[test]
    fn test_globset_match_uses_full_relative_path() {
        assert!(!standard_glob_match("foo/bar.rs", "*.rs").unwrap());
        assert!(!standard_glob_match("sub/resource_a", "resource_*").unwrap());
        assert!(standard_glob_match("foo/bar.rs", "**/*.rs").unwrap());
        assert!(!standard_glob_match("foo/bar.rs", "*.md").unwrap());
    }

    #[test]
    fn test_globset_match_uses_standard_recursive_semantics() {
        assert!(standard_glob_match("a/b/c.md", "a/**/*.md").unwrap());
        assert!(!standard_glob_match("a/b/c.md", "*/c.md").unwrap());
        assert!(standard_glob_match("a/c.md", "*/c.md").unwrap());
        assert!(standard_glob_match("foo/bar", "foo/**").unwrap());
        assert!(standard_glob_match("foo/bar/baz", "foo/**").unwrap());
        assert!(standard_glob_match("foo", "**/foo").unwrap());
        assert!(standard_glob_match("a.md", "**/*.md").unwrap());
        assert!(standard_glob_match("x/a.md", "**/*.md").unwrap());
    }

    #[test]
    fn test_standard_glob_match_empty_pattern_rejected() {
        assert!(standard_glob_match("a", "").is_err());
    }

    #[test]
    fn test_standard_glob_match_root_only_pattern_returns_false() {
        assert!(!standard_glob_match("a", "/").unwrap());
        assert!(!standard_glob_match("a", "///").unwrap());
    }

    #[test]
    fn test_standard_glob_match_dot_only_pattern_still_rejected() {
        assert!(standard_glob_match("a", ".").is_err());
        assert!(standard_glob_match("a", "./").is_err());
        assert!(standard_glob_match("a", "././").is_err());
    }

    #[test]
    fn test_standard_glob_match_invalid_segment_is_rejected() {
        assert!(standard_glob_match("foo", "[").is_err());
        assert!(standard_glob_match("foo", "foo[bar").is_err());
        assert!(standard_glob_match("foo", "[]").is_err());
    }

    #[test]
    fn test_standard_glob_match_normalizes_dot_segments() {
        assert!(standard_glob_match("a/./b.md", "a/b.md").unwrap());
        assert!(!standard_glob_match("a/b.md", "./b.md").unwrap());
        assert!(standard_glob_match("./a/b.md", "a/b.md").unwrap());
    }

    #[test]
    fn test_prepared_glob_matches_globset_behavior() {
        let cases = [
            ("foo/bar.rs", "**/*.rs"),
            ("a/b/c.md", "**/*.md"),
            ("foo", "**/foo"),
            ("foo/bar/baz", "foo/**"),
            ("a", "/"),
        ];

        for (path, pattern) in cases {
            let prepared = PreparedGlob::new(pattern).unwrap();
            assert_eq!(prepared.is_match(path), standard_glob_match(path, pattern).unwrap());
        }
    }

    #[test]
    fn test_offset_token_rejects_scope_mismatch() {
        let token = encode_offset_token(2, "/root", "*.md", false, None);

        assert_eq!(
            decode_offset_token(Some(&token), "/root", "*.md", false, None).unwrap(),
            2
        );
        assert!(decode_offset_token(Some(&token), "/root", "*.txt", false, None).is_err());
    }

    #[test]
    fn test_validate_pattern_distinguishes_root_only_and_dot_only() {
        validate_pattern("/").unwrap();
        validate_pattern("///").unwrap();
        assert!(validate_pattern(".").is_err());
        assert!(validate_pattern("./").is_err());
        validate_pattern("a/./b").unwrap();
    }

    #[test]
    fn test_compare_rel_paths_uses_path_component_order() {
        assert_eq!(compare_rel_paths("a", "a"), Ordering::Equal);
        assert_eq!(compare_rel_paths("a", "a/b"), Ordering::Less);
        assert_eq!(compare_rel_paths("a/b/c.txt", "a/b.txt"), Ordering::Less);
        assert_eq!(compare_rel_paths("a//b", "a/b"), Ordering::Equal);
        assert_eq!(compare_rel_paths("./a", "a"), Ordering::Equal);
    }
}
