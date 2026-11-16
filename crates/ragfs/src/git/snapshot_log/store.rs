use async_trait::async_trait;
use thiserror::Error;

use super::SNAPSHOT_LOG_MAX_ROWS_PER_COMMIT;

/// Provider-specific SnapshotLog indexable length limits.
#[derive(Debug, Clone, Copy)]
pub struct SnapshotLogLimits {
    /// Maximum account id length in bytes.
    pub account_bytes: usize,
    /// Maximum branch name length in bytes.
    pub branch_bytes: usize,
    /// Maximum path length in bytes.
    pub path_bytes: usize,
    /// Maximum rows written for one commit.
    pub max_rows_per_commit: usize,
}

impl Default for SnapshotLogLimits {
    fn default() -> Self {
        Self {
            account_bytes: 255,
            branch_bytes: 512,
            path_bytes: 2048,
            max_rows_per_commit: SNAPSHOT_LOG_MAX_ROWS_PER_COMMIT,
        }
    }
}

/// One SnapshotLog row to append for a commit.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SnapshotLogRow {
    /// Account-relative path, or `/` for the account root.
    pub path: String,
    /// Commit object id as 40 lowercase hex characters.
    pub commit_oid: String,
}

/// One path-history hit returned by a provider query.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SnapshotLogHit {
    /// Commit object id as 40 lowercase hex characters.
    pub commit_oid: String,
    /// Highest SnapshotLog row id that matched this commit.
    pub last_id: i64,
}

/// SnapshotLog provider error.
#[derive(Debug, Error)]
pub enum SnapshotLogError {
    /// SQL provider error.
    #[error("snapshot log sql error: {0}")]
    Sql(#[from] sqlx::Error),
    /// Filesystem error while preparing a provider.
    #[error("snapshot log io error: {0}")]
    Io(#[from] std::io::Error),
    /// Invalid provider configuration.
    #[error("snapshot log config error: {0}")]
    Config(String),
    /// Latest indexed commit did not match the append parent.
    #[error("snapshot log stale latest: expected parent {expected:?}, actual latest {actual:?}")]
    StaleLatest {
        /// Parent commit the caller expected.
        expected: Option<String>,
        /// Current latest indexed commit.
        actual: Option<String>,
    },
    /// Append request had no rows.
    #[error("snapshot log append requires at least one row")]
    EmptyRows,
    /// Append request exceeded provider row cap.
    #[error("snapshot log append has {count} rows, limit {limit}")]
    TooManyRows {
        /// Requested row count.
        count: usize,
        /// Provider row limit.
        limit: usize,
    },
    /// Append row cannot be indexed by this provider.
    #[error("snapshot log row is not indexable: {0}")]
    UnindexableRow(String),
}

impl SnapshotLogError {
    /// Return true when the error means latest indexed commit was not parent.
    pub fn is_stale_latest(&self) -> bool {
        matches!(self, Self::StaleLatest { .. })
    }
}

/// Async storage trait used by `GitService`.
#[async_trait]
pub trait SnapshotLogStore: Send + Sync {
    /// Return provider limits used for safe query/write bypass decisions.
    fn limits(&self) -> SnapshotLogLimits;

    /// Load the newest visible SnapshotLog row for `account+branch`.
    async fn load_latest_indexed_commit(
        &self,
        account: &str,
        branch: &str,
    ) -> Result<Option<(i64, String)>, SnapshotLogError>;

    /// Append all rows only when provider latest equals `parent_commit_oid`.
    async fn append_rows_if_latest(
        &self,
        account: &str,
        branch: &str,
        parent_commit_oid: Option<&str>,
        rows: &[SnapshotLogRow],
    ) -> Result<(), SnapshotLogError>;

    /// Query matching commits for any path, de-duplicated by commit oid.
    async fn query_path_commits(
        &self,
        account: &str,
        branch: &str,
        paths: &[String],
        max_id: i64,
        limit: usize,
    ) -> Result<Vec<SnapshotLogHit>, SnapshotLogError>;
}

/// Return whether an account and branch fit provider index limits.
pub fn can_index_account_branch(
    limits: SnapshotLogLimits,
    account: &str,
    branch: &str,
) -> bool {
    account.len() <= limits.account_bytes && branch.len() <= limits.branch_bytes
}

/// Return whether every path fits provider index limits.
pub fn can_index_paths(limits: SnapshotLogLimits, paths: &[String]) -> bool {
    paths
        .iter()
        .all(|path| !path.is_empty() && path.len() <= limits.path_bytes)
}

/// Expand changed file paths to the file and all ancestor directories.
pub fn fanout_changed_paths(paths: &[String]) -> Vec<String> {
    let mut out = std::collections::BTreeSet::new();
    for path in paths {
        let path = path.trim_matches('/');
        if path.is_empty() {
            continue;
        }
        out.insert(path.to_string());
        let mut current = path;
        while let Some(idx) = current.rfind('/') {
            current = &current[..idx];
            if !current.is_empty() {
                out.insert(current.to_string());
            }
        }
    }
    out.insert("/".to_string());
    out.into_iter().collect()
}

pub(crate) fn validate_append(
    limits: SnapshotLogLimits,
    account: &str,
    branch: &str,
    rows: &[SnapshotLogRow],
) -> Result<(), SnapshotLogError> {
    if rows.is_empty() {
        return Err(SnapshotLogError::EmptyRows);
    }
    if rows.len() > limits.max_rows_per_commit {
        return Err(SnapshotLogError::TooManyRows {
            count: rows.len(),
            limit: limits.max_rows_per_commit,
        });
    }
    if !can_index_account_branch(limits, account, branch) {
        return Err(SnapshotLogError::UnindexableRow(
            "account or branch exceeds provider limit".to_string(),
        ));
    }
    for row in rows {
        if row.path.is_empty() {
            return Err(SnapshotLogError::UnindexableRow(
                "path must not be empty".to_string(),
            ));
        }
        if row.path.len() > limits.path_bytes {
            return Err(SnapshotLogError::UnindexableRow(
                "path exceeds provider limit".to_string(),
            ));
        }
        if row.commit_oid.len() != 40 || !row.commit_oid.bytes().all(|b| b.is_ascii_hexdigit()) {
            return Err(SnapshotLogError::UnindexableRow(
                "commit_oid must be 40 hex characters".to_string(),
            ));
        }
    }
    Ok(())
}

pub(crate) fn ensure_latest_matches(
    expected: Option<&str>,
    actual: Option<&str>,
) -> Result<(), SnapshotLogError> {
    match (expected, actual) {
        (None, None) => Ok(()),
        (Some(expected), Some(actual)) if expected == actual => Ok(()),
        _ => Err(SnapshotLogError::StaleLatest {
            expected: expected.map(str::to_string),
            actual: actual.map(str::to_string),
        }),
    }
}
