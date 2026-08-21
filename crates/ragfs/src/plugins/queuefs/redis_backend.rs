use super::backend::{Message, QueueBackend, StoredMessage};
use super::{RedisMode, RedisQueueOptions};
use crate::core::errors::{Error, Result};
use redis::cluster::ClusterClient;
use redis::sentinel::{SentinelClient, SentinelClientBuilder, SentinelServerType};
use redis::{
    Connection, ConnectionAddr, ConnectionInfo, ConnectionLike, IntoConnectionInfo, RedisError,
    RedisConnectionInfo, RedisResult, ServerErrorKind, TlsMode,
};
use r2d2::{ManageConnection, Pool};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc::{self, RecvTimeoutError, Sender};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use uuid::Uuid;

const HEARTBEAT_TTL_SECS: u64 = 30;
const HEARTBEAT_INTERVAL_SECS: u64 = 10;
const STARTUP_RECOVERY_SWEEPS: usize = 3;
const REDIS_POOL_MAX_SIZE: u32 = 2;
const REDIS_POOL_CHECKOUT_TIMEOUT: Duration = Duration::from_secs(5);
const SENTINEL_DISCOVERY_TIMEOUT: Duration = Duration::from_secs(30);

const CREATE_QUEUE_SCRIPT: &str = r#"
if redis.call('SADD', KEYS[1], ARGV[1]) == 0 then
    return 0
end
redis.call('HSET', KEYS[2], 'created_at', ARGV[2], 'last_updated', ARGV[2])
return 1
"#;

const REMOVE_QUEUE_SCRIPT: &str = r#"
local removed = 0
local queues = redis.call('SMEMBERS', KEYS[1])
for _, queue in ipairs(queues) do
    if queue == ARGV[1] or string.sub(queue, 1, string.len(ARGV[1]) + 1) == ARGV[1] .. '/' then
        local prefix = ARGV[2] .. queue
        local pending_key = prefix .. ':pending'
        local processing_key = prefix .. ':processing'
        local message_prefix = prefix .. ':msg:'
        local pending = redis.call('LRANGE', pending_key, 0, -1)
        for _, id in ipairs(pending) do
            redis.call('DEL', message_prefix .. id)
        end
        local processing = redis.call('ZRANGE', processing_key, 0, -1)
        for _, member in ipairs(processing) do
            local separator = string.find(member, '|', 1, true)
            if separator then
                redis.call('DEL', message_prefix .. string.sub(member, 1, separator - 1))
            end
        end
        redis.call('DEL', prefix .. ':meta', pending_key, processing_key)
        redis.call('SREM', KEYS[1], queue)
        removed = removed + 1
    end
end
return removed
"#;

const ENQUEUE_SCRIPT: &str = r#"
if redis.call('SISMEMBER', KEYS[1], ARGV[1]) == 0 then
    return 0
end
redis.call('SET', KEYS[2], ARGV[3])
redis.call('RPUSH', KEYS[3], ARGV[2])
redis.call('HSET', KEYS[4], 'last_updated', ARGV[4])
return 1
"#;

const DEQUEUE_SCRIPT: &str = r#"
local id = redis.call('LPOP', KEYS[1])
if not id then
    return nil
end
local payload = redis.call('GET', ARGV[1] .. id)
if not payload then
    redis.call('LPUSH', KEYS[1], id)
    return redis.error_reply('queuefs payload missing for message ' .. id)
end
redis.call('ZADD', KEYS[2], ARGV[3], id .. '|' .. ARGV[2])
return {id, payload}
"#;

const PEEK_SCRIPT: &str = r#"
local id = redis.call('LINDEX', KEYS[1], 0)
if not id then
    return nil
end
local payload = redis.call('GET', ARGV[1] .. id)
if not payload then
    return redis.error_reply('queuefs payload missing for message ' .. id)
end
return payload
"#;

const LIST_UNACKED_SCRIPT: &str = r#"
local result = {}
local pending = redis.call('LRANGE', KEYS[1], 0, -1)
for _, id in ipairs(pending) do
    local payload = redis.call('GET', ARGV[1] .. id)
    if not payload then
        return redis.error_reply('queuefs payload missing for message ' .. id)
    end
    table.insert(result, payload)
end
local processing = redis.call('ZRANGE', KEYS[2], 0, -1)
for _, member in ipairs(processing) do
    local separator = string.find(member, '|', 1, true)
    if separator then
        local id = string.sub(member, 1, separator - 1)
        local payload = redis.call('GET', ARGV[1] .. id)
        if not payload then
            return redis.error_reply('queuefs payload missing for message ' .. id)
        end
        table.insert(result, payload)
    end
end
return result
"#;

const ACK_SCRIPT: &str = r#"
local members = redis.call('ZRANGE', KEYS[1], 0, -1)
for _, member in ipairs(members) do
    if string.sub(member, 1, string.len(ARGV[1]) + 1) == ARGV[1] .. '|' then
        redis.call('ZREM', KEYS[1], member)
        redis.call('DEL', KEYS[2])
        return 1
    end
end
return 0
"#;

const CLEAR_SCRIPT: &str = r#"
local pending = redis.call('LRANGE', KEYS[1], 0, -1)
for _, id in ipairs(pending) do
    redis.call('DEL', ARGV[1] .. id)
end
local processing = redis.call('ZRANGE', KEYS[2], 0, -1)
for _, member in ipairs(processing) do
    local separator = string.find(member, '|', 1, true)
    if separator then
        redis.call('DEL', ARGV[1] .. string.sub(member, 1, separator - 1))
    end
end
redis.call('DEL', KEYS[1], KEYS[2])
return #pending + #processing
"#;

const RECOVER_STALE_SCRIPT: &str = r#"
local recovered = 0
local members = redis.call('ZRANGE', KEYS[1], 0, -1)
for _, member in ipairs(members) do
    local separator = string.find(member, '|', 1, true)
    if separator then
        local id = string.sub(member, 1, separator - 1)
        local instance = string.sub(member, separator + 1)
        if redis.call('EXISTS', ARGV[1] .. instance .. ':alive') == 0 then
            redis.call('ZREM', KEYS[1], member)
            redis.call('RPUSH', KEYS[2], id)
            recovered = recovered + 1
        end
    end
end
return recovered
"#;

struct QueueKeys {
    meta: String,
    pending: String,
    processing: String,
    message_prefix: String,
}

impl QueueKeys {
    /// Build all Redis keys owned by one queue.
    fn new(key_prefix: &str, queue: &str) -> Self {
        let prefix = format!("{}{queue}", queue_key_prefix(key_prefix));
        Self {
            meta: format!("{prefix}:meta"),
            pending: format!("{prefix}:pending"),
            processing: format!("{prefix}:processing"),
            message_prefix: format!("{prefix}:msg:"),
        }
    }

    /// Build the payload key for one message.
    fn message(&self, message_id: &str) -> String {
        format!("{}{message_id}", self.message_prefix)
    }
}

