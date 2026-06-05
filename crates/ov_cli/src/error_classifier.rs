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

#[cfg(test)]
mod tests {
    use super::looks_like_auth_error;

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
