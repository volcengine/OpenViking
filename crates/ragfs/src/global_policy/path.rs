//! Canonical path validation for policy rules and evaluation targets.

use super::{GlobalPolicyError, PROTECTED_ROOTS};

/// Validate a canonical, literal path relative to one user's root.
///
/// Ambiguous input is rejected, never normalized. Percent escapes, URI syntax,
/// glob syntax and path-segment edge whitespace are not part of this protocol.
/// The user root itself is expressed through the policy default, not `""`.
pub fn validate_policy_path(path: &str) -> Result<&str, GlobalPolicyError> {
    if path.is_empty() {
        return Err(GlobalPolicyError::invalid(
            "policy path must be a non-empty string",
        ));
    }
    for character in path.chars() {
        if matches!(
            character,
            '\\' | ':' | '%' | '?' | '#' | '*' | '[' | ']' | '{' | '}'
        ) || character.is_control()
        {
            return Err(GlobalPolicyError::invalid(
                "policy path contains unsupported characters",
            ));
        }
    }
    let parts: Vec<&str> = path.split('/').collect();
    if parts[0] == "~"
        || parts
            .iter()
            .any(|part| part.is_empty() || *part == "." || *part == ".." || *part != part.trim())
    {
        return Err(GlobalPolicyError::invalid(
            "policy path must be a canonical relative path",
        ));
    }
    Ok(path)
}

/// Return whether the first path segment is a permanent protected root.
pub(crate) fn is_protected_path(path: &str) -> bool {
    path.split('/')
        .next()
        .is_some_and(|root| PROTECTED_ROOTS.contains(&root))
}
