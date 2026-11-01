//! Same-machine, multi-process locks stored in one authoritative SQLite database.

use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use async_trait::async_trait;
use rusqlite::{params, Connection, OptionalExtension, TransactionBehavior};

use super::path::validate_normalized_logical_path as validate_path;
use super::provider::{
    AcquisitionChange, AtomicAcquisition, AtomicAcquisitionResult, PathLockHandleMode,
    PathLockProvider,
};
use super::types::{LockToken, PathLockError, PathLockKind, PathLockRequest, PathLockResult};

/// Transactional local lock storage. All participants must share the database and namespace.
pub struct SQLitePathLockProvider {
    // A single connection also serializes reads. A future read pool can use WAL
    // snapshots independently while retaining serialized write transactions.
    conn: Arc<Mutex<Connection>>,
    namespace: String,
}

// Absorb transient SQLite writer contention even when the manager's logical
// lock wait timeout is zero. This wait runs only on a blocking worker thread.
const SQLITE_BUSY_TIMEOUT: Duration = Duration::from_millis(100);
// Three total attempts, with 1 ms / 4 ms backoff after Busy. Each attempt keeps
// SQLite's 100 ms busy timeout; the backoff is additional to database waits.
const SQLITE_BUSY_ATTEMPTS: usize = 3;

fn db_error(error: rusqlite::Error) -> PathLockError {
    match &error {
        rusqlite::Error::SqliteFailure(code, _)
            if matches!(
                code.code,
                rusqlite::ErrorCode::DatabaseBusy | rusqlite::ErrorCode::DatabaseLocked
            ) =>
        {
            PathLockError::Busy {
                lock_path: "sqlite".to_string(),
                operation: "transaction".to_string(),
            }
        }
        _ => PathLockError::Io(format!("SQLite PathLock: {error}")),
    }
}

// Fixed-width decimal TEXT preserves the entire u128 range and lexical ordering.
fn timestamp(time_ns: u128) -> String {
    format!("{time_ns:039}")
}

fn validate_owner(owner: &str) -> PathLockResult<()> {
    if owner.is_empty() || owner.contains(':') {
        return Err(PathLockError::InvalidRequest(
            "lock owner must be nonempty and contain no ':'".to_string(),
        ));
    }
    Ok(())
}

fn ancestors(path: &str) -> Vec<&str> {
    if path == "/" {
        return Vec::new();
    }
    let mut result = vec!["/"];
    for (index, _) in path.match_indices('/').skip(1) {
        result.push(&path[..index]);
    }
    result
}

fn descendant_bounds(path: &str) -> (String, String) {
    if path == "/" {
        ("/".to_string(), "0".to_string())
    } else {
        // '/' is immediately followed by '0' in SQLite's BINARY collation.
        (format!("{path}/"), format!("{path}0"))
    }
}

fn decode_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<LockToken> {
    let time: String = row.get(1)?;
    let kind: String = row.get(2)?;
    let invalid = || rusqlite::Error::InvalidQuery;
    Ok(LockToken {
        owner_id: row.get(0)?,
        time_ns: time.parse().map_err(|_| invalid())?,
        lock_type: match kind.as_str() {
            "E" => PathLockKind::Exact,
            "T" => PathLockKind::Tree,
            _ => return Err(invalid()),
        },
    })
}

fn read(conn: &Connection, namespace: &str, path: &str) -> PathLockResult<Option<LockToken>> {
    conn.query_row(
        "SELECT owner, time_ns, kind FROM pathlock_tokens WHERE namespace=?1 AND path=?2",
        params![namespace, path],
        decode_row,
    )
    .optional()
    .map_err(db_error)
}

fn write(conn: &Connection, namespace: &str, path: &str, token: &LockToken) -> PathLockResult<()> {
    conn.execute(
        "INSERT INTO pathlock_tokens(namespace, path, owner, time_ns, kind)
         VALUES (?1,?2,?3,?4,?5)
         ON CONFLICT(namespace,path) DO UPDATE SET
         owner=excluded.owner, time_ns=excluded.time_ns, kind=excluded.kind",
        params![
            namespace,
            path,
            token.owner_id,
            timestamp(token.time_ns),
            token.lock_type.to_code().to_string()
        ],
    )
    .map_err(db_error)?;
    Ok(())
}

fn conflict(path: &str, token: &LockToken) -> PathLockError {
    PathLockError::Conflict {
        lock_path: path.to_string(),
        owner: token.owner_id.clone(),
        kind: token.lock_type,
    }
}

