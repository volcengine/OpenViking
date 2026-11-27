//! Format-based discovery of credentials in text.
//! Positions are Unicode character offsets, suitable for Python string slicing.

use regex::{Captures, Error as RegexError, Regex, RegexBuilder};
use serde::Deserialize;
use std::cmp::Reverse;
use std::collections::{BinaryHeap, HashSet};
use std::sync::OnceLock;

// Keep these budgets aligned with Python's PrivacySanitizer defaults.
const MAX_CONTENT_BYTES: usize = 16 * 1024 * 1024;
const MAX_MATCHES: usize = 10_000;

/// Resource limits for one set of custom rules. Built-in detectors have their own budget.
pub struct PrivacyRuleLimits {
    /// Maximum number of custom rules.
    pub max_rules: usize,
    /// Maximum UTF-8 bytes of custom-rule JSON.
    pub max_source_bytes: usize,
    /// Combined program budget, weighted by capture count to bound fallback workspaces.
    /// Also bounds each search thread's lazy DFA caches, split across patterns.
    pub max_compiled_bytes: usize,
}

/// Program budget for the built-in detectors, compiled once per process.
const BUILTIN_COMPILED_BYTES: usize = 16 * 1024 * 1024;
/// Bound on one search thread's lazy DFA caches for the built-in detectors.
const BUILTIN_DFA_CACHE_BYTES: usize = 32 * 1024 * 1024;

/// Smallest program budget charged to one pattern.
const MIN_PATTERN_PROGRAM_BYTES: usize = 4 * 1024;
/// Forward, reverse and reverse-inner lazy DFAs may each keep a cache.
const LAZY_DFA_CACHES_PER_PATTERN: usize = 3;
/// Conservative reservation for NFAs and auxiliary compiled structures.
const RETAINED_BYTES_PER_PROGRAM_BYTE: usize = 5;
/// regex-automata 0.4's default one-pass DFA limit is independent of size_limit.
const ONEPASS_DFA_BUDGET_BYTES: usize = 1024 * 1024;

impl Default for PrivacyRuleLimits {
    fn default() -> Self {
        Self {
            // Custom rules are maintained as configuration, like Privacy fields.
            max_rules: 128,
            max_source_bytes: 1024 * 1024,
            // Regex's default program limit, shared by the custom rules.
            max_compiled_bytes: 10 * 1024 * 1024,
        }
    }
}

#[derive(Deserialize)]
struct Config {
    detectors: Vec<DetectorConfig>,
    reference_pattern: String,
}

/// Detectors generated from a public rule set; see the file's `source` entry.
#[derive(Deserialize)]
struct ImportedConfig {
    detectors: Vec<DetectorConfig>,
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

#[derive(Clone)]
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

/// Compiles patterns against one shared program budget.
struct Compiler {
    limit: usize,
    remaining: usize,
    cache_limit: usize,
    program_bytes: usize,
}

impl Compiler {
    fn new(limit: usize, cache_bytes: usize, pattern_count: usize) -> Self {
        Self {
            limit,
            remaining: limit,
            // Each search thread keeps its own lazy DFA caches for every pattern.
            cache_limit: cache_bytes / pattern_count.max(1) / LAZY_DFA_CACHES_PER_PATTERN,
            program_bytes: 0,
        }
    }

