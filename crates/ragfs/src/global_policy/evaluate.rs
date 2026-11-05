//! Fail-closed path evaluation for ordinary user content.

use super::path::{is_protected_path, validate_policy_path};
use super::{GlobalPolicy, PolicyEffect};

/// Evaluate ordinary-content rules for one owner-relative path.
///
/// A missing policy, invalid path, or permanently protected root fails closed.
/// Protected roots override the policy default and every explicit rule.
pub fn is_global_policy_path_allowed(policy: Option<&GlobalPolicy>, path: &str) -> bool {
    if validate_policy_path(path).is_err() || is_protected_path(path) {
        return false;
    }
    let Some(policy) = policy else {
        return false;
    };

    let mut allowed = policy.default_effect() == PolicyEffect::Allow;
    for rule in policy.rules() {
        if path == rule.path
            || (rule.recursive
                && path
                    .strip_prefix(rule.path.as_str())
                    .is_some_and(|remainder| remainder.starts_with('/')))
        {
            if rule.effect == PolicyEffect::Deny {
                return false;
            }
            allowed = true;
        }
    }
    allowed
}
