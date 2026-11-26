//! Durable shared queues. Runtime performs DML only; provision postgres_schema.sql first.
//! Blocking postgres clients never run or drop on Tokio's async workers.
use super::backend::{Message, QueueState, StoredMessage};
use crate::core::errors::{Error, Result};
use postgres::{Client, Config, IsolationLevel, Transaction};
use postgres_native_tls::MakeTlsConnector;
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc, Mutex,
};
use std::time::Duration;
use tokio::task::JoinHandle;

const TIMEOUTS: &str = "SET LOCAL statement_timeout='5s'; SET LOCAL lock_timeout='1s';
    SET LOCAL synchronous_commit=on; SET LOCAL search_path=pg_catalog,public";

fn db_error(error: postgres::Error) -> Error {
    // Never include the DSN, server detail, SQL parameters, or stored message content.
    Error::config(format!(
        "PostgreSQL QueueFS operation failed (SQLSTATE {})",
        error.code().map(|c| c.code()).unwrap_or("unavailable")
    ))
}
fn lease_error() -> Error {
    Error::InvalidOperation("PostgreSQL QueueFS consumer lease lost; restart this instance".into())
}
fn connect(config: &Config) -> Result<Client> {
    let tls = native_tls::TlsConnector::builder()
        .build()
        .map_err(|_| Error::config("PostgreSQL QueueFS TLS initialization failed"))?;
    config.connect(MakeTlsConnector::new(tls)).map_err(db_error)
}

struct Connection {
    client: Mutex<Option<Client>>,
    config: Config,
}
impl Drop for Connection {
    fn drop(&mut self) {
        let client = self
            .client
            .get_mut()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .take();
        if client.is_some() {
            std::thread::spawn(move || drop(client));
        }
    }
}
impl Connection {
    fn with<T>(&self, f: impl FnOnce(&mut Client) -> Result<T>) -> Result<T> {
        let mut guard = self
            .client
            .lock()
            .map_err(|_| Error::config("PostgreSQL QueueFS connection mutex poisoned"))?;
        if guard.as_ref().is_none_or(Client::is_closed) {
            // Reconnect only for a NEW operation, never replay an uncertain commit.
            *guard = Some(connect(&self.config)?);
        }
        f(guard.as_mut().expect("connected client"))
    }
}
struct Inner {
    connection: Connection,
    namespace: String,
    owner: String,
    stopped: AtomicBool,
}
/// PostgreSQL queue namespace with an independently renewed consumer lease.
pub struct PostgresQueueStorage {
    inner: Arc<Inner>,
    heartbeat: JoinHandle<()>,
}
impl Drop for PostgresQueueStorage {
    fn drop(&mut self) {
        self.heartbeat.abort();
        // Do not release ownership early: a blocking operation can still be in flight.
        // Other consumers recover these messages after the database lease expires.
    }
}

fn validate_name(name: &str, max: usize, label: &str) -> Result<()> {
    if name.is_empty() || name.len() > max || name.contains('\0') {
        return Err(Error::config(format!(
            "PostgreSQL QueueFS {label} must contain 1..={max} UTF-8 bytes without NUL"
        )));
    }
    Ok(())
}
fn decoded(payload: String) -> Result<Message> {
    serde_json::from_str::<StoredMessage>(&payload)
        .map(StoredMessage::into_message)
        .map_err(|_| Error::config("PostgreSQL QueueFS stored message is invalid"))
}
fn live(tx: &mut Transaction<'_>, inner: &Inner) -> Result<()> {
    if inner.stopped.load(Ordering::Acquire)
        || tx
            .query_opt(
                "SELECT owner FROM public.ov_queuefs_workers
                 WHERE namespace=$1 AND owner=$2 AND lease_expires_at>clock_timestamp()
                 FOR SHARE",
                &[&inner.namespace, &inner.owner],
            )
            .map_err(db_error)?
            .is_none()
    {
        return Err(lease_error());
    }
    Ok(())
}

