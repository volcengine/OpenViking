//! High-throughput replacement of registered sensitive values in UTF-8 text.
//!
//! This module deliberately knows nothing about OpenViking users, Privacy
//! paths, or authorization. Callers provide opaque patterns and replacement
//! references; RAGFS owns only automaton construction and text rewriting.

use aho_corasick::{AhoCorasick, AhoCorasickBuilder, MatchKind};
use std::collections::HashSet;

/// Replacement emitted for one non-overlapping leftmost-longest match.
#[derive(Clone, PartialEq, Eq)]
pub struct PrivacyReplacement {
    /// Match start as a Unicode character offset.
    pub start: usize,
    /// Match end as an exclusive Unicode character offset.
    pub end: usize,
    /// Opaque reference or ambiguity placeholder inserted into the output.
    pub reference: String,
}

/// Sanitized text and metadata for its replacements.
#[derive(Clone, PartialEq, Eq)]
pub struct PrivacySanitizeResult {
    /// Rewritten UTF-8 text.
    pub content: String,
    /// Non-overlapping replacements in source order.
    pub replacements: Vec<PrivacyReplacement>,
    /// Reference candidates for ambiguous values that occurred in the input.
    pub ambiguous_references: Vec<Vec<String>>,
}

#[derive(Clone)]
enum PatternTarget {
    Reference(String),
    Ambiguous(Vec<String>),
}

/// Immutable Aho-Corasick matcher for one Privacy catalog revision.
pub struct PrivacyMatcher {
    matcher: Option<AhoCorasick>,
    targets: Vec<PatternTarget>,
    ambiguous_placeholder: String,
}

impl PrivacyMatcher {
    /// Build a matcher from `(sensitive value, candidate references)` entries.
    ///
    /// Candidate references are sorted and deduplicated. One candidate produces
    /// a normal replacement; multiple candidates produce the supplied ambiguity
    /// placeholder and are reported in the result. Empty patterns and entries
    /// without references are rejected.
    pub fn new(
        entries: Vec<(String, Vec<String>)>,
        ambiguous_placeholder: impl Into<String>,
    ) -> Result<Self, String> {
        let mut patterns = Vec::with_capacity(entries.len());
        let mut targets = Vec::with_capacity(entries.len());
        let mut seen_patterns = HashSet::with_capacity(entries.len());

        for (pattern, mut references) in entries {
            if pattern.is_empty() {
                return Err("privacy match pattern must not be empty".to_string());
            }
            if !seen_patterns.insert(pattern.clone()) {
                return Err("privacy match patterns must be unique".to_string());
            }
            references.sort_unstable();
            references.dedup();
            if references.is_empty() {
                return Err("privacy match pattern requires at least one reference".to_string());
            }
            patterns.push(pattern);
            targets.push(if references.len() == 1 {
                PatternTarget::Reference(references.pop().expect("one reference remains"))
            } else {
                PatternTarget::Ambiguous(references)
            });
        }

        let matcher = if patterns.is_empty() {
            None
        } else {
            Some(
                AhoCorasickBuilder::new()
                    .match_kind(MatchKind::LeftmostLongest)
                    .build(patterns)
                    .map_err(|err| format!("failed to build Privacy matcher: {err}"))?,
            )
        };
        Ok(Self {
            matcher,
            targets,
            ambiguous_placeholder: ambiguous_placeholder.into(),
        })
    }

    /// Rewrite all non-overlapping leftmost-longest matches.
    pub fn sanitize(&self, content: &str) -> PrivacySanitizeResult {
        self.sanitize_limited(content, usize::MAX).0
    }

