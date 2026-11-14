//! High-throughput replacement of registered sensitive values in UTF-8 text.
//!
//! This module deliberately knows nothing about OpenViking users, Privacy
//! paths, or authorization. Callers provide opaque patterns and replacement
//! references; RAGFS owns only automaton construction and text rewriting.

use aho_corasick::{
    automaton::Automaton, nfa::contiguous::NFA, AhoCorasick, AhoCorasickBuilder, Anchored, Input,
    Match, MatchKind, PatternID,
};
use regex::Regex;
use std::collections::HashSet;
use std::sync::{Arc, LazyLock};

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
    reverse_matcher: Option<NFA>,
    shorter_prefixes: Vec<Option<PatternID>>,
    contained_values: Vec<bool>,
    targets: Vec<PatternTarget>,
    ambiguous_placeholder: String,
    preferred: Option<Arc<PrivacyMatcher>>,
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

        let mut shorter_prefixes = vec![None; patterns.len()];
        let mut parent_prefix = vec![patterns.len(); patterns.len()];
        let mut ordered: Vec<usize> = (0..patterns.len()).collect();
        ordered.sort_unstable_by(|&a, &b| patterns[a].cmp(&patterns[b]));
        let mut prefixes: Vec<usize> = Vec::new();
        for index in ordered {
            let value = &patterns[index];
            while prefixes
                .last()
                .is_some_and(|&p| !value.starts_with(&patterns[p]))
            {
                prefixes.pop();
            }
            parent_prefix[index] = prefixes.last().copied().unwrap_or(patterns.len());
            for &prefix in prefixes.iter().rev() {
                let end = patterns[prefix].len();
                if !(value[..end].chars().next_back().is_some_and(word_char)
                    && value[end..].chars().next().is_some_and(word_char))
                {
                    shorter_prefixes[index] = Some(PatternID::must(prefix));
                    break;
                }
            }
            prefixes.push(index);
        }
        let reverse_matcher = if patterns.len() > 1 {
            Some(
                NFA::new(
                    patterns
                        .iter()
                        .map(|value| value.bytes().rev().collect::<Vec<_>>()),
                )
                .map_err(|err| format!("failed to build reverse Privacy matcher: {err}"))?,
            )
        } else {
            None
        };
        let mut contained = vec![false; patterns.len()];
        // Each registered prefix is marked once, even when values overlap heavily.
        let mut unmarked: Vec<_> = (0..=patterns.len()).collect();
        if let Some(reverse_matcher) = &reverse_matcher {
            for (index, value) in patterns.iter().enumerate() {
                let mut sid = reverse_matcher
                    .start_state(Anchored::No)
                    .expect("reverse NFA supports unanchored searches");
                for byte in value.bytes().rev() {
                    sid = reverse_matcher.next_state(Anchored::No, sid, byte);
                    if !reverse_matcher.is_match(sid) {
                        continue;
                    }
                    let matched = reverse_matcher.match_pattern(sid, 0).as_usize();
                    let first = if matched == index {
                        parent_prefix[index]
                    } else {
                        matched
                    };
                    let mut next = next_unmarked_prefix(&mut unmarked, first);
                    while next < patterns.len() {
                        contained[next] = true;
                        unmarked[next] = next_unmarked_prefix(&mut unmarked, parent_prefix[next]);
                        next = unmarked[next];
                    }
                }
            }
        }
        let matcher = if patterns.is_empty() {
            None
        } else {
            Some(
                AhoCorasickBuilder::new()
                    .match_kind(MatchKind::Standard)
                    .build(patterns)
                    .map_err(|err| format!("failed to build Privacy matcher: {err}"))?,
            )
        };
        Ok(Self {
            matcher,
            reverse_matcher: reverse_matcher.filter(|_| contained.iter().any(|&v| v)),
            shorter_prefixes,
            contained_values: contained,
            targets,
            ambiguous_placeholder: ambiguous_placeholder.into(),
            preferred: None,
        })
    }

    /// Share another matcher, preferring its reference for an identical span.
    pub fn with_preferred(mut self, preferred: Arc<Self>) -> Self {
        self.preferred = Some(preferred);
        self
    }

    /// Rewrite all non-overlapping leftmost-longest matches.
    pub fn sanitize(&self, content: &str) -> PrivacySanitizeResult {
        self.sanitize_limited(content, usize::MAX).0
    }

    /// Rewrite matches while bounding replacement metadata allocation.
    ///
    /// Exceeding the replacement limit returns the original content with empty
    /// metadata and sets the boolean. Invalid or overlapping candidates do not
    /// consume the replacement budget.
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
        let mut source_byte_cursor = 0;
        let mut source_char_cursor = 0;
        let completed = self.visit_valid_matches(content, |owner, matched| {
            if result.replacements.len() >= max_replacements {
                return false;
            }
            let start = matched.start();
            let end = matched.end();
            source_char_cursor += content[source_byte_cursor..start].chars().count();
            let char_start = source_char_cursor;
            source_char_cursor += content[start..end].chars().count();
            let char_end = source_char_cursor;
            let reference = match &owner.targets[matched.pattern().as_usize()] {
                PatternTarget::Reference(reference) => reference.clone(),
                PatternTarget::Ambiguous(candidates) => {
                    result.ambiguous_references.push(candidates.clone());
                    owner.ambiguous_placeholder.clone()
                }
            };
            on_match(start, end, &reference);
            result.replacements.push(PrivacyReplacement {
                start: char_start,
                end: char_end,
                reference,
            });
            source_byte_cursor = end;
            true
        });
        if completed {
            (result, false)
        } else {
            (PrivacyMatchResult::default(), true)
        }
    }

    fn visit_valid_matches<'a>(
        &'a self,
        content: &str,
        mut on_match: impl FnMut(&'a Self, Match) -> bool,
    ) -> bool {
        let mut start = 0;
        for reference in PRIVACY_REFERENCE
            .find_iter(content)
            .map(|r| r.range())
            .chain(std::iter::once(content.len()..content.len()))
        {
            if !self.visit_segment_matches(content, start, reference.start, &mut on_match) {
                return false;
            }
            start = reference.end;
        }
        true
    }

    fn visit_segment_matches<'a>(
        &'a self,
        content: &str,
        start: usize,
        end: usize,
        mut on_match: impl FnMut(&'a Self, Match) -> bool,
    ) -> bool {
        let Some(preferred) = &self.preferred else {
            return self.visit_own_matches(content, start, end, |matched| on_match(self, matched));
        };
        let next_own = |start| {
            let mut found = None;
            self.visit_own_matches(content, start, end, |matched| {
                found = Some(matched);
                false
            });
            found
        };
        let mut own = next_own(start);
        let mut shared = preferred.next_match(content, start, end);
        loop {
            let (owner, matched) = match (own, shared) {
                (None, None) => return true,
                (Some(matched), None) => (self, matched),
                (None, Some(shared)) => shared,
                (Some(own), Some(shared))
                    if shared.1.start() < own.start()
                        || (shared.1.start() == own.start() && shared.1.end() >= own.end()) =>
                {
                    shared
                }
                (Some(own), _) => (self, own),
            };
            if !on_match(owner, matched) {
                return false;
            }
            if own.is_some_and(|item| item.start() < matched.end()) {
                own = next_own(matched.end());
            }
            if shared.is_some_and(|(_, item)| item.start() < matched.end()) {
                shared = preferred.next_match(content, matched.end(), end);
            }
        }
    }

    fn next_match(&self, content: &str, start: usize, end: usize) -> Option<(&Self, Match)> {
        let mut found = None;
        self.visit_segment_matches(content, start, end, |owner, matched| {
            found = Some((owner, matched));
            false
        });
        found
    }

    fn visit_own_matches(
        &self,
        content: &str,
        start: usize,
        end: usize,
        mut on_match: impl FnMut(Match) -> bool,
    ) -> bool {
        let Some(matcher) = &self.matcher else {
            return true;
        };
        let mut consumed = start;
        let width = matcher.max_pattern_len();
        let mut candidates = Vec::new();
        let segment_end = end;
        let mut at = start;
        loop {
            let mut advanced = false;
            for first in matcher.find_overlapping_iter(Input::new(content).span(at..segment_end)) {
                // A value absent from every other value cannot be hidden by a longer match.
                if !self.contained_values[first.pattern().as_usize()] {
                    if !is_match_boundary(content, first.start())
                        || !is_match_boundary(content, first.end())
                    {
                        continue;
                    }
                    if !on_match(first) {
                        return false;
                    }
                    consumed = first.end();
                    at = consumed;
                    advanced = true;
                    break;
                }
                // Resolve starts in one pattern-length window with enough right lookahead.
                let window_start = at.max(first.end().saturating_sub(width));
                let safe_end = segment_end.min(window_start.saturating_add(width));
                let scan_end = segment_end.min(safe_end.saturating_add(width - 1));
                let reverse_matcher = self
                    .reverse_matcher
                    .as_ref()
                    .expect("contained values require reverse matching");
                let mut sid = reverse_matcher
                    .start_state(Anchored::No)
                    .expect("reverse NFA supports unanchored searches");
                candidates.clear();
                for start in (window_start..scan_end).rev() {
                    sid = reverse_matcher.next_state(Anchored::No, sid, content.as_bytes()[start]);
                    if start >= safe_end
                        || !reverse_matcher.is_match(sid)
                        || !is_match_boundary(content, start)
                    {
                        continue;
                    }
                    // Reverse matching yields the longest value at this original start.
                    let mut pid = reverse_matcher.match_pattern(sid, 0);
                    let mut end = start + reverse_matcher.pattern_len(pid);
                    if !is_match_boundary(content, end) {
                        let Some(prefix) = self.shorter_prefixes[pid.as_usize()] else {
                            continue;
                        };
                        pid = prefix;
                        end = start + reverse_matcher.pattern_len(pid);
                    }
                    candidates.push(Match::new(pid, start..end));
                }
                for &matched in candidates.iter().rev() {
                    if matched.start() < consumed {
                        continue;
                    }
                    if !on_match(matched) {
                        return false;
                    }
                    consumed = matched.end();
                }
                at = safe_end.max(consumed);
                advanced = true;
                break;
            }
            if !advanced {
                break;
            }
        }
        true
    }
}

