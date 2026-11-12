use std::time::Duration;

use async_trait::async_trait;
use sqlx::mysql::MySqlPoolOptions;
use sqlx::{MySql, MySqlPool, QueryBuilder};

use super::store::{
    ensure_latest_matches, validate_append, SnapshotLogError, SnapshotLogHit, SnapshotLogLimits,
    SnapshotLogRow, SnapshotLogStore,
};

const MYSQL_SCHEMA: &str = r#"CREATE TABLE IF NOT EXISTS snapshot_log (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  account VARBINARY(255) NOT NULL,
  branch VARBINARY(512) NOT NULL,
  path VARBINARY(2048) NOT NULL,
  commit_oid CHAR(40) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uniq_snapshot_log_path_commit (account, branch, path, commit_oid),
  KEY idx_snapshot_log_latest (account, branch, id DESC),
  KEY idx_snapshot_log_lookup (account, branch, path, id DESC)
)"#;

/// MySQL-backed SnapshotLog store.
pub struct MySqlSnapshotLogStore {
    pool: MySqlPool,
    limits: SnapshotLogLimits,
}

impl MySqlSnapshotLogStore {
    /// Open a MySQL SnapshotLog store and run idempotent migrations.
    pub async fn connect(
        dsn: &str,
        max_connections: u32,
        connect_timeout_ms: u64,
    ) -> Result<Self, SnapshotLogError> {
        if dsn.is_empty() {
            return Err(SnapshotLogError::Config(
                "mysql snapshot_log dsn must not be empty".to_string(),
            ));
        }
        let pool = MySqlPoolOptions::new()
            .max_connections(max_connections)
            .acquire_timeout(Duration::from_millis(connect_timeout_ms))
            .connect(dsn)
            .await?;
        sqlx::query(MYSQL_SCHEMA).execute(&pool).await?;
        Ok(Self {
            pool,
            limits: SnapshotLogLimits::default(),
        })
    }
}

#[async_trait]
impl SnapshotLogStore for MySqlSnapshotLogStore {
    /// Return MySQL provider limits.
    fn limits(&self) -> SnapshotLogLimits {
        self.limits
    }

    /// Load newest indexed commit for an account branch.
    async fn load_latest_indexed_commit(
        &self,
        account: &str,
        branch: &str,
    ) -> Result<Option<(i64, String)>, SnapshotLogError> {
        let row = sqlx::query_as::<_, (i64, String)>(
            "SELECT id, commit_oid FROM snapshot_log \
             WHERE account = ? AND branch = ? ORDER BY id DESC LIMIT 1",
        )
        .bind(account)
        .bind(branch)
        .fetch_optional(&self.pool)
        .await?;
        Ok(row)
    }

    /// Append rows after checking latest under `SELECT ... FOR UPDATE`.
    async fn append_rows_if_latest(
        &self,
        account: &str,
        branch: &str,
        parent_commit_oid: Option<&str>,
        rows: &[SnapshotLogRow],
    ) -> Result<(), SnapshotLogError> {
        validate_append(self.limits, account, branch, rows)?;

        let mut tx = self.pool.begin().await?;
        let latest = sqlx::query_as::<_, (i64, String)>(
            "SELECT id, commit_oid FROM snapshot_log \
             WHERE account = ? AND branch = ? ORDER BY id DESC LIMIT 1 FOR UPDATE",
        )
        .bind(account)
        .bind(branch)
        .fetch_optional(&mut *tx)
        .await?;
        ensure_latest_matches(
            parent_commit_oid,
            latest.as_ref().map(|(_, oid)| oid.as_str()),
        )?;
        for row in rows {
            sqlx::query(
                "INSERT INTO snapshot_log (account, branch, path, commit_oid) \
                 VALUES (?, ?, ?, ?)",
            )
            .bind(account)
            .bind(branch)
            .bind(&row.path)
            .bind(&row.commit_oid)
            .execute(&mut *tx)
            .await?;
        }
        tx.commit().await?;
        Ok(())
    }

    /// Query path hits using bound dynamic `IN` parameters.
    async fn query_path_commits(
        &self,
        account: &str,
        branch: &str,
        paths: &[String],
        max_id: i64,
        limit: usize,
    ) -> Result<Vec<SnapshotLogHit>, SnapshotLogError> {
        if limit == 0 || paths.is_empty() {
            return Ok(Vec::new());
        }
        let mut builder = QueryBuilder::<MySql>::new(
            "SELECT commit_oid, MAX(id) AS last_id FROM snapshot_log \
             WHERE account = ",
        );
        builder
            .push_bind(account)
            .push(" AND branch = ")
            .push_bind(branch)
            .push(" AND path IN (");
        let mut separated = builder.separated(", ");
        for path in paths {
            separated.push_bind(path);
        }
        separated.push_unseparated(") AND id <= ");
        builder
            .push_bind(max_id)
            .push(" GROUP BY commit_oid ORDER BY last_id DESC LIMIT ")
            .push_bind(limit as i64);
        let rows = builder
            .build_query_as::<(String, i64)>()
            .fetch_all(&self.pool)
            .await?;
        Ok(rows
            .into_iter()
            .map(|(commit_oid, last_id)| SnapshotLogHit {
                commit_oid,
                last_id,
            })
            .collect())
    }
}
