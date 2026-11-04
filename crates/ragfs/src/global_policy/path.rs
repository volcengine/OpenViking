//! Canonical path validation for policy rules and evaluation targets.

use super::{GlobalPolicyError, POLICY_FILENAME, PROTECTED_ROOTS};

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

/// Return whether an absolute AGFS path addresses a user's reserved policy file.
///
/// The only accepted shape is `/local/{account_id}/user/{owner_user_id}/GLOBAL_POLICY.md`.
/// Files with the same name below another directory are ordinary content and are
/// not validated as policies.
pub fn is_user_policy_file_path(path: &str) -> bool {
    let mut parts = path.split('/');
    parts.next() == Some("")
        && parts.next() == Some("local")
        && parts.next().is_some()
        && parts.next() == Some("user")
        && parts.next().is_some()
        && parts.next() == Some(POLICY_FILENAME)
        && parts.next().is_none()
}

/// Return whether the first path segment is a permanent protected root.
pub(crate) fn is_protected_path(path: &str) -> bool {
    path.split('/')
        .next()
        .is_some_and(|root| PROTECTED_ROOTS.contains(&root))
}
