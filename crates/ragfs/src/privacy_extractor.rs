//! Format-based discovery of credentials in text.
//! Positions are Unicode character offsets, suitable for Python string slicing.

use regex::{Captures, Error as RegexError, Regex, RegexBuilder};
use serde::Deserialize;
use std::cmp::Reverse;
use std::collections::{BinaryHeap, HashSet};

// Keep these budgets aligned with Python's PrivacySanitizer defaults.
const MAX_CONTENT_BYTES: usize = 16 * 1024 * 1024;
const MAX_MATCHES: usize = 10_000;

/// Resource limits for one compiled rule set, including the built-in detectors.
pub struct PrivacyRuleLimits {
    /// Maximum number of custom rules, excluding built-ins.
    pub max_rules: usize,
    /// Maximum UTF-8 bytes of custom-rule JSON.
    pub max_source_bytes: usize,
    /// Maximum combined compiled regex program budget.
    pub max_compiled_bytes: usize,
}

impl Default for PrivacyRuleLimits {
    fn default() -> Self {
        Self {
            // Custom rules are maintained as configuration, like Privacy fields.
            max_rules: 128,
            max_source_bytes: 1024 * 1024,
            // Split regex's default 10 MiB program limit across the whole rule set.
            max_compiled_bytes: 10 * 1024 * 1024,
        }
    }
}

#[derive(Deserialize)]
struct Config {
    detectors: Vec<DetectorConfig>,
    reference_pattern: String,
}

#[derive(Deserialize)]
struct DetectorConfig {
    id: String,
    pattern: String,
    #[serde(default)]
    reject_repeated: bool,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CustomRule {
    id: String,
    prefix: Option<String>,
    min_length: Option<usize>,
    regex: Option<String>,
}

struct Detector {
    id: String,
    pattern: Regex,
    reject_repeated: bool,
}

impl Detector {
    fn accepts(&self, captures: &Captures<'_>, never_mask: &HashSet<String>) -> bool {
        let value = captures.get(0).expect("whole detector match").as_str();
        if never_mask.contains(value)
            || ["EXAMPLE", "your_", "<", "${"]
                .iter()
                .any(|marker| value.contains(*marker))
            || (self.reject_repeated
                && has_repeated_chars(
                    captures
                        .name("privacy_body")
                        .map_or(value, |body| body.as_str()),
                ))
        {
            return false;
        }
        // Rust regex has no backreferences; PEM boundary labels must agree.
        self.id != "pem_private_key"
            || captures.name("begin_label").map(|m| m.as_str())
                == captures.name("end_label").map(|m| m.as_str())
    }
}

/// Compiled, immutable credential format detectors.
pub struct PrivacyExtractor {
    detectors: Vec<Detector>,
    reference: Regex,
}

#[derive(Clone, Debug)]
struct Hit {
    start: usize,
    end: usize,
    field: String,
}

impl PrivacyExtractor {
    /// Compile the built-in credential detectors once for reuse across messages.
    pub fn new() -> Result<Self, String> {
        Self::with_custom_rules("[]")
    }

    /// Compile built-in and validated user-defined detectors together.
    pub fn with_custom_rules(custom_rules_json: &str) -> Result<Self, String> {
        Self::with_limits(custom_rules_json, PrivacyRuleLimits::default())
    }

