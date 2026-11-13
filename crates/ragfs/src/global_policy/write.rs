//! Write-time registration for the user-controlled GLOBAL policy.

use std::sync::Arc;

use crate::write_hook::{PathMatcher, WriteContext, WriteHook, WriteHookError, WriteHookRegistry};

use super::{is_user_policy_file_path, validate_policy_write, POLICY_FILENAME};

const HOOK_NAME: &str = "global-policy";

struct GlobalPolicyWriteHook;

impl WriteHook for GlobalPolicyWriteHook {
    fn name(&self) -> &str {
        HOOK_NAME
    }

    fn on_write(&self, context: &WriteContext<'_>) -> Result<(), WriteHookError> {
        // The suffix matcher also sees same-named files in other directories.
        // Only the canonical user-root policy path is validated as a policy.
        if !is_user_policy_file_path(context.path) {
            return Ok(());
        }
        validate_policy_write(context.data)
            .map_err(|error| WriteHookError::new(HOOK_NAME, error.code(), error.to_string()))
    }
}

/// Register write-time validation for the reserved user policy file.
///
/// The registered matcher is the fixed `GLOBAL_POLICY.md` suffix. The hook
/// narrows it to `/local/{account_id}/user/{owner_user_id}/GLOBAL_POLICY.md`,
/// so files with the same name elsewhere remain ordinary content.
pub fn register_write_hooks(registry: &mut WriteHookRegistry) {
    registry.register(
        PathMatcher::suffix(format!("/{POLICY_FILENAME}")),
        Arc::new(GlobalPolicyWriteHook),
    );
}
