#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct MissingTrustedIdentityFields {
    pub(crate) account: bool,
    pub(crate) user: bool,
}

pub(crate) fn missing_trusted_identity_fields(
    message: &str,
) -> Option<MissingTrustedIdentityFields> {
    let lower = message.to_ascii_lowercase();
    let describes_missing_value = lower.contains("must include")
        || lower.contains("requires")
        || lower.contains("required")
        || lower.contains("missing");
    let mentions_trusted_identity = lower.contains("trusted")
        && (lower.contains("x-openviking-account")
            || lower.contains("x-openviking-user")
            || lower.contains("account_id")
            || lower.contains("user_id"));
    if !describes_missing_value || !mentions_trusted_identity {
        return None;
    }

    let fields = MissingTrustedIdentityFields {
        account: lower.contains("x-openviking-account") || lower.contains("account_id"),
        user: lower.contains("x-openviking-user") || lower.contains("user_id"),
    };
    (fields.account || fields.user).then_some(fields)
}

pub(crate) fn looks_like_missing_api_key_error(message: &str) -> bool {
    let lower = message.to_ascii_lowercase();
    lower.contains("missing api key") || lower.contains("api key header required")
}

pub(crate) fn looks_like_auth_error(message: &str) -> bool {
    let lower = message.to_ascii_lowercase();
    lower.contains("api key")
        || lower.contains("unauthorized")
        || lower.contains("forbidden")
        || lower.contains("authentication")
        || lower.contains("trusted openviking chat requires")
        || lower.contains("trusted mode requests must include")
        || lower
            .split(|ch: char| !ch.is_ascii_alphanumeric())
            .any(|token| token == "auth")
}

pub(crate) fn looks_like_gateway_dev_boundary_error(message: &str) -> bool {
    let lower = message.to_ascii_lowercase();
    lower.contains("dev auth can only be used when gateway and openviking server are localhost")
        || (lower.contains("auth_mode changed to dev")
            && lower.contains("gateway")
            && lower.contains("openviking server"))
}

pub(crate) fn looks_like_gateway_standalone_proxy_error(message: &str) -> bool {
    let lower = message.to_ascii_lowercase();
    lower.contains("openviking upstream is not configured")
        || (lower.contains("vikingbot gateway proxy")
            && lower.contains("no available openviking server"))
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
    use super::{
        extra_forbidden_field, looks_like_auth_error, looks_like_gateway_dev_boundary_error,
        looks_like_gateway_standalone_proxy_error, looks_like_missing_api_key_error,
        missing_trusted_identity_fields,
    };

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
            "Trusted OpenViking chat requires X-OpenViking-Account and X-OpenViking-User.",
            "Trusted mode requests must include X-OpenViking-Account.",
        ] {
            assert!(looks_like_auth_error(message), "{message}");
        }
    }

    #[test]
    fn extracts_missing_trusted_identity_fields() {
        let both = missing_trusted_identity_fields(
            "Trusted mode requests must include X-OpenViking-Account or explicit account_id in the URL and X-OpenViking-User or explicit user_id in the URL.",
        )
        .expect("trusted identity fields");
        assert!(both.account);
        assert!(both.user);

        let user =
            missing_trusted_identity_fields("Trusted OpenViking chat requires X-OpenViking-User.")
                .expect("trusted user field");
        assert!(!user.account);
        assert!(user.user);

        let wrapped = missing_trusted_identity_fields(
            "Request failed (401 Unauthorized): {\"detail\":\"Trusted request is missing account_id and user_id\"}",
        )
        .expect("wrapped trusted identity fields");
        assert!(wrapped.account);
        assert!(wrapped.user);

        assert!(missing_trusted_identity_fields("API key is invalid").is_none());
    }

    #[test]
    fn detects_missing_api_key_without_matching_invalid_keys() {
        for message in [
            "Missing API Key when resolving identity.",
            "Missing API Key in trusted mode with Root API Key enabled.",
            "OpenViking API key header required",
        ] {
            assert!(looks_like_missing_api_key_error(message), "{message}");
        }
        assert!(!looks_like_missing_api_key_error("API key is invalid"));
    }

    #[test]
    fn avoids_auth_substring_false_positives() {
        for message in ["author not found", "authority unavailable"] {
            assert!(!looks_like_auth_error(message), "{message}");
        }
    }

    #[test]
    fn detects_gateway_dev_boundary_errors() {
        for message in [
            "Request failed (403 Forbidden): {\"detail\":\"OpenViking dev auth can only be used when gateway and OpenViking server are localhost\"}",
            "Request failed (403 Forbidden): {\"detail\":\"OpenViking server auth_mode changed to dev, but dev auth can only be used when gateway and OpenViking server are localhost\"}",
        ] {
            assert!(looks_like_gateway_dev_boundary_error(message), "{message}");
        }
    }

    #[test]
    fn detects_gateway_standalone_proxy_errors() {
        for message in [
            "OpenViking upstream is not configured",
            "VikingBot gateway proxy is active, but no available OpenViking server is configured",
        ] {
            assert!(
                looks_like_gateway_standalone_proxy_error(message),
                "{message}"
            );
        }
    }
}
