//! Rebuildable SQL index of commits in first-parent order.

use super::SnapshotLogError;
use async_trait::async_trait;

/// Immutable commit metadata plus the local deletion timestamp.
#[derive(Debug, Clone, PartialEq, Eq, sqlx::FromRow)]
pub struct CommitIndexRow {
    /// Branch name without the refs/heads/ prefix.
    pub branch: String,
    /// Monotonic position within the indexed branch.
    pub seq: i64,
    /// Hex ID of the commit object.
    pub commit_oid: String,
    /// Original first parent from the commit object, before grafts.
    pub parent_oid: Option<String>,
    /// Root tree recorded by this commit.
    pub tree_oid: String,
    /// Committer timestamp in Unix seconds.
    pub committed_at: i64,
    /// Local copy of the overlay tombstone timestamp.
    pub deleted_at: Option<i64>,
}

/// Parent continuity alone cannot detect a bad tree_oid pointing at a different valid tree.
/// Validate the immutable row before allowing garbage collection to trust its tree.
#[derive(sqlx::FromRow)]
pub(super) struct StoredCommitRow {
    #[sqlx(flatten)]
    row: CommitIndexRow,
    integrity: Vec<u8>,
}

impl StoredCommitRow {
    pub(super) fn verify(self, account: &str) -> Result<CommitIndexRow, SnapshotLogError> {
        let row = &self.row;
        let expected = row_integrity(
            account,
            &row.branch,
            row.seq,
            &row.commit_oid,
            row.parent_oid.as_deref(),
            &row.tree_oid,
            row.committed_at,
        );
        if self.integrity != expected {
            return Err(SnapshotLogError::UnindexableRow(format!(
                "commit index checksum mismatch at sequence {}",
                row.seq
            )));
        }
        Ok(self.row)
    }
}

pub(super) fn row_integrity(
    account: &str,
    branch: &str,
    seq: i64,
    commit: &str,
    raw_parent: Option<&str>,
    tree: &str,
    time: i64,
) -> Vec<u8> {
    use sha2::{Digest, Sha256};
    let mut hash = Sha256::new();
    for field in [
        account,
        branch,
        commit,
        raw_parent.unwrap_or_default(),
        tree,
    ] {
        hash.update((field.len() as u64).to_be_bytes());
        hash.update(field.as_bytes());
    }
    hash.update(seq.to_be_bytes());
    hash.update(time.to_be_bytes());
    hash.finalize().to_vec()
}

/// SQL storage for branch history derived from Git objects and the history overlay.
#[async_trait]
pub trait CommitIndexStore: Send + Sync {
    /// Append after verifying the branch tip in the same transaction.
    async fn append_commit_if_latest(
        &self,
        account: &str,
        branch: &str,
        parent: Option<&str>,
        commit: &str,
        tree: &str,
        time: i64,
    ) -> Result<i64, SnapshotLogError> {
        self.append_with_raw_parent(account, branch, parent, parent, commit, tree, time)
            .await
    }

    /// A grafted commit's original parent may differ from its indexed predecessor.
    async fn append_with_raw_parent(
        &self,
        account: &str,
        branch: &str,
        previous: Option<&str>,
        raw_parent: Option<&str>,
        commit: &str,
        tree: &str,
        time: i64,
    ) -> Result<i64, SnapshotLogError>;
    /// Return the greatest sequence number and its associated commit.
    async fn latest_commit(
        &self,
        account: &str,
        branch: &str,
    ) -> Result<Option<CommitIndexRow>, SnapshotLogError>;
    /// Look up a commit by full hex ID within one account.
    async fn get_commit(
        &self,
        account: &str,
        oid: &str,
    ) -> Result<Option<CommitIndexRow>, SnapshotLogError>;
    /// Find the preceding row without a recorded deletion.
    async fn prev_live(
        &self,
        account: &str,
        branch: &str,
        before: i64,
    ) -> Result<Option<CommitIndexRow>, SnapshotLogError>;
    /// Find the following row without a recorded deletion.
    async fn next_live(
        &self,
        account: &str,
        branch: &str,
        after: i64,
    ) -> Result<Option<CommitIndexRow>, SnapshotLogError>;
    /// Read branch history oldest first and validate each row before returning it.
    async fn list_branch(
        &self,
        account: &str,
        branch: &str,
    ) -> Result<Vec<CommitIndexRow>, SnapshotLogError>;
    /// Record overlay tombstones after their CAS succeeded.
    async fn mark_deleted(
        &self,
        account: &str,
        oids: &[String],
        at: i64,
    ) -> Result<(), SnapshotLogError>;
    /// Remove rows for physically reclaimed commits.
    async fn purge_commits(&self, account: &str, oids: &[String]) -> Result<(), SnapshotLogError>;
    /// Discard indexed history before rebuilding the branch.
    async fn clear_branch(&self, account: &str, branch: &str) -> Result<(), SnapshotLogError>;
}

