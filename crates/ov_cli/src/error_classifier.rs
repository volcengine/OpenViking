pub(crate) fn looks_like_auth_error(message: &str) -> bool {
    let lower = message.to_ascii_lowercase();
    lower.contains("api key")
        || lower.contains("unauthorized")
        || lower.contains("forbidden")
        || lower.contains("authentication")
        || lower
            .split(|ch: char| !ch.is_ascii_alphanumeric())
            .any(|token| token == "auth")
}

/// Detect the kernel's pydantic `extra="forbid"` rejection — e.g.
/// "body.tags: Extra inputs are not permitted" — and return the offending field
/// name. This usually means the target OpenViking instance is on a different
/// version than the CLI (the field is missing, renamed, or removed), so callers
/// can surface a version-mismatch hint instead of the raw API error.
pub(crate) fn extra_forbidden_field(message: &str) -> Option<String> {
    if !message.contains("Extra inputs are not permitted") {
        return None;
    }
    // The kernel reports the location as `body.<field>: Extra inputs ...`.
    let after = message.split("body.").nth(1)?;
    let field = after
        .split(|ch: char| ch == ':' || ch.is_whitespace())
        .next()?
        .trim();
    if field.is_empty() {
        None
    } else {
        Some(field.to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::{extra_forbidden_field, looks_like_auth_error};

    #[test]
    fn extracts_extra_forbidden_field() {
        assert_eq!(
            extra_forbidden_field(
                "[INVALID_ARGUMENT] Invalid request parameters: body.tags: Extra inputs are not permitted."
            ),
            Some("tags".to_string())
        );
        assert_eq!(
            extra_forbidden_field("body.args: Extra inputs are not permitted"),
            Some("args".to_string())
        );
    }

    #[test]
    fn ignores_non_extra_forbidden_errors() {
        assert_eq!(extra_forbidden_field("API key is invalid"), None);
        assert_eq!(extra_forbidden_field("body.query: Field required"), None);
    }

    #[test]
    fn detects_auth_errors() {
        for message in [
            "API key is invalid",
            "request was unauthorized",
            "forbidden",
            "authentication failed",
            "auth failed",
        ] {
            assert!(looks_like_auth_error(message), "{message}");
        }
    }

    #[test]
    fn avoids_auth_substring_false_positives() {
        for message in ["author not found", "authority unavailable"] {
            assert!(!looks_like_auth_error(message), "{message}");
        }
    }
}