struct SentinelConnection {
    connection: Connection,
    generation: ConnectionGeneration,
    snapshot: u64,
}

impl SentinelConnection {
    /// Wrap one Sentinel connection with the current topology generation.
    fn new(connection: Connection, generation: ConnectionGeneration) -> Self {
        let snapshot = generation.snapshot();
        Self {
            connection,
            generation,
            snapshot,
        }
    }

    /// Invalidate this and every other connection from the same Sentinel generation.
    fn invalidate_topology(&self) {
        self.generation.invalidate();
    }

    /// Return whether this connection belongs to the current Sentinel topology.
    fn is_current(&self) -> bool {
        self.generation.is_current(self.snapshot)
    }
}

#[derive(Clone, Default)]
struct ConnectionGeneration(Arc<AtomicU64>);

impl ConnectionGeneration {
    /// Capture the generation assigned to a newly created connection.
    fn snapshot(&self) -> u64 {
        self.0.load(Ordering::SeqCst)
    }

    /// Advance the generation after a Sentinel topology error.
    fn invalidate(&self) {
        self.0.fetch_add(1, Ordering::SeqCst);
    }

    /// Return whether a connection belongs to the current generation.
    fn is_current(&self, snapshot: u64) -> bool {
        self.snapshot() == snapshot
    }
}

struct SingletonPoolManager {
    client: redis::Client,
    connect_timeout: Duration,
    command_timeout: Duration,
}

impl SingletonPoolManager {
    /// Build a Singleton manager with physical connect and command timeouts.
    fn new(client: redis::Client, options: &RedisQueueOptions) -> Self {
        Self {
            client,
            connect_timeout: Duration::from_millis(options.connect_timeout_ms),
            command_timeout: Duration::from_millis(options.command_timeout_ms),
        }
    }
}

impl ManageConnection for SingletonPoolManager {
    type Connection = Connection;
    type Error = RedisError;

    /// Open one Singleton connection with the configured physical connect timeout.
    fn connect(&self) -> RedisResult<Self::Connection> {
        let connection = self
            .client
            .get_connection_with_timeout(self.connect_timeout)?;
        configure_connection(&connection, self.command_timeout)?;
        Ok(connection)
    }

    /// Validate one checked-out Singleton connection.
    fn is_valid(&self, connection: &mut Self::Connection) -> RedisResult<()> {
        validate_connection(connection)
    }

    /// Return whether r2d2 must discard this Singleton connection.
    fn has_broken(&self, connection: &mut Self::Connection) -> bool {
        !connection.is_open()
    }
}

struct SentinelPoolManager {
    client: Mutex<SentinelClient>,
    discovery_runtime: tokio::runtime::Runtime,
    connect_timeout: Duration,
    command_timeout: Duration,
    generation: ConnectionGeneration,
}

impl SentinelPoolManager {
    /// Build a Sentinel manager from `client` and `options`, returning an initialization error.
    fn new(client: SentinelClient, options: &RedisQueueOptions) -> RedisResult<Self> {
        let discovery_runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .map_err(RedisError::from)?;
        Ok(Self {
            client: Mutex::new(client),
            discovery_runtime,
            connect_timeout: Duration::from_millis(options.connect_timeout_ms),
            command_timeout: Duration::from_millis(options.command_timeout_ms),
            generation: ConnectionGeneration::default(),
        })
    }
}

impl ManageConnection for SentinelPoolManager {
    type Connection = SentinelConnection;
    type Error = RedisError;

    /// Discover the current Sentinel master and open one data connection.
    fn connect(&self) -> RedisResult<Self::Connection> {
        let client = {
            let mut sentinel = self.client.lock().map_err(|_| {
                RedisError::from((
                    redis::ErrorKind::Client,
                    "redis sentinel client lock poisoned",
                ))
            })?;
            self.discovery_runtime.block_on(async {
                match tokio::time::timeout(
                    SENTINEL_DISCOVERY_TIMEOUT,
                    sentinel.async_get_client(),
                )
                .await
                {
                    Ok(result) => result,
                    Err(_) => Err(RedisError::from(std::io::Error::new(
                        std::io::ErrorKind::TimedOut,
                        "redis sentinel discovery timed out",
                    ))),
                }
            })
        }?;
        let connection = client.get_connection_with_timeout(self.connect_timeout)?;
        configure_connection(&connection, self.command_timeout)?;
        Ok(SentinelConnection::new(
            connection,
            self.generation.clone(),
        ))
    }

    /// Validate one checked-out Sentinel data connection.
    fn is_valid(&self, connection: &mut Self::Connection) -> RedisResult<()> {
        if !connection.is_current() {
            return Err(RedisError::from((
                redis::ErrorKind::Io,
                "redis sentinel topology changed",
            )));
        }
        validate_connection(&mut connection.connection)
    }

    /// Return whether r2d2 must discard this Sentinel data connection.
    fn has_broken(&self, connection: &mut Self::Connection) -> bool {
        !connection.is_current() || !connection.connection.is_open()
    }
}

#[derive(Clone)]
enum RedisPool {
    Singleton(Pool<SingletonPoolManager>),
    Cluster(Pool<ClusterClient>),
    Sentinel(Pool<SentinelPoolManager>),
}

impl RedisPool {
    /// Build and initialize the configured topology pool.
    fn open(options: &RedisQueueOptions) -> Result<Self> {
        let config_error = |error| {
            Error::config(format!("invalid queuefs redis client configuration: {error}"))
        };
        match options.mode {
            RedisMode::Singleton => {
                let info = endpoint_connection_info(&options.endpoints[0], options)
                    .map_err(config_error)?;
                let client = redis::Client::open(info).map_err(config_error)?;
                build_pool(SingletonPoolManager::new(client, options)).map(Self::Singleton)
            }
            RedisMode::Cluster => {
                let nodes = options
                    .endpoints
                    .iter()
                    .map(|endpoint| endpoint_connection_info(endpoint, options))
                    .collect::<RedisResult<Vec<_>>>()
                    .map_err(config_error)?;
                let mut builder = ClusterClient::builder(nodes)
                    .retries(0)
                    .connection_timeout(Duration::from_millis(options.connect_timeout_ms))
                    .response_timeout(Duration::from_millis(options.command_timeout_ms));
                if let Some(username) = &options.username {
                    builder = builder.username(username.clone());
                }
                if let Some(password) = &options.password {
                    builder = builder.password(password.clone());
                }
                if options.tls_enabled {
                    builder = builder
                        .tls(tls_mode(options))
                        .danger_accept_invalid_hostnames(options.tls_insecure_skip_verify);
                }
                build_pool(builder.build().map_err(config_error)?).map(Self::Cluster)
            }
            RedisMode::Sentinel => {
                let sentinels = options
                    .endpoints
                    .iter()
                    .map(|endpoint| sentinel_addr(endpoint, options))
                    .collect::<RedisResult<Vec<_>>>()
                    .map_err(config_error)?;
                let mut builder = SentinelClientBuilder::new(
                    sentinels,
                    options
                        .master_name
                        .clone()
                        .expect("sentinel master_name is validated"),
                    SentinelServerType::Master,
                )
                .map_err(config_error)?
                .set_client_to_redis_db(options.db);
                if let Some(username) = &options.username {
                    builder = builder.set_client_to_redis_username(username.clone());
                }
                if let Some(password) = &options.password {
                    builder = builder.set_client_to_redis_password(password.clone());
                }
                if let Some(username) = &options.sentinel_username {
                    builder = builder.set_client_to_sentinel_username(username.clone());
                }
                if let Some(password) = &options.sentinel_password {
                    builder = builder.set_client_to_sentinel_password(password.clone());
                }
                if options.tls_enabled {
                    let mode = tls_mode(options);
                    builder = builder
                        .set_client_to_redis_tls_mode(mode)
                        .set_client_to_sentinel_tls_mode(mode);
                }
                let client = builder.build().map_err(config_error)?;
                let manager = SentinelPoolManager::new(client, options).map_err(config_error)?;
                build_pool(manager).map(Self::Sentinel)
            }
        }
    }

