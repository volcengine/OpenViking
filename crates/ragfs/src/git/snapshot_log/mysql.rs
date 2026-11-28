use std::time::Duration;

use async_trait::async_trait;
use sqlx::mysql::MySqlPoolOptions;
use sqlx::{MySql, MySqlPool, QueryBuilder};

use super::store::{
    ensure_latest_matches, validate_append, validate_service_name, SnapshotLogError,
    SnapshotLogHit, SnapshotLogLimits, SnapshotLogRow, SnapshotLogStore,
};

const MYSQL_INSERT_BATCH_ROWS: usize = 1_000;

/// MySQL-backed SnapshotLog store.
pub struct MySqlSnapshotLogStore {
    pool: MySqlPool,
    limits: SnapshotLogLimits,
    service_name: String,
}

impl MySqlSnapshotLogStore {
    /// Connect to a pre-provisioned MySQL table without creating or migrating it.
    pub async fn connect(
        service_name: &str,
        dsn: &str,
        max_connections: u32,
        connect_timeout_ms: u64,
    ) -> Result<Self, SnapshotLogError> {
        validate_service_name(service_name)?;
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
        // Fail initialization when deployment DDL/permissions are missing, allowing
        // the binding's best-effort fallback to the Git scan path. The runtime role
        // needs only DML (SELECT/INSERT), so the table is provisioned separately.
        sqlx::query(
            "SELECT id, service_name, account, branch, path, commit_oid FROM snapshot_log LIMIT 0",
        )
        .execute(&pool)
        .await?;
        Ok(Self {
            pool,
            limits: SnapshotLogLimits::default(),
            service_name: service_name.to_string(),
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
             WHERE service_name = ? AND account = ? AND branch = ? ORDER BY id DESC LIMIT 1",
        )
        .bind(&self.service_name)
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
             WHERE service_name = ? AND account = ? AND branch = ? ORDER BY id DESC LIMIT 1 FOR UPDATE",
        )
        .bind(&self.service_name)
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
                "INSERT INTO snapshot_log (service_name, account, branch, path, commit_oid) ",
            );
            builder.push_values(chunk, |mut row_builder, row| {
                row_builder
                    .push_bind(&self.service_name)
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
        let offset =
            i64::try_from(offset).map_err(|err| sqlx::Error::Encode(Box::new(err)))?;
        let mut builder = QueryBuilder::<MySql>::new(
            "SELECT commit_oid, MAX(id) AS last_id FROM snapshot_log \
             WHERE service_name = ",
        );
        builder
            .push_bind(&self.service_name)
            .push(" AND account = ")
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
