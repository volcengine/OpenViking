//! Cross-process PathLock storage using the same holder protocol as SQLite.
//!
//! Every operation takes one transaction-scoped advisory lock per namespace.
//! This protects absent paths and subtree predicates, not just existing rows.
//! All cooperating writers must use this provider and the same primary database.

use std::sync::{Arc, Mutex};
use std::time::Duration;

use async_trait::async_trait;
use postgres::{Client, Config, IsolationLevel, Transaction};
use postgres_native_tls::MakeTlsConnector;

use super::codec::LockTokenCodec;
use super::path::validate_normalized_logical_path as validate_path;
use super::provider::{
    acquire_holder_records, descendant_bounds, mutate_holder_record, AtomicAcquisitionResult,
    HolderStore, PathLockHandleMode, PathLockProvider,
};
use super::types::{LockToken, PathLockError, PathLockKind, PathLockRequest, PathLockResult};

/// Credentials are held in the private connection configuration, never Debug/log output.
pub struct PostgresPathLockProvider {
    connection: Arc<Mutex<Option<Client>>>,
    config: Config,
    namespace: String,
    lock_expire_ns: u128,
}

impl Drop for PostgresPathLockProvider {
    fn drop(&mut self) {
        // postgres::Client also drives its private runtime during Drop. Move the
        // entire Arc, rather than cloning it, so the last reference cannot be
        // destroyed on the async caller after this method returns. In-flight
        // operations already own their references on blocking workers.
        let connection = std::mem::replace(&mut self.connection, Arc::new(Mutex::new(None)));
        std::thread::spawn(move || drop(connection));
    }
}

// Bound network establishment and PostgreSQL waits independently of logical lock wait.
const CONNECT_TIMEOUT: Duration = Duration::from_secs(5);
const SET_TIMEOUTS: &str = "SET LOCAL statement_timeout = '5s'; SET LOCAL lock_timeout = '1s';
     SET LOCAL synchronous_commit = on";

fn db_error(error: postgres::Error) -> PathLockError {
    // Do not expose server messages: they can contain SQL parameters or credentials.
    if error
        .code()
        .is_some_and(|code| matches!(code.code(), "55P03" | "57014" | "40P01" | "40001"))
    {
        PathLockError::Busy {
            lock_path: "postgres".into(),
            operation: "transaction".into(),
        }
    } else {
        PathLockError::Io(format!(
            "PostgreSQL PathLock operation failed (SQLSTATE {})",
            error.code().map_or("connection", |code| code.code())
        ))
    }
}

fn connect(config: &Config) -> PathLockResult<Client> {
    let tls = native_tls::TlsConnector::builder()
        .build()
        .map_err(|_| PathLockError::Io("PostgreSQL TLS initialization failed".into()))?;
    config.connect(MakeTlsConnector::new(tls)).map_err(db_error)
}

fn timestamp(time: u128) -> String {
    format!("{time:039}")
}

fn token(row: &postgres::Row) -> PathLockResult<LockToken> {
    LockTokenCodec::decode(row.get::<_, &str>("token"))
        .map_err(|_| PathLockError::Internal("invalid PostgreSQL PathLock token".into()))
}

fn validate_owner(owner: &str) -> PathLockResult<()> {
    if owner.is_empty() || owner.contains(':') {
        return Err(PathLockError::InvalidRequest("invalid lock owner".into()));
    }
    Ok(())
}

/// Preserve the manager's lease duration, while using only the primary's clock.
fn clock(tx: &mut Transaction<'_>, now: u128, stale: u128) -> PathLockResult<(u128, u128)> {
    let raw: String = tx
        .query_one(
            "SELECT floor(extract(epoch FROM clock_timestamp()) * 1000000000)::text",
            &[],
        )
        .map_err(db_error)?
        .get(0);
    let db_now = raw
        .parse::<u128>()
        .map_err(|_| PathLockError::Internal("invalid PostgreSQL clock".into()))?;
    Ok((db_now, db_now.saturating_sub(now.saturating_sub(stale))))
}

struct TxStore<'a, 'b> {
    tx: &'a mut Transaction<'b>,
    namespace: &'a str,
}

