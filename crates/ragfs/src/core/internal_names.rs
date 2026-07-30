//! Static built-in internal names shared across the RAGFS stack.
//!
//! Every module that references `.path.ovlock`, `.exact.ovlock.*`, `.redirect.json`,
//! or `.sync_log.json` must import these constants from this single source of truth.
//! There is no dynamic extension mechanism.

/// Path-lock marker for directory-level locks.
pub const PATH_LOCK_FILE: &str = ".path.ovlock";

/// Prefix for exact (sidecar) lock files: `.exact.ovlock.<safe_name>.<sha1-prefix>`.
pub const EXACT_LOCK_FILE_PREFIX: &str = ".exact.ovlock.";

/// Multi-write redirect metadata file.
pub const REDIRECT_FILE: &str = ".redirect.json";

/// Multi-write sync-log metadata file.
pub const SYNC_LOG_FILE: &str = ".sync_log.json";

/// Returns `true` when `name` is a runtime path-lock file (`.path.ovlock` or `.exact.ovlock.*`).
pub fn is_hidden_runtime_lock_name(name: &str) -> bool {
    name == PATH_LOCK_FILE || name.starts_with(EXACT_LOCK_FILE_PREFIX)
}

/// Returns `true` when `name` is any hidden internal name (lock files, redirect, sync-log).
pub fn is_hidden_internal_name(name: &str) -> bool {
    is_hidden_runtime_lock_name(name) || name == REDIRECT_FILE || name == SYNC_LOG_FILE
}