    /// Checkout one pooled connection and execute one command without replaying failures.
    fn execute<T>(
        &self,
        operation: &str,
        call: impl FnOnce(&mut dyn ConnectionLike) -> RedisResult<T>,
    ) -> Result<T> {
        match self {
            Self::Singleton(pool) => execute_pool(pool, operation, call),
            Self::Cluster(pool) => execute_pool(pool, operation, call),
            Self::Sentinel(pool) => {
                let mut connection = pool.get().map_err(|error| pool_error(operation, error))?;
                let result = call(&mut connection.connection);
                if let Err(error) = &result {
                    if is_sentinel_topology_error(error) {
                        connection.invalidate_topology();
                    }
                }
                result.map_err(|error| redis_error(operation, error))
            }
        }
    }

    /// Execute one best-effort command only when an idle pooled connection is available.
    fn try_execute<T>(
        &self,
        call: impl FnOnce(&mut dyn ConnectionLike) -> RedisResult<T>,
    ) -> Option<RedisResult<T>> {
        match self {
            Self::Singleton(pool) => try_execute_pool(pool, call),
            Self::Cluster(pool) => try_execute_pool(pool, call),
            Self::Sentinel(pool) => {
                let mut connection = pool.try_get()?;
                let result = call(&mut connection.connection);
                if let Err(error) = &result {
                    if is_sentinel_topology_error(error) {
                        connection.invalidate_topology();
                    }
                }
                Some(result)
            }
        }
    }
}

/// Execute one command through a pool backed by a redis-rs native manager.
fn execute_pool<M, T>(
    pool: &Pool<M>,
    operation: &str,
    call: impl FnOnce(&mut dyn ConnectionLike) -> RedisResult<T>,
) -> Result<T>
where
    M: ManageConnection,
    M::Connection: ConnectionLike,
{
    let mut connection = pool.get().map_err(|error| pool_error(operation, error))?;
    call(&mut *connection).map_err(|error| redis_error(operation, error))
}

/// Execute one best-effort command through an available native pooled connection.
fn try_execute_pool<M, T>(
    pool: &Pool<M>,
    call: impl FnOnce(&mut dyn ConnectionLike) -> RedisResult<T>,
) -> Option<RedisResult<T>>
where
    M: ManageConnection,
    M::Connection: ConnectionLike,
{
    let mut connection = pool.try_get()?;
    Some(call(&mut *connection))
}

/// Redis-backed QueueFS implementation.
pub(super) struct RedisQueueBackend {
    pool: RedisPool,
    key_prefix: String,
    instance_id: String,
    heartbeat_stop: Option<Sender<()>>,
    heartbeat_thread: Option<JoinHandle<()>>,
    startup_recovery_stop: Option<Sender<()>>,
    startup_recovery_thread: Option<JoinHandle<()>>,
}

impl RedisQueueBackend {
    /// Connect to Redis, register this instance, recover stale work, and start heartbeats.
    pub(super) fn open(options: RedisQueueOptions) -> Result<Self> {
        let pool = RedisPool::open(&options)?;
        let key_prefix = options.key_prefix;
        let instance_id = Uuid::new_v4().to_string();
        let heartbeat_key = heartbeat_key(&key_prefix, &instance_id);
        refresh_heartbeat(&pool, &heartbeat_key)?;
        let mut backend = Self {
            pool,
            key_prefix,
            instance_id,
            heartbeat_stop: None,
            heartbeat_thread: None,
            startup_recovery_stop: None,
            startup_recovery_thread: None,
        };
        backend.start_heartbeat(heartbeat_key);
        backend.start_startup_recovery();
        Ok(backend)
    }

    /// Execute a Redis operation using one checked-out pooled connection.
    fn with_connection<T>(
        &self,
        operation: &str,
        call: impl FnOnce(&mut dyn ConnectionLike) -> redis::RedisResult<T>,
    ) -> Result<T> {
        self.pool.execute(operation, call)
    }

    /// Start the heartbeat thread using a separate pool checkout for every renewal.
    /// `key` is this instance's heartbeat key in Redis.
    /// Returns no value; the spawned thread is stored on `self`.
    fn start_heartbeat(&mut self, key: String) {
        let (sender, receiver) = mpsc::channel();
        let pool = self.pool.clone();
        self.heartbeat_stop = Some(sender);
        self.heartbeat_thread = Some(std::thread::spawn(move || {
            loop {
                if let Err(error) = refresh_heartbeat(&pool, &key) {
                    tracing::warn!("queuefs redis heartbeat failed: {error}");
                }
                match receiver.recv_timeout(Duration::from_secs(HEARTBEAT_INTERVAL_SECS)) {
                    Ok(()) | Err(RecvTimeoutError::Disconnected) => break,
                    Err(RecvTimeoutError::Timeout) => {}
                }
            }
        }));
    }

    /// Start a bounded startup recovery thread.
    /// The thread runs at offsets 0, 30, and 60 seconds from startup unless stopped early.
    /// Returns no value; the spawned thread is stored on `self`.
    fn start_startup_recovery(&mut self) {
        let (sender, receiver) = mpsc::channel();
        let pool = self.pool.clone();
        let key_prefix = self.key_prefix.clone();
        self.startup_recovery_stop = Some(sender);
        self.startup_recovery_thread = Some(std::thread::spawn(move || {
            // ponytail: keep startup recovery bounded to the restart TTL window;
            // upgrade to steady-state scanning only if deployments need long-lived handoff.
            let started_at = Instant::now();
            for sweep_index in 0..STARTUP_RECOVERY_SWEEPS {
                let deadline = started_at + Self::startup_recovery_delay_before_sweep(sweep_index);
                loop {
                    let now = Instant::now();
                    if now >= deadline {
                        break;
                    }
                    match receiver.recv_timeout(deadline.saturating_duration_since(now)) {
                        Ok(()) | Err(RecvTimeoutError::Disconnected) => return,
                        Err(RecvTimeoutError::Timeout) => {}
                    }
                }
                Self::run_startup_recovery_sweep(&pool, &key_prefix);
            }
        }));
    }