    /// Compile with deployment-specific resource limits.
    pub fn with_limits(custom_rules_json: &str, limits: PrivacyRuleLimits) -> Result<Self, String> {
        if limits.max_rules == 0 || limits.max_source_bytes == 0 || limits.max_compiled_bytes == 0 {
            return Err("Privacy rule limits must be positive".into());
        }
        if custom_rules_json.len() > limits.max_source_bytes {
            return Err(format!(
                "Privacy rule source limit exceeded ({} bytes)",
                limits.max_source_bytes
            ));
        }
        let rules: Vec<CustomRule> =
            serde_json::from_str(custom_rules_json).map_err(|error| error.to_string())?;
        if rules.len() > limits.max_rules {
            return Err(format!(
                "Privacy rule count limit exceeded ({})",
                limits.max_rules
            ));
        }
        let cfg: Config = serde_json::from_str(include_str!("privacy_extractor_rules.json"))
            .map_err(|error| error.to_string())?;
        let pattern_count = cfg
            .detectors
            .len()
            .checked_add(rules.len())
            .and_then(|count| count.checked_add(1))
            .ok_or_else(|| "Privacy rule count overflow".to_string())?;
        let pattern_size_limit = limits.max_compiled_bytes / pattern_count;
        let size_error = || {
            format!(
                "Privacy rule compiled size limit exceeded ({} bytes)",
                limits.max_compiled_bytes
            )
        };
        if pattern_size_limit == 0 {
            return Err(size_error());
        }
        let compile = |pattern: &str, id: &str| -> Result<Regex, String> {
            RegexBuilder::new(pattern)
                .size_limit(pattern_size_limit)
                .build()
                .map_err(|error| match error {
                    RegexError::CompiledTooBig(_) => size_error(),
                    _ => format!("Invalid Privacy rule {id}: {error}"),
                })
        };
        let mut detectors = cfg
            .detectors
            .into_iter()
            .map(|detector| {
                Ok(Detector {
                    pattern: compile(&detector.pattern, &detector.id)?,
                    id: detector.id,
                    reject_repeated: detector.reject_repeated,
                })
            })
            .collect::<Result<Vec<_>, String>>()?;
        let mut ids: HashSet<_> = detectors.iter().map(|d| d.id.clone()).collect();
        for rule in rules {
            if rule.id.is_empty()
                || !rule
                    .id
                    .chars()
                    .all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '-'))
                || !ids.insert(rule.id.clone())
            {
                return Err("Privacy rule IDs must be nonempty unique identifiers".into());
            }
            let pattern = match (rule.prefix, rule.min_length, rule.regex) {
                (Some(prefix), Some(min_length), None) if !prefix.is_empty() && min_length > 0 => {
                    format!(
                        "{}(?P<privacy_body>[A-Za-z0-9_-]{{{min_length},}})",
                        regex::escape(&prefix)
                    )
                }
                (None, None, Some(pattern)) if !pattern.is_empty() => pattern,
                _ => {
                    return Err(
                        "Privacy rules require prefix with positive min_length, or regex".into(),
                    );
                }
            };
            let pattern = compile(&pattern, &rule.id)?;
            if pattern.is_match("") {
                return Err("Privacy rules must not match an empty string".into());
            }
            detectors.push(Detector {
                id: rule.id,
                pattern,
                reject_repeated: true,
            });
        }
        let reference = compile(&cfg.reference_pattern, "reference")?;
        Ok(Self {
            detectors,
            reference,
        })
    }

    /// Return disjoint credential spans and detector IDs in character coordinates.
    pub fn extract(&self, s: &str) -> Result<Vec<(usize, usize, String)>, String> {
        self.extract_excluding(s, &HashSet::new())
    }

    /// Discover values while excluding exact user-declared literals.
    pub fn extract_excluding(
        &self,
        s: &str,
        never_mask: &HashSet<String>,
    ) -> Result<Vec<(usize, usize, String)>, String> {
        if s.len() > MAX_CONTENT_BYTES {
            return Err("Privacy extraction text limit exceeded".into());
        }
        let references: Vec<_> = self
            .reference
            .find_iter(s)
            .map(|m| (m.start(), m.end()))
            .collect();
        let mut streams: Vec<_> = self
            .detectors
            .iter()
            .map(|detector| detector.pattern.captures_iter(s).peekable())
            .collect();
        // Each detector contributes only its next match. Ties retain detector order.
        let mut pending = BinaryHeap::new();
        for (index, stream) in streams.iter_mut().enumerate() {
            if let Some(captures) = stream.peek() {
                let matched = captures.get(0).expect("whole detector match");
                pending.push(Reverse((matched.start(), Reverse(matched.end()), index)));
            }
        }
        let mut disjoint: Vec<Hit> = Vec::new();
        let mut reference_index = 0;
        let mut consumed = 0;
        while let Some(Reverse((start, Reverse(end), index))) = pending.pop() {
            let captures = streams[index].next().expect("pending detector match");
            while reference_index < references.len() && references[reference_index].1 <= start {
                reference_index += 1;
            }
            if start >= consumed
                && start < end
                && !s[..start].chars().next_back().is_some_and(token_char)
                && !s[end..].chars().next().is_some_and(token_char)
                && !(reference_index < references.len() && references[reference_index].0 < end)
                && self.detectors[index].accepts(&captures, never_mask)
            {
                if disjoint.len() >= MAX_MATCHES {
                    return Err("Privacy extraction match limit exceeded".into());
                }
                disjoint.push(Hit {
                    start,
                    end,
                    field: self.detectors[index].id.clone(),
                });
                consumed = end;
            }
            while streams[index]
                .peek()
                .is_some_and(|c| c.get(0).expect("whole detector match").start() < consumed)
            {
                streams[index].next();
            }
            if let Some(captures) = streams[index].peek() {
                let matched = captures.get(0).expect("whole detector match");
                pending.push(Reverse((matched.start(), Reverse(matched.end()), index)));
            }
        }
        Ok(to_char_offsets(s, disjoint))
    }
}

fn token_char(c: char) -> bool {
    c.is_alphanumeric() || matches!(c, '_' | '-')
}