    fn compile(&mut self, pattern: &str, id: &str) -> Result<Regex, String> {
        // Grow the allowance geometrically instead of charging every pattern the full budget.
        let mut budget = MIN_PATTERN_PROGRAM_BYTES.min(self.remaining);
        loop {
            match RegexBuilder::new(pattern)
                .size_limit(budget)
                .dfa_size_limit(self.cache_limit)
                .build()
            {
                Ok(regex) => {
                    // Capture tables scale as NFA states × capture slots, even for find().
                    // Charge before any search can allocate these deferred workspaces.
                    self.remaining = budget
                        .checked_mul(regex.captures_len())
                        .and_then(|charge| self.remaining.checked_sub(charge))
                        .ok_or_else(|| {
                            format!(
                                "Privacy rule {id} exceeds the capture workspace budget \
                                 ({} weighted program bytes per rule set)",
                                self.limit
                            )
                        })?;
                    self.program_bytes += budget;
                    return Ok(regex);
                }
                Err(RegexError::CompiledTooBig(_)) if budget < self.remaining => {
                    budget = budget.saturating_mul(2).min(self.remaining);
                }
                Err(RegexError::CompiledTooBig(_)) => {
                    return Err(format!(
                        "Privacy rule {id} exceeds the remaining compiled size budget \
                         ({} bytes per rule set)",
                        self.limit
                    ));
                }
                Err(error) => return Err(format!("Invalid Privacy rule {id}: {error}")),
            }
        }
    }
}

struct Builtins {
    detectors: Vec<Detector>,
    reference: Regex,
}

/// Built-in detectors are identical for every rule set, so they are compiled once and
/// each extractor clones the handles, which share the compiled programs.
fn builtins() -> Result<&'static Builtins, String> {
    static BUILTINS: OnceLock<Result<Builtins, String>> = OnceLock::new();
    BUILTINS
        .get_or_init(|| {
            let cfg: Config = serde_json::from_str(include_str!("privacy_extractor_rules.json"))
                .map_err(|error| error.to_string())?;
            let imported: ImportedConfig =
                serde_json::from_str(include_str!("privacy_extractor_rules_betterleaks.json"))
                    .map_err(|error| error.to_string())?;
            let configs: Vec<_> = cfg
                .detectors
                .into_iter()
                .chain(imported.detectors)
                .collect();
            let mut compiler = Compiler::new(
                BUILTIN_COMPILED_BYTES,
                BUILTIN_DFA_CACHE_BYTES,
                configs.len() + 1,
            );
            let detectors = configs
                .into_iter()
                .map(|detector| {
                    Ok(Detector {
                        pattern: compiler.compile(&detector.pattern, &detector.id)?,
                        id: detector.id,
                        reject_repeated: detector.reject_repeated,
                    })
                })
                .collect::<Result<Vec<_>, String>>()?;
            let reference = compiler.compile(&cfg.reference_pattern, "reference")?;
            Ok(Builtins {
                detectors,
                reference,
            })
        })
        .as_ref()
        .map_err(Clone::clone)
}

/// Compiled, immutable credential format detectors.
#[derive(Clone)]
pub struct PrivacyExtractor {
    detectors: Vec<Detector>,
    reference: Regex,
    reserved_bytes: usize,
}

#[derive(Clone, Debug)]
struct Hit {
    start: usize,
    end: usize,
    field: String,
}

impl PrivacyExtractor {
    /// Build an extractor with only the built-in credential detectors.
    pub fn new() -> Result<Self, String> {
        Self::with_custom_rules("[]")
    }

    /// Combine the built-in detectors with validated user-defined ones.
    pub fn with_custom_rules(custom_rules_json: &str) -> Result<Self, String> {
        Self::with_limits(custom_rules_json, PrivacyRuleLimits::default())
    }

