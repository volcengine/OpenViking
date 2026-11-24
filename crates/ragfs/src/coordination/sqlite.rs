//! Cross-process local coordination. Transactions serialize revision updates;
//! conditional statements protect leader ownership across expiry and takeover.

use super::cache::{ALLOCATE, LATEST, PUBLISH};
use super::{CLEANUP_INTERVAL, REVISION_TTL};
use crate::cache_runtime::{CacheError, CacheResult};
use rusqlite::{params, Connection, OptionalExtension, TransactionBehavior};
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

pub(super) struct SqliteBackend {
    connection: Arc<Mutex<Database>>,
    namespace: String,
}

struct Database {
    connection: Connection,
    last_cleanup: Instant,
}

fn db_error(error: impl std::fmt::Display) -> CacheError {
    CacheError::Unavailable(format!("coordination SQLite: {error}"))
}

fn now_millis() -> CacheResult<i64> {
    i64::try_from(
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(db_error)?
            .as_millis(),
    )
    .map_err(db_error)
}

fn cleanup(connection: &mut Connection) -> CacheResult<()> {
    let tx = connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(db_error)?;
    let now = now_millis()?;
    tx.execute(
        "DELETE FROM coordination_leaders WHERE expires_at<=?1",
        params![now],
    )
    .map_err(db_error)?;
    tx.execute(
        "DELETE FROM coordination_revisions WHERE expires_at<=?1",
        params![now],
    )
    .map_err(db_error)?;
    tx.commit().map_err(db_error)
}