    /// Return the delay before one bounded startup recovery sweep.
    /// `sweep_index` is zero-based and maps to the documented 0/30/60-second schedule.
    /// Returns the sweep offset from startup.
    fn startup_recovery_delay_before_sweep(sweep_index: usize) -> Duration {
        Duration::from_secs(HEARTBEAT_TTL_SECS * sweep_index as u64)
    }

    /// Run one startup recovery sweep.
    /// `pool` provides Redis connections and `key_prefix` selects the queue namespace.
    /// Returns no value; recovery results are emitted through logs.
    fn run_startup_recovery_sweep(pool: &RedisPool, key_prefix: &str) {
        match Self::recover_stale(pool, key_prefix) {
            Ok(recovered) => {
                if recovered > 0 {
                    tracing::info!("queuefs redis recovered {recovered} stale message(s)");
                }
            }
            Err(error) => tracing::warn!("queuefs redis startup recover_stale failed: {error}"),
        }
    }

    /// Recover processing messages whose owning instance heartbeat has expired.
    fn recover_stale(pool: &RedisPool, key_prefix: &str) -> Result<usize> {
        let queue_names_key = queue_names_key(key_prefix);
        let instance_key_prefix = instance_key_prefix(key_prefix);
        let queues = pool.execute("recover_stale list queues", |connection| {
            redis::cmd("SMEMBERS")
                .arg(&queue_names_key)
                .query::<Vec<String>>(connection)
        })?;
        let mut recovered = 0;
        for queue in queues {
            let keys = QueueKeys::new(key_prefix, &queue);
            recovered += pool.execute("recover_stale", |connection| {
                redis::Script::new(RECOVER_STALE_SCRIPT)
                    .key(&keys.processing)
                    .key(&keys.pending)
                    .arg(&instance_key_prefix)
                    .invoke::<usize>(connection)
            })?;
        }
        Ok(recovered)
    }

    /// Deserialize one stored Redis payload.
    fn decode_message(payload: &str) -> Result<Message> {
        serde_json::from_str::<StoredMessage>(payload)
            .map(StoredMessage::into_message)
            .map_err(|error| Error::Serialization(format!("invalid queue payload: {error}")))
    }

    /// Require that a queue is registered.
    fn require_queue(&self, queue_name: &str) -> Result<()> {
        if self.queue_exists_result(queue_name)? {
            Ok(())
        } else {
            Err(Error::NotFound(format!(
                "queue '{}' not found",
                queue_name
            )))
        }
    }

    /// Return whether a queue is registered while preserving Redis failures.
    fn queue_exists_result(&self, queue_name: &str) -> Result<bool> {
        let queue_names_key = queue_names_key(&self.key_prefix);
        self.with_connection("queue_exists", |connection| {
            redis::cmd("SISMEMBER")
                .arg(&queue_names_key)
                .arg(queue_name)
                .query::<bool>(connection)
        })
    }
}

impl Drop for RedisQueueBackend {
    /// Stop and join the heartbeat thread without waiting for its next interval.
    fn drop(&mut self) {
        if let Some(sender) = self.heartbeat_stop.take() {
            let _ = sender.send(());
        }
        if let Some(sender) = self.startup_recovery_stop.take() {
            let _ = sender.send(());
        }
        if let Some(thread) = self.heartbeat_thread.take() {
            let _ = thread.join();
        }
        if let Some(thread) = self.startup_recovery_thread.take() {
            let _ = thread.join();
        }
        let _ = self.pool.try_execute(|connection| {
            redis::cmd("DEL")
                .arg(heartbeat_key(&self.key_prefix, &self.instance_id))
                .query::<()>(connection)
        });
    }
}

impl QueueBackend for RedisQueueBackend {
    /// Create a queue and its metadata atomically.
    fn create_queue(&mut self, name: &str) -> Result<()> {
        let queue_names_key = queue_names_key(&self.key_prefix);
        let keys = QueueKeys::new(&self.key_prefix, name);
        let created = self.with_connection("create_queue", |connection| {
            redis::Script::new(CREATE_QUEUE_SCRIPT)
                .key(&queue_names_key)
                .key(&keys.meta)
                .arg(name)
                .arg(unix_secs(SystemTime::now()))
                .invoke::<i64>(connection)
        })?;
        if created == 0 {
            return Err(Error::AlreadyExists(format!(
                "queue '{}' already exists",
                name
            )));
        }
        Ok(())
    }

    /// Remove a queue and all of its Redis keys atomically.
    fn remove_queue(&mut self, name: &str) -> Result<()> {
        let queue_names_key = queue_names_key(&self.key_prefix);
        let queue_key_prefix = queue_key_prefix(&self.key_prefix);
        let removed = self.with_connection("remove_queue", |connection| {
            redis::Script::new(REMOVE_QUEUE_SCRIPT)
                .key(&queue_names_key)
                .arg(name)
                .arg(&queue_key_prefix)
                .invoke::<i64>(connection)
        })?;
        if removed == 0 {
            return Err(Error::NotFound(format!("queue '{}' not found", name)));
        }
        Ok(())
    }

    /// Return whether a queue is registered.
    fn queue_exists(&self, name: &str) -> bool {
        match self.queue_exists_result(name) {
            Ok(exists) => exists,
            Err(error) => {
                tracing::error!(
                    queue = name,
                    error = %error,
                    "queuefs redis queue_exists failed; returning false"
                );
                false
            }
        }
    }

    /// List registered queues matching the requested prefix.
    fn list_queues(&self, prefix: &str) -> Vec<String> {
        let queue_names_key = queue_names_key(&self.key_prefix);
        let mut queues = match self.with_connection("list_queues", |connection| {
            redis::cmd("SMEMBERS")
                .arg(&queue_names_key)
                .query::<Vec<String>>(connection)
        }) {
            Ok(queues) => queues,
            Err(error) => {
                tracing::error!(
                    prefix,
                    error = %error,
                    "queuefs redis list_queues failed; returning an empty list"
                );
                return Vec::new();
            }
        };
        queues.retain(|queue| queue.starts_with(prefix));
        queues.sort();
        queues
    }

    /// Store a payload and append its ID to the pending list atomically.
    fn enqueue(&mut self, queue_name: &str, msg: Message) -> Result<()> {
        let queue_names_key = queue_names_key(&self.key_prefix);
        let keys = QueueKeys::new(&self.key_prefix, queue_name);
        let payload = serde_json::to_string(&StoredMessage::from_message(&msg))?;
        let enqueued = self.with_connection("enqueue", |connection| {
            redis::Script::new(ENQUEUE_SCRIPT)
                .key(&queue_names_key)
                .key(keys.message(&msg.id))
                .key(&keys.pending)
                .key(&keys.meta)
                .arg(queue_name)
                .arg(&msg.id)
                .arg(&payload)
                .arg(unix_secs(SystemTime::now()))
                .invoke::<i64>(connection)
        })?;
        if enqueued == 0 {
            return Err(Error::NotFound(format!(
                "queue '{}' not found",
                queue_name
            )));
        }
        Ok(())
    }

