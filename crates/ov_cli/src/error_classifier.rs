#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum ApiErrorKind {
    Authentication,
    Permission,
    InvalidRequest,
    NotFound,
    Conflict,
    ResourceExhausted,
    DeadlineExceeded,
    Unavailable,
    Other,
}

pub(crate) fn api_error_kind(code: Option<&str>, status: Option<u16>) -> ApiErrorKind {
    match code {
        Some("UNAUTHENTICATED") => return ApiErrorKind::Authentication,
        Some("PERMISSION_DENIED") => return ApiErrorKind::Permission,
        Some("INVALID_ARGUMENT" | "INVALID_URI" | "UNSUPPORTED_URI" | "UNSUPPORTED_MODE") => {
            return ApiErrorKind::InvalidRequest;
        }
        Some("NOT_FOUND") => return ApiErrorKind::NotFound,
        Some("CONFLICT" | "ALREADY_EXISTS" | "ABORTED") => return ApiErrorKind::Conflict,
        Some("RESOURCE_EXHAUSTED") => return ApiErrorKind::ResourceExhausted,
        Some("DEADLINE_EXCEEDED") => return ApiErrorKind::DeadlineExceeded,
        Some("UNAVAILABLE") => return ApiErrorKind::Unavailable,
        _ => {}
    }

    match status {
        Some(401) => ApiErrorKind::Authentication,
        Some(403) => ApiErrorKind::Permission,
        Some(400 | 422) => ApiErrorKind::InvalidRequest,
        Some(404) => ApiErrorKind::NotFound,
        Some(409) => ApiErrorKind::Conflict,
        Some(429) => ApiErrorKind::ResourceExhausted,
        Some(504) => ApiErrorKind::DeadlineExceeded,
        Some(502 | 503) => ApiErrorKind::Unavailable,
        _ => ApiErrorKind::Other,
    }
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
    use super::{ApiErrorKind, api_error_kind, extra_forbidden_field};

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
    fn classifies_known_api_codes_without_message_matching() {
        assert_eq!(
            api_error_kind(Some("UNAUTHENTICATED"), Some(401)),
            ApiErrorKind::Authentication
        );
        assert_eq!(
            api_error_kind(Some("PERMISSION_DENIED"), Some(403)),
            ApiErrorKind::Permission
        );
        assert_eq!(
            api_error_kind(Some("PROCESSING_ERROR"), Some(500)),
            ApiErrorKind::Other
        );
        assert_eq!(api_error_kind(None, Some(404)), ApiErrorKind::NotFound);
    }
}