// Both providers use the same parameter syntax. Instantiate the SQL against their concrete
// connection types so decoding and transaction handling stay checked by sqlx.
macro_rules! impl_commit_index {
    ($store:ty, $select:literal, $begin:literal, $lock:literal) => {
        #[async_trait]
        impl super::CommitIndexStore for $store {
            async fn append_with_raw_parent(
                &self,
                account: &str,
                branch: &str,
                previous: Option<&str>,
                raw_parent: Option<&str>,
                commit: &str,
                tree: &str,
                time: i64,
            ) -> Result<i64, SnapshotLogError> {
                let mut tx = self.pool.begin_with($begin).await?;
                let latest = sqlx::query_as::<_, (i64, String)>(concat!(
                            "SELECT seq, commit_oid FROM snapshot_commit WHERE account=? AND branch=? ORDER BY seq DESC LIMIT 1", $lock))
                            .bind(account).bind(branch).fetch_optional(&mut *tx).await?;
                ensure_latest_matches(previous, latest.as_ref().map(|(_, oid)| oid.as_str()))?;
                let seq = latest.map_or(1, |(seq, _)| seq + 1);
                let integrity = super::commit_index::row_integrity(account, branch, seq, commit, raw_parent, tree, time);
                sqlx::query("INSERT INTO snapshot_commit(account, branch, seq, commit_oid, parent_oid, tree_oid, committed_at, integrity)
                            VALUES(?,?,?,?,?,?,?,?)")
                    .bind(account).bind(branch).bind(seq).bind(commit).bind(raw_parent)
                    .bind(tree).bind(time).bind(integrity).execute(&mut *tx).await?;
                tx.commit().await?;
                Ok(seq)
            }

            async fn latest_commit(
                &self,
                account: &str,
                branch: &str,
            ) -> Result<Option<super::CommitIndexRow>, SnapshotLogError> {
                let row = sqlx::query_as::<_, super::commit_index::StoredCommitRow>(concat!(
                    $select,
                    " WHERE account=? AND branch=? ORDER BY seq DESC LIMIT 1"
                ))
                .bind(account)
                .bind(branch)
                .fetch_optional(&self.pool)
                .await?;
                row.map(|row| row.verify(account)).transpose()
            }

            async fn get_commit(
                &self,
                account: &str,
                oid: &str,
            ) -> Result<Option<super::CommitIndexRow>, SnapshotLogError> {
                let row = sqlx::query_as::<_, super::commit_index::StoredCommitRow>(concat!(
                    $select,
                    " WHERE account=? AND commit_oid=?"
                ))
                .bind(account)
                .bind(oid)
                .fetch_optional(&self.pool)
                .await?;
                row.map(|row| row.verify(account)).transpose()
            }

            async fn prev_live(
                &self,
                account: &str,
                branch: &str,
                before: i64,
            ) -> Result<Option<super::CommitIndexRow>, SnapshotLogError> {
                let row = sqlx::query_as::<_, super::commit_index::StoredCommitRow>(concat!(
                    $select,
                    " WHERE account=? AND branch=? AND seq<? AND deleted_at IS NULL ORDER BY seq DESC LIMIT 1"
                ))
                .bind(account)
                .bind(branch)
                .bind(before)
                .fetch_optional(&self.pool)
                .await?;
                row.map(|row| row.verify(account)).transpose()
            }

            async fn next_live(
                &self,
                account: &str,
                branch: &str,
                after: i64,
            ) -> Result<Option<super::CommitIndexRow>, SnapshotLogError> {
                let row = sqlx::query_as::<_, super::commit_index::StoredCommitRow>(concat!(
                    $select,
                    " WHERE account=? AND branch=? AND seq>? AND deleted_at IS NULL ORDER BY seq LIMIT 1"
                ))
                .bind(account)
                .bind(branch)
                .bind(after)
                .fetch_optional(&self.pool)
                .await?;
                row.map(|row| row.verify(account)).transpose()
            }

            async fn list_branch(
                &self,
                account: &str,
                branch: &str,
            ) -> Result<Vec<super::CommitIndexRow>, SnapshotLogError> {
                let rows = sqlx::query_as::<_, super::commit_index::StoredCommitRow>(concat!(
                    $select, " WHERE account=? AND branch=? ORDER BY seq"
                )).bind(account).bind(branch).fetch_all(&self.pool).await?;
                rows.into_iter().map(|row| row.verify(account)).collect()
            }

            async fn mark_deleted(
                &self,
                account: &str,
                oids: &[String],
                at: i64,
            ) -> Result<(), SnapshotLogError> {
                let mut tx = self.pool.begin().await?;
                for oid in oids {
                    sqlx::query("UPDATE snapshot_commit SET deleted_at=? WHERE account=? AND commit_oid=?")
                        .bind(at)
                        .bind(account)
                        .bind(oid)
                        .execute(&mut *tx)
                        .await?;
                }
                Ok(tx.commit().await?)
            }

            async fn purge_commits(&self, account: &str, oids: &[String]) -> Result<(), SnapshotLogError> {
                let mut tx = self.pool.begin().await?;
                for oid in oids {
                    sqlx::query("DELETE FROM snapshot_commit WHERE account=? AND commit_oid=?")
                                .bind(account).bind(oid).execute(&mut *tx).await?;
                }
                Ok(tx.commit().await?)
            }

            async fn clear_branch(&self, account: &str, branch: &str) -> Result<(), SnapshotLogError> {
                sqlx::query("DELETE FROM snapshot_commit WHERE account=? AND branch=?")
                    .bind(account)
                    .bind(branch)
                    .execute(&self.pool)
                    .await?;
                Ok(())
            }
        }
    };
}

pub(super) use impl_commit_index;
