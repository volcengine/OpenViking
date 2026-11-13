//! Fixed-rule discovery of credentials in text and named fields.
//! Positions are Unicode character offsets, suitable for Python string slicing.

use regex::Regex;
use serde::Deserialize;
use std::collections::{HashMap, HashSet};

#[derive(Deserialize)]
struct Config {
    normalized_names: HashSet<String>,
    suffixes: Vec<String>,
    token_hints: Vec<String>,
    token_pattern: String,
    assignment_pattern: String,
    cli_pattern: String,
    auth_pattern: String,
    scheme_pattern: String,
    bracket_pattern: String,
    reference_pattern: String,
}

/// Compiled, immutable fixed rules used for Session credential discovery.
pub struct PrivacyExtractor {
    cfg: Config,
    token: Regex,
    assignment: Regex,
    cli: Regex,
    auth: Regex,
    scheme: Regex,
    bracket: Regex,
    reference: Regex,
    bold: Regex,
    code_delimiter: Regex,
    unicode_alphanumeric: Regex,
}

#[derive(Clone, Debug)]
struct Hit {
    start: usize,
    end: usize,
    field: String,
}

impl PrivacyExtractor {
    /// Compile the built-in credential rules once for reuse across messages.
    pub fn new() -> Result<Self, String> {
        let cfg: Config = serde_json::from_str(include_str!("privacy_extractor_rules.json"))
            .map_err(|error| error.to_string())?;
        Ok(Self {
            token: Regex::new(&cfg.token_pattern).map_err(|error| error.to_string())?,
            assignment: Regex::new(&format!("(?i){}", cfg.assignment_pattern))
                .map_err(|error| error.to_string())?,
            cli: Regex::new(&cfg.cli_pattern).map_err(|error| error.to_string())?,
            auth: Regex::new(&cfg.auth_pattern).map_err(|error| error.to_string())?,
            scheme: Regex::new(&cfg.scheme_pattern).map_err(|error| error.to_string())?,
            bracket: Regex::new(&cfg.bracket_pattern).map_err(|error| error.to_string())?,
            reference: Regex::new(&cfg.reference_pattern).map_err(|error| error.to_string())?,
            bold: Regex::new(r"(?:^|[\s(>=:])\*\*(?P<text>[^\s*](?:[^\r\n]*?[^\s*])?)\*\*")
                .map_err(|error| error.to_string())?,
            code_delimiter: Regex::new(r"`+|~{3,}").map_err(|error| error.to_string())?,
            unicode_alphanumeric: Regex::new(r"^[\p{L}\p{N}]$")
                .map_err(|error| error.to_string())?,
            cfg,
        })
    }

    fn word(&self, c: char) -> bool {
        if c.is_ascii() {
            return c.is_ascii_alphanumeric() || c == '_';
        }
        // General categories Letter and Number match Python's alphanumeric
        // definition. Rust's Alphabetic property also includes combining marks.
        self.unicode_alphanumeric
            .is_match(c.encode_utf8(&mut [0; 4]))
    }

    fn boundary(&self, s: &str, pos: usize) -> bool {
        s[..pos]
            .chars()
            .next_back()
            .map_or(true, |c| c != '-' && !self.word(c))
    }

    /// Whether a structured input key denotes a complete sensitive value.
    pub fn is_sensitive_key(&self, key: &str) -> bool {
        let lower = key.to_lowercase().replace('-', "_");
        self.cfg
            .suffixes
            .iter()
            .any(|suffix| lower.ends_with(suffix))
            || self.cfg.normalized_names.contains(&normalize_key(&lower))
    }

