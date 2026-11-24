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
  KEY idx_snapshot_log_commit (account, branch, commit_oid),
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
        // Existing path indexes all start with path or id after the branch. Repair looks
        // up every path for a commit, including installations predating snapshot deletion.
        let exists: Option<i32> = sqlx::query_scalar("SELECT 1 FROM information_schema.statistics
            WHERE table_schema = DATABASE() AND table_name = 'snapshot_log' AND index_name = 'idx_snapshot_log_commit' LIMIT 1")
            .fetch_optional(&pool).await?;
        if exists.is_none() {
            if let Err(err) = sqlx::query(
                "CREATE INDEX idx_snapshot_log_commit ON snapshot_log(account, branch, commit_oid)",
            )
            .execute(&pool)
            .await
            {
                // Another server may have run this migration concurrently.
                if !matches!(&err, sqlx::Error::Database(db) if db.try_downcast_ref::<sqlx::mysql::MySqlDatabaseError>().is_some_and(|err| err.number() == 1061))
                {
                    return Err(err.into());
                }
            }
        }

        sqlx::query("CREATE TABLE IF NOT EXISTS snapshot_log_generation (
            account VARBINARY(255) NOT NULL, branch VARBINARY(512) NOT NULL, root VARCHAR(40) CHARACTER SET ascii NOT NULL,
            PRIMARY KEY(account, branch))").execute(&pool).await?;
        sqlx::query("CREATE TABLE IF NOT EXISTS snapshot_commit (
            account VARBINARY(255) NOT NULL, branch VARBINARY(512) NOT NULL, seq BIGINT NOT NULL,
            commit_oid CHAR(40) CHARACTER SET ascii NOT NULL, parent_oid CHAR(40) CHARACTER SET ascii,
            tree_oid CHAR(40) CHARACTER SET ascii NOT NULL, committed_at BIGINT NOT NULL, deleted_at BIGINT, integrity BINARY(32) NOT NULL,
            PRIMARY KEY(account, branch, seq), UNIQUE KEY(account, commit_oid),
            KEY idx_snapshot_commit_time(account, branch, committed_at))").execute(&pool).await?;
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
    async fn append_rows_at_generation(
        &self,
        account: &str,
        branch: &str,
        parent_commit_oid: Option<&str>,
        rows: &[SnapshotLogRow],
        generation: Option<&str>,
    ) -> Result<(), SnapshotLogError> {
        validate_append(self.limits, account, branch, rows)?;

        let mut tx = self.pool.begin().await?;
        if let Some(expected) = generation {
            let actual = self.lock_generation(&mut tx, account, branch).await?;
            ensure_latest_matches(Some(expected), Some(&actual))?;
        }
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
        offset: usize,
        limit: usize,
    ) -> Result<Vec<SnapshotLogHit>, SnapshotLogError> {
        if limit == 0 || paths.is_empty() {
            return Ok(Vec::new());
        }
        let offset = i64::try_from(offset).map_err(|err| sqlx::Error::Encode(Box::new(err)))?;
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
            .push_bind(limit as i64)
            .push(" OFFSET ")
            .push_bind(offset);
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

super::commit_index::impl_commit_index!(
    MySqlSnapshotLogStore,
    "SELECT CONVERT(branch USING utf8mb4) AS branch, seq, commit_oid, parent_oid, tree_oid, committed_at, deleted_at, integrity FROM snapshot_commit",
    "START TRANSACTION",
    " FOR UPDATE"
);

super::repair::impl_path_history_repair!(MySqlSnapshotLogStore, MySql, "START TRANSACTION",
    "INSERT INTO snapshot_log_generation(account,branch,root) VALUES(?,?,'') ON DUPLICATE KEY UPDATE root=root", " FOR UPDATE", "path");