impl TxStore<'_, '_> {
    fn delete(&mut self, path: &str) -> PathLockResult<bool> {
        self.tx
            .execute(
                "DELETE FROM public.ov_pathlock_tokens WHERE namespace=$1 AND path=$2",
                &[&self.namespace, &path],
            )
            .map(|count| count == 1)
            .map_err(db_error)
    }
}

impl HolderStore for TxStore<'_, '_> {
    fn get(&mut self, path: &str) -> PathLockResult<Option<LockToken>> {
        self.tx
            .query_opt(
                "SELECT token FROM public.ov_pathlock_tokens WHERE namespace=$1 AND path=$2",
                &[&self.namespace, &path],
            )
            .map_err(db_error)?
            .as_ref()
            .map(token)
            .transpose()
    }

    fn put(&mut self, path: &str, value: &LockToken) -> PathLockResult<()> {
        self.tx
            .execute(
                "INSERT INTO public.ov_pathlock_tokens(namespace,path,owner,time_ns,token)
             VALUES ($1,$2,$3,$4,$5)
             ON CONFLICT(namespace,path) DO UPDATE SET
             owner=excluded.owner,time_ns=excluded.time_ns,token=excluded.token",
                &[
                    &self.namespace,
                    &path,
                    &value.owner_id,
                    &timestamp(value.time_ns),
                    &LockTokenCodec::encode(value),
                ],
            )
            .map_err(db_error)?;
        Ok(())
    }

    fn remove_stale(
        &mut self,
        path: &str,
        subtree: bool,
        owner: &str,
        stale: u128,
    ) -> PathLockResult<usize> {
        let (lower, upper) = descendant_bounds(path);
        self.tx
            .execute(
                "DELETE FROM public.ov_pathlock_tokens WHERE namespace=$1 AND owner<>$2
             AND time_ns<$3 AND (path=$4 OR ($5 AND path>=$6 AND path<$7 AND path<>$4))",
                &[
                    &self.namespace,
                    &owner,
                    &timestamp(stale),
                    &path,
                    &subtree,
                    &lower,
                    &upper,
                ],
            )
            .map(|count| count as usize)
            .map_err(db_error)
    }

    fn live_descendant(
        &mut self,
        root: &str,
        owner: &str,
        stale: u128,
    ) -> PathLockResult<Option<(String, LockToken)>> {
        let (lower, upper) = descendant_bounds(root);
        self.tx
            .query_opt(
                "SELECT path,token FROM public.ov_pathlock_tokens
             WHERE namespace=$1 AND path>=$2 AND path<$3 AND path<>$4
             AND owner<>$5 AND time_ns>=$6 LIMIT 1",
                &[
                    &self.namespace,
                    &lower,
                    &upper,
                    &root,
                    &owner,
                    &timestamp(stale),
                ],
            )
            .map_err(db_error)?
            .map(|row| Ok((row.get("path"), token(&row)?)))
            .transpose()
    }
}