    fn env_end(&self, s: &str, p: usize) -> Option<usize> {
        let tail = &s[p..];
        if tail.starts_with("${") {
            let mut n = 0;
            for (i, c) in tail[2..].char_indices() {
                if c == '}' {
                    return (n > 0).then_some(p + 2 + i + 1);
                }
                if c == '{' || space(c) {
                    return None;
                }
                n += 1;
            }
        } else if tail.starts_with('$') {
            let mut chars = tail[1..].char_indices();
            let (_, first) = chars.next()?;
            if !first.is_ascii_alphabetic() && first != '_' {
                return None;
            }
            let mut end = p + 1 + first.len_utf8();
            for (i, c) in chars {
                if !self.word(c) {
                    break;
                }
                end = p + 1 + i + c.len_utf8();
            }
            return Some(end);
        } else if tail.starts_with('<') {
            for (i, c) in tail[1..].char_indices() {
                if c == '<' {
                    return None;
                }
                if c == '>' {
                    return (i > 0).then_some(p + 1 + i + 1);
                }
            }
        }
        None
    }

    fn full_reference(&self, s: &str) -> bool {
        self.reference
            .find(s)
            .is_some_and(|m| m.start() == 0 && m.end() == s.len())
    }

    fn add(
        &self,
        hits: &mut Vec<Hit>,
        s: &str,
        start: usize,
        end: usize,
        key: &str,
        structured: bool,
    ) -> Result<(), String> {
        let value = &s[start..end];
        if value.is_empty()
            || (!structured && (value == "|" || value == ">"))
            || self.full_reference(value)
            || self.env_end(value, 0) == Some(value.len())
        {
            return Ok(());
        }
        if hits.len() >= 100_000 {
            return Err("Privacy extraction match limit exceeded".into());
        }
        let mut field = String::new();
        let mut previous = None;
        for c in key.chars() {
            if previous.is_some_and(|v: char| v.is_ascii_lowercase() || v.is_ascii_digit())
                && c.is_ascii_uppercase()
            {
                field.push('_');
            }
            if c == '-' || c == ' ' {
                field.push('_');
            } else {
                field.extend(c.to_lowercase());
            }
            previous = Some(c);
        }
        hits.push(Hit { start, end, field });
        Ok(())
    }

    fn mask_markdown_markers(&self, s: &str) -> Option<String> {
        let delimiters: Vec<_> = self.code_delimiter.find_iter(s).collect();
        // Index equal-length runs once so unmatched inline delimiters stay linear-time.
        let mut next = vec![None; delimiters.len()];
        let mut following = HashMap::new();
        for (i, delimiter) in delimiters.iter().enumerate().rev() {
            let key = (delimiter.as_str().as_bytes()[0], delimiter.len());
            next[i] = following.insert(key, i);
        }
        let mut masked = None;
        let mut cursor = 0;
        let mut i = 0;
        while i < delimiters.len() {
            let open = delimiters[i];
            let marker = open.as_str().as_bytes()[0];
            let fenced = open.len() >= 3
                && fence_line_start(s, open.start())
                && (marker != b'`' || !s[open.end()..].split('\n').next().unwrap().contains('`'));
            let close = if fenced {
                (i + 1..delimiters.len()).find(|&j| {
                    let m = delimiters[j];
                    m.as_str().as_bytes()[0] == marker
                        && m.len() >= open.len()
                        && fence_line_start(s, m.start())
                        && s[m.end()..].split('\n').next().unwrap().trim().is_empty()
                })
            } else if marker == b'`' {
                next[i]
            } else {
                None
            };
            if close.is_none() && !fenced {
                i += 1;
                continue;
            }
            self.mask_bold_markers(s, cursor, open.start(), &mut masked);
            let bytes = masked.get_or_insert_with(|| s.as_bytes().to_vec());
            bytes[open.start()..open.end()].fill(b' ');
            cursor = if let Some(j) = close {
                let end = delimiters[j];
                bytes[end.start()..end.end()].fill(b' ');
                i = j + 1;
                end.end()
            } else {
                i = delimiters.len();
                s.len()
            };
        }
        self.mask_bold_markers(s, cursor, s.len(), &mut masked);
        masked.map(|bytes| String::from_utf8(bytes).expect("ASCII replacement preserves UTF-8"))
    }

    fn mask_bold_markers(&self, s: &str, start: usize, end: usize, masked: &mut Option<Vec<u8>>) {
        // Emphasis inside code is literal; only scan the gaps between code regions.
        for cap in self.bold.captures_iter(&s[start..end]) {
            let text = cap.name("text").unwrap();
            let bytes = masked.get_or_insert_with(|| s.as_bytes().to_vec());
            bytes[start + text.start() - 2..start + text.start()].fill(b' ');
            bytes[start + text.end()..start + text.end() + 2].fill(b' ');
        }
    }

