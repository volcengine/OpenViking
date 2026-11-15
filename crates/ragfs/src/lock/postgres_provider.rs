//! Cross-process PathLock storage using the same holder protocol as SQLite.
//!
//! Every operation takes one transaction-scoped advisory lock per namespace.
//! This protects absent paths and subtree predicates, not just existing rows.
//! All cooperating writers must use this provider and the same primary database.

use std::collections::HashMap;
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

// Index only bounded values. Full namespace/path equality remains authoritative,
// and the namespace advisory lock serializes insert-or-update even on hash collisions.
// Keep migration transactional: existing leases must survive changing the primary key.
const INITIALIZE_SCHEMA: &str = r#"
DO $prefix$
BEGIN
    -- convert_to(text, 'UTF8') is deterministic in a UTF8 database. Never
    -- claim immutability for textsend(), which follows client_encoding.
    IF current_setting('server_encoding') <> 'UTF8' THEN
        RAISE EXCEPTION 'PostgreSQL PathLock requires UTF8 server encoding';
    END IF;
    IF to_regprocedure('public.path_prefix_2048(text)') IS NULL THEN
        EXECUTE $function$
            CREATE FUNCTION public.path_prefix_2048(text) RETURNS bytea
            LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
            AS 'SELECT pg_catalog.substring(pg_catalog.convert_to($1, ''UTF8''), 1, 2048)'
        $function$;
    END IF;
END $prefix$;
CREATE TABLE IF NOT EXISTS public.ov_pathlock_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    namespace TEXT COLLATE "C" NOT NULL,
    path TEXT COLLATE "C" NOT NULL,
    owner TEXT NOT NULL,
    time_ns TEXT COLLATE "C" NOT NULL CHECK(length(time_ns)=39),
    token TEXT NOT NULL
);
DO $migration$
DECLARE
    key_name text;
    key_columns text[];
BEGIN
    SELECT c.conname, array_agg(a.attname::text ORDER BY k.ordinality)
      INTO key_name, key_columns
      FROM pg_constraint c
      CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY k(attnum, ordinality)
      JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.attnum
      WHERE c.conrelid='public.ov_pathlock_tokens'::regclass AND c.contype='p'
      GROUP BY c.conname;
    IF key_columns = ARRAY['namespace','path'] THEN
        ALTER TABLE public.ov_pathlock_tokens
            ADD COLUMN id UUID NOT NULL DEFAULT gen_random_uuid();
        EXECUTE format('ALTER TABLE public.ov_pathlock_tokens DROP CONSTRAINT %I', key_name);
        ALTER TABLE public.ov_pathlock_tokens ADD PRIMARY KEY(id);
        DROP INDEX IF EXISTS public.ov_pathlock_expiry;
    ELSIF key_columns IS DISTINCT FROM ARRAY['id'] THEN
        RAISE EXCEPTION 'Unsupported PostgreSQL PathLock schema';
    END IF;
END $migration$;
CREATE INDEX IF NOT EXISTS ov_pathlock_lookup
    ON public.ov_pathlock_tokens(md5(namespace),md5(path));
CREATE INDEX IF NOT EXISTS ov_pathlock_expiry
    ON public.ov_pathlock_tokens(md5(namespace),time_ns);
CREATE INDEX IF NOT EXISTS ov_pathlock_subtree_prefix_2048
    ON public.ov_pathlock_tokens(md5(namespace),public.path_prefix_2048(path));
"#;

#[derive(Debug, PartialEq, Eq)]
enum SchemaState {
    Ready,
    NeedsInitialization,
}

fn incompatible_schema() -> PathLockError {
    PathLockError::Io(
        "Incompatible PostgreSQL PathLock schema; ask the migration role to repair it".into(),
    )
}

