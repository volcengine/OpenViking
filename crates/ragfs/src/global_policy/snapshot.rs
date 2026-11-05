//! Immutable parsed-policy snapshot and its availability status.
//!
//! A snapshot is loaded once. Reuse, expiry and eviction are managed by the
//! caller; this module never reloads a source on its own.

use std::sync::Arc;

use super::{is_global_policy_path_allowed, GlobalPolicy, GlobalPolicyError};

/// Availability of a loaded policy. Every non-valid status denies access.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PolicyStatus {
    /// A valid v1 policy was loaded.
    Valid,
    /// No policy file exists at the owner root.
    Missing,
    /// The policy source could not be read.
    Unreadable(String),
    /// The source was read but is not a valid v1 policy.
    Invalid(GlobalPolicyError),
}

impl PolicyStatus {
    /// Short status name exposed to callers.
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Valid => "valid",
            Self::Missing => "missing",
            Self::Unreadable(_) => "unreadable",
            Self::Invalid(GlobalPolicyError::Invalid(_)) => "invalid",
            Self::Invalid(GlobalPolicyError::UnsupportedSchema { .. }) => "unsupported_schema",
        }
    }

    /// Diagnostic message for non-valid statuses.
    pub fn message(&self) -> Option<String> {
        match self {
            Self::Valid => None,
            Self::Missing => Some("GLOBAL_POLICY_MISSING: policy file not found".to_string()),
            Self::Unreadable(reason) => Some(format!("GLOBAL_POLICY_UNAVAILABLE: {reason}")),
            Self::Invalid(error) => Some(format!("{}: {error}", error.code())),
        }
    }
}

/// One loaded policy generation. A missing or invalid policy carries no grant.
#[derive(Debug, Clone)]
pub struct PolicySnapshot {
    policy: Option<Arc<GlobalPolicy>>,
    status: PolicyStatus,
}

impl PolicySnapshot {
    /// Snapshot wrapping a valid parsed policy.
    pub fn valid(policy: Arc<GlobalPolicy>) -> Self {
        Self {
            policy: Some(policy),
            status: PolicyStatus::Valid,
        }
    }

    /// Snapshot for an owner without a policy file.
    pub fn missing() -> Self {
        Self {
            policy: None,
            status: PolicyStatus::Missing,
        }
    }

    /// Snapshot for a read failure.
    pub fn unreadable(reason: String) -> Self {
        Self {
            policy: None,
            status: PolicyStatus::Unreadable(reason),
        }
    }

    /// Parse source text into a valid or invalid snapshot without raising.
    pub fn from_text(text: &str) -> Self {
        match GlobalPolicy::parse(text) {
            Ok(policy) => Self::valid(Arc::new(policy)),
            Err(error) => Self {
                policy: None,
                status: PolicyStatus::Invalid(error),
            },
        }
    }

    /// Borrow the parsed policy when the snapshot is valid.
    pub fn policy(&self) -> Option<&GlobalPolicy> {
        self.policy.as_deref()
    }

    /// Clone the parsed policy when the snapshot is valid.
    pub fn policy_arc(&self) -> Option<Arc<GlobalPolicy>> {
        self.policy.clone()
    }

    /// Availability status.
    pub fn status(&self) -> &PolicyStatus {
        &self.status
    }

    /// Evaluate ordinary-content rules, failing closed for every non-valid status.
    pub fn allows_path(&self, path: &str) -> bool {
        is_global_policy_path_allowed(self.policy.as_deref(), path)
    }
}