impl PostgresQueueStorage {
    /// Connect using a DSN environment variable; requires an administrator-provisioned schema.
    pub async fn from_env(env: &str, namespace: &str, lease_seconds: u64) -> Result<Self> {
        validate_name(namespace, 256, "namespace")?;
        if !(10..=86_400).contains(&lease_seconds) {
            return Err(Error::config(
                "PostgreSQL QueueFS lease_seconds must be 10..=86400",
            ));
        }
        if env.is_empty()
            || !env
                .bytes()
                .enumerate()
                .all(|(i, b)| b == b'_' || b.is_ascii_alphabetic() || (i > 0 && b.is_ascii_digit()))
        {
            return Err(Error::config(
                "PostgreSQL QueueFS DSN environment variable name is invalid",
            ));
        }
        let dsn = std::env::var(env)
            .map_err(|_| Error::config("PostgreSQL QueueFS DSN environment variable is unset"))?;
        let mut config: Config = dsn
            .parse()
            .map_err(|_| Error::config("PostgreSQL QueueFS DSN is invalid"))?;
        config.connect_timeout(Duration::from_secs(5));
        config.target_session_attrs(postgres::config::TargetSessionAttrs::ReadWrite);
        let namespace = namespace.to_owned();
        let owner = uuid::Uuid::new_v4().to_string();
        config.application_name(&format!("ov-queuefs-{owner}"));
        let seconds = lease_seconds as f64;
        let (inner, heartbeat_connection) = tokio::task::spawn_blocking(move || {
            let mut client = connect(&config)?;
            let heartbeat_client = connect(&config)?;
            let mut tx = client.transaction().map_err(db_error)?;
            tx.batch_execute(TIMEOUTS).map_err(db_error)?;
            let ready = tx.query_one(
                "SELECT (SELECT count(*)=1 AND min(version)=1 FROM public.ov_queuefs_schema)
                 AND (SELECT count(*)=4 AND bool_and(c.relkind='r' AND c.relpersistence='p' AND NOT c.relrowsecurity)
                      FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                      WHERE n.nspname='public' AND c.relname IN
                      ('ov_queuefs_schema','ov_queuefs_queues','ov_queuefs_workers','ov_queuefs_messages'))",
                &[],
            ).map_err(|_| Error::config(
                "PostgreSQL QueueFS schema is missing or inaccessible; provision postgres_schema.sql with the migration role"
            ))?.get::<_, bool>(0);
            if !ready {
                return Err(Error::config("PostgreSQL QueueFS requires schema version 1, logged tables and no RLS"));
            }
            tx.execute(
                "INSERT INTO public.ov_queuefs_workers(namespace,owner,lease_expires_at)
                 VALUES($1,$2,clock_timestamp()+$3*interval '1 second')",
                &[&namespace, &owner, &seconds],
            ).map_err(db_error)?;
            tx.commit().map_err(db_error)?;
            let heartbeat_connection = Arc::new(Connection {
                client: Mutex::new(Some(heartbeat_client)), config: config.clone(),
            });
            Ok((Arc::new(Inner {
                connection: Connection { client: Mutex::new(Some(client)), config },
                namespace, owner, stopped: AtomicBool::new(false),
            }), heartbeat_connection))
        }).await.map_err(|_| Error::config("PostgreSQL QueueFS initialization worker failed"))??;
        let weak = Arc::downgrade(&inner);
        let heartbeat = tokio::spawn(async move {
            let mut ticks = tokio::time::interval(Duration::from_secs(lease_seconds / 3));
            ticks.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
            loop {
                ticks.tick().await;
                let Some(inner) = weak.upgrade() else { break };
                if inner.stopped.load(Ordering::Acquire) {
                    break;
                }
                let connection = heartbeat_connection.clone();
                let renewed = tokio::task::spawn_blocking(move || {
                    connection.with(|client| {
                        let mut tx = client.transaction().map_err(db_error)?;
                        tx.batch_execute(TIMEOUTS).map_err(db_error)?;
                        let count = tx
                            .execute(
                                "UPDATE public.ov_queuefs_workers
                         SET lease_expires_at=clock_timestamp()+$3*interval '1 second'
                         WHERE namespace=$1 AND owner=$2 AND lease_expires_at>clock_timestamp()",
                                &[&inner.namespace, &inner.owner, &seconds],
                            )
                            .map_err(db_error)?;
                        tx.commit().map_err(db_error)?;
                        Ok(count)
                    })
                })
                .await;
                if matches!(renewed, Ok(Ok(0))) {
                    break;
                }
                // A transient error may heal before expiry; the conditional UPDATE
                // can never resurrect an expired consumer.
            }
        });
        Ok(Self { inner, heartbeat })
    }