impl SQLitePathLockProvider {
    /// Open a persistent local database. Its parent directory must already exist.
    ///
    /// Use the same absolute file path and namespace in every cooperating process.
    /// SQLite file locking is intended for local disks, not network filesystems.
    /// Runtime SQLite Busy waits use 100 ms per attempt, with at most three
    /// attempts and 1 ms / 4 ms backoff, independently of the manager's logical
    /// lock wait timeout. All waits run on blocking worker threads.
    pub fn new(db_path: &str, namespace: &str) -> PathLockResult<Self> {
        if !Path::new(db_path).is_absolute() || namespace.trim().is_empty() {
            return Err(PathLockError::InvalidRequest(
                "SQLite PathLock requires an absolute sqlite_path and nonempty service_name"
                    .to_string(),
            ));
        }
        let conn = Connection::open(db_path).map_err(db_error)?;
        // Allow concurrent process initialization.
        conn.busy_timeout(Duration::from_secs(5))
            .map_err(db_error)?;
        // FULL favors durability of committed lock records over NORMAL's lower
        // synchronization cost; losing a held token could allow conflicting writes.
        conn.execute_batch(
            "PRAGMA journal_mode=WAL;
             PRAGMA synchronous=FULL;
             CREATE TABLE IF NOT EXISTS pathlock_tokens (
                 namespace TEXT NOT NULL,
                 path TEXT COLLATE BINARY NOT NULL,
                 owner TEXT NOT NULL,
                 time_ns TEXT COLLATE BINARY NOT NULL CHECK(length(time_ns)=39),
                 kind TEXT NOT NULL CHECK(kind IN ('E','T')),
                 PRIMARY KEY(namespace,path)
             ) WITHOUT ROWID;
             CREATE INDEX IF NOT EXISTS pathlock_expiry ON pathlock_tokens(namespace,time_ns);",
        )
        .map_err(db_error)?;
        conn.busy_timeout(SQLITE_BUSY_TIMEOUT).map_err(db_error)?;
        Ok(Self {
            conn: Arc::new(Mutex::new(conn)),
            namespace: namespace.to_string(),
        })
    }

    async fn run<T, F>(&self, mut operation: F) -> PathLockResult<T>
    where
        T: Send + 'static,
        F: FnMut(&mut Connection, &str) -> PathLockResult<T> + Send + 'static,
    {
        let conn = self.conn.clone();
        let namespace = self.namespace.clone();
        tokio::task::spawn_blocking(move || {
            let mut conn = conn
                .lock()
                .map_err(|_| PathLockError::Internal("SQLite connection mutex poisoned".into()))?;
            for attempt in 0..SQLITE_BUSY_ATTEMPTS {
                match operation(&mut conn, &namespace) {
                    Err(PathLockError::Busy { .. }) if attempt + 1 < SQLITE_BUSY_ATTEMPTS => {
                        // Transaction closures drop (and roll back) before returning.
                        // If rollback failed, clean up before replaying any mutations.
                        if !conn.is_autocommit() {
                            conn.execute_batch("ROLLBACK").map_err(db_error)?;
                        }
                        std::thread::sleep(Duration::from_millis(1 << (2 * attempt)));
                    }
                    result => return result,
                }
            }
            unreachable!("the final Busy attempt returns its error")
        })
        .await
        .map_err(|error| PathLockError::Internal(format!("SQLite worker failed: {error}")))?
    }
}