impl PostgresPathLockProvider {
    /// Resolve credentials once from an environment variable.
    /// Use sslmode=require for remote databases; native TLS verifies the hostname
    /// and certificate against the system trust store.
    pub fn from_env(variable: &str, namespace: &str, expire_secs: f64) -> PathLockResult<Self> {
        if variable.trim().is_empty() || namespace.trim().is_empty() {
            return Err(PathLockError::InvalidRequest(
                "PostgreSQL PathLock requires postgres_dsn_env and service_name".into(),
            ));
        }
        let lock_expire_ns = Duration::try_from_secs_f64(expire_secs)
            .ok()
            .filter(|duration| *duration >= Duration::from_secs(1))
            .ok_or_else(|| {
                PathLockError::InvalidRequest(
                    "PostgreSQL PathLock expiry must be finite, >= 1 second, and representable"
                        .into(),
                )
            })?
            .as_nanos();
        let dsn = std::env::var(variable).map_err(|_| {
            PathLockError::InvalidRequest(
                "PostgreSQL connection environment variable is unset".into(),
            )
        })?;
        let mut config: Config = dsn.parse().map_err(|_| {
            PathLockError::InvalidRequest("invalid PostgreSQL connection configuration".into())
        })?;
        config.connect_timeout(CONNECT_TIMEOUT);
        config.application_name(&format!("ov-pathlock:{namespace}"));
        let mut client = connect(&config)?;
        let mut tx = client.transaction().map_err(db_error)?;
        tx.batch_execute(SET_TIMEOUTS).map_err(db_error)?;
        // A separate, global initialization lock serializes concurrent CREATE TABLE.
        tx.query_one("SELECT pg_advisory_xact_lock(187003451, 0)", &[])
            .map_err(db_error)?;
        tx.batch_execute(
            "CREATE TABLE IF NOT EXISTS public.ov_pathlock_tokens (
                namespace TEXT COLLATE \"C\" NOT NULL,
                path TEXT COLLATE \"C\" NOT NULL,
                owner TEXT NOT NULL,
                time_ns TEXT COLLATE \"C\" NOT NULL CHECK(length(time_ns)=39),
                token TEXT NOT NULL,
                PRIMARY KEY(namespace,path)
             );
             CREATE INDEX IF NOT EXISTS ov_pathlock_expiry
             ON public.ov_pathlock_tokens(namespace,time_ns);",
        )
        .map_err(db_error)?;
        tx.commit().map_err(db_error)?;
        Ok(Self {
            connection: Arc::new(Mutex::new(Some(client))),
            config,
            namespace: namespace.to_owned(),
            lock_expire_ns,
        })
    }

    async fn run<T, F>(&self, operation: F) -> PathLockResult<T>
    where
        T: Send + 'static,
        F: FnOnce(&mut TxStore<'_, '_>) -> PathLockResult<T> + Send + 'static,
    {
        let connection = self.connection.clone();
        let config = self.config.clone();
        let namespace = self.namespace.clone();
        tokio::task::spawn_blocking(move || {
            let mut connection = connection.lock().map_err(|_| {
                PathLockError::Internal("PostgreSQL connection mutex poisoned".into())
            })?;
            if connection.as_ref().is_none_or(Client::is_closed) {
                *connection = Some(connect(&config)?);
            }
            let client = connection.as_mut().unwrap();
            // Never replay an operation after a connection/commit failure. Its outcome
            // may be unknown; only a subsequent, separate operation may reconnect.
            let result = (|| {
                // Obtain a fresh snapshot after waiting for the advisory lock,
                // even when the connection's default isolation is Repeatable Read.
                let mut tx = client
                    .build_transaction()
                    .isolation_level(IsolationLevel::ReadCommitted)
                    .start()
                    .map_err(db_error)?;
                tx.batch_execute(SET_TIMEOUTS).map_err(db_error)?;
                // Collisions only serialize unrelated namespaces; rows stay separate.
                tx.query_one(
                    "SELECT pg_advisory_xact_lock(187003452, hashtext($1))",
                    &[&namespace],
                )
                .map_err(db_error)?;
                let result = operation(&mut TxStore {
                    tx: &mut tx,
                    namespace: &namespace,
                })?;
                tx.commit().map_err(db_error)?;
                Ok(result)
            })();
            // Discard on every error, including failed rollback on an open socket.
            if result.is_err() {
                *connection = None;
            }
            result
        })
        .await
        .map_err(|_| PathLockError::Internal("PostgreSQL worker failed".into()))?
    }

    async fn mutate_many(
        &self,
        paths: Vec<String>,
        owner: String,
        holder: String,
        now: u128,
        stale: u128,
        release: bool,
    ) -> PathLockResult<Vec<bool>> {
        self.run(move |store| {
            let (now, stale) = clock(store.tx, now, stale)?;
            let mut changed = Vec::with_capacity(paths.len());
            for path in paths {
                validate_path(&path)?;
                let next = store.get(&path)?.and_then(|current| {
                    mutate_holder_record(&current, &owner, &holder, now, stale, release)
                });
                match &next {
                    Some(value) if value.holders.is_empty() => {
                        store.delete(&path)?;
                    }
                    Some(value) => store.put(&path, value)?,
                    None => {}
                }
                changed.push(next.is_some());
            }
            Ok(changed)
        })
        .await
    }
}

