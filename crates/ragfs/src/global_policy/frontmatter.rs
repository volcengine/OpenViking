//! Strict OKF frontmatter splitting and YAML interpretation.

use std::fmt;

use serde::de::{self, Deserialize, Deserializer, MapAccess, SeqAccess, Visitor};
use serde_yaml::from_str;

use super::path::{is_protected_path, validate_policy_path};
use super::{GlobalPolicy, GlobalPolicyError, GlobalPolicyRule, PolicyEffect, POLICY_TYPE};

/// Parse a complete v1 policy document without returning a partial grant.
pub(super) fn parse(text: &str) -> Result<GlobalPolicy, GlobalPolicyError> {
    let lines = split_lines_keepends(text);
    if lines.is_empty() || !is_delimiter(lines[0]) {
        return Err(GlobalPolicyError::invalid(
            "policy requires YAML frontmatter",
        ));
    }
    let closing = lines
        .iter()
        .enumerate()
        .skip(1)
        .find(|(_, line)| is_delimiter(line))
        .map(|(index, _)| index)
        .ok_or_else(|| GlobalPolicyError::invalid("policy frontmatter is not closed"))?;
    let yaml_text = lines[1..closing].concat();
    let body = lines[closing + 1..].concat();

    reject_unsupported_yaml_syntax(&yaml_text)?;
    let document: StrictValue = from_str(&yaml_text)
        .map_err(|error| GlobalPolicyError::invalid(format!("invalid policy YAML: {error}")))?;
    let (schema_version, default, rules) = parse_document(document)?;

    Ok(GlobalPolicy {
        default,
        rules,
        body,
        schema_version,
        policy_hash: hash_policy(text),
    })
}

fn parse_document(
    document: StrictValue,
) -> Result<(u32, PolicyEffect, Vec<GlobalPolicyRule>), GlobalPolicyError> {
    let entries = match document {
        StrictValue::Map(entries) => entries,
        _ => {
            return Err(GlobalPolicyError::invalid(
                "policy fields must be type, schema_version, default and rules",
            ));
        }
    };

    let mut type_value = None;
    let mut schema_value = None;
    let mut default_value = None;
    let mut rules_value = None;
    for (key, value) in entries {
        let key = match key {
            StrictValue::Str(key) => key,
            _ => {
                return Err(GlobalPolicyError::invalid(
                    "policy YAML keys must be unique strings",
                ));
            }
        };
        match key.as_str() {
            "type" => set_once(&mut type_value, value, "type")?,
            "schema_version" => set_once(&mut schema_value, value, "schema_version")?,
            "default" => set_once(&mut default_value, value, "default")?,
            "rules" => set_once(&mut rules_value, value, "rules")?,
            _ => {
                return Err(GlobalPolicyError::invalid(
                    "policy fields must be type, schema_version, default and rules",
                ));
            }
        }
    }

    let policy_type = expect_string(type_value.ok_or_else(missing_fields)?, "type")?;
    if policy_type != POLICY_TYPE {
        return Err(GlobalPolicyError::invalid("invalid policy type"));
    }
    let schema_version = parse_schema_version(schema_value.ok_or_else(missing_fields)?)?;
    let default = PolicyEffect::parse(&expect_string(
        default_value.ok_or_else(missing_fields)?,
        "default",
    )?)
    .ok_or_else(|| GlobalPolicyError::invalid("policy requires an allow/deny default"))?;
    let rules = match rules_value.ok_or_else(missing_fields)? {
        StrictValue::Seq(rules) => rules,
        _ => {
            return Err(GlobalPolicyError::invalid("policy requires a rules list"));
        }
    };

    let mut parsed_rules = Vec::with_capacity(rules.len());
    for rule in rules {
        parsed_rules.push(parse_rule(rule)?);
    }
    Ok((schema_version, default, parsed_rules))
}

fn parse_rule(value: StrictValue) -> Result<GlobalPolicyRule, GlobalPolicyError> {
    let entries = match value {
        StrictValue::Map(entries) => entries,
        _ => {
            return Err(GlobalPolicyError::invalid(
                "policy rule fields must be effect, path and recursive",
            ));
        }
    };

    let mut effect_value = None;
    let mut path_value = None;
    let mut recursive_value = None;
    for (key, value) in entries {
        let key = match key {
            StrictValue::Str(key) => key,
            _ => {
                return Err(GlobalPolicyError::invalid(
                    "policy YAML keys must be unique strings",
                ));
            }
        };
        match key.as_str() {
            "effect" => set_once(&mut effect_value, value, "effect")?,
            "path" => set_once(&mut path_value, value, "path")?,
            "recursive" => set_once(&mut recursive_value, value, "recursive")?,
            _ => {
                return Err(GlobalPolicyError::invalid(
                    "policy rule fields must be effect, path and recursive",
                ));
            }
        }
    }

    let effect = PolicyEffect::parse(&expect_string(
        effect_value.ok_or_else(rule_missing_fields)?,
        "rule effect",
    )?)
    .ok_or_else(|| GlobalPolicyError::invalid("policy rule requires an allow/deny effect"))?;
    let path = expect_string(path_value.ok_or_else(rule_missing_fields)?, "rule path")?;
    let path = validate_policy_path(&path)?;
    let recursive = match recursive_value.ok_or_else(rule_missing_fields)? {
        StrictValue::Bool(recursive) => recursive,
        _ => {
            return Err(GlobalPolicyError::invalid(
                "policy rule requires a boolean recursive",
            ));
        }
    };
    if effect == PolicyEffect::Allow && is_protected_path(path) {
        return Err(GlobalPolicyError::invalid(
            "policy cannot allow privacy or sessions",
        ));
    }
    Ok(GlobalPolicyRule {
        effect,
        path: path.to_string(),
        recursive,
    })
}