    /// Move the oldest pending message to processing and return its payload atomically.
    fn dequeue(&mut self, queue_name: &str) -> Result<Option<Message>> {
        self.require_queue(queue_name)?;
        let keys = QueueKeys::new(&self.key_prefix, queue_name);
        let result = self.with_connection("dequeue", |connection| {
            redis::Script::new(DEQUEUE_SCRIPT)
                .key(&keys.pending)
                .key(&keys.processing)
                .arg(&keys.message_prefix)
                .arg(&self.instance_id)
                .arg(unix_secs(SystemTime::now()))
                .invoke::<Option<Vec<String>>>(connection)
        })?;
        result
            .map(|values| {
                values
                    .get(1)
                    .ok_or_else(|| Error::internal("redis dequeue returned no payload"))
                    .and_then(|payload| Self::decode_message(payload))
            })
            .transpose()
    }

    /// Read the oldest pending payload without changing queue state.
    fn peek(&self, queue_name: &str) -> Result<Option<Message>> {
        self.require_queue(queue_name)?;
        let keys = QueueKeys::new(&self.key_prefix, queue_name);
        self.with_connection("peek", |connection| {
            redis::Script::new(PEEK_SCRIPT)
                .key(&keys.pending)
                .arg(&keys.message_prefix)
                .invoke::<Option<String>>(connection)
        })?
        .map(|payload| Self::decode_message(&payload))
        .transpose()
    }

    /// Return the number of pending messages.
    fn size(&self, queue_name: &str) -> Result<usize> {
        self.require_queue(queue_name)?;
        let keys = QueueKeys::new(&self.key_prefix, queue_name);
        self.with_connection("size", |connection| {
            redis::cmd("LLEN")
                .arg(&keys.pending)
                .query::<usize>(connection)
        })
    }

    /// Return all pending and processing messages without changing their state.
    fn list_unacked(&self, queue_name: &str) -> Result<Vec<Message>> {
        self.require_queue(queue_name)?;
        let keys = QueueKeys::new(&self.key_prefix, queue_name);
        let payloads = self.with_connection("list_unacked", |connection| {
            redis::Script::new(LIST_UNACKED_SCRIPT)
                .key(&keys.pending)
                .key(&keys.processing)
                .arg(&keys.message_prefix)
                .invoke::<Vec<String>>(connection)
        })?;
        payloads
            .iter()
            .map(|payload| Self::decode_message(payload))
            .collect()
    }

    /// Delete all pending, processing, and payload keys for one queue atomically.
    fn clear(&mut self, queue_name: &str) -> Result<()> {
        self.require_queue(queue_name)?;
        let keys = QueueKeys::new(&self.key_prefix, queue_name);
        self.with_connection("clear", |connection| {
            redis::Script::new(CLEAR_SCRIPT)
                .key(&keys.pending)
                .key(&keys.processing)
                .arg(&keys.message_prefix)
                .invoke::<usize>(connection)
        })?;
        Ok(())
    }

    /// Return the latest timestamp among the current pending messages.
    fn get_last_enqueue_time(&self, queue_name: &str) -> Result<SystemTime> {
        self.require_queue(queue_name)?;
        let keys = QueueKeys::new(&self.key_prefix, queue_name);
        let pending_ids = self.with_connection("get_last_enqueue_time list pending", |connection| {
            redis::cmd("LRANGE")
                .arg(&keys.pending)
                .arg(0)
                .arg(-1)
                .query::<Vec<String>>(connection)
        })?;
        if pending_ids.is_empty() {
            return Ok(UNIX_EPOCH);
        }
        let payloads = self.with_connection("get_last_enqueue_time load payloads", |connection| {
            let mut command = redis::cmd("MGET");
            for id in &pending_ids {
                command.arg(keys.message(id));
            }
            command.query::<Vec<Option<String>>>(connection)
        })?;
        let payloads = payloads
            .into_iter()
            .enumerate()
            .map(|(index, payload)| {
                payload.ok_or_else(|| {
                    Error::internal(format!(
                        "redis get_last_enqueue_time missing payload for message {}",
                        pending_ids[index]
                    ))
                })
            })
            .collect::<Result<Vec<_>>>()?;
        // ponytail: scan current pending payloads on demand; if this becomes hot, add a dedicated pending timestamp index.
        last_enqueue_time_from_pending_payloads(&payloads)
    }

    /// Remove a processing record and its payload atomically.
    fn ack(&mut self, queue_name: &str, msg_id: &str) -> Result<bool> {
        self.require_queue(queue_name)?;
        let keys = QueueKeys::new(&self.key_prefix, queue_name);
        self.with_connection("ack", |connection| {
            redis::Script::new(ACK_SCRIPT)
                .key(&keys.processing)
                .key(keys.message(msg_id))
                .arg(msg_id)
                .invoke::<bool>(connection)
        })
    }
}

/// Return the queue registry key for one namespace.
fn queue_names_key(key_prefix: &str) -> String {
    format!("{}names", queue_key_prefix(key_prefix))
}

/// Return the queue key prefix for one namespace.
fn queue_key_prefix(key_prefix: &str) -> String {
    format!("{{{key_prefix}}}:ov:queue:")
}

/// Return the instance key prefix for one namespace.
fn instance_key_prefix(key_prefix: &str) -> String {
    format!("{}instance:", queue_key_prefix(key_prefix))
}

/// Return the heartbeat key for one instance.
fn heartbeat_key(key_prefix: &str, instance_id: &str) -> String {
    format!("{}{instance_id}:alive", instance_key_prefix(key_prefix))
}

/// Return Unix seconds for Redis scores and metadata.
fn unix_secs(time: SystemTime) -> u64 {
    time.duration_since(UNIX_EPOCH).unwrap_or_default().as_secs()
}

/// Return the latest timestamp among the current pending payloads.
fn last_enqueue_time_from_pending_payloads(payloads: &[String]) -> Result<SystemTime> {
    payloads.iter().try_fold(UNIX_EPOCH, |latest, payload| {
        let timestamp = serde_json::from_str::<StoredMessage>(payload)
            .map(StoredMessage::into_message)
            .map(|message| message.timestamp)
            .map_err(|error| Error::Serialization(format!("invalid queue payload: {error}")))?;
        Ok(if timestamp > latest { timestamp } else { latest })
    })
}

/// Build one data-node connection descriptor with authentication, database, and TLS.
fn endpoint_connection_info(
    endpoint: &str,
    options: &RedisQueueOptions,
) -> redis::RedisResult<ConnectionInfo> {
    let info = endpoint.into_connection_info()?;
    let mut redis_settings = RedisConnectionInfo::default()
        .set_db(options.db)
        .set_protocol(info.redis_settings().protocol());
    if let Some(username) = &options.username {
        redis_settings = redis_settings.set_username(username);
    }
    if let Some(password) = &options.password {
        redis_settings = redis_settings.set_password(password);
    }
    let address = if options.tls_enabled {
        force_tls(info.addr().clone(), options.tls_insecure_skip_verify)
    } else {
        info.addr().clone()
    };
    Ok(info
        .set_redis_settings(redis_settings)
        .set_addr(address))
}