    fn value_span(&self, s: &str, mut start: usize) -> (usize, usize) {
        if s[start..].starts_with("${") {
            if let Some(end) = self.env_end(s, start) {
                return (start, end);
            }
        }
        let mut end = start;
        if let Some(quote @ ('\'' | '"')) = char_at(s, start) {
            start += 1;
            end = start;
            while let Some(c) = char_at(s, end) {
                if c == '\\' {
                    end = advance(s, end, 2);
                } else if c == quote {
                    break;
                } else {
                    end += c.len_utf8();
                }
            }
        } else {
            while let Some(c) = char_at(s, end) {
                if space(c) || ",;，；\"'<>}{]".contains(c) {
                    break;
                }
                if c == '[' {
                    match self.bracket.find_at(s, end) {
                        Some(m) if m.start() == end => end = m.end(),
                        _ => break,
                    }
                } else {
                    end += c.len_utf8();
                }
            }
        }
        (start, end)
    }

    fn shell_span(&self, s: &str, start: usize) -> (usize, usize) {
        let encoded = s[start..].starts_with("\\\"");
        let mut quote = if encoded { Some('"') } else { None };
        let width = if encoded { 2 } else { 1 };
        let mut end = start + if encoded { 2 } else { 0 };
        let mut first_quote_end = None;
        while let Some(c) = char_at(s, end) {
            if encoded && c == '\\' && quote != Some('\'') {
                let mut run = end + 1;
                while char_at(s, run) == Some('\\') {
                    run += 1;
                }
                if run == end + 1 && char_at(s, run) == Some('"') {
                    if quote.is_some() {
                        quote = None;
                        if first_quote_end.is_none() {
                            first_quote_end = Some(end);
                        }
                    } else {
                        quote = Some('"');
                    }
                    end = run + 1;
                } else {
                    end = advance(s, run, 1);
                }
            } else if c == '\\' && quote != Some('\'') {
                end = advance(s, end, 2);
            } else if let Some(q) = quote {
                if c == q && (!encoded || q == '\'') {
                    quote = None;
                    if first_quote_end.is_none() {
                        first_quote_end = Some(end);
                    }
                }
                end += c.len_utf8();
            } else if c == '\'' || c == '"' {
                if encoded && c == '"' {
                    break;
                }
                quote = Some(c);
                end += 1;
            } else if space(c) || ";&|()<>".contains(c) {
                break;
            } else {
                end += c.len_utf8();
            }
        }
        if start < end
            && (encoded || matches!(char_at(s, start), Some('\'' | '"')))
            && first_quote_end == end.checked_sub(width)
        {
            (start + width, end - width)
        } else {
            (start, end)
        }
    }

    fn scheme_end(&self, s: &str, pos: usize, end: usize) -> Option<usize> {
        if pos > 0 && self.word(s[..pos].chars().next_back()?) {
            return None;
        }
        self.scheme
            .find_at(&s[..end], pos)
            .filter(|m| m.start() == pos)
            .map(|m| m.end())
    }