fn parse_schema_version(value: StrictValue) -> Result<u32, GlobalPolicyError> {
    match value {
        StrictValue::Int(version) => schema_version(version),
        StrictValue::UInt(version) => {
            let version = i128::try_from(version).unwrap_or(i128::MAX);
            schema_version(version)
        }
        _ => Err(GlobalPolicyError::invalid(
            "policy schema_version must be an integer",
        )),
    }
}

fn schema_version(version: i128) -> Result<u32, GlobalPolicyError> {
    if version == 1 {
        Ok(1)
    } else {
        Err(GlobalPolicyError::UnsupportedSchema { version })
    }
}

fn expect_string(value: StrictValue, field: &str) -> Result<String, GlobalPolicyError> {
    match value {
        StrictValue::Str(value) => Ok(value),
        _ => Err(GlobalPolicyError::invalid(format!(
            "policy {field} must be a string"
        ))),
    }
}

fn set_once(
    slot: &mut Option<StrictValue>,
    value: StrictValue,
    field: &str,
) -> Result<(), GlobalPolicyError> {
    if slot.is_some() {
        return Err(GlobalPolicyError::invalid(format!(
            "duplicate policy field: {field}"
        )));
    }
    *slot = Some(value);
    Ok(())
}

fn missing_fields() -> GlobalPolicyError {
    GlobalPolicyError::invalid("policy fields must be type, schema_version, default and rules")
}

fn rule_missing_fields() -> GlobalPolicyError {
    GlobalPolicyError::invalid("policy rule fields must be effect, path and recursive")
}

pub(super) fn hash_policy(text: &str) -> String {
    use sha2::{Digest, Sha256};
    use std::fmt::Write;

    let digest = Sha256::digest(text.as_bytes());
    let mut output = String::with_capacity(digest.len() * 2);
    for byte in digest {
        write!(&mut output, "{byte:02x}").expect("writing to a String cannot fail");
    }
    output
}

fn split_lines_keepends(text: &str) -> Vec<&str> {
    let bytes = text.as_bytes();
    let mut lines = Vec::new();
    let mut start = 0;
    let mut index = 0;
    while index < bytes.len() {
        match bytes[index] {
            b'\n' => {
                index += 1;
                lines.push(&text[start..index]);
                start = index;
            }
            b'\r' => {
                index += 1;
                if index < bytes.len() && bytes[index] == b'\n' {
                    index += 1;
                }
                lines.push(&text[start..index]);
                start = index;
            }
            _ => index += 1,
        }
    }
    if start < text.len() {
        lines.push(&text[start..]);
    }
    lines
}

fn is_delimiter(line: &str) -> bool {
    line.trim_end_matches(['\r', '\n']) == "---"
}

/// Reject YAML constructs that make a policy's meaning depend on aliasing or tags.
fn reject_unsupported_yaml_syntax(yaml: &str) -> Result<(), GlobalPolicyError> {
    #[derive(Clone, Copy, PartialEq, Eq)]
    enum State {
        Plain,
        SingleQuoted,
        DoubleQuoted,
        Comment,
    }

    let mut state = State::Plain;
    let mut previous = None;
    let mut escaped = false;
    let mut characters = yaml.chars().peekable();
    while let Some(character) = characters.next() {
        match state {
            State::SingleQuoted => {
                if character == '\'' {
                    if characters.peek() == Some(&'\'') {
                        characters.next();
                    } else {
                        state = State::Plain;
                    }
                }
            }
            State::DoubleQuoted => {
                if escaped {
                    escaped = false;
                } else if character == '\\' {
                    escaped = true;
                } else if character == '"' {
                    state = State::Plain;
                }
            }
            State::Comment => {
                if character == '\n' {
                    state = State::Plain;
                }
            }
            State::Plain => {
                if character == '#' && is_token_start(previous) {
                    state = State::Comment;
                } else if character == '\'' {
                    state = State::SingleQuoted;
                } else if character == '"' {
                    state = State::DoubleQuoted;
                } else if matches!(character, '&' | '*' | '!') && is_token_start(previous) {
                    return Err(GlobalPolicyError::invalid(
                        "policy YAML anchors, aliases and tags are not supported",
                    ));
                } else if character == '<'
                    && characters.peek() == Some(&'<')
                    && is_token_start(previous)
                {
                    return Err(GlobalPolicyError::invalid(
                        "policy YAML merge keys are not supported",
                    ));
                }
            }
        }
        previous = Some(character);
    }
    Ok(())
}

