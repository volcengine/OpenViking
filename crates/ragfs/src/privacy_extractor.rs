//! Format-based discovery of credentials in text.
//! Positions are Unicode character offsets, suitable for Python string slicing.

use regex::Regex;
use serde::Deserialize;
use std::cmp::Reverse;

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

struct Detector {
    id: String,
    pattern: Regex,
    reject_repeated: bool,
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
        let cfg: Config = serde_json::from_str(include_str!("privacy_extractor_rules.json"))
            .map_err(|error| error.to_string())?;
        let detectors = cfg
            .detectors
            .into_iter()
            .map(|detector| {
                Ok(Detector {
                    pattern: Regex::new(&detector.pattern).map_err(|error| error.to_string())?,
                    id: detector.id,
                    reject_repeated: detector.reject_repeated,
                })
            })
            .collect::<Result<Vec<_>, String>>()?;
        Ok(Self {
            detectors,
            reference: Regex::new(&cfg.reference_pattern).map_err(|error| error.to_string())?,
        })
    }

    /// Return disjoint credential spans and detector IDs in character coordinates.
    pub fn extract(&self, s: &str) -> Result<Vec<(usize, usize, String)>, String> {
        if s.len() > 4 * 1024 * 1024 && s.chars().count() > 4 * 1024 * 1024 {
            return Err("Privacy extraction text limit exceeded".into());
        }
        let references: Vec<_> = self
            .reference
            .find_iter(s)
            .map(|m| (m.start(), m.end()))
            .collect();
        let mut hits = Vec::new();
        for detector in &self.detectors {
            let mut reference_index = 0;
            for captures in detector.pattern.captures_iter(s) {
                let matched = captures.get(0).expect("whole detector match");
                let start = matched.start();
                let end = matched.end();
                if s[..start].chars().next_back().is_some_and(token_char)
                    || s[end..].chars().next().is_some_and(token_char)
                {
                    continue;
                }
                while reference_index < references.len() && references[reference_index].1 <= start {
                    reference_index += 1;
                }
                if reference_index < references.len() && references[reference_index].0 < end {
                    continue;
                }
                let value = matched.as_str();
                if ["EXAMPLE", "your_", "<", "${"]
                    .iter()
                    .any(|marker| value.contains(*marker))
                    || (detector.reject_repeated && has_repeated_chars(value))
                {
                    continue;
                }
                // Rust regex has no backreferences; PEM boundary labels must agree.
                if detector.id == "pem_private_key"
                    && captures.name("begin_label").map(|m| m.as_str())
                        != captures.name("end_label").map(|m| m.as_str())
                {
                    continue;
                }
                if hits.len() >= 100_000 {
                    return Err("Privacy extraction match limit exceeded".into());
                }
                hits.push(Hit {
                    start,
                    end,
                    field: detector.id.clone(),
                });
            }
        }
        hits.sort_by_key(|hit| (hit.start, Reverse(hit.end)));
        let mut disjoint: Vec<Hit> = Vec::new();
        for hit in hits {
            if disjoint.last().is_none_or(|last| hit.start >= last.end) {
                disjoint.push(hit);
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
        assert!(extractor
            .extract(&"x".repeat(4 * 1024 * 1024 + 1))
            .unwrap_err()
            .contains("text limit"));
        let secret = ["AKIA", "BCDEFGHJKLMN2345", " "].concat();
        assert!(extractor
            .extract(&secret.repeat(100_001))
            .unwrap_err()
            .contains("match limit"));
    }
}