fn has_repeated_chars(value: &str) -> bool {
    let mut previous = None;
    let mut run = 0;
    for c in value.chars() {
        run = if previous == Some(c) { run + 1 } else { 1 };
        if run >= 6 {
            return true;
        }
        previous = Some(c);
    }
    false
}

fn to_char_offsets(s: &str, hits: Vec<Hit>) -> Vec<(usize, usize, String)> {
    if s.is_ascii() {
        return hits
            .into_iter()
            .map(|h| (h.start, h.end, h.field))
            .collect();
    }
    let mut byte = 0;
    let mut count = 0;
    let mut out = Vec::with_capacity(hits.len());
    for h in hits {
        count += s[byte..h.start].chars().count();
        let start = count;
        count += s[h.start..h.end].chars().count();
        byte = h.end;
        out.push((start, count, h.field));
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[derive(Deserialize)]
    struct Examples {
        detectors: Vec<DetectorExamples>,
    }

    #[derive(Deserialize)]
    struct DetectorExamples {
        id: String,
        positive: Vec<Vec<String>>,
        negative: Vec<Vec<String>>,
    }

    #[test]
    fn detector_examples() {
        let extractor = PrivacyExtractor::new().unwrap();
        let examples: Examples =
            serde_json::from_str(include_str!("privacy_extractor_rules.json")).unwrap();
        for detector in examples.detectors {
            for fragments in detector.positive {
                let value = fragments.concat();
                assert_eq!(
                    extractor.extract(&value).unwrap(),
                    vec![(0, value.chars().count(), detector.id.clone())],
                    "positive example for {}: {value}",
                    detector.id,
                );
            }
            for fragments in detector.negative {
                let value = fragments.concat();
                assert!(
                    extractor.extract(&value).unwrap().is_empty(),
                    "negative example for {}: {value}",
                    detector.id,
                );
            }
        }
    }

    #[test]
    fn unicode_offsets_and_references_preserve_complete_values() {
        let extractor = PrivacyExtractor::new().unwrap();
        let secret = ["AKLT", "aB3dE5gH7jK9mN2pQ4sT6vW8yZ0cF1rU"].concat();
        let pem = [
            "-----BEGIN PRIVATE KEY-----\n",
            "YWJjZGVmZw==\n",
            "-----END PRIVATE KEY-----",
        ]
        .concat();
        let reference = ["{{ov_privacy:skill:", &secret, ":key}}"].concat();
        let text = format!("标题🙂 \"{secret}\"；{reference}\n{pem}");
        let chars: Vec<_> = text.chars().collect();
        let values: Vec<String> = extractor
            .extract(&text)
            .unwrap()
            .iter()
            .map(|(start, end, _)| chars[*start..*end].iter().collect())
            .collect();
        assert_eq!(values, [secret, pem]);
    }

    #[test]
    fn token_boundaries_reject_partial_credentials() {
        let extractor = PrivacyExtractor::new().unwrap();
        let secret = ["AKLT", "aB3dE5gH7jK9mN2pQ4sT6vW8yZ0cF1rU"].concat();
        for prefix in ['a', '9', '_', '-', '中', '²'] {
            let text = format!("{prefix}{secret}");
            assert!(extractor.extract(&text).unwrap().is_empty(), "{text}");
        }
        for suffix in ['_', '-', '中', '²'] {
            let text = format!("{secret}{suffix}");
            assert!(extractor.extract(&text).unwrap().is_empty(), "{text}");
        }
    }

    #[test]
    fn extraction_limits_are_enforced() {
        let extractor = PrivacyExtractor::new().unwrap();
        for unit in ["x", "中", "🙂"] {
            let mut text = unit.repeat(MAX_CONTENT_BYTES / unit.len());
            text.push_str(&" ".repeat(MAX_CONTENT_BYTES % unit.len()));
            assert!(extractor.extract(&text).unwrap().is_empty());
            text.push(' ');
            assert!(extractor.extract(&text).unwrap_err().contains("text limit"));
        }
        let secret = ["AKLT", "aB3dE5gH7jK9mN2pQ4sT6vW8yZ0cF1rU", " "].concat();
        assert_eq!(
            extractor
                .extract(&secret.repeat(MAX_MATCHES))
                .unwrap()
                .len(),
            MAX_MATCHES
        );
        assert!(extractor
            .extract(&secret.repeat(MAX_MATCHES + 1))
            .unwrap_err()
            .contains("match limit"));
    }
    #[test]
    fn custom_rules_share_filters_exclusions_and_reference_avoidance() {
        let extractor = PrivacyExtractor::with_custom_rules(
            r#"[
            {"id":"gateway","prefix":"acme.gw_","min_length":12},
            {"id":"ticket","regex":"acme_tk_[A-Za-z0-9]{12}"}
        ]"#,
        )
        .unwrap();
        let first = "acme.gw_aB3dE5gH7jK9";
        let second = "acme_tk_xY7zA9bC2dE4";
        let text = format!("🙂 {first} {second} acme.gw_AAAAAAbcdefg {{[[unused]]}} {{{{ov_privacy:skill:{first}:key}}}}");
        let excluded = HashSet::from([first.to_string()]);
        let matches = extractor.extract_excluding(&text, &excluded).unwrap();
        assert_eq!(matches.len(), 1);
        let (start, end, id) = &matches[0];
        assert_eq!(id, "ticket");
        assert_eq!(
            text.chars()
                .skip(*start)
                .take(end - start)
                .collect::<String>(),
            second
        );
        assert!(extractor.extract(&text).unwrap().len() == 2);
    }

    #[test]
    fn custom_rules_reject_invalid_configuration() {
        for rules in [
            r#"[{"id":"x","regex":"("}]"#,
            r#"[{"id":"x","regex":"a*"}]"#,
            r#"[{"id":"x","regex":"(?=secret)"}]"#,
            r#"[{"id":"x","prefix":"abc","min_length":0}]"#,
            r#"[{"id":"x","prefix":"abc","min_length":12,"regex":"secret"}]"#,
            r#"[{"id":"x","prefix":"abc"}]"#,
            r#"[{"id":"volcengine_ak","regex":"secret"}]"#,
            r#"[{"id":"x","regex":"abc"},{"id":"x","regex":"def"}]"#,
        ] {
            assert!(
                PrivacyExtractor::with_custom_rules(rules).is_err(),
                "{rules}"
            );
        }
        let oversized = serde_json::json!([{"id":"x", "regex":"[a-zA-Z0-9]{1000000000}"}]);
        assert!(PrivacyExtractor::with_custom_rules(&oversized.to_string()).is_err());
    }

    #[test]
    fn rule_resource_budgets_are_enforced() {
        let limits = PrivacyRuleLimits::default();
        let rules: Vec<_> = (0..=limits.max_rules)
            .map(|n| serde_json::json!({"id":format!("rule{n}"),"regex":"token_[a-z0-9]{12}"}))
            .collect();
        assert!(
            PrivacyExtractor::with_custom_rules(&serde_json::to_string(&rules).unwrap())
                .err()
                .unwrap()
                .contains("count limit")
        );

        let source = r#"[{"id":"token","regex":"token_[a-z0-9]{12}"}]"#;
        assert!(PrivacyExtractor::with_limits(
            source,
            PrivacyRuleLimits {
                max_source_bytes: source.len() - 1,
                ..PrivacyRuleLimits::default()
            }
        )
        .err()
        .unwrap()
        .contains("source limit"));

        // Each pattern fits regex's former per-pattern limit, but the set does not.
        let costly =
            r#"[{"id":"one","regex":"[a-z0-9]{100000}"},{"id":"two","regex":"[a-z0-9]{100000}"}]"#;
        assert!(PrivacyExtractor::with_custom_rules(costly)
            .err()
            .unwrap()
            .contains("compiled size limit"));
    }
    #[test]
    fn custom_rules_accept_long_values_and_skip_zero_width_matches() {
        let prefix = "汉字".repeat(300);
        let id = "gateway_".repeat(10);
        let body = "aB3dE5gH7jK9".repeat(350);
        let rules = serde_json::json!([
            {"id":id, "prefix":prefix,"min_length":4097},
            {"id":"regex", "regex":format!("{prefix}[A-Za-z0-9_-]+")},
        ]);
        let extractor = PrivacyExtractor::with_custom_rules(&rules.to_string()).unwrap();
        let text = format!("{prefix}{body}");
        assert_eq!(
            extractor.extract(&text).unwrap(),
            vec![(0, text.chars().count(), id)]
        );
        let zero_width =
            PrivacyExtractor::with_custom_rules(r#"[{"id":"boundary","regex":"\\b"}]"#).unwrap();
        assert!(zero_width.extract("ordinary word").unwrap().is_empty());
    }

    #[test]
    fn overlapping_detectors_only_count_final_results() {
        let rules: Vec<_> = (0..64)
            .map(|n| serde_json::json!({"id":format!("rule{n}"),"prefix":"corp_","min_length":20}))
            .collect();
        let extractor =
            PrivacyExtractor::with_custom_rules(&serde_json::to_string(&rules).unwrap()).unwrap();
        let text = "corp_A7b9C2d4E6f8G0h1J3k5 ".repeat(313);
        let hits = extractor.extract(&text).unwrap();
        assert_eq!(hits.len(), 313);
        assert!(hits.iter().all(|(_, _, id)| id == "rule0"));
    }
}