#[async_trait]
impl PathLockProvider for SQLitePathLockProvider {
    fn name(&self) -> &'static str {
        "sqlite"
    }

    fn handle_mode(&self) -> PathLockHandleMode {
        PathLockHandleMode::LogicalPath
    }

    async fn try_acquire_batch_atomic(
        &self,
        requests: &[PathLockRequest],
        owner_id: &str,
        now_ns: u128,
        stale_before_ns: u128,
    ) -> PathLockResult<Option<Vec<AtomicAcquisition>>> {
        Ok(self
            .try_acquire_batch_atomic_with_stats(requests, owner_id, now_ns, stale_before_ns)
            .await?
            .map(|result| result.acquisitions))
    }

    async fn try_acquire_batch_atomic_with_stats(
        &self,
        requests: &[PathLockRequest],
        owner_id: &str,
        now_ns: u128,
        stale_before_ns: u128,
    ) -> PathLockResult<Option<AtomicAcquisitionResult>> {
        validate_owner(owner_id)?;
        if requests.is_empty() {
            return Err(PathLockError::InvalidRequest("empty lock batch".into()));
        }
        for request in requests {
            validate_path(&request.path)?;
        }
        let requests = requests.to_vec();
        let owner = owner_id.to_string();
        self.run(move |conn, namespace| {
            let tx = conn
                .transaction_with_behavior(TransactionBehavior::Immediate)
                .map_err(db_error)?;
            let stale_before = timestamp(stale_before_ns);
            // Opportunistic global cleanup is bounded. Conflicting stale records
            // must also be removed completely below so their owners cannot revive
            // them after a successful takeover.
            let mut stale_tokens_removed = tx
                .execute(
                    "DELETE FROM pathlock_tokens WHERE (namespace,path) IN (
                 SELECT namespace,path FROM pathlock_tokens
                 WHERE namespace=?1 AND time_ns<?2 AND owner<>?3 LIMIT 256)",
                    params![namespace, stale_before, owner],
                )
                .map_err(db_error)?;
            let mut changes = Vec::with_capacity(requests.len());
            for request in &requests {
                stale_tokens_removed += tx
                    .execute(
                        "DELETE FROM pathlock_tokens
                         WHERE namespace=?1 AND path=?2 AND owner<>?3 AND time_ns<?4",
                        params![namespace, request.path, owner, stale_before],
                    )
                    .map_err(db_error)?;
                let existing = read(&tx, namespace, &request.path)?;
                if let Some(token) = &existing {
                    if token.owner_id != owner && token.time_ns >= stale_before_ns {
                        return Err(conflict(&request.path, token));
                    }
                }
                for ancestor in ancestors(&request.path) {
                    stale_tokens_removed += tx
                        .execute(
                            "DELETE FROM pathlock_tokens
                             WHERE namespace=?1 AND path=?2 AND owner<>?3
                             AND time_ns<?4 AND kind='T'",
                            params![namespace, ancestor, owner, stale_before],
                        )
                        .map_err(db_error)?;
                    if let Some(token) = read(&tx, namespace, ancestor)? {
                        if token.lock_type == PathLockKind::Tree
                            && token.owner_id != owner
                            && token.time_ns >= stale_before_ns
                        {
                            return Err(conflict(ancestor, &token));
                        }
                    }
                }
                if request.kind == PathLockKind::Tree {
                    let (lower, upper) = descendant_bounds(&request.path);
                    stale_tokens_removed += tx
                        .execute(
                            "DELETE FROM pathlock_tokens
                             WHERE namespace=?1 AND path>=?2 AND path<?3 AND path<>?4
                             AND owner<>?5 AND time_ns<?6",
                            params![namespace, lower, upper, request.path, owner, stale_before],
                        )
                        .map_err(db_error)?;
                    let found = tx
                        .query_row(
                            "SELECT path, owner, time_ns, kind FROM pathlock_tokens
                             WHERE namespace=?1 AND path>=?2 AND path<?3 AND path<>?4
                             AND owner<>?5 AND time_ns>=?6 LIMIT 1",
                            params![namespace, lower, upper, request.path, owner, stale_before],
                            |row| {
                                let path: String = row.get(0)?;
                                Ok((
                                    path,
                                    LockToken {
                                        owner_id: row.get(1)?,
                                        time_ns: row
                                            .get::<_, String>(2)?
                                            .parse()
                                            .map_err(|_| rusqlite::Error::InvalidQuery)?,
                                        lock_type: match row.get::<_, String>(3)?.as_str() {
                                            "E" => PathLockKind::Exact,
                                            "T" => PathLockKind::Tree,
                                            _ => return Err(rusqlite::Error::InvalidQuery),
                                        },
                                    },
                                ))
                            },
                        )
                        .optional()
                        .map_err(db_error)?;
                    if let Some((path, token)) = found {
                        return Err(conflict(&path, &token));
                    }
                }
                let replacement = LockToken {
                    owner_id: owner.clone(),
                    time_ns: now_ns,
                    lock_type: request.kind,
                };
                let change = match existing {
                    Some(previous) if previous.owner_id == owner => {
                        if previous.lock_type == PathLockKind::Exact
                            && request.kind == PathLockKind::Tree
                        {
                            write(&tx, namespace, &request.path, &replacement)?;
                            AcquisitionChange::Upgraded {
                                previous,
                                replacement,
                            }
                        } else {
                            AcquisitionChange::Reentrant
                        }
                    }
                    _ => {
                        write(&tx, namespace, &request.path, &replacement)?;
                        AcquisitionChange::Created { replacement }
                    }
                };
                changes.push(AtomicAcquisition {
                    handle: request.path.clone(),
                    change,
                });
            }
            tx.commit().map_err(db_error)?;
            Ok(Some(AtomicAcquisitionResult {
                acquisitions: changes,
                stale_tokens_removed,
            }))
        })
        .await
    }

    async fn rollback_acquisitions_atomic(
        &self,
        acquisitions: &[AtomicAcquisition],
        owner_id: &str,
    ) -> PathLockResult<Option<()>> {
        let acquisitions = acquisitions.to_vec();
        let owner = owner_id.to_string();
        self.run(move |conn, namespace| {
            let tx = conn
                .transaction_with_behavior(TransactionBehavior::Immediate)
                .map_err(db_error)?;
            // Reverse order also handles repeated paths within a batch.
            for acquisition in acquisitions.iter().rev() {
                match &acquisition.change {
                    AcquisitionChange::Reentrant => {}
                    AcquisitionChange::Created { replacement }
                    | AcquisitionChange::Upgraded { replacement, .. }
                        if replacement.owner_id != owner =>
                    {
                        return Err(PathLockError::InvalidRequest(
                            "rollback owner mismatch".into(),
                        ));
                    }
                    AcquisitionChange::Created { replacement } => {
                        if read(&tx, namespace, &acquisition.handle)?.as_ref() != Some(replacement)
                        {
                            return Err(PathLockError::Internal("rollback token changed".into()));
                        }
                        tx.execute(
                            "DELETE FROM pathlock_tokens WHERE namespace=?1 AND path=?2",
                            params![namespace, acquisition.handle],
                        )
                        .map_err(db_error)?;
                    }
                    AcquisitionChange::Upgraded {
                        previous,
                        replacement,
                    } => {
                        if previous.owner_id != owner {
                            return Err(PathLockError::InvalidRequest(
                                "rollback previous owner mismatch".into(),
                            ));
                        }
                        if read(&tx, namespace, &acquisition.handle)?.as_ref() != Some(replacement)
                        {
                            return Err(PathLockError::Internal("rollback token changed".into()));
                        }
                        write(&tx, namespace, &acquisition.handle, previous)?;
                    }
                }
            }
            tx.commit().map_err(db_error)?;
            Ok(Some(()))
        })
        .await
    }

    async fn is_path_locked_atomic(
        &self,
        path: &str,
        _now_ns: u128,
        stale_before_ns: u128,
        ignore_stale: bool,
    ) -> PathLockResult<Option<bool>> {
        validate_path(path)?;
        let path = path.to_string();
        self.run(move |conn, namespace| {
            let tx = conn.transaction().map_err(db_error)?;
            if read(&tx, namespace, &path)?
                .is_some_and(|token| !ignore_stale || token.time_ns >= stale_before_ns)
            {
                return Ok(Some(true));
            }
            for ancestor in ancestors(&path) {
                if read(&tx, namespace, ancestor)?.is_some_and(|token| {
                    token.lock_type == PathLockKind::Tree
                        && (!ignore_stale || token.time_ns >= stale_before_ns)
                }) {
                    return Ok(Some(true));
                }
            }
            Ok(Some(false))
        })
        .await
    }

    async fn read_token(&self, path: &str) -> PathLockResult<Option<LockToken>> {
        let path = path.to_string();
        self.run(move |conn, namespace| read(conn, namespace, &path))
            .await
    }

    async fn try_create_token(&self, path: &str, token: &LockToken) -> PathLockResult<()> {
        validate_path(path)?;
        validate_owner(&token.owner_id)?;
        let path = path.to_string();
        let token = token.clone();
        self.run(move |conn, namespace| {
            let tx = conn
                .transaction_with_behavior(TransactionBehavior::Immediate)
                .map_err(db_error)?;
            if let Some(existing) = read(&tx, namespace, &path)? {
                return Err(conflict(&path, &existing));
            }
            write(&tx, namespace, &path, &token)?;
            tx.commit().map_err(db_error)
        })
        .await
    }

    async fn compare_and_write_token(
        &self,
        path: &str,
        expected: &LockToken,
        replacement: &LockToken,
    ) -> PathLockResult<bool> {
        validate_path(path)?;
        validate_owner(&replacement.owner_id)?;
        let path = path.to_string();
        let expected = expected.clone();
        let replacement = replacement.clone();
        self.run(move |conn, namespace| {
            let tx = conn
                .transaction_with_behavior(TransactionBehavior::Immediate)
                .map_err(db_error)?;
            let matched = read(&tx, namespace, &path)?.as_ref() == Some(&expected);
            if matched {
                write(&tx, namespace, &path, &replacement)?;
            }
            tx.commit().map_err(db_error)?;
            Ok(matched)
        })
        .await
    }

    async fn refresh_token(&self, path: &str, owner: &str, time_ns: u128) -> PathLockResult<bool> {
        let path = path.to_string();
        let owner = owner.to_string();
        self.run(move |conn, namespace| {
            conn.execute(
                "UPDATE pathlock_tokens SET time_ns=?1 WHERE namespace=?2 AND path=?3 AND owner=?4",
                params![timestamp(time_ns), namespace, path, owner],
            )
            .map(|count| count == 1)
            .map_err(db_error)
        })
        .await
    }

    async fn remove_token(&self, path: &str, owner: &str, _force: bool) -> PathLockResult<bool> {
        let path = path.to_string();
        let owner = owner.to_string();
        self.run(move |conn, namespace| {
            conn.execute(
                "DELETE FROM pathlock_tokens WHERE namespace=?1 AND path=?2 AND owner=?3",
                params![namespace, path, owner],
            )
            .map(|count| count == 1)
            .map_err(db_error)
        })
        .await
    }

    async fn refresh_tokens(
        &self,
        paths: &[String],
        owner: &str,
        time_ns: u128,
    ) -> Vec<PathLockResult<bool>> {
        let validation: Vec<_> = paths.iter().map(|path| validate_path(path)).collect();
        let valid_paths: Vec<_> = paths
            .iter()
            .zip(&validation)
            .filter(|(_, result)| result.is_ok())
            .map(|(path, _)| path.clone())
            .collect();
        if valid_paths.is_empty() {
            return validation
                .into_iter()
                .map(|result| result.map(|_| false))
                .collect();
        }
        let owner = owner.to_string();
        // One worker and transaction per lease; bounded IN lists stay below SQLite
        // parameter limits. RETURNING identifies each owned path without extra reads.
        let result = self
            .run(move |conn, namespace| {
                let tx = conn
                    .transaction_with_behavior(TransactionBehavior::Immediate)
                    .map_err(db_error)?;
                let mut refreshed = std::collections::HashSet::new();
                for batch in valid_paths.chunks(256) {
                    let placeholders = vec!["?"; batch.len()].join(",");
                    let sql = format!(
                        "UPDATE pathlock_tokens SET time_ns=? WHERE namespace=? AND owner=?
                     AND path IN ({placeholders}) RETURNING path"
                    );
                    let mut args = vec![timestamp(time_ns), namespace.to_string(), owner.clone()];
                    args.extend_from_slice(batch);
                    let mut stmt = tx.prepare(&sql).map_err(db_error)?;
                    let rows = stmt
                        .query_map(rusqlite::params_from_iter(args), |row| {
                            row.get::<_, String>(0)
                        })
                        .map_err(db_error)?;
                    for row in rows {
                        refreshed.insert(row.map_err(db_error)?);
                    }
                }
                tx.commit().map_err(db_error)?;
                Ok(refreshed)
            })
            .await;
        paths
            .iter()
            .zip(validation)
            .map(|(path, validation)| {
                validation?;
                match &result {
                    Ok(refreshed) => Ok(refreshed.contains(path)),
                    Err(PathLockError::Busy {
                        lock_path,
                        operation,
                    }) => Err(PathLockError::Busy {
                        lock_path: lock_path.clone(),
                        operation: operation.clone(),
                    }),
                    Err(error) => Err(PathLockError::Io(error.to_string())),
                }
            })
            .collect()
    }

    async fn scan_descendant_locks(&self, root: &str) -> PathLockResult<Vec<String>> {
        // Trait compatibility and diagnostics only: the manager's atomic acquisition
        // hook returns early and never uses this fallback scan for SQLite.
        validate_path(root)?;
        let root = root.to_string();
        self.run(move |conn, namespace| {
            let (lower, upper) = descendant_bounds(&root);
            let mut stmt = conn
                .prepare(
                    "SELECT path FROM pathlock_tokens
                 WHERE namespace=?1 AND path>=?2 AND path<?3 AND path<>?4",
                )
                .map_err(db_error)?;
            let rows = stmt
                .query_map(params![namespace, lower, upper, root], |row| row.get(0))
                .map_err(db_error)?;
            rows.collect::<rusqlite::Result<Vec<_>>>().map_err(db_error)
        })
        .await
    }
}

#[cfg(test)]
#[path = "sqlite_provider_tests.rs"]
mod tests;