    /// Rewrite matches while bounding replacement metadata allocation.
    ///
    /// When the limit is exceeded, the original content is returned with empty
    /// metadata and the boolean is set. Callers can then report the file as too
    /// dense without retaining a potentially attacker-controlled match vector.
    pub fn sanitize_limited(
        &self,
        content: &str,
        max_replacements: usize,
    ) -> (PrivacySanitizeResult, bool) {
        let Some(matcher) = &self.matcher else {
            return (
                PrivacySanitizeResult {
                    content: content.to_string(),
                    replacements: Vec::new(),
                    ambiguous_references: Vec::new(),
                },
                false,
            );
        };

        let mut rewritten = String::with_capacity(content.len());
        let mut replacements = Vec::new();
        let mut ambiguous_references = Vec::new();
        let mut source_byte_cursor = 0;
        let mut source_char_cursor = 0;

        for matched in matcher.find_iter(content) {
            if replacements.len() >= max_replacements {
                return (
                    PrivacySanitizeResult {
                        content: content.to_string(),
                        replacements: Vec::new(),
                        ambiguous_references: Vec::new(),
                    },
                    true,
                );
            }
            let start = matched.start();
            let end = matched.end();
            rewritten.push_str(&content[source_byte_cursor..start]);
            source_char_cursor += content[source_byte_cursor..start].chars().count();
            let char_start = source_char_cursor;
            source_char_cursor += content[start..end].chars().count();
            let char_end = source_char_cursor;

            let target = &self.targets[matched.pattern().as_usize()];
            let reference = match target {
                PatternTarget::Reference(reference) => reference.clone(),
                PatternTarget::Ambiguous(candidates) => {
                    ambiguous_references.push(candidates.clone());
                    self.ambiguous_placeholder.clone()
                }
            };
            rewritten.push_str(&reference);
            replacements.push(PrivacyReplacement {
                start: char_start,
                end: char_end,
                reference,
            });
            source_byte_cursor = end;
        }
        if replacements.is_empty() {
            rewritten.push_str(content);
        } else {
            rewritten.push_str(&content[source_byte_cursor..]);
        }

        (
            PrivacySanitizeResult {
                content: rewritten,
                replacements,
                ambiguous_references,
            },
            false,
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn matcher(entries: Vec<(&str, Vec<&str>)>) -> PrivacyMatcher {
        PrivacyMatcher::new(
            entries
                .into_iter()
                .map(|(value, refs)| {
                    (
                        value.to_string(),
                        refs.into_iter().map(str::to_string).collect(),
                    )
                })
                .collect(),
            "{{ov_privacy:ambiguous}}",
        )
        .unwrap()
    }

    #[test]
    fn uses_leftmost_longest_and_unicode_character_offsets() {
        let matcher = matcher(vec![
            ("秘密", vec!["short"]),
            ("秘密值", vec!["long"]),
            ("token", vec!["token-ref"]),
        ]);
        let result = matcher.sanitize("前缀秘密值 token");
        assert_eq!(result.content, "前缀long token-ref");
        assert_eq!(result.replacements.len(), 2);
        assert_eq!(
            (result.replacements[0].start, result.replacements[0].end),
            (2, 5)
        );
        assert_eq!(
            (result.replacements[1].start, result.replacements[1].end),
            (6, 11)
        );
    }

    #[test]
    fn reports_ambiguity_without_exposing_pattern() {
        let matcher = matcher(vec![("secret", vec!["ref-b", "ref-a", "ref-a"])]);
        let result = matcher.sanitize("x secret y");
        assert_eq!(result.content, "x {{ov_privacy:ambiguous}} y");
        assert_eq!(result.ambiguous_references, vec![vec!["ref-a", "ref-b"]]);
    }

    #[test]
    fn empty_catalog_is_a_noop() {
        let matcher = PrivacyMatcher::new(Vec::new(), "ambiguous").unwrap();
        let result = matcher.sanitize("unchanged");
        assert_eq!(result.content, "unchanged");
        assert!(result.replacements.is_empty());
    }

    #[test]
    fn limited_scan_stops_without_returning_partial_rewrites() {
        let matcher = matcher(vec![("secret", vec!["ref"])]);
        let (result, limit_exceeded) = matcher.sanitize_limited("secret secret secret", 2);
        assert!(limit_exceeded);
        assert_eq!(result.content, "secret secret secret");
        assert!(result.replacements.is_empty());

        let (result, limit_exceeded) = matcher.sanitize_limited("secret secret", 2);
        assert!(!limit_exceeded);
        assert_eq!(result.content, "ref ref");
        assert_eq!(result.replacements.len(), 2);
    }
}
