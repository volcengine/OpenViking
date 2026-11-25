//! Repair path rows and record the history generation in one transaction.
//! Each process may have its own SQLite database while sharing object storage.

use super::SnapshotLogError;

/// Path changes for one surviving boundary child of a deleted interval.
pub struct PathHistoryRepair {
    /// IDs of commits skipped by this graft.
    pub deleted: Vec<String>,
    /// Retained child whose indexed changes must be updated.
    pub child: String,
    /// Exact changed paths including directory ancestors.
    pub keep_paths: Vec<String>,
}

pub(super) fn decode_path(bytes: Vec<u8>) -> Result<String, SnapshotLogError> {
    String::from_utf8(bytes)
        .map_err(|_| SnapshotLogError::UnindexableRow("invalid UTF-8 path".into()))
}

macro_rules! impl_path_history_repair {
    ($store:ty, $db:ty, $begin:literal, $insert:literal, $lock:literal, $path:literal, $p1:literal, $p2:literal, $p3:literal) => {
        impl $store {
            async fn lock_generation<'a>(
                &self,
                tx: &mut sqlx::Transaction<'a, $db>,
                account: &str,
                branch: &str,
            ) -> Result<String, SnapshotLogError> {
                sqlx::query($insert)
                    .bind(account)
                    .bind(branch)
                    .execute(&mut **tx)
                    .await?;
                Ok(sqlx::query_scalar(concat!(
                    concat!(
                        "SELECT root FROM snapshot_log_generation WHERE account=",
                        $p1,
                        " AND branch=",
                        $p2
                    ),
                    $lock
                ))
                .bind(account)
                .bind(branch)
                .fetch_one(&mut **tx)
                .await?)
            }

            async fn repair_run(
                &self,
                tx: &mut sqlx::Transaction<'_, $db>,
                account: &str,
                branch: &str,
                run: &super::PathHistoryRepair,
            ) -> Result<bool, SnapshotLogError> {
                use std::collections::{HashMap, HashSet};
                let mut missing: HashSet<_> = run.keep_paths.iter().map(String::as_str).collect();
                let rows = sqlx::query_as::<_, (i64, Vec<u8>)>(concat!(
                    "SELECT id, ",
                    $path,
                    concat!(
                        " FROM snapshot_log WHERE account=",
                        $p1,
                        " AND branch=",
                        $p2,
                        " AND commit_oid=",
                        $p3
                    )
                ))
                .bind(account)
                .bind(branch)
                .bind(&run.child)
                .fetch_all(&mut **tx)
                .await?;
                if rows.is_empty() {
                    return Ok(false);
                }
                for (id, path) in rows {
                    if !missing.remove(super::repair::decode_path(path)?.as_str()) {
                        sqlx::query(concat!("DELETE FROM snapshot_log WHERE id=", $p1))
                            .bind(id)
                            .execute(&mut **tx)
                            .await?;
                    }
                }
                let mut reusable = HashMap::new();
                for oid in &run.deleted {
                    let rows = sqlx::query_as::<_, (i64, Vec<u8>)>(concat!(
                        "SELECT id, ",
                        $path,
                        concat!(
                            " FROM snapshot_log WHERE account=",
                            $p1,
                            " AND branch=",
                            $p2,
                            " AND commit_oid=",
                            $p3
                        )
                    ))
                    .bind(account)
                    .bind(branch)
                    .bind(oid)
                    .fetch_all(&mut **tx)
                    .await?;
                    for (id, path) in rows {
                        let path = super::repair::decode_path(path)?;
                        if missing.contains(path.as_str()) {
                            let best = reusable.entry(path).or_insert(id);
                            *best = (*best).max(id);
                        }
                    }
                }
                if reusable.len() != missing.len() {
                    return Ok(false);
                }
                for id in reusable.into_values() {
                    sqlx::query(concat!(
                        "UPDATE snapshot_log SET commit_oid=",
                        $p1,
                        " WHERE id=",
                        $p2
                    ))
                    .bind(&run.child)
                    .bind(id)
                    .execute(&mut **tx)
                    .await?;
                }
                for oid in &run.deleted {
                    sqlx::query(concat!(
                        "DELETE FROM snapshot_log WHERE account=",
                        $p1,
                        " AND branch=",
                        $p2,
                        " AND commit_oid=",
                        $p3
                    ))
                    .bind(account)
                    .bind(branch)
                    .bind(oid)
                    .execute(&mut **tx)
                    .await?;
                }
                Ok(true)
            }
        }

        #[async_trait]
        impl super::PathHistoryGeneration for $store {
            async fn clear_branch(
                &self,
                account: &str,
                branch: &str,
            ) -> Result<(), SnapshotLogError> {
                let mut tx = self.pool.begin_with($begin).await?;
                self.lock_generation(&mut tx, account, branch).await?;
                sqlx::query(concat!(
                    "DELETE FROM snapshot_log WHERE account=",
                    $p1,
                    " AND branch=",
                    $p2
                ))
                .bind(account)
                .bind(branch)
                .execute(&mut *tx)
                .await?;
                Ok(tx.commit().await?)
            }

            async fn reparent_after_delete(
                &self,
                account: &str,
                branch: &str,
                deleted_commit_oids: &[String],
                child_commit_oid: &str,
                keep_paths: &[String],
            ) -> Result<bool, SnapshotLogError> {
                let mut tx = self.pool.begin_with($begin).await?;
                self.lock_generation(&mut tx, account, branch).await?;
                let run = super::PathHistoryRepair {
                    deleted: deleted_commit_oids.to_vec(),
                    child: child_commit_oid.to_owned(),
                    keep_paths: keep_paths.to_vec(),
                };
                if !self.repair_run(&mut tx, account, branch, &run).await? {
                    return Ok(false);
                }
                tx.commit().await?;
                Ok(true)
            }

            async fn generation(
                &self,
                account: &str,
                branch: &str,
            ) -> Result<String, SnapshotLogError> {
                Ok(sqlx::query_scalar(concat!(
                    "SELECT root FROM snapshot_log_generation WHERE account=",
                    $p1,
                    " AND branch=",
                    $p2
                ))
                .bind(account)
                .bind(branch)
                .fetch_optional(&self.pool)
                .await?
                .unwrap_or_default())
            }

            async fn prepare_generation(
                &self,
                account: &str,
                branch: &str,
                root: &str,
            ) -> Result<(), SnapshotLogError> {
                let mut tx = self.pool.begin_with($begin).await?;
                if self.lock_generation(&mut tx, account, branch).await? != root {
                    sqlx::query(concat!(
                        "DELETE FROM snapshot_log WHERE account=",
                        $p1,
                        " AND branch=",
                        $p2
                    ))
                    .bind(account)
                    .bind(branch)
                    .execute(&mut *tx)
                    .await?;
                    sqlx::query(concat!(
                        "UPDATE snapshot_log_generation SET root=",
                        $p1,
                        " WHERE account=",
                        $p2,
                        " AND branch=",
                        $p3
                    ))
                    .bind(root)
                    .bind(account)
                    .bind(branch)
                    .execute(&mut *tx)
                    .await?;
                }
                Ok(tx.commit().await?)
            }

            async fn repair_generation(
                &self,
                account: &str,
                branch: &str,
                base: &str,
                root: &str,
                runs: &[super::PathHistoryRepair],
            ) -> Result<bool, SnapshotLogError> {
                let mut tx = self.pool.begin_with($begin).await?;
                if self.lock_generation(&mut tx, account, branch).await? != base {
                    return Ok(false);
                }
                for run in runs {
                    if !self.repair_run(&mut tx, account, branch, run).await? {
                        return Ok(false);
                    }
                }
                sqlx::query(concat!(
                    "UPDATE snapshot_log_generation SET root=",
                    $p1,
                    " WHERE account=",
                    $p2,
                    " AND branch=",
                    $p3
                ))
                .bind(root)
                .bind(account)
                .bind(branch)
                .execute(&mut *tx)
                .await?;
                tx.commit().await?;
                Ok(true)
            }
        }
    };
}