    /// Return disjoint credential spans in Unicode character coordinates.
    pub fn extract(
        &self,
        s: &str,
        field_name: Option<&str>,
    ) -> Result<Vec<(usize, usize, String)>, String> {
        if s.len() > 4 * 1024 * 1024 && s.chars().count() > 4 * 1024 * 1024 {
            return Err("Privacy extraction text limit exceeded".into());
        }
        let mut hits = Vec::new();
        if let Some(field) = field_name.filter(|v| self.is_sensitive_key(v)) {
            let mut start = 0;
            if matches!(
                normalize_key(&field.to_lowercase()).as_str(),
                "authorization" | "proxyauthorization"
            ) {
                start = self.scheme_end(s, 0, s.len()).unwrap_or(0);
            }
            self.add(&mut hits, s, start, s.len(), field, true)?;
            return Ok(to_char_offsets(s, hits));
        }
        // Replace Markdown delimiters with whitespace, retaining original offsets and values.
        let source = s;
        let scan_text = self.mask_markdown_markers(s);
        let s = scan_text.as_deref().unwrap_or(s);
        if s.contains(['=', ':', '：']) {
            let mut cursor = 0;
            let mut covered = 0;
            while cursor < s.len() {
                let Some(cap) = self.assignment.captures_at(s, cursor) else {
                    break;
                };
                let m = cap.get(0).unwrap();
                if !self.boundary(s, m.start()) {
                    cursor = advance(s, m.start(), 1);
                    continue;
                }
                cursor = m.end();
                let key = cap.name("key").unwrap().as_str();
                if m.start() < covered || !self.is_sensitive_key(key) {
                    continue;
                }
                let start = m.end();
                let auth_start = start + usize::from(matches!(char_at(s, start), Some('\'' | '"')));
                let start = if matches!(
                    normalize_key(&key.to_lowercase()).as_str(),
                    "authorization" | "proxyauthorization"
                ) {
                    self.scheme_end(s, auth_start, s.len()).unwrap_or(start)
                } else {
                    start
                };
                let (start, end) = self.value_span(s, start);
                self.add(&mut hits, source, start, end, key, false)?;
                covered = end;
            }
        }
        if s.contains("--") || s.contains("-u") || s.contains("-U") {
            let mut cursor = 0;
            let mut covered = 0;
            while cursor < s.len() {
                let Some(cap) = self.cli.captures_at(s, cursor) else {
                    break;
                };
                let m = cap.get(0).unwrap();
                if !self.boundary(s, m.start()) {
                    cursor = advance(s, m.start(), 1);
                    continue;
                }
                cursor = m.end();
                if cap.name("short").is_some() && m.end() == m.start() + 2 {
                    if char_at(s, m.end()).map_or(true, |c| space(c) || c == '=') {
                        continue;
                    }
                }
                if m.start() < covered {
                    continue;
                }
                let mut key = cap.name("key").map_or("user", |v| v.as_str());
                let normalized = normalize_key(&key.to_lowercase());
                let combined = normalized == "user" || normalized == "proxyuser";
                if normalized == "oauth2bearer" {
                    key = "access_token";
                }
                if !combined && !self.is_sensitive_key(key) {
                    continue;
                }
                let mut start = m.end();
                loop {
                    let count = if s[start..].starts_with("\\\r\n") {
                        3
                    } else if s[start..].starts_with("\\\n") {
                        2
                    } else {
                        break;
                    };
                    start += count;
                    while matches!(char_at(s, start), Some(' ' | '\t')) {
                        start += 1;
                    }
                }
                if s[start..].starts_with("--") {
                    continue;
                }
                let (mut start, end) = self.shell_span(s, start);
                if end > start && (!combined || s[start..end].contains(':')) {
                    covered = end;
                    if normalized == "authorization" || normalized == "proxyauthorization" {
                        start = self.scheme_end(s, start, end).unwrap_or(start);
                    }
                    self.add(
                        &mut hits,
                        source,
                        start,
                        end,
                        if combined { "credential" } else { key },
                        false,
                    )?;
                }
            }
        }
        if self.cfg.token_hints.iter().any(|h| s.contains(h)) {
            let mut cursor = 0;
            while cursor < s.len() {
                let Some(m) = self.token.find_at(s, cursor) else {
                    break;
                };
                if !self.boundary(s, m.start()) {
                    cursor = advance(s, m.start(), 1);
                    continue;
                }
                cursor = m.end();
                if (m.as_str().starts_with("AKIA") || m.as_str().starts_with("ASIA"))
                    && char_at(s, m.end()).is_some_and(token_char)
                {
                    continue;
                }
                self.add(&mut hits, source, m.start(), m.end(), "credential", false)?;
            }
        }
        if s.contains(['b', 'B']) {
            let mut cursor = 0;
            while cursor < s.len() {
                let Some(cap) = self.auth.captures_at(s, cursor) else {
                    break;
                };
                let m = cap.get(0).unwrap();
                if m.start() > 0 && self.word(s[..m.start()].chars().next_back().unwrap()) {
                    cursor = advance(s, m.start(), 1);
                    continue;
                }
                cursor = m.end();
                let value = cap.name("value").unwrap();
                self.add(
                    &mut hits,
                    source,
                    value.start(),
                    value.end(),
                    "authorization",
                    false,
                )?;
            }
        }
        let refs: Vec<_> = self
            .reference
            .find_iter(s)
            .map(|m| (m.start(), m.end()))
            .collect();
        let mut r = 0;
        let mut merged: Vec<Hit> = Vec::new();
        hits.sort_by_key(|h| h.start);
        for h in hits {
            while r < refs.len() && refs[r].1 <= h.start {
                r += 1;
            }
            if r < refs.len() && refs[r].0 <= h.start && h.end <= refs[r].1 {
                continue;
            }
            if let Some(last) = merged.last_mut() {
                if h.start < last.end {
                    last.end = last.end.max(h.end);
                    continue;
                }
            }
            merged.push(h);
        }
        Ok(to_char_offsets(source, merged))
    }
}