/// Build one Sentinel node address with the configured transport security.
fn sentinel_addr(
    endpoint: &str,
    options: &RedisQueueOptions,
) -> redis::RedisResult<ConnectionAddr> {
    let info = endpoint.into_connection_info()?;
    Ok(if options.tls_enabled {
        force_tls(info.addr().clone(), options.tls_insecure_skip_verify)
    } else {
        info.addr().clone()
    })
}

/// Convert a TCP address into its TLS form while preserving non-TCP transports.
fn force_tls(address: ConnectionAddr, insecure: bool) -> ConnectionAddr {
    match address {
        ConnectionAddr::Tcp(host, port) | ConnectionAddr::TcpTls { host, port, .. } => {
            ConnectionAddr::TcpTls {
                host,
                port,
                insecure,
                tls_params: None,
            }
        }
        address => address,
    }
}

/// Return the redis-rs TLS mode represented by QueueFS settings.
fn tls_mode(options: &RedisQueueOptions) -> TlsMode {
    if options.tls_insecure_skip_verify {
        TlsMode::Insecure
    } else {
        TlsMode::Secure
    }
}

/// Apply QueueFS command timeouts to one direct Redis connection.
fn configure_connection(
    connection: &Connection,
    command_timeout: Duration,
) -> redis::RedisResult<()> {
    let timeout = Some(command_timeout);
    connection.set_read_timeout(timeout)?;
    connection.set_write_timeout(timeout)?;
    Ok(())
}

/// Validate that one direct Redis connection is still usable.
fn validate_connection(connection: &mut Connection) -> RedisResult<()> {
    if connection.check_connection() {
        Ok(())
    } else {
        Err(RedisError::from((
            redis::ErrorKind::Io,
            "redis connection closed",
        )))
    }
}

/// Return whether one Sentinel response means the connected master is no longer writable.
fn is_sentinel_topology_error(error: &RedisError) -> bool {
    matches!(
        error.kind(),
        redis::ErrorKind::Server(ServerErrorKind::ReadOnly | ServerErrorKind::MasterDown)
    )
}

/// Build one initialized r2d2 pool with the QueueFS capacity and checkout timeout.
fn build_pool<M>(manager: M) -> Result<Pool<M>>
where
    M: ManageConnection,
{
    Pool::builder()
        .max_size(REDIS_POOL_MAX_SIZE)
        .connection_timeout(REDIS_POOL_CHECKOUT_TIMEOUT)
        .build(manager)
        .map_err(|error| pool_error("connect", error))
}

/// Map r2d2 initialization and checkout failures into a QueueFS network error.
fn pool_error(operation: &str, error: r2d2::Error) -> Error {
    Error::Network(format!("redis {operation} pool error: {error}"))
}

/// Refresh one instance heartbeat through one pooled logical connection.
fn refresh_heartbeat(pool: &RedisPool, key: &str) -> Result<()> {
    pool.execute("heartbeat", |connection| {
        redis::cmd("SET")
            .arg(key)
            .arg("1")
            .arg("EX")
            .arg(HEARTBEAT_TTL_SECS)
            .query::<()>(connection)
    })
}

