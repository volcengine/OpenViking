//! Static built-in internal names shared across the RAGFS stack.
//!
//! Every module that references `.path.ovlock` or `.exact.ovlock.*` must import
//! these constants from this single source of truth.

/// Path-lock marker for directory-level locks.
pub const PATH_LOCK_FILE: &str = ".path.ovlock";

/// Prefix for exact (sidecar) lock files: `.exact.ovlock.<safe_name>.<sha1-prefix>`.
pub const EXACT_LOCK_FILE_PREFIX: &str = ".exact.ovlock.";

/// Returns `true` when `name` is a runtime path-lock file (`.path.ovlock` or `.exact.ovlock.*`).
pub fn is_hidden_runtime_lock_name(name: &str) -> bool {
    // Reserve the whole prefix, including names not produced by the lock resolver.
    name == PATH_LOCK_FILE || name.starts_with(EXACT_LOCK_FILE_PREFIX)
}

/// Returns `true` when `name` is a hidden runtime lock name.
pub fn is_hidden_internal_name(name: &str) -> bool {
    is_hidden_runtime_lock_name(name)
}

/// Returns `true` when a logical path belongs to V2 multi-write internals.
pub fn is_multiwrite_internal_path(path: &str) -> bool {
    let Some(relative) = path.strip_prefix('/') else {
        return false;
    };
    let components = relative.split('/').collect::<Vec<_>>();
    if components
        .last()
        .is_some_and(|name| is_hidden_internal_name(name))
    {
        return true;
    }
    if matches!(
        components.as_slice(),
        ["local", "_system", ".multiwrite.json"]
    ) {
        return true;
    }
    matches!(
        components.as_slice(),
        ["local", account, "_system", "partitions", ..] if !account.is_empty()
    ) || matches!(
        components.as_slice(),
        ["local", account, "temp", ..] if !account.is_empty()
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reserves_entire_exact_lock_prefix() {
        for name in [
            ".exact.ovlock.",
            ".exact.ovlock.probe.md",
            ".exact.ovlock.probe.md.0123456789abcdef",
        ] {
            assert!(is_hidden_runtime_lock_name(name));
            assert!(is_hidden_internal_name(name));
        }
        for name in [
            "probe.md",
            "tasks",
            "_system",
            ".exact.ovlock",
            "x.exact.ovlock.foo",
        ] {
            assert!(!is_hidden_internal_name(name));
        }
    }
}
