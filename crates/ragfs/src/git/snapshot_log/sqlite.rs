use std::path::Path;
use std::str::FromStr;
use std::time::Duration;

use async_trait::async_trait;
use sqlx::sqlite::{SqliteConnectOptions, SqliteJournalMode, SqlitePoolOptions};
use sqlx::{QueryBuilder, Sqlite, SqlitePool};

use super::store::{
    ensure_latest_matches, validate_append, SnapshotLogError, SnapshotLogHit, SnapshotLogLimits,
    SnapshotLogRow, SnapshotLogStore,
};

const SQLITE_SCHEMA: &[&str] = &[
    r#"CREATE TABLE IF NOT EXISTS snapshot_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account TEXT NOT NULL,
  branch TEXT NOT NULL,
  path TEXT NOT NULL,
  commit_oid TEXT NOT NULL,
  created_at INTEGER NOT NULL DEFAULT (unixepoch())
)"#,
    r#"CREATE UNIQUE INDEX IF NOT EXISTS uniq_snapshot_log_path_commit
ON snapshot_log (account, branch, path, commit_oid)"#,
    r#"CREATE INDEX IF NOT EXISTS idx_snapshot_log_lookup
ON snapshot_log (account, branch, path, id DESC)"#,
    r#"CREATE INDEX IF NOT EXISTS idx_snapshot_log_latest
ON snapshot_log (account, branch, id DESC)"#,
];

/// SQLite-backed SnapshotLog store.
pub struct SqliteSnapshotLogStore {
    pool: SqlitePool,
    limits: SnapshotLogLimits,
}

impl SqliteSnapshotLogStore {
    /// Open a SQLite SnapshotLog store and run idempotent migrations.
    pub async fn connect(path: &str) -> Result<Self, SnapshotLogError> {
        if !Path::new(path).is_absolute() {
            return Err(SnapshotLogError::Config(
                "sqlite snapshot_log path must be absolute".to_string(),
            ));
        }
        if let Some(parent) = Path::new(path).parent() {
            tokio::fs::create_dir_all(parent).await?;
        }
        let dsn = format!("sqlite://{path}");
        let options = SqliteConnectOptions::from_str(&dsn)?
            .create_if_missing(true)
            .journal_mode(SqliteJournalMode::Wal)
            .busy_timeout(Duration::from_secs(5));
        let pool = SqlitePoolOptions::new()
            .max_connections(5)
            .connect_with(options)
            .await?;
        for statement in SQLITE_SCHEMA {
            sqlx::query(statement).execute(&pool).await?;
        }
        Ok(Self {
            pool,
            limits: SnapshotLogLimits::default(),
        })
    }
}

#[async_trait]
impl SnapshotLogStore for SqliteSnapshotLogStore {
    /// Return SQLite provider limits.
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

    /// Append rows after checking latest in a `BEGIN IMMEDIATE` transaction.
    async fn append_rows_if_latest(
        &self,
        account: &str,
        branch: &str,
        parent_commit_oid: Option<&str>,
        rows: &[SnapshotLogRow],
    ) -> Result<(), SnapshotLogError> {
        validate_append(self.limits, account, branch, rows)?;

        let mut conn = self.pool.acquire().await?;
        sqlx::query("BEGIN IMMEDIATE").execute(&mut *conn).await?;

        let result = async {
            let latest = sqlx::query_as::<_, (i64, String)>(
                "SELECT id, commit_oid FROM snapshot_log \
                 WHERE account = ? AND branch = ? ORDER BY id DESC LIMIT 1",
            )
            .bind(account)
            .bind(branch)
            .fetch_optional(&mut *conn)
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
                .execute(&mut *conn)
                .await?;
            }
            Ok::<(), SnapshotLogError>(())
        }
        .await;

        match result {
            Ok(()) => {
                sqlx::query("COMMIT").execute(&mut *conn).await?;
                Ok(())
            }
            Err(err) => {
                let _ = sqlx::query("ROLLBACK").execute(&mut *conn).await;
                Err(err)
            }
        }
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
        let mut builder = QueryBuilder::<Sqlite>::new(
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
