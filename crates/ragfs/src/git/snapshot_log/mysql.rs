use std::time::Duration;

use async_trait::async_trait;
use sqlx::mysql::MySqlPoolOptions;
use sqlx::{MySql, MySqlPool, QueryBuilder};

use super::store::{
    ensure_latest_matches, validate_append, SnapshotLogError, SnapshotLogHit, SnapshotLogLimits,
    SnapshotLogRow, SnapshotLogStore,
};

const MYSQL_INSERT_BATCH_ROWS: usize = 1_000;

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
        let row = sqlx::query_as::<_, (i64, Vec<u8>)>(
            "SELECT id, commit_oid FROM snapshot_log \
             WHERE account = ? AND branch = ? ORDER BY id DESC LIMIT 1",
        )
        .bind(account)
        .bind(branch)
        .fetch_optional(&self.pool)
        .await?;
        row.map(|(id, commit_oid)| decode_commit_oid(commit_oid).map(|commit_oid| (id, commit_oid)))
            .transpose()
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
        let latest = sqlx::query_as::<_, (i64, Vec<u8>)>(
            "SELECT id, commit_oid FROM snapshot_log \
             WHERE account = ? AND branch = ? ORDER BY id DESC LIMIT 1 FOR UPDATE",
        )
        .bind(account)
        .bind(branch)
        .fetch_optional(&mut *tx)
        .await?;
        ensure_latest_matches(
            parent_commit_oid,
            latest
                .as_ref()
                .map(|(_, oid)| decode_commit_oid(oid.clone()))
                .transpose()?
                .as_deref(),
        )?;
        for chunk in rows.chunks(MYSQL_INSERT_BATCH_ROWS) {
            let mut builder = QueryBuilder::<MySql>::new(
                "INSERT INTO snapshot_log (account, branch, path, commit_oid) ",
            );
            builder.push_values(chunk, |mut row_builder, row| {
                row_builder
                    .push_bind(account)
                    .push_bind(branch)
                    .push_bind(&row.path)
                    .push_bind(&row.commit_oid);
            });
            builder.build().execute(&mut *tx).await?;
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
            .build_query_as::<(Vec<u8>, i64)>()
            .fetch_all(&self.pool)
            .await?;
        rows.into_iter()
            .map(|(commit_oid, last_id)| {
                decode_commit_oid(commit_oid).map(|commit_oid| SnapshotLogHit {
                    commit_oid,
                    last_id,
                })
            })
            .collect()
    }
}

/// Decode commit OIDs read from MySQL binary-collated columns.
fn decode_commit_oid(commit_oid: Vec<u8>) -> Result<String, SnapshotLogError> {
    String::from_utf8(commit_oid)
        .map_err(|_| SnapshotLogError::UnindexableRow("stored commit_oid is not utf-8".to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Verifies MySQL binary-collated commit OID bytes decode to strings.
    #[test]
    fn decode_commit_oid_accepts_ascii_hex_bytes() {
        let oid = b"0123456789abcdef0123456789abcdef01234567".to_vec();

        let decoded = decode_commit_oid(oid).unwrap();

        assert_eq!(decoded, "0123456789abcdef0123456789abcdef01234567");
    }
}