fn fence_line_start(s: &str, pos: usize) -> bool {
    let mut prefix = s[..pos].bytes().rev();
    for _ in 0..3 {
        match prefix.next() {
            None | Some(b'\n') => return true,
            Some(b' ') => (),
            _ => return false,
        }
    }
    matches!(prefix.next(), None | Some(b'\n'))
}

fn normalize_key(s: &str) -> String {
    s.chars()
        .filter(|c| !matches!(c, '-' | '_' | ' '))
        .collect()
}
fn space(c: char) -> bool {
    c.is_whitespace() || matches!(c, '\u{1c}'..='\u{1f}')
}
fn token_char(c: char) -> bool {
    c.is_ascii_alphanumeric() || "_./+=~-".contains(c)
}
fn char_at(s: &str, p: usize) -> Option<char> {
    s.get(p..)?.chars().next()
}
fn advance(s: &str, mut p: usize, n: usize) -> usize {
    for _ in 0..n {
        if let Some(c) = char_at(s, p) {
            p += c.len_utf8();
        }
    }
    p
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

    #[test]
    fn basic_requires_authentication_context() {
        let extractor = PrivacyExtractor::new().unwrap();
        let text = "Basic usage; Authorization: Basic demo-secret";
        let chars: Vec<_> = text.chars().collect();
        let values: Vec<String> = extractor
            .extract(text, None)
            .unwrap()
            .iter()
            .map(|(start, end, _)| chars[*start..*end].iter().collect())
            .collect();
        assert_eq!(values, ["demo-secret"]);
        assert_eq!(
            extractor
                .extract("Basic demo-secret", Some("Authorization"))
                .unwrap(),
            vec![(6, 17, "authorization".to_owned())]
        );
    }

    #[test]
    fn unicode_offsets_and_overlaps_cover_complete_credentials() {
        let extractor = PrivacyExtractor::new().unwrap();
        let text = "前缀 password='中文密码' Authorization: Bearer sk-abcdefghijklmnopqrstuv";
        let hits = extractor.extract(text, None).unwrap();
        let chars: Vec<_> = text.chars().collect();
        let values: Vec<String> = hits
            .iter()
            .map(|(s, e, _)| chars[*s..*e].iter().collect())
            .collect();
        assert_eq!(values, ["中文密码", "sk-abcdefghijklmnopqrstuv"]);
    }

    #[test]
    fn references_and_environment_placeholders_are_not_new_credentials() {
        let extractor = PrivacyExtractor::new().unwrap();
        let text = "password=${PASSWORD} {{ov_privacy:alice:viking://~/privacy/session/sk-abcdefghijklmnopqrstuv#/values/pat}}";
        assert!(extractor.extract(text, None).unwrap().is_empty());
        for value in ["${PASSWORD}", "$PASSWORD中", "$PASSWORD²", "$PASSWORD¼"] {
            assert!(extractor
                .extract(value, Some("password"))
                .unwrap()
                .is_empty());
        }
        // A combining mark ends the environment name and leaves a literal value.
        assert_eq!(
            extractor
                .extract("$PASSWORD\u{0345}", Some("password"))
                .unwrap(),
            vec![(0, 10, "password".to_owned())]
        );
    }

    #[test]
    fn markdown_delimiters_preserve_value_boundaries_and_literal_symbols() {
        let extractor = PrivacyExtractor::new().unwrap();
        for (text, expected) in [
            ("**API_KEY=secret-value**", "secret-value"),
            ("1. **API_KEY=secret-value**.", "secret-value"),
            ("password=**secret-value**", "secret-value"),
            ("**Authorization: Bearer secret-value**", "secret-value"),
            ("**sk-abcdefghijklmnopqrstuv**", "sk-abcdefghijklmnopqrstuv"),
            ("password=secret**", "secret**"),
            ("password=ab**cd", "ab**cd"),
            ("password=***", "***"),
            ("**password='secret**'**", "secret**"),
            ("password='**secret**'", "**secret**"),
            ("`API_KEY=secret-value`", "secret-value"),
            ("password=`secret-value`", "secret-value"),
            ("``API_KEY=secret-value``", "secret-value"),
            ("**`API_KEY=secret-value`**", "secret-value"),
            ("**API_KEY=`secret-value`**", "secret-value"),
            ("`password=**secret**`", "**secret**"),
            ("``password=has`tick``", "has`tick"),
            ("````password=has```ticks````", "has```ticks"),
            ("`Authorization: Bearer secret-value`", "secret-value"),
            ("`--api-key secret-value`", "secret-value"),
            ("``--password='secret`**'``", "secret`**"),
            ("password='literal`tick`**'", "literal`tick`**"),
            ("password=unpaired`", "unpaired`"),
            ("`unclosed password=secret-value", "secret-value"),
            ("`password='**secret**'`", "**secret**"),
            ("```\npassword=**secret**\n```", "**secret**"),
            ("```sh\npassword=**secret**\n````", "**secret**"),
            ("~~~sh\npassword=**secret**\n~~~~", "**secret**"),
            ("```sh\npassword=**secret**", "**secret**"),
            ("~~~sh\npassword=**secret**", "**secret**"),
            ("**heading** password=secret**", "secret**"),
            ("中文 **password=密码**", "密码"),
            ("中文 `password=密码`。", "密码"),
        ] {
            let chars: Vec<_> = text.chars().collect();
            let values: Vec<String> = extractor
                .extract(text, None)
                .unwrap()
                .iter()
                .map(|(s, e, _)| chars[*s..*e].iter().collect())
                .collect();
            assert_eq!(values, [expected], "{text}");
        }
        assert_eq!(
            extractor.extract("**secret**", Some("password")).unwrap(),
            vec![(0, 10, "password".to_owned())]
        );
        let text = "`password=one` **password=two** ``password=three`tick`` password='four`**'";
        let chars: Vec<_> = text.chars().collect();
        let values: Vec<String> = extractor
            .extract(text, None)
            .unwrap()
            .iter()
            .map(|(s, e, _)| chars[*s..*e].iter().collect())
            .collect();
        assert_eq!(values, ["one", "two", "three`tick", "four`**"]);
    }

    #[test]
    fn unicode_categories_preserve_credential_boundaries() {
        let extractor = PrivacyExtractor::new().unwrap();
        // Letters, numbers and underscores continue identifiers.
        for prefix in ['A', '9', '_', '中', '²', '¼'] {
            for suffix in ["sk-abcdefghijklmnopqrstuv", "Bearer secret-value"] {
                let text = format!("{prefix}{suffix}");
                assert!(extractor.extract(&text, None).unwrap().is_empty(), "{text}");
            }
        }
        // Combining marks, other connectors and format characters form boundaries.
        for prefix in ['\u{0345}', '\u{0363}', '\u{203f}', '\u{200c}', '🙂'] {
            let text = format!("{prefix}password=secret-value");
            assert_eq!(
                extractor.extract(&text, None).unwrap(),
                vec![(10, 22, "password".to_owned())]
            );
        }
    }
}