#[async_trait]
impl PathLockProvider for PostgresPathLockProvider {
    async fn replace_holder(
        &self,
        path: &str,
        owner: &str,
        holder: &str,
        restored: super::types::LockHolder,
        _stale_before: u128,
    ) -> PathLockResult<()> {
        validate_path(path)?;
        let (path, owner, holder, restored) = (
            path.to_owned(),
            owner.to_owned(),
            holder.to_owned(),
            restored,
        );
        let lock_expire_ns = self.lock_expire_ns;
        self.run(move |store| {
            // The manager's stale threshold uses its local clock. Use the
            // configured duration and database clock for rollback as well.
            let (_, stale) = clock(store.tx, lock_expire_ns, 0)?;
            let Some(mut value) = store.get(&path)? else {
                return Ok(());
            };
            if value.owner_id != owner || !value.holders.iter().any(|h| h.id == holder) {
                return Ok(());
            }
            value
                .holders
                .retain(|h| h.id != holder && h.time_ns >= stale);
            if restored.time_ns >= stale && !value.holders.iter().any(|h| h.id == restored.id) {
                value.holders.push(restored);
            }
            value.recompute();
            if value.holders.is_empty() {
                store.delete(&path)?;
            } else {
                store.put(&path, &value)?;
            }
            Ok(())
        })
        .await
    }