/// Map redis-rs failures into the QueueFS error categories.
fn redis_error(operation: &str, error: redis::RedisError) -> Error {
    if error.is_timeout() {
        Error::Timeout(format!("redis {operation} error: {error}"))
    } else if error.is_io_error() || error.is_connection_dropped() {
        Error::Network(format!("redis {operation} error: {error}"))
    } else {
        Error::internal(format!("redis {operation} error: {error}"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{Duration, Instant, UNIX_EPOCH};

    /// Exercise one complete QueueFS message lifecycle.
    fn assert_queue_flow(mut backend: RedisQueueBackend) {
        let queue = format!("queuefs-test-{}", Uuid::new_v4());
        backend.create_queue(&queue).unwrap();
        let message = Message::new(b"topology".to_vec());
        let message_id = message.id.clone();
        backend.enqueue(&queue, message).unwrap();
        assert_eq!(backend.peek(&queue).unwrap().unwrap().id, message_id);
        assert_eq!(backend.dequeue(&queue).unwrap().unwrap().id, message_id);
        assert!(backend.ack(&queue, &message_id).unwrap());
        backend.remove_queue(&queue).unwrap();
    }

    #[test]
    /// Build queue keys under the configured namespace.
    fn queue_keys_use_the_documented_layout() {
        let keys = QueueKeys::new("tenant-a", "Semantic");

        assert_eq!(queue_names_key("tenant-a"), "{tenant-a}:ov:queue:names");
        assert_eq!(keys.meta, "{tenant-a}:ov:queue:Semantic:meta");
        assert_eq!(keys.pending, "{tenant-a}:ov:queue:Semantic:pending");
        assert_eq!(
            keys.processing,
            "{tenant-a}:ov:queue:Semantic:processing"
        );
        assert_eq!(
            keys.message("message-id"),
            "{tenant-a}:ov:queue:Semantic:msg:message-id"
        );
        assert_eq!(
            heartbeat_key("tenant-a", "instance-id"),
            "{tenant-a}:ov:queue:instance:instance-id:alive"
        );
    }

    #[test]
    /// Derive the last enqueue time from the latest pending message timestamp.
    fn last_enqueue_time_comes_from_pending_messages() {
        let mut first = Message::new(b"first".to_vec());
        first.timestamp = UNIX_EPOCH + Duration::from_secs(10);
        let mut second = Message::new(b"second".to_vec());
        second.timestamp = UNIX_EPOCH + Duration::from_secs(20);
        let payloads = vec![
            serde_json::to_string(&StoredMessage::from_message(&first)).unwrap(),
            serde_json::to_string(&StoredMessage::from_message(&second)).unwrap(),
        ];

        assert_eq!(
            last_enqueue_time_from_pending_payloads(&payloads).unwrap(),
            UNIX_EPOCH + Duration::from_secs(20)
        );
    }

    #[test]
    /// Discard Sentinel connections only for errors that indicate a stale master.
    fn sentinel_topology_errors_invalidate_the_connection() {
        assert!(is_sentinel_topology_error(&redis::RedisError::from((
            redis::ErrorKind::Server(ServerErrorKind::ReadOnly),
            "old master is read-only",
        ))));
        assert!(is_sentinel_topology_error(&redis::RedisError::from((
            redis::ErrorKind::Server(ServerErrorKind::MasterDown),
            "master is down",
        ))));
        assert!(!is_sentinel_topology_error(&redis::RedisError::from((
            redis::ErrorKind::Server(ServerErrorKind::ResponseError),
            "ordinary command error",
        ))));
    }

    #[test]
    /// Invalidate every pooled Sentinel connection created before a topology change.
    fn sentinel_topology_generation_invalidates_all_old_connections() {
        let generation = ConnectionGeneration::default();
        let first = generation.snapshot();
        let second = generation.snapshot();
        assert!(generation.is_current(first));
        assert!(generation.is_current(second));

        generation.invalidate();

        assert!(!generation.is_current(first));
        assert!(!generation.is_current(second));
        assert!(generation.is_current(generation.snapshot()));
    }

    #[test]
    #[ignore = "requires QUEUEFS_REDIS_TEST_URL"]
    /// Replace a dead Singleton connection before use without replaying command failures.
    fn redis_backend_reconnects_singleton_after_disconnect() {
        let endpoint =
            std::env::var("QUEUEFS_REDIS_TEST_URL").expect("QUEUEFS_REDIS_TEST_URL is required");
        let mut backend = RedisQueueBackend::open(RedisQueueOptions {
            endpoints: vec![endpoint.clone()],
            key_prefix: format!("queuefs-test-{}", Uuid::new_v4()),
            ..RedisQueueOptions::default()
        })
        .unwrap();
        backend.create_queue("reconnect").unwrap();
        let client_id = backend
            .with_connection("test client id", |connection| {
                redis::cmd("CLIENT").arg("ID").query::<i64>(connection)
            })
            .unwrap();
        let mut control = redis::Client::open(endpoint)
            .unwrap()
            .get_connection()
            .unwrap();
        redis::cmd("CLIENT")
            .arg("KILL")
            .arg("ID")
            .arg(client_id)
            .query::<usize>(&mut control)
            .unwrap();

        assert_eq!(backend.size("reconnect").unwrap(), 0);

        let mut command_calls = 0;
        let result = backend.with_connection("test command failure", |_connection| {
            command_calls += 1;
            RedisResult::<()>::Err(redis::RedisError::from((
                redis::ErrorKind::Server(ServerErrorKind::ResponseError),
                "test command failure",
            )))
        });
        assert!(result.is_err());
        assert_eq!(command_calls, 1);

        backend.remove_queue("reconnect").unwrap();
    }

    #[test]
    #[ignore = "requires QUEUEFS_REDIS_CLUSTER_TEST_URLS"]
    /// Execute QueueFS Lua scripts through a real Redis Cluster connection.
    fn redis_backend_cluster_executes_queue_flow() {
        let endpoints = std::env::var("QUEUEFS_REDIS_CLUSTER_TEST_URLS")
            .expect("QUEUEFS_REDIS_CLUSTER_TEST_URLS is required");
        let options = RedisQueueOptions {
            mode: RedisMode::Cluster,
            endpoints: endpoints.split(',').map(str::to_string).collect(),
            key_prefix: format!("queuefs-test-{}", Uuid::new_v4()),
            ..RedisQueueOptions::default()
        };

        assert_queue_flow(RedisQueueBackend::open(options).unwrap());
    }

    #[test]
    #[ignore = "requires QUEUEFS_REDIS_SENTINEL_TEST_URLS"]
    /// Execute QueueFS operations through Sentinel master discovery.
    fn redis_backend_sentinel_executes_queue_flow() {
        let endpoints = std::env::var("QUEUEFS_REDIS_SENTINEL_TEST_URLS")
            .expect("QUEUEFS_REDIS_SENTINEL_TEST_URLS is required");
        let options = RedisQueueOptions {
            mode: RedisMode::Sentinel,
            endpoints: endpoints.split(',').map(str::to_string).collect(),
            master_name: Some(
                std::env::var("QUEUEFS_REDIS_SENTINEL_MASTER")
                    .unwrap_or_else(|_| "mymaster".to_string()),
            ),
            key_prefix: format!("queuefs-test-{}", Uuid::new_v4()),
            ..RedisQueueOptions::default()
        };

        assert_queue_flow(RedisQueueBackend::open(options).unwrap());
    }

    #[test]
    #[ignore = "requires QUEUEFS_REDIS_SENTINEL_TEST_URLS and a replica"]
    /// Rediscover the Sentinel master after the connected master is demoted.
    fn redis_backend_sentinel_reconnects_after_failover() {
        let endpoints = std::env::var("QUEUEFS_REDIS_SENTINEL_TEST_URLS")
            .expect("QUEUEFS_REDIS_SENTINEL_TEST_URLS is required");
        let master_name = std::env::var("QUEUEFS_REDIS_SENTINEL_MASTER")
            .unwrap_or_else(|_| "mymaster".to_string());
        let sentinel_endpoint = endpoints.split(',').next().unwrap();
        let mut sentinel = redis::Client::open(sentinel_endpoint)
            .unwrap()
            .get_connection()
            .unwrap();
        let original_master = redis::cmd("SENTINEL")
            .arg("GET-MASTER-ADDR-BY-NAME")
            .arg(&master_name)
            .query::<Vec<String>>(&mut sentinel)
            .unwrap();
        let original_master_client = redis::Client::open(format!(
            "redis://{}:{}",
            original_master[0], original_master[1]
        ))
        .unwrap();
        let mut backend = RedisQueueBackend::open(RedisQueueOptions {
            mode: RedisMode::Sentinel,
            endpoints: endpoints.split(',').map(str::to_string).collect(),
            master_name: Some(master_name.clone()),
            key_prefix: format!("queuefs-test-{}", Uuid::new_v4()),
            ..RedisQueueOptions::default()
        })
        .unwrap();
        backend.create_queue("failover").unwrap();

        redis::cmd("SENTINEL")
            .arg("FAILOVER")
            .arg(&master_name)
            .query::<()>(&mut sentinel)
            .unwrap();
        let deadline = Instant::now() + Duration::from_secs(15);
        loop {
            let current_master = redis::cmd("SENTINEL")
                .arg("GET-MASTER-ADDR-BY-NAME")
                .arg(&master_name)
                .query::<Vec<String>>(&mut sentinel)
                .unwrap();
            let original_is_replica = original_master_client
                .get_connection()
                .and_then(|mut connection| {
                    redis::cmd("INFO")
                        .arg("replication")
                        .query::<String>(&mut connection)
                })
                .is_ok_and(|replication| replication.contains("role:slave"));
            if current_master != original_master && original_is_replica {
                break;
            }
            assert!(Instant::now() < deadline, "Sentinel failover timed out");
            std::thread::sleep(Duration::from_millis(100));
        }

        if backend
            .enqueue("failover", Message::new(b"first".to_vec()))
            .is_err()
        {
            backend
                .enqueue("failover", Message::new(b"second".to_vec()))
                .unwrap();
        }
        assert!(backend.size("failover").unwrap() > 0);
        backend.remove_queue("failover").unwrap();
    }

    #[test]
    #[ignore = "requires QUEUEFS_REDIS_TEST_URL"]
    /// Reject an unregistered queue inside the enqueue script before writing any keys.
    fn enqueue_script_rejects_unregistered_queue_atomically() {
        let endpoint =
            std::env::var("QUEUEFS_REDIS_TEST_URL").expect("QUEUEFS_REDIS_TEST_URL is required");
        let options = RedisQueueOptions {
            endpoints: vec![endpoint],
            key_prefix: format!("queuefs-test-{}", Uuid::new_v4()),
            ..RedisQueueOptions::default()
        };
        let mut backend = RedisQueueBackend::open(options).unwrap();
        let queue = "removed";
        let queue_names_key = queue_names_key(&backend.key_prefix);
        let keys = QueueKeys::new(&backend.key_prefix, queue);
        let message = Message::new(b"orphan".to_vec());
        let message_key = keys.message(&message.id);

        assert!(backend.enqueue(queue, message).is_err());
        let existing_keys = backend
            .with_connection("test_enqueue_missing_queue keys", |connection| {
                redis::cmd("EXISTS")
                    .arg(&queue_names_key)
                    .arg(&message_key)
                    .arg(&keys.pending)
                    .arg(&keys.meta)
                    .query::<usize>(connection)
            })
            .unwrap();
        assert_eq!(existing_keys, 0);
    }

    #[test]
    #[ignore = "requires QUEUEFS_REDIS_TEST_URL"]
    /// Remove a queue tree without deleting a same-prefix sibling queue.
    fn redis_backend_removes_nested_queues_recursively() {
        let endpoint =
            std::env::var("QUEUEFS_REDIS_TEST_URL").expect("QUEUEFS_REDIS_TEST_URL is required");
        let key_prefix = format!("queuefs-test-{}", Uuid::new_v4());
        let options = RedisQueueOptions {
            endpoints: vec![endpoint],
            key_prefix,
            ..RedisQueueOptions::default()
        };
        let mut backend = RedisQueueBackend::open(options).unwrap();
        backend.create_queue("worker-0").unwrap();
        backend.create_queue("worker-0/Embedding").unwrap();
        backend.create_queue("worker-01").unwrap();
        backend
            .enqueue("worker-0/Embedding", Message::new(b"nested".to_vec()))
            .unwrap();
        backend
            .enqueue("worker-01", Message::new(b"sibling".to_vec()))
            .unwrap();

        backend.remove_queue("worker-0").unwrap();

        assert!(!backend.queue_exists("worker-0"));
        assert!(!backend.queue_exists("worker-0/Embedding"));
        assert!(backend.queue_exists("worker-01"));
        assert_eq!(backend.size("worker-01").unwrap(), 1);
        backend.remove_queue("worker-01").unwrap();
    }

    #[test]
    #[ignore = "requires QUEUEFS_REDIS_TEST_URL"]
    /// Isolate queue state between independent Redis key prefixes.
    fn redis_backend_isolates_key_prefixes() {
        let endpoint =
            std::env::var("QUEUEFS_REDIS_TEST_URL").expect("QUEUEFS_REDIS_TEST_URL is required");
        let suffix = Uuid::new_v4();
        let mut first = RedisQueueBackend::open(RedisQueueOptions {
            endpoints: vec![endpoint.clone()],
            key_prefix: format!("queuefs-test-a-{suffix}"),
            ..RedisQueueOptions::default()
        })
        .unwrap();
        let mut second = RedisQueueBackend::open(RedisQueueOptions {
            endpoints: vec![endpoint],
            key_prefix: format!("queuefs-test-b-{suffix}"),
            ..RedisQueueOptions::default()
        })
        .unwrap();

        first.create_queue("Semantic").unwrap();
        assert!(!second.queue_exists("Semantic"));
        second.create_queue("Semantic").unwrap();
        first
            .enqueue("Semantic", Message::new(b"first".to_vec()))
            .unwrap();

        assert_eq!(first.size("Semantic").unwrap(), 1);
        assert_eq!(second.size("Semantic").unwrap(), 0);
        first.remove_queue("Semantic").unwrap();
        second.remove_queue("Semantic").unwrap();
    }

    #[test]
    #[ignore = "requires QUEUEFS_REDIS_TEST_URL"]
    /// Recover processing messages owned by instances without a heartbeat.
    fn redis_backend_recovers_messages_owned_by_dead_instances() {
        let endpoint =
            std::env::var("QUEUEFS_REDIS_TEST_URL").expect("QUEUEFS_REDIS_TEST_URL is required");
        let options = RedisQueueOptions {
            endpoints: vec![endpoint],
            ..RedisQueueOptions::default()
        };
        let queue = format!("queuefs-test-{}", Uuid::new_v4());
        let mut backend = RedisQueueBackend::open(options.clone()).unwrap();
        backend.create_queue(&queue).unwrap();
        let message = Message::new(b"payload".to_vec());
        let message_id = message.id.clone();
        backend.enqueue(&queue, message).unwrap();

        let keys = QueueKeys::new(&options.key_prefix, &queue);
        backend
            .with_connection("test_dead_owner", |connection| {
                redis::Script::new(
                    r#"
local id = redis.call('LPOP', KEYS[1])
redis.call('ZADD', KEYS[2], ARGV[1], id .. '|dead-instance')
return id
"#,
                )
                .key(&keys.pending)
                .key(&keys.processing)
                .arg(unix_secs(SystemTime::now()))
                .invoke::<String>(connection)
            })
            .unwrap();
        drop(backend);

        let mut recovered = RedisQueueBackend::open(options).unwrap();
        let dequeued = recovered.dequeue(&queue).unwrap().unwrap();
        assert_eq!(dequeued.id, message_id);
        assert_eq!(dequeued.data, b"payload");
        assert!(recovered.ack(&queue, &message_id).unwrap());
        recovered.remove_queue(&queue).unwrap();
    }

    #[test]
    #[ignore = "requires QUEUEFS_REDIS_TEST_URL"]
    /// Recover this instance's processing messages after a graceful restart.
    fn redis_backend_graceful_restart_recovers_processing_messages() {
        let endpoint =
            std::env::var("QUEUEFS_REDIS_TEST_URL").expect("QUEUEFS_REDIS_TEST_URL is required");
        let options = RedisQueueOptions {
            endpoints: vec![endpoint],
            ..RedisQueueOptions::default()
        };
        let queue = format!("queuefs-test-{}", Uuid::new_v4());
        let mut backend = RedisQueueBackend::open(options.clone()).unwrap();
        backend.create_queue(&queue).unwrap();
        let message = Message::new(b"payload".to_vec());
        let message_id = message.id.clone();
        backend.enqueue(&queue, message).unwrap();
        assert_eq!(backend.dequeue(&queue).unwrap().unwrap().id, message_id);
        drop(backend);

        let mut restarted = RedisQueueBackend::open(options).unwrap();
        assert_eq!(
            restarted.dequeue(&queue).unwrap().unwrap().id,
            message_id
        );
        assert!(restarted.ack(&queue, &message_id).unwrap());
        restarted.remove_queue(&queue).unwrap();
    }
}
