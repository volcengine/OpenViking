//! Glob helpers shared by the default trait implementation and backend
//! overrides.

use std::cmp::Ordering;

use globset::GlobBuilder;
use sha2::{Digest, Sha256};

use crate::core::{Error, Result};

/// Return whether the relative path matches the same glob semantics currently
/// used by `pathlib.PurePath.match()`.
///
/// The current Python implementation matches path *suffix* segments rather
/// than anchoring the pattern at the query root. For example:
///
/// - `PurePath("foo/bar.rs").match("*.rs") == True`
/// - `PurePath("a/b/c.md").match("a/**/*.md") == True`
/// - `PurePath("foo").match("**/foo") == False`
///
/// We preserve that contract here by:
///
/// 1. Splitting both pattern and path into non-empty `/`-separated segments.
/// 2. When the pattern contains no `/`, matching it against the basename only.
/// 3. Otherwise, matching the pattern segments against the path's trailing
///    segments of equal length.
/// 4. Using `globset` only for *single-segment* matching, so `/` semantics stay
///    under our control.
pub fn purepath_match(rel_path: &str, pattern: &str) -> Result<bool> {
    validate_pattern(pattern)?;

    let path_segments = split_segments(rel_path);
    let pattern_segments = split_segments(pattern);

    if pattern_segments.is_empty() {
        return Ok(false);
    }

    if pattern_segments.len() == 1 {
        let name = path_segments.last().map(String::as_str).unwrap_or("");
        return segment_matches(name, &pattern_segments[0]);
    }

    if pattern_segments.len() > path_segments.len() {
        return Ok(false);
    }

    let offset = path_segments.len() - pattern_segments.len();
    for (path_segment, pattern_segment) in
        path_segments[offset..].iter().zip(pattern_segments.iter())
    {
        if !segment_matches(path_segment, pattern_segment)? {
            return Ok(false);
        }
    }
    Ok(true)
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

fn segment_matches(value: &str, pattern: &str) -> Result<bool> {
    let glob = match GlobBuilder::new(pattern).literal_separator(true).build() {
        Ok(glob) => glob,
        Err(_) => return Ok(false),
    };
    Ok(glob.compile_matcher().is_match(value))
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
        compare_rel_paths, decode_offset_token, encode_offset_token, purepath_match,
        validate_pattern,
    };

    #[test]
    fn test_purepath_match_basename_behavior() {
        assert!(purepath_match("foo/bar.rs", "*.rs").unwrap());
        assert!(purepath_match("sub/resource_a", "resource_*").unwrap());
        assert!(!purepath_match("foo/bar.rs", "*.md").unwrap());
    }

    #[test]
    fn test_purepath_match_suffix_segments_behavior() {
        assert!(purepath_match("a/b/c.md", "a/**/*.md").unwrap());
        assert!(purepath_match("a/b/c.md", "*/c.md").unwrap());
        assert!(purepath_match("foo/bar", "foo/**").unwrap());
        assert!(!purepath_match("foo/bar/baz", "foo/**").unwrap());
        assert!(!purepath_match("foo", "**/foo").unwrap());
        assert!(!purepath_match("a.md", "**/*.md").unwrap());
        assert!(purepath_match("x/a.md", "**/*.md").unwrap());
    }

    #[test]
    fn test_purepath_match_empty_pattern_rejected() {
        assert!(purepath_match("a", "").is_err());
    }

    #[test]
    fn test_purepath_match_root_only_pattern_returns_false() {
        assert!(!purepath_match("a", "/").unwrap());
        assert!(!purepath_match("a", "///").unwrap());
    }

    #[test]
    fn test_purepath_match_dot_only_pattern_still_rejected() {
        assert!(purepath_match("a", ".").is_err());
        assert!(purepath_match("a", "./").is_err());
        assert!(purepath_match("a", "././").is_err());
    }

    #[test]
    fn test_purepath_match_invalid_segment_returns_false() {
        assert!(!purepath_match("foo", "[").unwrap());
        assert!(!purepath_match("foo", "foo[bar").unwrap());
        assert!(!purepath_match("foo", "[]").unwrap());
    }

    #[test]
    fn test_purepath_match_normalizes_dot_segments() {
        assert!(purepath_match("a/./b.md", "a/b.md").unwrap());
        assert!(purepath_match("a/b.md", "./b.md").unwrap());
        assert!(purepath_match("./a/b.md", "a/b.md").unwrap());
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