    fn name(&self) -> &'static str {
        "postgres"
    }
    fn handle_mode(&self) -> PathLockHandleMode {
        PathLockHandleMode::LogicalPath
    }

    async fn try_acquire_holders_atomic(
        &self,
        requests: &[PathLockRequest],
        owner: &str,
        holder: &str,
        now: u128,
        stale: u128,
        source: Option<&str>,
    ) -> PathLockResult<Option<AtomicAcquisitionResult>> {
        let requests = requests.to_vec();
        let (owner, holder, source) = (
            owner.to_owned(),
            holder.to_owned(),
            source.map(str::to_owned),
        );
        self.run(move |store| {
            let (now, stale) = clock(store.tx, now, stale)?;
            let cleaned = store
                .tx
                .execute(
                    "DELETE FROM public.ov_pathlock_tokens WHERE (namespace,path) IN (
                 SELECT namespace,path FROM public.ov_pathlock_tokens
                 WHERE namespace=$1 AND time_ns<$2 AND owner<>$3 LIMIT 256)",
                    &[&store.namespace, &timestamp(stale), &owner],
                )
                .map_err(db_error)?;
            let mut result = acquire_holder_records(
                store,
                &requests,
                &owner,
                &holder,
                now,
                stale,
                source.as_deref(),
            )?;
            result.stale_tokens_removed += cleaned as usize;
            Ok(Some(result))
        })
        .await
    }

    async fn mutate_holder(
        &self,
        path: &str,
        owner: &str,
        holder: &str,
        now: u128,
        stale: u128,
        release: bool,
    ) -> PathLockResult<bool> {
        self.mutate_many(
            vec![path.into()],
            owner.into(),
            holder.into(),
            now,
            stale,
            release,
        )
        .await
        .map(|changed| changed[0])
    }

    async fn refresh_holders(
        &self,
        paths: &[String],
        owner: &str,
        holder: &str,
        now: u128,
        stale: u128,
    ) -> Vec<PathLockResult<bool>> {
        match self
            .mutate_many(
                paths.to_vec(),
                owner.into(),
                holder.into(),
                now,
                stale,
                false,
            )
            .await
        {
            Ok(changed) => changed.into_iter().map(Ok).collect(),
            Err(error) => paths
                .iter()
                .map(|_| {
                    Err(match &error {
                        PathLockError::Busy {
                            lock_path,
                            operation,
                        } => PathLockError::Busy {
                            lock_path: lock_path.clone(),
                            operation: operation.clone(),
                        },
                        _ => PathLockError::Io(error.to_string()),
                    })
                })
                .collect(),
        }
    }

    async fn read_token(&self, path: &str) -> PathLockResult<Option<LockToken>> {
        validate_path(path)?;
        let path = path.to_owned();
        self.run(move |store| store.get(&path)).await
    }

    async fn try_create_token(&self, path: &str, value: &LockToken) -> PathLockResult<()> {
        validate_path(path)?;
        validate_owner(&value.owner_id)?;
        let (path, value) = (path.to_owned(), value.clone());
        self.run(move |store| {
            if let Some(existing) = store.get(&path)? {
                return Err(PathLockError::Conflict {
                    lock_path: path.clone(),
                    owner: existing.owner_id,
                    kind: existing.lock_type,
                });
            }
            store.put(&path, &value)
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
        let (path, expected, replacement) =
            (path.to_owned(), expected.clone(), replacement.clone());
        self.run(move |store| {
            let matched = store.get(&path)?.as_ref() == Some(&expected);
            if matched {
                store.put(&path, &replacement)?;
            }
            Ok(matched)
        })
        .await
    }

    async fn compare_and_remove_token(
        &self,
        path: &str,
        expected: &LockToken,
    ) -> PathLockResult<bool> {
        validate_path(path)?;
        let (path, expected) = (path.to_owned(), expected.clone());
        self.run(move |store| {
            let matched = store.get(&path)?.as_ref() == Some(&expected);
            if matched {
                store.delete(&path)?;
            }
            Ok(matched)
        })
        .await
    }

    async fn refresh_token(&self, path: &str, owner: &str, time: u128) -> PathLockResult<bool> {
        validate_path(path)?;
        let (path, owner) = (path.to_owned(), owner.to_owned());
        self.run(move |store| {
            let Some(mut value) = store.get(&path)? else {
                return Ok(false);
            };
            if value.owner_id != owner || !value.holders.is_empty() {
                return Ok(false);
            }
            value.time_ns = time;
            store.put(&path, &value)?;
            Ok(true)
        })
        .await
    }

    async fn remove_token(&self, path: &str, owner: &str, _force: bool) -> PathLockResult<bool> {
        validate_path(path)?;
        let (path, owner) = (path.to_owned(), owner.to_owned());
        self.run(move |store| match store.get(&path)? {
            Some(value) if value.owner_id == owner && value.holders.is_empty() => {
                store.delete(&path)
            }
            _ => Ok(false),
        })
        .await
    }

    async fn scan_descendant_locks(&self, root: &str) -> PathLockResult<Vec<String>> {
        validate_path(root)?;
        let root = root.to_owned();
        self.run(move |store| {
            let (lower, upper) = descendant_bounds(&root);
            store
                .tx
                .query(
                    "SELECT path FROM public.ov_pathlock_tokens
                 WHERE namespace=$1 AND path>=$2 AND path<$3 AND path<>$4",
                    &[&store.namespace, &lower, &upper, &root],
                )
                .map(|rows| rows.into_iter().map(|row| row.get(0)).collect())
                .map_err(db_error)
        })
        .await
    }

    async fn is_path_locked_atomic(
        &self,
        path: &str,
        now: u128,
        stale: u128,
        ignore_stale: bool,
    ) -> PathLockResult<Option<bool>> {
        validate_path(path)?;
        let path = path.to_owned();
        self.run(move |store| {
            let (_, stale) = clock(store.tx, now, stale)?;
            let mut current = Some(path.as_str());
            while let Some(ancestor) = current {
                if let Some(value) = store.get(ancestor)? {
                    let kind = if ignore_stale {
                        value.live_kind(stale)
                    } else {
                        Some(value.lock_type)
                    };
                    if kind.is_some() && (ancestor == path || kind == Some(PathLockKind::Tree)) {
                        return Ok(Some(true));
                    }
                }
                current = if ancestor == "/" {
                    None
                } else {
                    Some(ancestor.rsplit_once('/').map_or("/", |(parent, _)| {
                        if parent.is_empty() {
                            "/"
                        } else {
                            parent
                        }
                    }))
                };
            }
            Ok(Some(false))
        })
        .await
    }
}