    /// Compile custom rules with deployment-specific resource limits.
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
        let builtins = builtins()?;
        let mut detectors = builtins.detectors.clone();
        let mut compiler = Compiler::new(
            limits.max_compiled_bytes,
            limits.max_compiled_bytes,
            rules.len(),
        );
        let custom_count = rules.len();
        // Stored settings are not revalidated when detectors are added to the built-in
        // set, so custom IDs only need to be unique among themselves.
        let mut ids = HashSet::new();
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
            let pattern = compiler.compile(&pattern, &rule.id)?;
            if pattern.clone().is_match("") {
                return Err("Privacy rules must not match an empty string".into());
            }
            detectors.push(Detector {
                id: rule.id,
                pattern,
                reject_repeated: true,
            });
        }
        // Built-in programs are shared by every extractor and are not charged here.
        let reserved_bytes = compiler.program_bytes * RETAINED_BYTES_PER_PROGRAM_BYTE
            + ONEPASS_DFA_BUDGET_BYTES * custom_count;
        Ok(Self {
            detectors,
            reference: builtins.reference.clone(),
            reserved_bytes,
        })
    }

    /// Conservative reservation for this extractor's custom programs, excluding the shared
    /// built-in programs and temporary search caches.
    pub fn reserved_bytes(&self) -> usize {
        self.reserved_bytes
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

/// Credential formats are ASCII, so text in other scripts next to a token is a boundary.
fn token_char(c: char) -> bool {
    c.is_ascii_alphanumeric() || matches!(c, '_' | '-')
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
    fn imported_detectors_match_whole_tokens_in_context() {
        let extractor = PrivacyExtractor::new().unwrap();
        // Built-in programs are shared, so the default extractor reserves nothing of its own.
        assert_eq!(extractor.reserved_bytes(), 0);
        let body = "aB3dE5gH7jK9mN2pQ4sT6vW8yZ0cF1rU";
        let hex = "0a1b2c3d4e5f60718293a4b5c6d7e8f9";
        for (id, fragments) in [
            ("gcp-api-key", vec!["AIza", body, "aB3"]),
            ("gitlab-pat", vec!["glpat-", &body[..20]]),
            ("groq-api-key", vec!["gsk_", body, &body[..20]]),
            (
                "huggingface-access-token",
                vec!["hf_", "aBcDeFgHiJkLmNoPqRsTuVwXyZaBcDeFgH"],
            ),
            ("npm-access-token", vec!["npm_", body, &body[..4]]),
            ("openrouter-api-key", vec!["sk-or-v1-", hex, hex]),
            ("stripe-access-token", vec!["sk_live_", &body[..24]]),
            ("tavily-api-key", vec!["tvly-", body]),
        ] {
            let value = fragments.concat();
            for (prefix, suffix) in [
                ("", ""),
                ("key=\"", "\"&m=1"),
                ("令牌：", "，"),
                ("密钥是", "请保存"),
                ("(", ")."),
            ] {
                let text = format!("{prefix}{value}{suffix}");
                let start = prefix.chars().count();
                assert_eq!(
                    extractor.extract(&text).unwrap(),
                    vec![(start, start + value.chars().count(), id.to_string())],
                    "imported detector {id}: {text}",
                );
            }
            // A longer token is never reported as a shorter match plus a remainder.
            let longer = format!("{value}aB3");
            assert!(extractor
                .extract(&longer)
                .unwrap()
                .iter()
                .all(|(start, end, _)| (*start, *end) == (0, longer.len())));
        }
    }

    #[test]
    fn imported_detectors_need_no_capture_workspace() {
        let imported: ImportedConfig =
            serde_json::from_str(include_str!("privacy_extractor_rules_betterleaks.json")).unwrap();
        assert!(!imported.detectors.is_empty());
        for detector in imported.detectors {
            let pattern = Regex::new(&detector.pattern).unwrap();
            assert_eq!(pattern.captures_len(), 1, "{}", detector.id);
            assert!(!pattern.is_match(""), "{}", detector.id);
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
        // A custom ID may repeat a built-in one, but not another custom ID.
        assert!(PrivacyExtractor::with_custom_rules(
            r#"[{"id":"github","regex":"acme_tk_[A-Za-z0-9]{12}"}]"#
        )
        .is_ok());
        assert!(PrivacyExtractor::with_custom_rules(
            r#"[{"id":"a","regex":"acme_a_[a-z]{12}"},{"id":"a","regex":"acme_b_[a-z]{12}"}]"#
        )
        .is_err());
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
            .contains("Privacy rule two exceeds the remaining compiled size budget"));
        let captures = serde_json::json!([{
            "id": "captures", "regex": "([a-z0-9]{200})".repeat(100)
        }]);
        assert!(PrivacyExtractor::with_custom_rules(&captures.to_string())
            .err()
            .unwrap()
            .contains("capture workspace budget"));
    }
}