    async fn run<T: Send + 'static>(
        &self,
        operation: impl FnOnce(&mut Transaction<'_>, &Inner) -> Result<T> + Send + 'static,
    ) -> Result<T> {
        let inner = self.inner.clone();
        tokio::task::spawn_blocking(move || {
            inner.connection.with(|client| {
                let mut tx = client
                    .build_transaction()
                    .isolation_level(IsolationLevel::ReadCommitted)
                    .start()
                    .map_err(db_error)?;
                tx.batch_execute(TIMEOUTS).map_err(db_error)?;
                live(&mut tx, &inner)?;
                let value = operation(&mut tx, &inner)?;
                live(&mut tx, &inner)?;
                tx.commit().map_err(db_error)?;
                Ok(value)
            })
        })
        .await
        .map_err(|_| Error::config("PostgreSQL QueueFS operation worker failed"))?
    }
    async fn queue<T: Send + 'static>(
        &self,
        name: &str,
        exclusive: bool,
        operation: impl FnOnce(&mut Transaction<'_>, &Inner, &str) -> Result<T> + Send + 'static,
    ) -> Result<T> {
        validate_name(name, 1024, "queue name")?;
        let name = name.to_owned();
        self.run(move |tx, inner| {
            let sql = if exclusive {
                "SELECT name FROM public.ov_queuefs_queues WHERE namespace=$1 AND name=$2 FOR UPDATE"
            } else {
                "SELECT name FROM public.ov_queuefs_queues WHERE namespace=$1 AND name=$2 FOR SHARE"
            };
            if tx.query_opt(sql, &[&inner.namespace, &name]).map_err(db_error)?.is_none() {
                return Err(Error::NotFound(format!("queue '{name}' not found")));
            }
            operation(tx, inner, &name)
        }).await
    }
    /// Stop accepting operations and release this consumer after in-flight database operations finish.
    /// The caller must drain its business workers before invoking this method.
    pub async fn shutdown(&self) -> Result<()> {
        self.inner.stopped.store(true, Ordering::Release);
        self.heartbeat.abort();
        let inner = self.inner.clone();
        tokio::task::spawn_blocking(move || {
            inner.connection.with(|client| {
                let mut tx = client.transaction().map_err(db_error)?;
                tx.batch_execute(TIMEOUTS).map_err(db_error)?;
                tx.execute(
                    "DELETE FROM public.ov_queuefs_workers WHERE namespace=$1 AND owner=$2",
                    &[&inner.namespace, &inner.owner],
                )
                .map_err(db_error)?;
                tx.commit().map_err(db_error)
            })
        })
        .await
        .map_err(|_| Error::config("PostgreSQL QueueFS shutdown worker failed"))?
    }
    /// Create a queue; an existing queue returns AlreadyExists.
    pub async fn create_queue(&self, name: &str) -> Result<()> {
        validate_name(name, 1024, "queue name")?;
        let name = name.to_owned();
        self.run(move |tx, inner| {
            if tx.execute(
                "INSERT INTO public.ov_queuefs_queues(namespace,name) VALUES($1,$2) ON CONFLICT DO NOTHING",
                &[&inner.namespace, &name],
            ).map_err(db_error)? == 0 {
                return Err(Error::AlreadyExists(format!("queue '{name}' already exists")));
            }
            Ok(())
        }).await
    }
    /// Remove a queue and all its pending and processing messages.
    pub async fn remove_queue(&self, name: &str) -> Result<()> {
        self.queue(name, true, |tx, i, n| {
            tx.execute(
                "DELETE FROM public.ov_queuefs_queues WHERE namespace=$1 AND name=$2",
                &[&i.namespace, &n],
            )
            .map_err(db_error)?;
            Ok(())
        })
        .await
    }
    /// Check queue existence without hiding database errors.
    pub async fn queue_exists(&self, name: &str) -> Result<bool> {
        validate_name(name, 1024, "queue name")?;
        let name = name.to_owned();
        self.run(move |tx, i| {
            Ok(tx
                .query_opt(
                    "SELECT name FROM public.ov_queuefs_queues WHERE namespace=$1 AND name=$2",
                    &[&i.namespace, &name],
                )
                .map_err(db_error)?
                .is_some())
        })
        .await
    }
    /// List names with a literal prefix, scoped to this namespace.
    pub async fn list_queues(&self, prefix: &str) -> Result<Vec<String>> {
        let prefix = prefix.to_owned();
        self.run(move |tx, i| {
            Ok(tx
                .query(
                    "SELECT name FROM public.ov_queuefs_queues
             WHERE namespace=$1 AND starts_with(name,$2) ORDER BY name",
                    &[&i.namespace, &prefix],
                )
                .map_err(db_error)?
                .into_iter()
                .map(|r| r.get(0))
                .collect())
        })
        .await
    }
    /// Persist a message before returning; uncertain commits are never replayed.
    pub async fn enqueue(&self, name: &str, message: Message) -> Result<()> {
        message.validate_keys()?;
        for key in message.group_key.iter().chain(message.serial_key.iter()) {
            validate_name(key, 4096, "scheduling key")?;
        }
        validate_name(&message.id, 128, "message ID")?;
        let payload = serde_json::to_string(&StoredMessage::from_message(&message))
            .map_err(|_| Error::config("PostgreSQL QueueFS message serialization failed"))?;
        self.queue(name, false, move |tx, i, n| {
            tx.execute(
                "INSERT INTO public.ov_queuefs_messages(namespace,queue_name,message_id,payload,group_key,serial_key)
                 VALUES($1,$2,$3,$4,$5,$6)",
                &[&i.namespace, &n, &message.id, &payload, &message.group_key, &message.serial_key],
            )
            .map_err(db_error)?;
            Ok(())
        })
        .await
    }
    /// Recover expired owners and atomically claim the earliest unlocked pending message.
    pub async fn dequeue(&self, name: &str) -> Result<Option<Message>> {
        self.queue(name, false, |tx, i, n| {
            // Serialize the eligibility decision per queue, including absent
            // group owners. Row locks alone cannot protect these predicates.
            // Hash collisions only add contention; full key equality below is authoritative.
            tx.query_one(
                "SELECT pg_advisory_xact_lock(hashtextextended($1::text || ':' || $2::text,0))",
                &[&i.namespace, &n],
            ).map_err(db_error)?;
            // Lock candidates first, then use a NEW statement snapshot to check
            // ownership. A concurrent reclaim may have installed a newly
            // registered owner that was invisible in the first snapshot.
            let stale: Vec<i64> = tx
                .query(
                    "SELECT m.sequence FROM public.ov_queuefs_messages m
                 WHERE m.namespace=$1 AND m.queue_name=$2 AND m.owner IS NOT NULL
                 AND NOT EXISTS (SELECT 1 FROM public.ov_queuefs_workers w
                     WHERE w.namespace=m.namespace AND w.owner=m.owner
                     AND w.lease_expires_at>clock_timestamp())
                 FOR UPDATE OF m SKIP LOCKED",
                    &[&i.namespace, &n],
                )
                .map_err(db_error)?
                .into_iter()
                .map(|r| r.get(0))
                .collect();
            if !stale.is_empty() {
                tx.execute(
                    "UPDATE public.ov_queuefs_messages m SET owner=NULL
                     WHERE m.sequence=ANY($1) AND m.owner IS NOT NULL
                     AND NOT EXISTS (SELECT 1 FROM public.ov_queuefs_workers w
                         WHERE w.namespace=m.namespace AND w.owner=m.owner
                         AND w.lease_expires_at>clock_timestamp())",
                    &[&stale],
                )
                .map_err(db_error)?;
            }
            let row = tx
                .query_opt(
                    "WITH candidate AS (
                    SELECT candidate.sequence FROM public.ov_queuefs_messages candidate
                    WHERE candidate.namespace=$1 AND candidate.queue_name=$2 AND candidate.owner IS NULL
                      AND (candidate.serial_key IS NULL OR NOT EXISTS (
                          SELECT 1 FROM public.ov_queuefs_messages earlier
                          WHERE earlier.namespace=$1 AND earlier.queue_name=$2
                            AND md5(earlier.serial_key)=md5(candidate.serial_key)
                            AND earlier.serial_key=candidate.serial_key
                            AND (earlier.sequence<candidate.sequence OR earlier.owner IS NOT NULL)))
                      AND (candidate.group_key IS NULL OR NOT EXISTS (
                          SELECT 1 FROM public.ov_queuefs_messages active
                          WHERE active.namespace=$1 AND active.queue_name=$2
                            AND md5(active.group_key)=md5(candidate.group_key)
                            AND active.group_key=candidate.group_key
                            AND active.owner IS NOT NULL AND active.owner<>$3))
                    ORDER BY candidate.schedule_sequence FOR UPDATE OF candidate SKIP LOCKED LIMIT 1)
                 UPDATE public.ov_queuefs_messages m SET owner=$3
                 FROM candidate c WHERE m.sequence=c.sequence RETURNING m.payload",
                    &[&i.namespace, &n, &i.owner],
                )
                .map_err(db_error)?;
            row.map(|r| decoded(r.get(0))).transpose()
        })
        .await
    }
    /// Read the earliest pending message without claiming it.
    pub async fn peek(&self, name: &str) -> Result<Option<Message>> {
        self.queue(name, false, |tx, i, n| {
            tx.query_opt(
                "SELECT payload FROM public.ov_queuefs_messages
                 WHERE namespace=$1 AND queue_name=$2 AND owner IS NULL ORDER BY schedule_sequence LIMIT 1",
                &[&i.namespace, &n],
            )
            .map_err(db_error)?
            .map(|r| decoded(r.get(0)))
            .transpose()
        })
        .await
    }
    /// Read pending and processing counts from one database statement snapshot.
    pub async fn status(&self, name: &str) -> Result<QueueState> {
        self.queue(name, false, |tx, i, n| {
            let r = tx.query_one(
                "SELECT count(*) FILTER(WHERE owner IS NULL), count(*) FILTER(WHERE owner IS NOT NULL)
                 FROM public.ov_queuefs_messages WHERE namespace=$1 AND queue_name=$2",
                &[&i.namespace, &n],
            ).map_err(db_error)?;
            Ok(QueueState { pending: r.get::<_, i64>(0) as usize, processing: r.get::<_, i64>(1) as usize })
        }).await
    }
    /// Count pending messages (processing messages are reported by status).
    pub async fn size(&self, name: &str) -> Result<usize> {
        Ok(self.status(name).await?.pending)
    }
    /// Read all pending and processing messages in sequence order.
    pub async fn list_unacked(&self, name: &str) -> Result<Vec<Message>> {
        self.queue(name, false, |tx, i, n| {
            tx.query(
                "SELECT payload FROM public.ov_queuefs_messages
                 WHERE namespace=$1 AND queue_name=$2 ORDER BY schedule_sequence",
                &[&i.namespace, &n],
            )
            .map_err(db_error)?
            .into_iter()
            .map(|r| decoded(r.get(0)))
            .collect()
        })
        .await
    }
    /// Delete all messages, serialized against queue operations.
    pub async fn clear(&self, name: &str) -> Result<()> {
        self.queue(name, true, |tx, i, n| {
            tx.execute(
                "DELETE FROM public.ov_queuefs_messages WHERE namespace=$1 AND queue_name=$2",
                &[&i.namespace, &n],
            )
            .map_err(db_error)?;
            Ok(())
        })
        .await
    }
    /// Delete only a message claimed by this live consumer; duplicates return false.
    pub async fn ack(&self, name: &str, id: &str) -> Result<bool> {
        let id = id.to_owned();
        self.queue(name, false, move |tx, i, n| {
            Ok(tx
                .execute(
                    "DELETE FROM public.ov_queuefs_messages
             WHERE namespace=$1 AND queue_name=$2 AND message_id=$3 AND owner=$4",
                    &[&i.namespace, &n, &id, &i.owner],
                )
                .map_err(db_error)?
                == 1)
        })
        .await
    }

    /// Return this consumer's processing message to pending, retaining serial priority.
    pub async fn retry(&self, name: &str, id: &str) -> Result<bool> {
        let id = id.to_owned();
        self.queue(name, false, move |tx, i, n| {
            Ok(tx
                .execute(
                    "UPDATE public.ov_queuefs_messages
                 SET owner=NULL,schedule_sequence=nextval('public.ov_queuefs_messages_sequence_seq')
                 WHERE namespace=$1 AND queue_name=$2 AND message_id=$3 AND owner=$4",
                    &[&i.namespace, &n, &id, &i.owner],
                )
                .map_err(db_error)?
                == 1)
        })
        .await
    }
}