/// Inspect catalogs without executing DDL. A pre-provisioned runtime role does
/// not own the table, even when it has every privilege needed for lock operations.
fn inspect_schema(tx: &mut Transaction<'_>) -> PathLockResult<SchemaState> {
    if tx
        .query_one("SELECT current_setting('server_encoding')", &[])
        .map_err(db_error)?
        .get::<_, String>(0)
        != "UTF8"
    {
        return Err(PathLockError::Io(
            "PostgreSQL PathLock requires UTF8 server encoding".into(),
        ));
    }
    let table = tx
        .query_opt(
            "SELECT c.oid, c.relkind::text FROM pg_catalog.pg_class c
             JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
             WHERE n.nspname='public' AND c.relname='ov_pathlock_tokens'",
            &[],
        )
        .map_err(db_error)?;
    let Some(table) = table else {
        return Ok(SchemaState::NeedsInitialization);
    };
    if table.get::<_, String>(1) != "r" {
        return Err(incompatible_schema());
    }
    let table_oid: u32 = table.get(0);
    let columns = tx
        .query(
            "SELECT a.attname::text, a.atttypid::regtype::text, a.attnotnull,
                    co.collname::text, pg_catalog.pg_get_expr(d.adbin,d.adrelid)
             FROM pg_catalog.pg_attribute a
             LEFT JOIN pg_catalog.pg_collation co ON co.oid=a.attcollation
             LEFT JOIN pg_catalog.pg_attrdef d
               ON d.adrelid=a.attrelid AND d.adnum=a.attnum
             WHERE a.attrelid=$1 AND a.attnum>0 AND NOT a.attisdropped",
            &[&table_oid],
        )
        .map_err(db_error)?;
    for name in ["namespace", "path", "owner", "time_ns", "token"] {
        let Some(column) = columns.iter().find(|row| row.get::<_, String>(0) == name) else {
            return Err(incompatible_schema());
        };
        if column.get::<_, String>(1) != "text"
            || !column.get::<_, bool>(2)
            || (matches!(name, "namespace" | "path" | "time_ns")
                && column.get::<_, Option<String>>(3).as_deref() != Some("C"))
        {
            return Err(incompatible_schema());
        }
    }
    let primary_key = tx
        .query_opt(
            "SELECT ARRAY(
                 SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY k(num,ord)
                 JOIN pg_catalog.pg_attribute a
                   ON a.attrelid=c.conrelid AND a.attnum=k.num ORDER BY k.ord)
             FROM pg_catalog.pg_constraint c WHERE c.conrelid=$1 AND c.contype='p'",
            &[&table_oid],
        )
        .map_err(db_error)?
        .map(|row| row.get::<_, Vec<String>>(0));
    let id = columns.iter().find(|row| row.get::<_, String>(0) == "id");
    let legacy = primary_key.as_deref() == Some(&["namespace".into(), "path".into()]);
    if legacy {
        if id.is_some() {
            return Err(incompatible_schema());
        }
    } else if primary_key.as_deref() != Some(&["id".into()])
        || !id.is_some_and(|row| {
            row.get::<_, String>(1) == "uuid"
                && row.get::<_, bool>(2)
                && row.get::<_, Option<String>>(4).as_deref() == Some("gen_random_uuid()")
        })
    {
        return Err(incompatible_schema());
    }

    let mut needs_initialization = legacy;
    let function = tx
        .query_opt(
            "SELECT p.prorettype='bytea'::regtype, l.lanname::text,
                    p.provolatile::text, p.proisstrict, p.proparallel::text,
                    p.prosrc, p.prosecdef, p.proconfig IS NULL, p.prokind::text
             FROM pg_catalog.pg_proc p
             JOIN pg_catalog.pg_language l ON l.oid=p.prolang
             WHERE p.oid=to_regprocedure('public.path_prefix_2048(text)')",
            &[],
        )
        .map_err(db_error)?;
    if let Some(function) = function {
        let body = function
            .get::<_, String>(5)
            .split_whitespace()
            .collect::<Vec<_>>()
            .join(" ")
            .to_ascii_lowercase();
        if !function.get::<_, bool>(0)
            || function.get::<_, String>(1) != "sql"
            || function.get::<_, String>(2) != "i"
            || !function.get::<_, bool>(3)
            || function.get::<_, String>(4) != "s"
            || body != "select pg_catalog.substring(pg_catalog.convert_to($1, 'utf8'), 1, 2048)"
            || function.get::<_, bool>(6)
            || !function.get::<_, bool>(7)
            || function.get::<_, String>(8) != "f"
        {
            return Err(incompatible_schema());
        }
    } else {
        needs_initialization = true;
    }

    for (name, second_key) in [
        ("ov_pathlock_lookup", "md5(path)"),
        ("ov_pathlock_expiry", "time_ns"),
        ("ov_pathlock_subtree_prefix_2048", "path_prefix_2048(path)"),
    ] {
        let index = tx
            .query_opt(
                "SELECT i.indrelid=$1, i.indisvalid AND i.indisready AND i.indislive,
                        NOT i.indisunique AND i.indnkeyatts=2 AND i.indnatts=2
                          AND i.indpred IS NULL, am.amname::text,
                        pg_catalog.pg_get_indexdef(i.indexrelid,1,true),
                        pg_catalog.pg_get_indexdef(i.indexrelid,2,true)
                 FROM pg_catalog.pg_class c
                 JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                 LEFT JOIN pg_catalog.pg_index i ON i.indexrelid=c.oid
                 LEFT JOIN pg_catalog.pg_am am ON am.oid=c.relam
                 WHERE n.nspname='public' AND c.relname=$2",
                &[&table_oid, &name],
            )
            .map_err(db_error)?;
        if let Some(index) = index {
            // The legacy expiry index is deliberately replaced during migration.
            if legacy && name == "ov_pathlock_expiry" {
                continue;
            }
            if index.get::<_, Option<bool>>(0) != Some(true)
                || index.get::<_, Option<bool>>(1) != Some(true)
                || index.get::<_, Option<bool>>(2) != Some(true)
                || index.get::<_, Option<String>>(3).as_deref() != Some("btree")
                || index.get::<_, Option<String>>(4).as_deref() != Some("md5(namespace)")
                || index.get::<_, Option<String>>(5).as_deref() != Some(second_key)
            {
                return Err(incompatible_schema());
            }
        } else {
            needs_initialization = true;
        }
    }
    Ok(if needs_initialization {
        SchemaState::NeedsInitialization
    } else {
        SchemaState::Ready
    })
}

fn initialize_schema(tx: &mut Transaction<'_>) -> PathLockResult<()> {
    // Fix catalog deparsing and function resolution independently of the DSN's
    // search_path. All persistent objects still live in the public schema.
    tx.batch_execute("SET LOCAL search_path = pg_catalog, public")
        .map_err(db_error)?;
    if inspect_schema(tx)? == SchemaState::Ready {
        return Ok(());
    }
    tx.batch_execute(INITIALIZE_SCHEMA).map_err(|error| {
        if error.code().is_some_and(|code| code.code() == "42501") {
            PathLockError::Io(
                "PostgreSQL PathLock schema requires initialization or migration; \
                 ask the migration role to complete it before restarting (SQLSTATE 42501)"
                    .into(),
            )
        } else {
            db_error(error)
        }
    })?;
    if inspect_schema(tx)? != SchemaState::Ready {
        return Err(incompatible_schema());
    }
    Ok(())
}

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

/// Rebase new/renewed references, preserving unchanged database-clock history.
fn normalize_token_time(value: &mut LockToken, previous: Option<&LockToken>, db_now: u128) {
    let previous = previous.filter(|old| old.owner_id == value.owner_id);
    if value.holders.is_empty() {
        if !previous.is_some_and(|old| old.holders.is_empty() && old.time_ns == value.time_ns) {
            value.time_ns = db_now;
        }
        return;
    }
    for holder in &mut value.holders {
        let unchanged = previous.is_some_and(|old| {
            old.holders
                .iter()
                .any(|h| h.id == holder.id && h.time_ns == holder.time_ns)
                // add_holder preserves a live legacy reference during conversion.
                || (old.holders.is_empty()
                    && holder.id == "legacy"
                    && holder.time_ns == old.time_ns)
        });
        if !unchanged {
            holder.time_ns = db_now;
        }
    }
    value.recompute();
}