fn is_token_start(previous: Option<char>) -> bool {
    match previous {
        None => true,
        Some(character) => {
            // A block sequence entry is written as ``- `` (dash plus separation
            // space), so the indicator character itself is preceded by
            // whitespace. Treating a bare ``-`` as a token start would misread
            // plain scalars such as ``resources/release-!important`` as tags.
            character.is_whitespace()
                || matches!(
                    character,
                    ':' | ',' | '[' | ']' | '{' | '}' | '?' | '|' | '>'
                )
        }
    }
}

/// Minimal YAML value that preserves mapping entries for duplicate-key checks.
#[derive(Debug, Clone, PartialEq)]
enum StrictValue {
    Null,
    Bool(bool),
    Int(i128),
    UInt(u128),
    Float(f64),
    Str(String),
    Seq(Vec<StrictValue>),
    Map(Vec<(StrictValue, StrictValue)>),
}

impl<'de> Deserialize<'de> for StrictValue {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        deserializer.deserialize_any(StrictValueVisitor)
    }
}

struct StrictValueVisitor;

impl<'de> Visitor<'de> for StrictValueVisitor {
    type Value = StrictValue;

    fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("a YAML value")
    }

    fn visit_bool<E>(self, value: bool) -> Result<Self::Value, E> {
        Ok(StrictValue::Bool(value))
    }

    fn visit_i64<E>(self, value: i64) -> Result<Self::Value, E> {
        Ok(StrictValue::Int(i128::from(value)))
    }

    fn visit_u64<E>(self, value: u64) -> Result<Self::Value, E> {
        Ok(StrictValue::UInt(u128::from(value)))
    }

    fn visit_i128<E>(self, value: i128) -> Result<Self::Value, E> {
        Ok(StrictValue::Int(value))
    }

    fn visit_u128<E>(self, value: u128) -> Result<Self::Value, E> {
        Ok(StrictValue::UInt(value))
    }

    fn visit_f64<E>(self, value: f64) -> Result<Self::Value, E> {
        Ok(StrictValue::Float(value))
    }

    fn visit_str<E>(self, value: &str) -> Result<Self::Value, E> {
        Ok(StrictValue::Str(value.to_string()))
    }

    fn visit_borrowed_str<E>(self, value: &'de str) -> Result<Self::Value, E> {
        Ok(StrictValue::Str(value.to_string()))
    }

    fn visit_string<E>(self, value: String) -> Result<Self::Value, E> {
        Ok(StrictValue::Str(value))
    }

    fn visit_char<E>(self, value: char) -> Result<Self::Value, E> {
        Ok(StrictValue::Str(value.to_string()))
    }

    fn visit_unit<E>(self) -> Result<Self::Value, E> {
        Ok(StrictValue::Null)
    }

    fn visit_none<E>(self) -> Result<Self::Value, E> {
        Ok(StrictValue::Null)
    }

    fn visit_some<D>(self, deserializer: D) -> Result<Self::Value, D::Error>
    where
        D: Deserializer<'de>,
    {
        deserializer.deserialize_any(StrictValueVisitor)
    }

    fn visit_bytes<E>(self, _value: &[u8]) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        Err(de::Error::custom(
            "policy YAML binary values are not supported",
        ))
    }

    fn visit_byte_buf<E>(self, _value: Vec<u8>) -> Result<Self::Value, E>
    where
        E: de::Error,
    {
        Err(de::Error::custom(
            "policy YAML binary values are not supported",
        ))
    }

    fn visit_seq<A>(self, mut sequence: A) -> Result<Self::Value, A::Error>
    where
        A: SeqAccess<'de>,
    {
        let mut items = Vec::new();
        while let Some(item) = sequence.next_element::<StrictValue>()? {
            items.push(item);
        }
        Ok(StrictValue::Seq(items))
    }

    fn visit_map<A>(self, mut mapping: A) -> Result<Self::Value, A::Error>
    where
        A: MapAccess<'de>,
    {
        let mut entries: Vec<(StrictValue, StrictValue)> = Vec::new();
        while let Some(key) = mapping.next_key::<StrictValue>()? {
            if entries.iter().any(|(existing, _)| existing == &key) {
                return Err(de::Error::custom("policy YAML keys must be unique strings"));
            }
            let value = mapping.next_value::<StrictValue>()?;
            entries.push((key, value));
        }
        Ok(StrictValue::Map(entries))
    }
}
