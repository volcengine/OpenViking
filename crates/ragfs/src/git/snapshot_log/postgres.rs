use std::future::Future;
use std::str::FromStr;
use std::time::Duration;

use async_trait::async_trait;
use sqlx::postgres::{PgConnectOptions, PgPoolOptions};
use sqlx::{PgPool, Postgres, QueryBuilder};

use super::store::{
    ensure_latest_matches, validate_append, SnapshotLogError, SnapshotLogHit, SnapshotLogLimits,
    SnapshotLogRow, SnapshotLogStore,
};

const POSTGRES_INSERT_BATCH_ROWS: usize = 1_000;
const DEFAULT_OPERATION_TIMEOUT_MS: u64 = 3_000;

async fn with_timeout<T>(
    timeout: Duration,
    operation: impl Future<Output = Result<T, SnapshotLogError>>,
) -> Result<T, SnapshotLogError> {
    tokio::time::timeout(timeout, operation)
        .await
        .map_err(|_| {
            std::io::Error::new(
                std::io::ErrorKind::TimedOut,
                "postgres snapshot_log operation timed out",
            )
        })?
}

/// Shared PostgreSQL path-history index. Schema is provisioned separately.
pub struct PostgresSnapshotLogStore {
    pool: PgPool,
    limits: SnapshotLogLimits,
    operation_timeout: Duration,
}

impl PostgresSnapshotLogStore {
    /// Connect to a pre-provisioned database without creating or migrating tables.
    pub async fn connect(
        dsn: &str,
        max_connections: u32,
        connect_timeout_ms: u64,
    ) -> Result<Self, SnapshotLogError> {
        Self::connect_with_timeout(
            dsn,
            max_connections,
            connect_timeout_ms,
            DEFAULT_OPERATION_TIMEOUT_MS,
        )
        .await
    }

    /// Bound initialization and each complete index operation, including pool
    /// acquisition, lock waits and all statements in an append transaction.
    pub async fn connect_with_timeout(
        dsn: &str,
        max_connections: u32,
        connect_timeout_ms: u64,
        operation_timeout_ms: u64,
    ) -> Result<Self, SnapshotLogError> {
        if dsn.trim().is_empty()
            || max_connections == 0
            || connect_timeout_ms == 0
            || operation_timeout_ms == 0
            || operation_timeout_ms > i32::MAX as u64
        {
            return Err(SnapshotLogError::Config(
                "postgres snapshot_log requires a DSN, positive max_connections and connect_timeout_ms, and operation_timeout_ms in 1..=2147483647"
                    .to_string(),
            ));
        }
        // Never include a URL parser error, which could expose credentials.
        let options = PgConnectOptions::from_str(dsn)
            .map_err(|_| SnapshotLogError::Config("invalid postgres snapshot_log DSN".to_string()))?
            // Apply before any SQL, including startup probing. Server-side limits
            // also stop work and release transaction locks after client cancellation.
            // Appending options preserves a deployment's custom search_path/TLS.
            .options([
                ("statement_timeout", operation_timeout_ms),
                ("lock_timeout", operation_timeout_ms),
            ]);
        let operation_timeout = Duration::from_millis(operation_timeout_ms);
        with_timeout(operation_timeout, async {
            let pool = PgPoolOptions::new()
                .max_connections(max_connections)
                .acquire_timeout(Duration::from_millis(connect_timeout_ms))
                .connect_with(options)
                .await?;
            // Fail initialization if deployment DDL/permissions are missing, allowing
            // the binding's existing best-effort fallback to the Git scan path.
            sqlx::query("SELECT id, account, branch, path, commit_oid FROM snapshot_log LIMIT 0")
                .execute(&pool)
                .await?;
            Ok(Self {
                pool,
                limits: SnapshotLogLimits::default(),
                operation_timeout,
            })
        })
        .await
    }
}

#[async_trait]
impl SnapshotLogStore for PostgresSnapshotLogStore {
    fn limits(&self) -> SnapshotLogLimits {
        self.limits
    }

    async fn load_latest_indexed_commit(
        &self,
        account: &str,
        branch: &str,
    ) -> Result<Option<(i64, String)>, SnapshotLogError> {
        with_timeout(self.operation_timeout, async {
            Ok(sqlx::query_as::<_, (i64, String)>(
                "SELECT id, commit_oid FROM snapshot_log \
             WHERE account = $1 AND branch = $2 ORDER BY id DESC LIMIT 1",
            )
            .bind(account)
            .bind(branch)
            .fetch_optional(&self.pool)
            .await?)
        })
        .await
    }