struct TxStore<'a, 'b> {
    tx: &'a mut Transaction<'b>,
    namespace: &'a str,
    // The namespace gate protects both existing and absent paths. Keep this
    // cache inside the transaction, and synchronize it after successful writes.
    reads: HashMap<String, Option<LockToken>>,
    #[cfg(test)]
    read_queries: usize,
}

impl TxStore<'_, '_> {
    fn delete(&mut self, path: &str) -> PathLockResult<bool> {
        let count = self
            .tx
            .execute(
                "DELETE FROM public.ov_pathlock_tokens
                 WHERE md5(namespace)=md5($1) AND namespace=$1
                 AND md5(path)=md5($2) AND path=$2",
                &[&self.namespace, &path],
            )
            .map_err(db_error)?;
        self.reads.insert(path.into(), None);
        Ok(count == 1)
    }
}

impl HolderStore for TxStore<'_, '_> {
    fn get(&mut self, path: &str) -> PathLockResult<Option<LockToken>> {
        if let Some(value) = self.reads.get(path) {
            return Ok(value.clone());
        }
        let value = self
            .tx
            .query_opt(
                "SELECT token FROM public.ov_pathlock_tokens
                 WHERE md5(namespace)=md5($1) AND namespace=$1
                 AND md5(path)=md5($2) AND path=$2",
                &[&self.namespace, &path],
            )
            .map_err(db_error)?
            .as_ref()
            .map(token)
            .transpose()?;
        #[cfg(test)]
        {
            self.read_queries += 1;
        }
        self.reads.insert(path.into(), value.clone());
        Ok(value)
    }

    fn put(&mut self, path: &str, value: &LockToken) -> PathLockResult<()> {
        // Every caller holds the namespace gate. A non-unique hash index allows
        // colliding paths to coexist; never use a hash as an ON CONFLICT identity.
        let updated = self
            .tx
            .execute(
                "UPDATE public.ov_pathlock_tokens SET owner=$3,time_ns=$4,token=$5
                 WHERE md5(namespace)=md5($1) AND namespace=$1
                 AND md5(path)=md5($2) AND path=$2",
                &[
                    &self.namespace,
                    &path,
                    &value.owner_id,
                    &timestamp(value.time_ns),
                    &LockTokenCodec::encode(value),
                ],
            )
            .map_err(db_error)?;
        if updated == 0 {
            self.tx
                .execute(
                    "INSERT INTO public.ov_pathlock_tokens(namespace,path,owner,time_ns,token)
                 VALUES ($1,$2,$3,$4,$5)",
                    &[
                        &self.namespace,
                        &path,
                        &value.owner_id,
                        &timestamp(value.time_ns),
                        &LockTokenCodec::encode(value),
                    ],
                )
                .map_err(db_error)?;
        }
        self.reads.insert(path.into(), Some(value.clone()));
        Ok(())
    }