// Path compression skips prefixes already marked as contained.
fn next_unmarked_prefix(links: &mut [usize], mut index: usize) -> usize {
    let mut root = index;
    while links[root] != root {
        root = links[root];
    }
    while links[index] != index {
        let parent = links[index];
        links[index] = root;
        index = parent;
    }
    root
}
// Matches may not split adjacent word characters.
fn is_match_boundary(content: &str, at: usize) -> bool {
    !(content[..at].chars().next_back().is_some_and(word_char)
        && content[at..].chars().next().is_some_and(word_char))
}
fn word_char(c: char) -> bool {
    c.is_alphanumeric() || c == '_'
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
    #[test]
    fn boundaries_filter_before_leftmost_longest_selection() {
        let matcher = matcher(vec![
            ("pass123", vec!["short"]),
            ("pass123-v2", vec!["long"]),
            ("token-value", vec!["token"]),
            ("/path/", vec!["path"]),
        ]);
        let text =
            "pass123-v2x pass123-v2 中token-value token-value² _token-value token-value_ x/path/y";
        assert_eq!(
            matcher.sanitize(text).content,
            "short-v2x long 中token-value token-value² _token-value token-value_ xpathy"
        );
    }

    #[test]
    fn dense_overlaps_only_count_final_replacements() {
        let entries = (6..70)
            .map(|len| ("/".repeat(len), vec![format!("ref-{len}")]))
            .collect();
        let matcher = PrivacyMatcher::new(entries, "AMB").unwrap();
        let text = "/".repeat(4096);
        let (found, exceeded) = matcher.find_limited(&text, 60);
        assert!(!exceeded);
        assert_eq!(found.replacements.len(), 60);
        assert_eq!(found.replacements[0].reference, "ref-69");
        assert_eq!(found.replacements[59].reference, "ref-25");
        let (rewritten, exceeded) = matcher.sanitize_limited(&text, 59);
        assert!(exceeded);
        assert_eq!(rewritten.content, text);
        assert!(rewritten.replacements.is_empty());
    }

    #[test]
    fn shared_matchers_preserve_flat_selection_and_budgets() {
        let shared = Arc::new(matcher(vec![
            ("b-c", vec!["middle"]),
            ("c-d", vec!["tail"]),
            ("same", vec!["shared"]),
            ("pass123", vec!["short"]),
        ]));
        let combined = matcher(vec![
            ("a-b", vec!["local"]),
            ("same", vec!["local-same"]),
            ("pass123-v2", vec!["long"]),
        ])
        .with_preferred(shared);
        let flat = matcher(vec![
            ("b-c", vec!["middle"]),
            ("c-d", vec!["tail"]),
            ("same", vec!["shared"]),
            ("pass123", vec!["short"]),
            ("a-b", vec!["local"]),
            ("pass123-v2", vec!["long"]),
        ]);
        for input in [
            "a-b-c-d same pass123-v2 pass123-v2x",
            "🙂same a-b-c-d {{ov_privacy:same}} same",
            "中same same_ xsame same pass123-v2²",
        ] {
            for limit in 0..8 {
                let (actual, exceeded) = combined.sanitize_limited(input, limit);
                let (expected, expected_exceeded) = flat.sanitize_limited(input, limit);
                assert!(actual == expected);
                assert_eq!(exceeded, expected_exceeded);
            }
        }
    }
}