    async fn append_rows_if_latest(
        &self,
        account: &str,
        branch: &str,
        parent_commit_oid: Option<&str>,
        rows: &[SnapshotLogRow],
    ) -> Result<(), SnapshotLogError> {
        validate_append(self.limits, account, branch, rows)?;
        with_timeout(self.operation_timeout, async {
            let mut tx = self.pool.begin().await?;
            // The latest read must see an append committed while we waited for the
            // lock, even if the deployment's default isolation is REPEATABLE READ.
            sqlx::query("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
                .execute(&mut *tx)
                .await?;
            // Row locking cannot serialize two first appends to an empty branch.
            // Length-prefixing avoids ambiguous (account, branch) concatenations.
            // Hash collisions only serialize unrelated branches; equality is still
            // checked against the original account/branch columns.
            let lock_key = format!("{}:{}{}", account.len(), account, branch);
            sqlx::query("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))")
                .bind(lock_key)
                .execute(&mut *tx)
                .await?;
            let latest = sqlx::query_as::<_, (i64, String)>(
                "SELECT id, commit_oid FROM snapshot_log \
             WHERE account = $1 AND branch = $2 ORDER BY id DESC LIMIT 1",
            )
            .bind(account)
            .bind(branch)
            .fetch_optional(&mut *tx)
            .await?;
            ensure_latest_matches(
                parent_commit_oid,
                latest.as_ref().map(|(_, oid)| oid.as_str()),
            )?;
            for chunk in rows.chunks(POSTGRES_INSERT_BATCH_ROWS) {
                let mut builder = QueryBuilder::<Postgres>::new(
                    "INSERT INTO snapshot_log (account, branch, path, commit_oid) ",
                );
                builder.push_values(chunk, |mut values, row| {
                    values
                        .push_bind(account)
                        .push_bind(branch)
                        .push_bind(&row.path)
                        .push_bind(&row.commit_oid);
                });
                builder.build().execute(&mut *tx).await?;
            }
            tx.commit().await?;
            Ok(())
        })
        .await
    }

    async fn query_path_commits(
        &self,
        account: &str,
        branch: &str,
        paths: &[String],
        max_id: i64,
        offset: usize,
        limit: usize,
    ) -> Result<Vec<SnapshotLogHit>, SnapshotLogError> {
        if limit == 0 || paths.is_empty() {
            return Ok(Vec::new());
        }
        let offset = i64::try_from(offset).map_err(|err| sqlx::Error::Encode(Box::new(err)))?;
        with_timeout(self.operation_timeout, async {
            let rows = sqlx::query_as::<_, (String, i64)>(
                "SELECT commit_oid, MAX(id) AS last_id FROM snapshot_log \
             WHERE account = $1 AND branch = $2 \
               AND md5(path) IN (SELECT md5(p) FROM unnest($3::text[]) AS p) \
               AND path = ANY($3::text[]) AND id <= $4 \
             GROUP BY commit_oid ORDER BY last_id DESC LIMIT $5 OFFSET $6",
            )
            .bind(account)
            .bind(branch)
            .bind(paths)
            .bind(max_id)
            .bind(limit.min(i64::MAX as usize) as i64)
            .bind(offset)
            .fetch_all(&self.pool)
            .await?;
            Ok(rows
                .into_iter()
                .map(|(commit_oid, last_id)| SnapshotLogHit {
                    commit_oid,
                    last_id,
                })
                .collect())
        })
        .await
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn invalid_config_is_rejected_without_exposing_dsn() {
        for (dsn, max, timeout) in [
            ("", 1, 1000),
            ("postgresql://localhost/test", 0, 1000),
            ("postgresql://localhost/test", 1, 0),
            ("postgresql://user:secret@host:not-a-port/test", 1, 1000),
        ] {
            match PostgresSnapshotLogStore::connect(dsn, max, timeout).await {
                Err(SnapshotLogError::Config(message)) => assert!(!message.contains("secret")),
                _ => panic!("invalid configuration must fail before database access"),
            }
        }
        for timeout in [0, i32::MAX as u64 + 1] {
            assert!(matches!(
                PostgresSnapshotLogStore::connect_with_timeout(
                    "postgresql://localhost/test",
                    1,
                    1000,
                    timeout,
                )
                .await,
                Err(SnapshotLogError::Config(_))
            ));
        }
    }
}