    fn remove_stale(
        &mut self,
        path: &str,
        subtree: bool,
        owner: &str,
        stale: u128,
    ) -> PathLockResult<usize> {
        if !subtree {
            let count = self
                .tx
                .execute(
                    "DELETE FROM public.ov_pathlock_tokens
                     WHERE md5(namespace)=md5($1) AND namespace=$1
                     AND md5(path)=md5($2) AND path=$2 AND owner<>$3 AND time_ns<$4",
                    &[&self.namespace, &path, &owner, &timestamp(stale)],
                )
                .map_err(db_error)?;
            if count > 0 {
                self.reads.insert(path.into(), None);
            }
            return Ok(count as usize);
        }
        let (lower, upper) = descendant_bounds(path);
        let count = self
            .tx
            .execute(
                "DELETE FROM public.ov_pathlock_tokens
             WHERE md5(namespace)=md5($1) AND namespace=$1 AND owner<>$2
             AND time_ns<$3 AND (
                 (md5(path)=md5($4) AND path=$4) OR
                 ($5 AND public.path_prefix_2048(path)>=public.path_prefix_2048($6)
                     AND public.path_prefix_2048(path)<=public.path_prefix_2048($7)
                     AND path>=$6 AND path<$7 AND path<>$4))",
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
            .map_err(db_error)?;
        if count > 0 {
            // Some rows survive the owner/expiry predicate. Re-read affected
            // entries rather than treating the entire subtree as absent.
            self.reads.retain(|cached, _| {
                cached != path
                    && !(cached.as_str() >= lower.as_str() && cached.as_str() < upper.as_str())
            });
        }
        // A no-op cleanup must retain shared-ancestor and negative reads.
        Ok(count as usize)
    }

    fn live_descendant(
        &mut self,
        root: &str,
        owner: &str,
        stale: u128,
    ) -> PathLockResult<Option<(String, LockToken)>> {
        let (lower, upper) = descendant_bounds(root);
        // Prefix truncation can make both bounds equal. The coarse upper
        // comparison must be inclusive; full C-collated paths resolve ties.
        self.tx
            .query_opt(
                "SELECT path,token FROM public.ov_pathlock_tokens
             WHERE md5(namespace)=md5($1) AND namespace=$1
             AND public.path_prefix_2048(path)>=public.path_prefix_2048($2)
             AND public.path_prefix_2048(path)<=public.path_prefix_2048($3)
             AND path>=$2 AND path<$3 AND path<>$4
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
        // Read catalogs after the initialization gate using a fresh snapshot,
        // even if the connection defaults to Repeatable Read.
        let mut tx = client
            .build_transaction()
            .isolation_level(IsolationLevel::ReadCommitted)
            .start()
            .map_err(db_error)?;
        tx.batch_execute(SET_TIMEOUTS).map_err(db_error)?;
        // A separate, global initialization lock serializes concurrent CREATE TABLE.
        tx.query_one("SELECT pg_advisory_xact_lock(187003451, 0)", &[])
            .map_err(db_error)?;
        initialize_schema(&mut tx)?;
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
            let mut reuse_connection = false;
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
                    reads: HashMap::new(),
                    #[cfg(test)]
                    read_queries: 0,
                });
                match result {
                    Ok(value) => {
                        tx.commit().map_err(db_error)?;
                        reuse_connection = true;
                        Ok(value)
                    }
                    Err(error) => {
                        // Retain a business-conflict connection only after the
                        // server confirms rollback. Drop alone cannot tell us
                        // whether rollback failed on an otherwise open socket.
                        tx.rollback().map_err(db_error)?;
                        reuse_connection = matches!(error, PathLockError::Conflict { .. });
                        Err(error)
                    }
                }
            })();
            // SQL/setup, commit and rollback failures remain conservative:
            // discard, without replaying the operation.
            if !reuse_connection || client.is_closed() {
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
            // Bulk expiry runs before any cached reads in this transaction.
            let cleaned = store
                .tx
                .execute(
                    "DELETE FROM public.ov_pathlock_tokens WHERE id IN (
                 SELECT id FROM public.ov_pathlock_tokens
                 WHERE md5(namespace)=md5($1) AND namespace=$1
                 AND time_ns<$2 AND owner<>$3 LIMIT 256)",
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
        let (path, mut value) = (path.to_owned(), value.clone());
        self.run(move |store| {
            if let Some(existing) = store.get(&path)? {
                return Err(PathLockError::Conflict {
                    lock_path: path.clone(),
                    owner: existing.owner_id,
                    kind: existing.lock_type,
                });
            }
            let (db_now, _) = clock(store.tx, value.time_ns, 0)?;
            normalize_token_time(&mut value, None, db_now);
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
        let (path, expected, mut replacement) =
            (path.to_owned(), expected.clone(), replacement.clone());
        self.run(move |store| {
            let matched = store.get(&path)?.as_ref() == Some(&expected);
            if matched {
                // Compare the caller's original snapshot before rebasing writes.
                let (db_now, _) = clock(store.tx, replacement.time_ns, 0)?;
                normalize_token_time(&mut replacement, Some(&expected), db_now);
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
            let (db_now, _) = clock(store.tx, time, 0)?;
            value.time_ns = db_now;
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
                 WHERE md5(namespace)=md5($1) AND namespace=$1
                 AND public.path_prefix_2048(path)>=public.path_prefix_2048($2)
                 AND public.path_prefix_2048(path)<=public.path_prefix_2048($3)
                 AND path>=$2 AND path<$3 AND path<>$4",
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

#[cfg(test)]
mod cache_tests {
    use super::*;

    async fn provider(namespace: &str) -> PostgresPathLockProvider {
        let namespace = namespace.to_owned();
        tokio::task::spawn_blocking(move || {
            PostgresPathLockProvider::from_env("OV_TEST_PG_DSN", &namespace, 30.0).unwrap()
        })
        .await
        .unwrap()
    }

    fn value(owner: &str, time: u128) -> LockToken {
        LockToken::held(owner, "h", PathLockKind::Exact, time)
    }

    #[tokio::test]
    #[ignore = "requires OV_TEST_PG_DSN"]
    async fn cached_reads_observe_insert_update_delete_and_return_independent_snapshots() {
        let p = provider(&format!("cache-test-{}", uuid::Uuid::new_v4())).await;
        p.run(|store| {
            assert!(store.get("/a")?.is_none());
            assert!(store.get("/a")?.is_none());
            assert_eq!(store.read_queries, 1, "negative reads issue one SELECT");
            let original = value("owner", 100);
            store.put("/a", &original)?;
            let mut snapshot = store.get("/a")?.unwrap();
            snapshot.owner_id = "changed-locally".into();
            assert_eq!(store.get("/a")?, Some(original));
            let replacement = value("new-owner", 200);
            store.put("/a", &replacement)?;
            assert_eq!(store.get("/a")?, Some(replacement.clone()));
            let persisted = store
                .tx
                .query_one(
                    "SELECT token FROM public.ov_pathlock_tokens WHERE namespace=$1 AND path='/a'",
                    &[&store.namespace],
                )
                .map_err(db_error)?;
            assert_eq!(token(&persisted)?, replacement);
            assert!(store.delete("/a")?);
            assert!(store.get("/a")?.is_none());
            assert!(!store.delete("/a")?);
            assert_eq!(store.read_queries, 1);
            Ok(())
        })
        .await
        .unwrap();
        assert!(p.read_token("/a").await.unwrap().is_none());
    }

    #[tokio::test]
    #[ignore = "requires OV_TEST_PG_DSN"]
    async fn stale_cleanup_invalidates_only_affected_paths_and_preserves_survivors() {
        let p = provider(&format!("cache-test-{}", uuid::Uuid::new_v4())).await;
        p.run(|store| {
            for (path, owner, time) in [
                ("/a", "other", 10),
                ("/a/stale", "other", 10),
                ("/a/live", "other", 200),
                ("/a/owned", "owner", 10),
                ("/ab", "other", 10),
                ("/outside", "other", 10),
            ] {
                store.put(path, &value(owner, time))?;
            }
            assert!(store.get("/a/missing")?.is_none());
            let reads = store.read_queries;
            assert_eq!(store.remove_stale("/a/live", false, "owner", 100)?, 0);
            assert_eq!(store.remove_stale("/a/missing", false, "owner", 100)?, 0);
            assert!(store.get("/a/live")?.is_some());
            assert!(store.get("/a/missing")?.is_none());
            assert_eq!(store.read_queries, reads, "no-op cleanup preserves reads");
            assert_eq!(store.remove_stale("/outside", false, "owner", 100)?, 1);
            assert!(store.get("/outside")?.is_none());
            assert_eq!(store.remove_stale("/a", true, "owner", 100)?, 2);
            assert!(store.get("/a")?.is_none());
            assert!(store.get("/a/stale")?.is_none());
            assert_eq!(store.get("/a/live")?, Some(value("other", 200)));
            assert_eq!(store.get("/a/owned")?, Some(value("owner", 10)));
            let reads = store.read_queries;
            assert!(store.get("/ab")?.is_some());
            assert!(store.get("/outside")?.is_none());
            assert_eq!(store.read_queries, reads, "prefix sibling stays cached");
            // Root bounds also cover every descendant, including Unicode.
            store.put("/中文/stale", &value("other", 10))?;
            assert_eq!(store.remove_stale("/", true, "owner", 100)?, 2);
            assert!(store.get("/ab")?.is_none());
            assert!(store.get("/中文/stale")?.is_none());
            assert!(store.get("/a/live")?.is_some());
            assert!(store.get("/a/owned")?.is_some());
            for path in ["/a/live", "/a/owned"] {
                store.delete(path)?;
            }
            Ok(())
        })
        .await
        .unwrap();
    }

    #[tokio::test]
    #[ignore = "requires OV_TEST_PG_DSN"]
    async fn cache_does_not_cross_transactions_or_hide_other_provider_updates() {
        let ns = format!("cache-test-{}", uuid::Uuid::new_v4());
        let first = provider(&ns).await;
        let second = provider(&ns).await;
        assert!(first.read_token("/a").await.unwrap().is_none());
        second
            .try_create_token("/a", &value("owner", 100))
            .await
            .unwrap();
        let original = first.read_token("/a").await.unwrap().unwrap();
        let replacement = value("next-owner", 200);
        assert!(second
            .compare_and_write_token("/a", &original, &replacement)
            .await
            .unwrap());
        let current = first.read_token("/a").await.unwrap().unwrap();
        assert_eq!(current.owner_id, "next-owner");
        assert!(second
            .compare_and_remove_token("/a", &current)
            .await
            .unwrap());
        assert!(first.read_token("/a").await.unwrap().is_none());
    }

    #[tokio::test]
    #[ignore = "requires OV_TEST_PG_DSN"]
    async fn rollback_discards_cached_insert_update_and_delete() {
        let p = provider(&format!("cache-test-{}", uuid::Uuid::new_v4())).await;
        p.try_create_token("/existing", &value("owner", 100))
            .await
            .unwrap();
        p.try_create_token("/deleted", &value("owner", 100))
            .await
            .unwrap();
        let original = p.read_token("/existing").await.unwrap().unwrap();
        let deleted = p.read_token("/deleted").await.unwrap().unwrap();
        let result: PathLockResult<()> = p
            .run(|store| {
                store.put("/new", &value("owner", 100))?;
                store.put("/existing", &value("other", 200))?;
                store.delete("/deleted")?;
                assert!(store.get("/new")?.is_some());
                assert_eq!(store.get("/existing")?.unwrap().owner_id, "other");
                assert!(store.get("/deleted")?.is_none());
                Err(PathLockError::Conflict {
                    lock_path: "/blocked".into(),
                    owner: "other".into(),
                    kind: PathLockKind::Tree,
                })
            })
            .await;
        assert!(matches!(result, Err(PathLockError::Conflict { .. })));
        assert!(p.read_token("/new").await.unwrap().is_none());
        assert_eq!(
            p.read_token("/existing").await.unwrap(),
            Some(original.clone())
        );
        assert_eq!(
            p.read_token("/deleted").await.unwrap(),
            Some(deleted.clone())
        );
        p.compare_and_remove_token("/existing", &original)
            .await
            .unwrap();
        p.compare_and_remove_token("/deleted", &deleted)
            .await
            .unwrap();
    }

    #[tokio::test]
    #[ignore = "requires OV_TEST_PG_DSN"]
    async fn batch_reads_shared_ancestors_once_and_preserves_holders_on_upgrade() {
        let p = provider(&format!("cache-test-{}", uuid::Uuid::new_v4())).await;
        p.run(|store| {
            let paths = [
                "/local/account/resources/a/b/c/d/src.md",
                "/local/account/resources/a/b/c/d/dst.md",
            ];
            let requests: Vec<_> = paths
                .iter()
                .map(|path| PathLockRequest {
                    path: (*path).into(),
                    kind: PathLockKind::Exact,
                })
                .collect();
            let result = acquire_holder_records(store, &requests, "owner", "first", 100, 0, None)?;
            assert_eq!(result.acquisitions.len(), 2);
            assert_eq!(store.read_queries, 10, "one SELECT per distinct path");
            let upgrade = PathLockRequest {
                path: paths[0].into(),
                kind: PathLockKind::Tree,
            };
            acquire_holder_records(store, &[upgrade], "owner", "second", 200, 0, None)?;
            let current = store.get(paths[0])?.unwrap();
            assert_eq!(current.lock_type, PathLockKind::Tree);
            assert_eq!(current.holders.len(), 2);
            assert!(current.holders.iter().any(|h| h.id == "first"));
            assert!(current.holders.iter().any(|h| h.id == "second"));
            assert_eq!(store.read_queries, 10);
            for path in paths {
                store.delete(path)?;
            }
            Ok(())
        })
        .await
        .unwrap();
    }
}

#[cfg(test)]
mod connection_tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::time::{SystemTime, UNIX_EPOCH};

    fn now() -> u128 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    }

    async fn provider(namespace: &str) -> PostgresPathLockProvider {
        let namespace = namespace.to_owned();
        tokio::task::spawn_blocking(move || {
            PostgresPathLockProvider::from_env("OV_TEST_PG_DSN", &namespace, 30.0).unwrap()
        })
        .await
        .unwrap()
    }

    async fn backend_pid(provider: &PostgresPathLockProvider) -> i32 {
        provider
            .run(|store| {
                Ok(store
                    .tx
                    .query_one("SELECT pg_backend_pid()", &[])
                    .map_err(db_error)?
                    .get::<_, i32>(0))
            })
            .await
            .unwrap()
    }

    async fn acquire(
        provider: &PostgresPathLockProvider,
        requests: &[PathLockRequest],
        owner: &str,
    ) -> PathLockResult<Option<AtomicAcquisitionResult>> {
        let now = now();
        provider
            .try_acquire_holders_atomic(
                requests,
                owner,
                "h",
                now,
                now.saturating_sub(30_000_000_000),
                None,
            )
            .await
    }

    async fn mutate(provider: &PostgresPathLockProvider, path: &str, owner: &str, release: bool) {
        let now = now();
        assert!(provider
            .mutate_holder(
                path,
                owner,
                "h",
                now,
                now.saturating_sub(30_000_000_000),
                release,
            )
            .await
            .unwrap());
    }

    #[tokio::test]
    #[ignore = "requires OV_TEST_PG_DSN"]
    async fn business_conflicts_reuse_connection_after_confirmed_rollback() {
        let namespace = format!("connection-test-{}", uuid::Uuid::new_v4());
        let owner = provider(&namespace).await;
        let contender = provider(&namespace).await;
        acquire(
            &owner,
            &[PathLockRequest {
                path: "/held".into(),
                kind: PathLockKind::Tree,
            }],
            "owner",
        )
        .await
        .unwrap();
        acquire(
            &contender,
            &[PathLockRequest {
                path: "/own".into(),
                kind: PathLockKind::Exact,
            }],
            "contender",
        )
        .await
        .unwrap();
        let original = owner.read_token("/held").await.unwrap().unwrap();
        let pid = backend_pid(&contender).await;
        for _ in 0..3 {
            assert!(matches!(
                acquire(
                    &contender,
                    &[
                        PathLockRequest {
                            path: "/new".into(),
                            kind: PathLockKind::Exact,
                        },
                        PathLockRequest {
                            path: "/held/child".into(),
                            kind: PathLockKind::Exact,
                        },
                    ],
                    "contender",
                )
                .await,
                Err(PathLockError::Conflict { .. })
            ));
            assert!(contender.read_token("/new").await.unwrap().is_none());
            assert_eq!(
                contender.read_token("/held").await.unwrap().unwrap(),
                original
            );
            mutate(&contender, "/own", "contender", false).await;
            assert_eq!(backend_pid(&contender).await, pid);
        }
        // Successful rollback also releases the namespace transaction gate.
        mutate(&owner, "/held", "owner", true).await;
        acquire(
            &contender,
            &[PathLockRequest {
                path: "/held/child".into(),
                kind: PathLockKind::Exact,
            }],
            "contender",
        )
        .await
        .unwrap();
        mutate(&contender, "/held/child", "contender", true).await;
        mutate(&contender, "/own", "contender", true).await;
        assert_eq!(backend_pid(&contender).await, pid);
    }

    #[tokio::test]
    #[ignore = "requires OV_TEST_PG_DSN"]
    async fn sql_and_rollback_errors_discard_connection() {
        let namespace = format!("connection-test-{}", uuid::Uuid::new_v4());
        let provider = provider(&namespace).await;
        let first_pid = backend_pid(&provider).await;
        let result = provider
            .run(|store| store.tx.batch_execute("SELECT 1/0").map_err(db_error))
            .await;
        assert!(matches!(result, Err(PathLockError::Io(_))));
        assert!(provider.connection.lock().unwrap().is_none());
        let second_pid = backend_pid(&provider).await;
        assert_ne!(second_pid, first_pid);

        let result = provider
            .run::<(), _>(|store| {
                let pid: i32 = store
                    .tx
                    .query_one("SELECT pg_backend_pid()", &[])
                    .map_err(db_error)?
                    .get(0);
                let dsn = std::env::var("OV_TEST_PG_DSN").unwrap();
                let mut admin = Client::connect(&dsn, postgres::NoTls).unwrap();
                assert!(admin
                    .query_one("SELECT pg_terminate_backend($1)", &[&pid])
                    .unwrap()
                    .get::<_, bool>(0));
                // Report a business conflict, but make the subsequent rollback
                // fail. The connection must not be retained based on the error alone.
                Err(PathLockError::Conflict {
                    lock_path: "/held".into(),
                    owner: "owner".into(),
                    kind: PathLockKind::Exact,
                })
            })
            .await;
        assert!(matches!(result, Err(PathLockError::Io(_))));
        assert!(provider.connection.lock().unwrap().is_none());
        assert_ne!(backend_pid(&provider).await, second_pid);
    }

    #[tokio::test]
    #[ignore = "requires OV_TEST_PG_DSN"]
    async fn commit_error_discards_connection_without_replaying_operation() {
        let namespace = format!("connection-test-{}", uuid::Uuid::new_v4());
        let provider = provider(&namespace).await;
        let pid = backend_pid(&provider).await;
        let calls = Arc::new(AtomicUsize::new(0));
        let operation_calls = calls.clone();
        let result = provider
            .run(move |store| {
                operation_calls.fetch_add(1, Ordering::SeqCst);
                // The insert succeeds; the deferred constraint fails at COMMIT.
                store
                    .tx
                    .batch_execute(
                        "CREATE TEMP TABLE ov_pathlock_commit_failure (
                             id INT PRIMARY KEY DEFERRABLE INITIALLY DEFERRED
                         );
                         INSERT INTO ov_pathlock_commit_failure VALUES (1),(1)",
                    )
                    .map_err(db_error)
            })
            .await;
        assert!(matches!(result, Err(PathLockError::Io(_))));
        assert_eq!(calls.load(Ordering::SeqCst), 1);
        assert!(provider.connection.lock().unwrap().is_none());
        assert_ne!(backend_pid(&provider).await, pid);
        assert_eq!(calls.load(Ordering::SeqCst), 1);
    }
}

#[cfg(test)]
mod schema_tests {
    use super::*;

    // These two tests use a separate disposable database. The admin DSN must
    // own its schema objects and be able to SET ROLE to the runtime DSN's user.
    // The runtime user needs CONNECT, public USAGE, table DML and function
    // EXECUTE, but must not own the table or have public CREATE.
    fn provisioned_schema() -> (Client, String) {
        let admin_dsn = std::env::var("OV_TEST_PG_SCHEMA_ADMIN_DSN").unwrap();
        let runtime_dsn = std::env::var("OV_TEST_PG_SCHEMA_RUNTIME_DSN").unwrap();
        let mut admin = Client::connect(&admin_dsn, postgres::NoTls).unwrap();
        let mut tx = admin.transaction().unwrap();
        tx.batch_execute(SET_TIMEOUTS).unwrap();
        tx.query_one("SELECT pg_advisory_xact_lock(187003451, 0)", &[])
            .unwrap();
        initialize_schema(&mut tx).unwrap();
        tx.commit().unwrap();
        let mut runtime = Client::connect(&runtime_dsn, postgres::NoTls).unwrap();
        let row = runtime
            .query_one(
                "SELECT current_user::text,
                        has_schema_privilege('public','CREATE'),
                        pg_has_role(c.relowner,'USAGE'),
                        has_table_privilege(c.oid,'SELECT,INSERT,UPDATE,DELETE'),
                        has_function_privilege('public.path_prefix_2048(text)','EXECUTE')
                 FROM pg_catalog.pg_class c
                 WHERE c.oid='public.ov_pathlock_tokens'::regclass",
                &[],
            )
            .unwrap();
        assert!(!row.get::<_, bool>(1), "runtime role must lack CREATE");
        assert!(
            !row.get::<_, bool>(2),
            "runtime role must not own the table"
        );
        assert!(row.get::<_, bool>(3));
        assert!(row.get::<_, bool>(4));
        assert_eq!(
            admin
                .query_one("SELECT current_database()::text", &[])
                .unwrap()
                .get::<_, String>(0),
            runtime
                .query_one("SELECT current_database()::text", &[])
                .unwrap()
                .get::<_, String>(0)
        );
        (admin, row.get(0))
    }

    #[tokio::test]
    #[ignore = "requires separate OV_TEST_PG_SCHEMA_ADMIN_DSN and OV_TEST_PG_SCHEMA_RUNTIME_DSN"]
    async fn provisioned_schema_runtime_role_can_build_and_use_locks() {
        use crate::core::builder::{build_default_stack, RagfsConfig};
        use crate::lock::PathLockConfig;

        tokio::task::spawn_blocking(|| drop(provisioned_schema()))
            .await
            .unwrap();
        let namespace = format!("schema-runtime-{}", uuid::Uuid::new_v4());
        let config = || RagfsConfig {
            pathlock: PathLockConfig {
                provider: "postgres".into(),
                service_name: Some(namespace.clone()),
                postgres_dsn_env: Some("OV_TEST_PG_SCHEMA_RUNTIME_DSN".into()),
                ..PathLockConfig::default()
            },
            ..RagfsConfig::default()
        };
        let first = build_default_stack(config()).await.unwrap();
        let held = first
            .pathlock_manager
            .acquire_tree("/shared", Duration::ZERO, None)
            .await
            .unwrap();
        // Reinitialization must preserve the existing lease.
        let second = build_default_stack(config()).await.unwrap();
        assert!(second
            .pathlock_manager
            .acquire_exact("/shared/child", Duration::ZERO, None)
            .await
            .is_err());
        first.pathlock_manager.refresh(&held).await.unwrap();
        first.pathlock_manager.release(&held).await.unwrap();
        let held = second
            .pathlock_manager
            .acquire_exact("/shared/child", Duration::ZERO, None)
            .await
            .unwrap();
        second.pathlock_manager.release(&held).await.unwrap();
    }

    #[test]
    #[ignore = "requires separate OV_TEST_PG_SCHEMA_ADMIN_DSN and OV_TEST_PG_SCHEMA_RUNTIME_DSN"]
    fn provisioned_schema_rejects_missing_and_incompatible_objects() {
        let (mut admin, runtime_role) = provisioned_schema();
        let set_role = format!("SET LOCAL ROLE \"{}\"", runtime_role.replace('"', "\"\""));
        let legacy_sql = r#"
            DROP TABLE public.ov_pathlock_tokens;
            CREATE TABLE public.ov_pathlock_tokens (
                namespace TEXT COLLATE "C" NOT NULL,
                path TEXT COLLATE "C" NOT NULL,
                owner TEXT NOT NULL,
                time_ns TEXT COLLATE "C" NOT NULL CHECK(length(time_ns)=39),
                token TEXT NOT NULL,
                PRIMARY KEY(namespace,path)
            );
            CREATE INDEX ov_pathlock_expiry ON public.ov_pathlock_tokens(namespace,time_ns);
        "#;
        for (mutation, expected_error) in [
            ("DROP INDEX public.ov_pathlock_lookup", "migration"),
            ("DROP FUNCTION public.path_prefix_2048(text) CASCADE", "migration"),
            (legacy_sql, "migration"),
            (
                "DROP INDEX public.ov_pathlock_lookup;
                 CREATE INDEX ov_pathlock_lookup ON public.ov_pathlock_tokens(md5(path),md5(namespace))",
                "Incompatible",
            ),
            (
                "DROP INDEX public.ov_pathlock_lookup;
                 CREATE INDEX ov_pathlock_lookup ON public.ov_pathlock_tokens(md5(namespace),md5(path))
                 WHERE owner='partial'",
                "Incompatible",
            ),
            (
                "ALTER TABLE public.ov_pathlock_tokens ALTER COLUMN id DROP DEFAULT",
                "Incompatible",
            ),
            (
                "ALTER TABLE public.ov_pathlock_tokens ALTER COLUMN time_ns TYPE TEXT COLLATE \"default\"",
                "Incompatible",
            ),
            (
                "CREATE OR REPLACE FUNCTION public.path_prefix_2048(text) RETURNS bytea
                 LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS 'SELECT convert_to($1, ''UTF8'')'",
                "Incompatible",
            ),
        ] {
            let mut tx = admin.transaction().unwrap();
            tx.batch_execute(SET_TIMEOUTS).unwrap();
            tx.batch_execute(mutation).unwrap();
            tx.batch_execute(&set_role).unwrap();
            let error = initialize_schema(&mut tx).unwrap_err().to_string();
            assert!(error.contains(expected_error), "{mutation}: {error}");
            // In particular, do not continue after a DDL permission error.
            tx.rollback().unwrap();
        }
        for (mutation, preserve_lease) in [
            ("DROP INDEX public.ov_pathlock_subtree_prefix_2048", true),
            (legacy_sql, true),
            (
                "DROP TABLE public.ov_pathlock_tokens; DROP FUNCTION public.path_prefix_2048(text)",
                false,
            ),
        ] {
            let mut tx = admin.transaction().unwrap();
            tx.batch_execute(SET_TIMEOUTS).unwrap();
            tx.batch_execute(mutation).unwrap();
            if preserve_lease {
                tx.batch_execute(
                    "INSERT INTO public.ov_pathlock_tokens(namespace,path,owner,time_ns,token)
                     VALUES ('schema-test','/held','owner',repeat('0',39),'preserved')",
                )
                .unwrap();
            }
            initialize_schema(&mut tx).unwrap();
            initialize_schema(&mut tx).unwrap();
            assert_eq!(inspect_schema(&mut tx).unwrap(), SchemaState::Ready);
            if preserve_lease {
                assert_eq!(
                    tx.query_one(
                        "SELECT token FROM public.ov_pathlock_tokens WHERE namespace='schema-test' AND path='/held'",
                        &[],
                    ).unwrap().get::<_, String>(0),
                    "preserved"
                );
            }
            tx.rollback().unwrap();
        }
    }

    #[test]
    #[ignore = "requires OV_TEST_PG_DSN"]
    fn schema_initialization_and_legacy_migration_preserve_records() {
        let dsn = std::env::var("OV_TEST_PG_DSN").unwrap();
        // Use session-local tables: migration tests must not replace the shared
        // integration-test table or interfere with other running providers.
        let sql = INITIALIZE_SCHEMA.replace("public.", "pg_temp.");
        for schema in ["fresh", "legacy", "uuid"] {
            let legacy = schema == "legacy";
            let mut client = Client::connect(&dsn, postgres::NoTls).unwrap();
            let mut tx = client.transaction().unwrap();
            if legacy {
                tx.batch_execute(
                    r#"CREATE TEMP TABLE ov_pathlock_tokens (
                        namespace TEXT COLLATE "C" NOT NULL,
                        path TEXT COLLATE "C" NOT NULL,
                        owner TEXT NOT NULL,
                        time_ns TEXT COLLATE "C" NOT NULL CHECK(length(time_ns)=39),
                        token TEXT NOT NULL,
                        PRIMARY KEY(namespace,path)
                    );
                    CREATE INDEX ov_pathlock_expiry ON ov_pathlock_tokens(namespace,time_ns);
                    INSERT INTO ov_pathlock_tokens VALUES
                        ('old','/held','owner',repeat('0',39),'preserved');"#,
                )
                .unwrap();
                tx.batch_execute("SAVEPOINT migration_failure").unwrap();
                assert!(tx.batch_execute(&format!("{sql} SELECT 1/0;")).is_err());
                tx.batch_execute("ROLLBACK TO SAVEPOINT migration_failure")
                    .unwrap();
                assert_eq!(
                    tx.query_one(
                        "SELECT token FROM pg_temp.ov_pathlock_tokens
                         WHERE namespace='old' AND path='/held'",
                        &[],
                    )
                    .unwrap()
                    .get::<_, String>(0),
                    "preserved"
                );
                assert_eq!(
                    tx.query_one(
                        "SELECT count(*) FROM pg_attribute
                         WHERE attrelid='pg_temp.ov_pathlock_tokens'::regclass
                         AND attname='id' AND NOT attisdropped",
                        &[],
                    )
                    .unwrap()
                    .get::<_, i64>(0),
                    0
                );
                assert!(tx
                    .query_one(
                        "SELECT to_regprocedure('pg_temp.path_prefix_2048(text)') IS NULL",
                        &[],
                    )
                    .unwrap()
                    .get::<_, bool>(0));
            }
            if schema == "uuid" {
                let old_sql = sql
                    .split("CREATE INDEX IF NOT EXISTS ov_pathlock_subtree_prefix_2048")
                    .next()
                    .unwrap();
                tx.batch_execute(old_sql).unwrap();
                tx.batch_execute(
                    "INSERT INTO pg_temp.ov_pathlock_tokens(namespace,path,owner,time_ns,token)
                     VALUES ('old','/held','owner',repeat('0',39),'preserved')",
                )
                .unwrap();
            }
            tx.batch_execute(&sql).unwrap();
            if schema != "fresh" {
                let row = tx
                    .query_one(
                        "SELECT id::text,token FROM pg_temp.ov_pathlock_tokens
                     WHERE namespace='old' AND path='/held'",
                        &[],
                    )
                    .unwrap();
                let id: String = row.get(0);
                assert_eq!(row.get::<_, String>(1), "preserved");
                tx.batch_execute(&sql).unwrap();
                let row = tx
                    .query_one(
                        "SELECT id::text,token FROM pg_temp.ov_pathlock_tokens
                     WHERE namespace='old' AND path='/held'",
                        &[],
                    )
                    .unwrap();
                assert_eq!(row.get::<_, String>(0), id);
                assert_eq!(row.get::<_, String>(1), "preserved");
            } else {
                tx.batch_execute(&sql).unwrap();
            }
            let indexes = tx
                .query(
                    "SELECT indexdef FROM pg_indexes
                 WHERE schemaname=(SELECT nspname FROM pg_namespace
                     WHERE oid=pg_my_temp_schema()) AND tablename='ov_pathlock_tokens'",
                    &[],
                )
                .unwrap();
            assert_eq!(indexes.len(), 4);
            assert!(indexes
                .iter()
                .any(|row| { row.get::<_, String>(0).contains("path_prefix_2048(path)") }));
            assert!(indexes.iter().all(|row| {
                let definition: String = row.get(0);
                !definition.contains("USING btree (namespace,")
                    && !definition.contains("USING btree (path)")
            }));
            let utf8 = format!("/{}", "😀".repeat(800));
            let prefix: Vec<u8> = tx
                .query_one("SELECT pg_temp.path_prefix_2048($1)", &[&utf8])
                .unwrap()
                .get(0);
            assert_eq!(prefix, utf8.as_bytes()[..2048]);
            assert!(std::str::from_utf8(&prefix).is_err());
            tx.batch_execute("SET LOCAL client_encoding = 'LATIN1'")
                .unwrap();
            let alternate: Vec<u8> = tx
                .query_one(
                    "SELECT pg_temp.path_prefix_2048('/' || repeat(chr(128512),800))",
                    &[],
                )
                .unwrap()
                .get(0);
            assert_eq!(alternate, prefix);
            // Rollback removes the temporary schema objects and all test rows.
            tx.rollback().unwrap();
        }
    }
}
