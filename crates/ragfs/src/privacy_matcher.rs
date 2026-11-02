//! High-throughput replacement of registered sensitive values in UTF-8 text.
//!
//! This module deliberately knows nothing about OpenViking users, Privacy
//! paths, or authorization. Callers provide opaque patterns and replacement
//! references; RAGFS owns only automaton construction and text rewriting.

use aho_corasick::{AhoCorasick, AhoCorasickBuilder, MatchKind};
use regex::Regex;
use std::collections::HashSet;
use std::sync::LazyLock;

static PRIVACY_REFERENCE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\{\{[ \t]*ov_privacy:[^{}\r\n]+\}\}").expect("fixed reference regex")
});

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

/// Match metadata without a rewritten copy of the input.
#[derive(Default)]
pub struct PrivacyMatchResult {
    /// Non-overlapping replacements in source order, using character offsets.
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
        let mut rewritten = String::with_capacity(content.len());
        let mut cursor = 0;
        let (result, exceeded) =
            self.visit_matches_limited(content, max_replacements, |start, end, reference| {
                rewritten.push_str(&content[cursor..start]);
                rewritten.push_str(reference);
                cursor = end;
            });
        if exceeded {
            rewritten = content.to_string();
        } else {
            rewritten.push_str(&content[cursor..]);
        }
        (
            PrivacySanitizeResult {
                content: rewritten,
                replacements: result.replacements,
                ambiguous_references: result.ambiguous_references,
            },
            exceeded,
        )
    }

    /// Find matches without constructing rewritten text.
    ///
    /// Exceeding the replacement limit discards all partial metadata, just as
    /// `sanitize_limited` does.
    pub fn find_limited(
        &self,
        content: &str,
        max_replacements: usize,
    ) -> (PrivacyMatchResult, bool) {
        self.visit_matches_limited(content, max_replacements, |_, _, _| {})
    }

    fn visit_matches_limited(
        &self,
        content: &str,
        max_replacements: usize,
        mut on_match: impl FnMut(usize, usize, &str),
    ) -> (PrivacyMatchResult, bool) {
        let mut result = PrivacyMatchResult::default();
        let Some(matcher) = &self.matcher else {
            return (result, false);
        };
        let mut source_byte_cursor = 0;
        let mut source_char_cursor = 0;
        let mut references = PRIVACY_REFERENCE.find_iter(content).peekable();

        for matched in matcher.find_iter(content) {
            while references
                .peek()
                .is_some_and(|item| item.end() <= matched.start())
            {
                references.next();
            }
            if references
                .peek()
                .is_some_and(|item| item.start() < matched.end())
            {
                continue;
            }
            if result.replacements.len() >= max_replacements {
                return (PrivacyMatchResult::default(), true);
            }
            let start = matched.start();
            let end = matched.end();
            source_char_cursor += content[source_byte_cursor..start].chars().count();
            let char_start = source_char_cursor;
            source_char_cursor += content[start..end].chars().count();
            let char_end = source_char_cursor;

            let target = &self.targets[matched.pattern().as_usize()];
            let reference = match target {
                PatternTarget::Reference(reference) => reference.clone(),
                PatternTarget::Ambiguous(candidates) => {
                    result.ambiguous_references.push(candidates.clone());
                    self.ambiguous_placeholder.clone()
                }
            };
            on_match(start, end, &reference);
            result.replacements.push(PrivacyReplacement {
                start: char_start,
                end: char_end,
                reference,
            });
            source_byte_cursor = end;
        }
        (result, false)
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
    fn registered_values_preserve_references_and_report_unicode_spans() {
        let reference = "{{ov_privacy:default:viking://~/privacy/session/demo#/values/key}}";
        let matcher = matcher(vec![
            ("秘密", vec!["short"]),
            ("秘密值", vec!["long"]),
            ("session", vec!["ref-b", "ref-a", "ref-a"]),
            ("default", vec!["owner-ref"]),
        ]);
        let input = format!("🙂秘密值 {reference} session default");
        let (found, exceeded) = matcher.find_limited(&input, 3);
        assert!(!exceeded);
        let spans: Vec<_> = found
            .replacements
            .iter()
            .map(|hit| (hit.start, hit.end, hit.reference.as_str()))
            .collect();
        let end = input.chars().count();
        assert_eq!(
            spans,
            vec![
                (1, 4, "long"),
                (end - 15, end - 8, "{{ov_privacy:ambiguous}}"),
                (end - 7, end, "owner-ref"),
            ]
        );
        assert_eq!(found.ambiguous_references, vec![vec!["ref-a", "ref-b"]]);
        assert_eq!(
            matcher.sanitize(&input).content,
            format!("🙂long {reference} {{{{ov_privacy:ambiguous}}}} owner-ref")
        );
    }

    #[test]
    fn match_limits_discard_partial_rewrites_and_metadata() {
        let matcher = matcher(vec![
            ("secret", vec!["ref"]),
            ("shared", vec!["ref-a", "ref-b"]),
        ]);
        for (input, expected, exceeded) in [
            ("shared secret", "{{ov_privacy:ambiguous}} ref", false),
            ("shared secret secret", "shared secret secret", true),
        ] {
            let (rewritten, rewrite_exceeded) = matcher.sanitize_limited(input, 2);
            let (found, find_exceeded) = matcher.find_limited(input, 2);
            assert_eq!(rewrite_exceeded, exceeded);
            assert_eq!(find_exceeded, exceeded);
            assert_eq!(rewritten.content, expected);
            assert_eq!(found.replacements.len(), if exceeded { 0 } else { 2 });
            if exceeded {
                assert!(rewritten.replacements.is_empty());
                assert!(rewritten.ambiguous_references.is_empty());
                assert!(found.ambiguous_references.is_empty());
            }
        }
    }
}