pub(super) use impl_path_history_repair;

/// Track which history overlay the path rows reflect, including across database processes.
#[async_trait::async_trait]
pub trait PathHistoryGeneration: Send + Sync {
    /// Discard path rows for one branch.
    async fn clear_branch(&self, account: &str, branch: &str) -> Result<(), SnapshotLogError>;
    /// Repair one interval atomically, returning false when a full backfill is necessary.
    async fn reparent_after_delete(
        &self,
        account: &str,
        branch: &str,
        deleted_commit_oids: &[String],
        child_commit_oid: &str,
        keep_paths: &[String],
    ) -> Result<bool, SnapshotLogError>;

    /// Get the indexed overlay root, or the empty string for pre-overlay history.
    async fn generation(&self, account: &str, branch: &str) -> Result<String, SnapshotLogError>;
    /// Clear stale path rows before backfilling a different overlay.
    async fn prepare_generation(
        &self,
        account: &str,
        branch: &str,
        root: &str,
    ) -> Result<(), SnapshotLogError>;
    /// Publish all graft corrections for a branch together with its new generation.
    async fn repair_generation(
        &self,
        account: &str,
        branch: &str,
        base: &str,
        root: &str,
        runs: &[PathHistoryRepair],
    ) -> Result<bool, SnapshotLogError>;
}
