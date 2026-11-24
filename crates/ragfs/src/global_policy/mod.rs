//! Strict parser and evaluator for user-controlled GLOBAL access policies.
//!
//! A policy document lives at `viking://user/{user_id}/GLOBAL_POLICY.md`.
//! This module only interprets the document; it does not resolve identities,
//! grant operations, or change existing authorization behavior. Snapshots are
//! loaded once, and callers own reuse, expiry and eviction.

mod evaluate;
mod frontmatter;
mod path;
mod snapshot;
mod write;

use std::fmt;

pub use evaluate::is_global_policy_path_allowed;
pub use path::{is_user_policy_file_path, validate_policy_path};
pub use snapshot::{PolicySnapshot, PolicyStatus};
pub use write::register_write_hooks;

/// Reserved policy filename at a user root.
pub const POLICY_FILENAME: &str = "GLOBAL_POLICY.md";

/// OKF type required by the v1 policy schema.
pub const POLICY_TYPE: &str = "global-access-policy";

/// Roots that a policy can never make readable to GLOBAL.
pub const PROTECTED_ROOTS: [&str; 2] = ["privacy", "sessions"];

/// A policy cannot be safely interpreted and must not grant access.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum GlobalPolicyError {
    /// The document is malformed or violates the v1 schema.
    Invalid(String),
    /// The document uses an unsupported integer schema version.
    UnsupportedSchema {
        /// The integer version found in the document.
        version: i128,
    },
}

impl GlobalPolicyError {
    /// Stable code for a malformed or schema-invalid policy.
    pub const INVALID_CODE: &'static str = "GLOBAL_POLICY_INVALID";

    /// Stable code for a policy with a valid but unsupported schema version.
    pub const UNSUPPORTED_SCHEMA_CODE: &'static str = "GLOBAL_POLICY_SCHEMA_UNSUPPORTED";

    /// Return the stable error code exposed to callers.
    pub fn code(&self) -> &'static str {
        match self {
            Self::Invalid(_) => Self::INVALID_CODE,
            Self::UnsupportedSchema { .. } => Self::UNSUPPORTED_SCHEMA_CODE,
        }
    }

    pub(crate) fn invalid(message: impl Into<String>) -> Self {
        Self::Invalid(message.into())
    }
}

impl fmt::Display for GlobalPolicyError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Invalid(message) => formatter.write_str(message),
            Self::UnsupportedSchema { version } => {
                write!(formatter, "unsupported policy schema_version: {version}")
            }
        }
    }
}

impl std::error::Error for GlobalPolicyError {}

/// Effect of a policy default or rule.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum PolicyEffect {
    /// The path is readable unless a later matching deny rule applies.
    Allow,
    /// The path is not readable.
    Deny,
}

impl PolicyEffect {
    /// Return the canonical lowercase effect name.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Allow => "allow",
            Self::Deny => "deny",
        }
    }

    pub(crate) fn parse(value: &str) -> Option<Self> {
        match value {
            "allow" => Some(Self::Allow),
            "deny" => Some(Self::Deny),
            _ => None,
        }
    }
}

/// One explicit allow or deny rule relative to the owner's root.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GlobalPolicyRule {
    /// Rule effect.
    pub effect: PolicyEffect,
    /// Canonical, literal path relative to the owner's root.
    pub path: String,
    /// Whether the rule also matches descendants of `path`.
    pub recursive: bool,
}

/// Validated v1 policy. The Markdown body is preserved verbatim.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GlobalPolicy {
    default: PolicyEffect,
    rules: Vec<GlobalPolicyRule>,
    body: String,
    schema_version: u32,
    policy_hash: String,
}

impl GlobalPolicy {
    /// Parse a complete v1 policy document or fail without a partial grant.
    pub fn parse(text: &str) -> Result<Self, GlobalPolicyError> {
        frontmatter::parse(text)
    }

    /// Policy default applied when no rule matches.
    pub fn default_effect(&self) -> PolicyEffect {
        self.default
    }

    /// Canonical lowercase policy default.
    pub fn default_str(&self) -> &'static str {
        self.default.as_str()
    }

    /// Explicit rules in document order.
    pub fn rules(&self) -> &[GlobalPolicyRule] {
        &self.rules
    }

    /// Markdown body after the closing frontmatter delimiter.
    pub fn body(&self) -> &str {
        &self.body
    }

    /// Parsed schema version. Only v1 is accepted today.
    pub fn schema_version(&self) -> u32 {
        self.schema_version
    }

    /// SHA-256 of the exact input text, used for cache invalidation.
    pub fn policy_hash(&self) -> &str {
        &self.policy_hash
    }

    /// Evaluate ordinary-content rules for one owner-relative path.
    ///
    /// The caller must still bind an actor and a single owner, and enforce the
    /// operation matrix. A path grant does not prove subtree visibility or
    /// safe summary provenance.
    pub fn allows_path(&self, path: &str) -> bool {
        is_global_policy_path_allowed(Some(self), path)
    }
}

/// Validate the complete bytes written to a user's reserved policy file.
///
/// This is the write-time counterpart of [`GlobalPolicy::parse`]. It rejects
/// non-UTF-8 content, malformed frontmatter, schema violations and unsupported
/// schema versions before the write reaches the filesystem, so a caller can
/// surface the error instead of leaving a fail-closed but silently invalid file.
pub fn validate_policy_write(data: &[u8]) -> Result<(), GlobalPolicyError> {
    let text = std::str::from_utf8(data)
        .map_err(|_| GlobalPolicyError::invalid("policy content must be UTF-8 text"))?;
    GlobalPolicy::parse(text).map(|_| ())
}
