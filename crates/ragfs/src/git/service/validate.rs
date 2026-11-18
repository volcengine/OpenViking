use super::*;

/// Validate an `account` id before it is used to build any filesystem path
/// (local backend) or S3 key prefix. This is the Rust-side equivalent of the
/// Python `validate_account_id` and is the single choke point that keeps a
/// crafted account (e.g. `../x`, `a/b`, `a\b`) from escaping its per-account
/// directory / key prefix when a binding is called directly.
///
/// Rules (mirroring `openviking/core/identifiers.py`):
/// - non-empty
/// - not `.` or `..`
/// - only `[A-Za-z0-9_.@-]` (rejects `/`, `\`, whitespace, control chars, …)
/// - at most one `@`
/// - must not start with `_`
pub(super) fn validate_account_id(account: &str) -> Result<(), GitError> {
    if account.is_empty() {
        return Err(GitError::InvalidAccountId("account_id is empty".into()));
    }
    if account == "." || account == ".." {
        return Err(GitError::InvalidAccountId(
            "account_id must not be '.' or '..'".into(),
        ));
    }
    if !account
        .bytes()
        .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'.' | b'@' | b'-'))
    {
        return Err(GitError::InvalidAccountId(format!(
            "account_id must be an alphanumeric string: {account:?}"
        )));
    }
    if account.bytes().filter(|&b| b == b'@').count() > 1 {
        return Err(GitError::InvalidAccountId(
            "account_id must have at most one @".into(),
        ));
    }
    if account.starts_with('_') {
        return Err(GitError::InvalidAccountId(
            "account_id cannot start with underscore _".into(),
        ));
    }
    Ok(())
}

/// Validate `project_dir` matches the rules of `TreeEditor::upsert`:
/// non-empty, no leading/trailing `/`, no empty components, no `.` / `..`
/// segments, no backslash, no control characters. The traversal-related
/// rules guard the same boundary as `validate_account_id`: a direct PyO3
/// caller could otherwise pass `project_dir="../other"` and have the
/// service splice or restore *outside* the account's tree once the path is
/// concatenated into `/local/{account}/{project_dir}/...`.
pub(super) fn validate_project_dir(project_dir: &str) -> Result<(), GitError> {
    if project_dir.is_empty() {
        return Err(GitError::InvalidProjectDir(
            "project_dir must be non-empty".into(),
        ));
    }
    if project_dir.starts_with('/') || project_dir.ends_with('/') {
        return Err(GitError::InvalidProjectDir(format!(
            "project_dir must not start or end with '/': {project_dir:?}"
        )));
    }
    for c in project_dir.split('/') {
        if c.is_empty() {
            return Err(GitError::InvalidProjectDir(format!(
                "project_dir contains empty segment: {project_dir:?}"
            )));
        }
        if c == "." || c == ".." {
            return Err(GitError::InvalidProjectDir(format!(
                "project_dir contains '.' or '..' segment: {project_dir:?}"
            )));
        }
    }
    if project_dir.contains('\\') {
        return Err(GitError::InvalidProjectDir(format!(
            "project_dir must not contain backslash: {project_dir:?}"
        )));
    }
    if project_dir.bytes().any(|b| b < 0x20 || b == 0x7f) {
        return Err(GitError::InvalidProjectDir(format!(
            "project_dir contains control character: {project_dir:?}"
        )));
    }
    Ok(())
}

/// Validate a user-supplied relative path that will be concatenated with
/// `/local/{account}/` (commit) or looked up in a Git tree (show). Same
/// reasoning as `validate_account_id` / `validate_project_dir`: the Rust
/// GitService is a native boundary, so it must defend against `..` /
/// backslash / control chars itself rather than trust the caller (PyO3
/// binding, future SDK consumer) to have normalized first.
///
/// Rules: non-empty; no leading/trailing `/`; no empty, `.`, or `..`
/// segment; no backslash; no control character.
pub(super) fn validate_relative_path(path: &str) -> Result<(), GitError> {
    if path.is_empty() {
        return Err(GitError::InvalidPath("path must be non-empty".into()));
    }
    if path.starts_with('/') || path.ends_with('/') {
        return Err(GitError::InvalidPath(format!(
            "path must not start or end with '/': {path:?}"
        )));
    }
    for c in path.split('/') {
        if c.is_empty() {
            return Err(GitError::InvalidPath(format!(
                "path contains empty segment: {path:?}"
            )));
        }
        if c == "." || c == ".." {
            return Err(GitError::InvalidPath(format!(
                "path contains '.' or '..' segment: {path:?}"
            )));
        }
    }
    if path.contains('\\') {
        return Err(GitError::InvalidPath(format!(
            "path must not contain backslash: {path:?}"
        )));
    }
    if path.bytes().any(|b| b < 0x20 || b == 0x7f) {
        return Err(GitError::InvalidPath(format!(
            "path contains control character: {path:?}"
        )));
    }
    Ok(())
}