impl SqliteBackend {
    pub fn new(path: &Path, namespace: &str) -> CacheResult<Self> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).map_err(db_error)?;
        }
        let mut connection = Connection::open(path).map_err(db_error)?;
        connection
            .busy_timeout(Duration::from_secs(5))
            .map_err(db_error)?;
        connection
            .execute_batch(
                "PRAGMA journal_mode=WAL;
             PRAGMA synchronous=FULL;
             CREATE TABLE IF NOT EXISTS coordination_leaders (
                 namespace TEXT NOT NULL, key TEXT NOT NULL, token TEXT NOT NULL,
                 expires_at INTEGER NOT NULL, PRIMARY KEY(namespace, key));
             CREATE INDEX IF NOT EXISTS coordination_leaders_expiry
                 ON coordination_leaders(expires_at);
             CREATE TABLE IF NOT EXISTS coordination_revision_counters (
                 namespace TEXT PRIMARY KEY NOT NULL, allocated INTEGER NOT NULL);
             CREATE TABLE IF NOT EXISTS coordination_revisions (
                 namespace TEXT NOT NULL, key TEXT NOT NULL,
                 published INTEGER NOT NULL, expires_at INTEGER NOT NULL,
                 PRIMARY KEY(namespace, key));
             CREATE INDEX IF NOT EXISTS coordination_revisions_expiry
                 ON coordination_revisions(expires_at);",
            )
            .map_err(db_error)?;
        // Startup removes crashed clients' expired leases, including other
        // namespaces in this database, without touching any still-live term.
        cleanup(&mut connection)?;
        Ok(Self {
            connection: Arc::new(Mutex::new(Database {
                connection,
                last_cleanup: Instant::now(),
            })),
            namespace: namespace.into(),
        })
    }

    async fn run<T: Send + 'static>(
        &self,
        operation: impl FnOnce(&mut Connection, &str) -> CacheResult<T> + Send + 'static,
    ) -> CacheResult<T> {
        let connection = self.connection.clone();
        let namespace = self.namespace.clone();
        tokio::task::spawn_blocking(move || {
            let mut database = connection.lock().map_err(db_error)?;
            if database.last_cleanup.elapsed() >= CLEANUP_INTERVAL {
                cleanup(&mut database.connection)?;
                database.last_cleanup = Instant::now();
            }
            operation(&mut database.connection, &namespace)
        })
        .await
        .map_err(db_error)?
    }

    pub async fn acquire(&self, key: &str, token: &str, ttl: Duration) -> CacheResult<bool> {
        self.leader(key, token, Some(ttl), true).await
    }

    pub async fn renew(&self, key: &str, token: &str, ttl: Duration) -> CacheResult<bool> {
        self.leader(key, token, Some(ttl), false).await
    }

    pub async fn release(&self, key: &str, token: &str) -> CacheResult<bool> {
        self.leader(key, token, None, false).await
    }

    async fn leader(
        &self,
        key: &str,
        token: &str,
        ttl: Option<Duration>,
        acquire: bool,
    ) -> CacheResult<bool> {
        let key = key.to_string();
        let token = token.to_string();
        self.run(move |connection, namespace| {
            // Obtain the write lock before sampling time: lock waits must not
            // allow an expired lease to renew against an outdated timestamp.
            let tx = connection.transaction_with_behavior(TransactionBehavior::Immediate).map_err(db_error)?;
            let now = now_millis()?;
            let expiry = now.checked_add(ttl.map(|ttl| ttl.as_millis() as i64).unwrap_or(0))
                .ok_or_else(|| CacheError::InvalidArgument("lease expiry overflow".into()))?;
            let changed = if acquire {
                tx.execute(
                    "INSERT INTO coordination_leaders VALUES (?1, ?2, ?3, ?4)
                     ON CONFLICT(namespace, key) DO UPDATE SET token=excluded.token, expires_at=excluded.expires_at
                     WHERE coordination_leaders.expires_at <= ?5",
                    params![namespace, key, token, expiry, now],
                )
            } else if ttl.is_some() {
                tx.execute(
                    "UPDATE coordination_leaders SET expires_at=?4
                     WHERE namespace=?1 AND key=?2 AND token=?3 AND expires_at>?5",
                    params![namespace, key, token, expiry, now],
                )
            } else {
                tx.execute("DELETE FROM coordination_leaders WHERE namespace=?1 AND key=?2 AND token=?3",
                    params![namespace, key, token])
            }.map_err(db_error)?;
            tx.commit().map_err(db_error)?;
            Ok(changed == 1)
        }).await
    }

    pub async fn revision(
        &self,
        key: &str,
        operation: &'static str,
        value: i64,
    ) -> CacheResult<i64> {
        let key = key.to_string();
        self.run(move |connection, namespace| {
            if operation == LATEST {
                let now = now_millis()?;
                let publication: Option<(i64, i64)> = connection
                    .query_row(
                        "SELECT published, expires_at FROM coordination_revisions
                     WHERE namespace=?1 AND key=?2",
                        params![namespace, key],
                        |row| Ok((row.get(0)?, row.get(1)?)),
                    )
                    .optional()
                    .map_err(db_error)?;
                return match publication {
                    Some((published, expires)) if expires > now => Ok(published),
                    Some(_) => {
                        // The condition preserves a concurrently refreshed publication.
                        connection
                            .execute(
                                "DELETE FROM coordination_revisions
                             WHERE namespace=?1 AND key=?2 AND expires_at<=?3",
                                params![namespace, key, now],
                            )
                            .map_err(db_error)?;
                        Ok(0)
                    }
                    None => Ok(0),
                };
            }
            let tx = connection
                .transaction_with_behavior(TransactionBehavior::Immediate)
                .map_err(db_error)?;
            let now = now_millis()?;
            let result = match operation {
                ALLOCATE => {
                    let allocated: i64 = tx.query_row(
                        "SELECT allocated FROM coordination_revision_counters WHERE namespace=?1",
                        params![namespace], |row| row.get(0),
                    ).optional().map_err(db_error)?.unwrap_or(0);
                    let next = allocated
                        .checked_add(1)
                        .ok_or_else(|| CacheError::InvalidArgument("revision exhausted".into()))?;
                    tx.execute(
                        "INSERT INTO coordination_revision_counters VALUES (?1, ?2)
                         ON CONFLICT(namespace) DO UPDATE SET allocated=excluded.allocated",
                        params![namespace, next],
                    )
                    .map_err(db_error)?;
                    next
                }
                PUBLISH => {
                    let allocated: i64 = tx.query_row(
                        "SELECT allocated FROM coordination_revision_counters WHERE namespace=?1",
                        params![namespace], |row| row.get(0),
                    ).optional().map_err(db_error)?.unwrap_or(0);
                    if value > allocated {
                        return Err(CacheError::InvalidArgument(
                            "revision was not allocated".into(),
                        ));
                    }
                    let published: i64 = tx
                        .query_row(
                            "SELECT published FROM coordination_revisions
                         WHERE namespace=?1 AND key=?2 AND expires_at>?3",
                            params![namespace, key, now],
                            |row| row.get(0),
                        )
                        .optional()
                        .map_err(db_error)?
                        .unwrap_or(0);
                    let latest = published.max(value);
                    tx.execute(
                        "INSERT INTO coordination_revisions VALUES (?1, ?2, ?3, ?4)
                         ON CONFLICT(namespace, key) DO UPDATE
                         SET published=excluded.published, expires_at=excluded.expires_at",
                        params![
                            namespace,
                            key,
                            latest,
                            now + REVISION_TTL.as_millis() as i64
                        ],
                    )
                    .map_err(db_error)?;
                    latest
                }
                _ => unreachable!(),
            };
            tx.commit().map_err(db_error)?;
            Ok(result)
        })
        .await
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn traffic_reclaims_unqueried_expired_records_without_resetting_counter() {
        let directory = tempfile::tempdir().unwrap();
        let backend =
            SqliteBackend::new(&directory.path().join("coordination.db"), "cleanup").unwrap();
        assert_eq!(backend.revision("old", ALLOCATE, 0).await.unwrap(), 1);
        backend.revision("old", PUBLISH, 1).await.unwrap();
        backend
            .acquire("old-watch", "token", Duration::from_secs(60))
            .await
            .unwrap();
        {
            let mut database = backend.connection.lock().unwrap();
            database
                .connection
                .execute_batch(
                    "UPDATE coordination_revisions SET expires_at=0;
                 UPDATE coordination_leaders SET expires_at=0;",
                )
                .unwrap();
            database.last_cleanup = Instant::now() - CLEANUP_INTERVAL;
        }
        assert_eq!(backend.revision("new", ALLOCATE, 0).await.unwrap(), 2);
        let database = backend.connection.lock().unwrap();
        for table in ["coordination_revisions", "coordination_leaders"] {
            let count: i64 = database
                .connection
                .query_row(&format!("SELECT COUNT(*) FROM {table}"), [], |row| {
                    row.get(0)
                })
                .unwrap();
            assert_eq!(count, 0);
        }
    }
}
