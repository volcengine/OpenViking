use super::config::{parse_endpoint, RedisDeploymentMode};
use super::RedisProviderConfig;
use crate::cache_runtime::{
    CacheError, CacheResult, Expiration, ListDirection, ListInsertPosition, ListInsertRequest,
    ListMoveRequest, ScriptValue, SetCondition, SetOptions, SetResult,
};
use bytes::Bytes;
use fred::error::{Error as FredError, ErrorKind};
use fred::prelude::*;
use fred::types::{
    config::{ClusterDiscoveryPolicy, Server, TlsConfig, TlsConnector, TlsHostMapping},
    lists::{LMoveDirection, ListLocation},
    ConnectHandle, Expiration as FredExpiration, Map, SetOptions as FredSetOptions, Value,
};
use futures::Future;
use std::collections::HashMap;
use std::env;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::{Mutex, RwLock, Semaphore};

pub(super) struct RedisClient {
    client: Client,
    connection_task: Mutex<Option<ConnectHandle>>,
    scripts: RwLock<HashMap<String, String>>,
    concurrency: Arc<Semaphore>,
    concurrency_limit: u32,
    command_timeout: Duration,
    deployment_mode: RedisDeploymentMode,
    closed: AtomicBool,
}

impl RedisClient {
    pub(super) async fn connect(config: &RedisProviderConfig) -> CacheResult<Self> {
        config.validate()?;
        let deployment_mode = config.deployment_mode()?;
        let fred_config = fred_config(config, deployment_mode)?;
        let mut builder = Builder::from_config(fred_config);
        builder
            .with_connection_config(|connection| {
                connection.connection_timeout = Duration::from_millis(config.connect_timeout_ms);
                connection.internal_command_timeout =
                    Duration::from_millis(config.command_timeout_ms);
                connection.max_command_attempts = 1;
                connection.max_command_buffer_len = config.pool_size;
            })
            .with_performance_config(|performance| {
                performance.default_command_timeout =
                    Duration::from_millis(config.command_timeout_ms);
            })
            .set_policy(ReconnectPolicy::new_exponential(0, 100, 30_000, 2));
        let client = builder
            .build()
            .map_err(|error| map_fred_error("build", error))?;
        let connect_timeout = Duration::from_millis(config.connect_timeout_ms);
        let connection_task = client.connect();
        match tokio::time::timeout(connect_timeout, client.wait_for_connect()).await {
            Ok(Ok(())) => {}
            Ok(Err(error)) => {
                let _ = terminate_connection_task(&client, connection_task, connect_timeout).await;
                return Err(map_fred_error("connect", error));
            }
            Err(_) => {
                let _ = terminate_connection_task(&client, connection_task, connect_timeout).await;
                return Err(CacheError::Timeout(format!(
                    "Redis connect exceeded {} ms",
                    config.connect_timeout_ms
                )));
            }
        }
        let concurrency_limit = u32::try_from(config.pool_size).map_err(|_| {
            CacheError::InvalidArgument("Redis pool_size exceeds the supported limit".into())
        })?;
        let result = Self {
            client,
            connection_task: Mutex::new(Some(connection_task)),
            scripts: RwLock::new(HashMap::new()),
            concurrency: Arc::new(Semaphore::new(config.pool_size)),
            concurrency_limit,
            command_timeout: Duration::from_millis(config.command_timeout_ms),
            deployment_mode,
            closed: AtomicBool::new(false),
        };
        if let Err(error) = result.health_check().await {
            let _ = result.close().await;
            return Err(error);
        }
        Ok(result)
    }

    async fn execute<T, F>(&self, operation: &'static str, future: F) -> CacheResult<T>
    where
        T: Send,
        F: Future<Output = Result<T, FredError>> + Send,
    {
        if self.closed.load(Ordering::Acquire) {
            return Err(CacheError::Closed);
        }
        let work = async {
            let _permit = Arc::clone(&self.concurrency)
                .acquire_owned()
                .await
                .map_err(|_| CacheError::Closed)?;
            future
                .await
                .map_err(|error| map_fred_error(operation, error))
        };
        tokio::time::timeout(self.command_timeout, work)
            .await
            .map_err(|_| {
                CacheError::Timeout(format!(
                    "Redis {operation} exceeded {} ms",
                    self.command_timeout.as_millis()
                ))
            })?
    }

    pub(super) async fn health_check(&self) -> CacheResult<()> {
        let _: String = self
            .execute("PING", self.client.ping::<String>(None))
            .await?;
        Ok(())
    }

    pub(super) async fn get(&self, key: &str) -> CacheResult<Option<Bytes>> {
        self.execute("GET", self.client.get(key)).await
    }

    pub(super) async fn set(
        &self,
        key: &str,
        value: Bytes,
        options: SetOptions,
        default_ttl: Option<Duration>,
    ) -> CacheResult<SetResult> {
        if options.keep_ttl && options.expiration.is_some() {
            return Err(CacheError::InvalidArgument(
                "Redis SET cannot combine expiration with keep_ttl".into(),
            ));
        }
        let expiration = fred_expiration(options, default_ttl)?;
        let condition = match options.condition {
            SetCondition::None => None,
            SetCondition::Nx => Some(FredSetOptions::NX),
            SetCondition::Xx => Some(FredSetOptions::XX),
        };
        let response: Option<String> = self
            .execute(
                "SET",
                self.client.set(key, value, expiration, condition, false),
            )
            .await?;
        Ok(if response.is_some() {
            SetResult::Applied
        } else {
            SetResult::ConditionNotMet
        })
    }

    pub(super) async fn del(&self, keys: &[String]) -> CacheResult<u64> {
        if keys.is_empty() {
            return Ok(0);
        }
        self.execute("DEL", self.client.del(keys.to_vec())).await
    }

    pub(super) async fn mget(&self, keys: &[String]) -> CacheResult<Vec<Option<Bytes>>> {
        if keys.is_empty() {
            return Ok(Vec::new());
        }
        if self.deployment_mode == RedisDeploymentMode::Cluster && !keys_share_slot(keys) {
            let mut values = Vec::with_capacity(keys.len());
            for key in keys {
                values.push(self.get(key).await?);
            }
            return Ok(values);
        }
        self.execute("MGET", self.client.mget(keys.to_vec())).await
    }

    pub(super) async fn mset(
        &self,
        entries: Vec<(String, Bytes)>,
        default_ttl: Option<Duration>,
    ) -> CacheResult<()> {
        if entries.is_empty() {
            return Ok(());
        }
        let keys = entries
            .iter()
            .map(|(key, _)| key.clone())
            .collect::<Vec<_>>();
        self.require_same_slot(&keys, "MSET")?;
        if let Some(default_ttl) = default_ttl {
            let expiration = duration_to_expiration(default_ttl)?;
            let pipeline = self.client.pipeline();
            for (key, value) in entries {
                let _: () = pipeline
                    .set(key, value, Some(expiration.clone()), None, false)
                    .await
                    .map_err(|error| map_fred_error("MSET pipeline", error))?;
            }
            let _: () = self.execute("MSET pipeline", pipeline.all()).await?;
        } else {
            let values = entries.into_iter().collect::<Map>();
            self.execute("MSET", self.client.mset(values)).await?;
        }
        Ok(())
    }

    pub(super) async fn incr_by(&self, key: &str, delta: i64) -> CacheResult<i64> {
        self.execute("INCRBY", self.client.incr_by(key, delta))
            .await
    }

    pub(super) async fn sismember(&self, key: &str, member: &[u8]) -> CacheResult<bool> {
        self.execute("SISMEMBER", self.client.sismember(key, member))
            .await
    }

    pub(super) async fn smembers(&self, key: &str) -> CacheResult<Vec<Bytes>> {
        self.execute("SMEMBERS", self.client.smembers(key)).await
    }

    pub(super) async fn scard(&self, key: &str) -> CacheResult<u64> {
        self.execute("SCARD", self.client.scard(key)).await
    }

    pub(super) async fn lpush(&self, key: &str, values: Vec<Bytes>) -> CacheResult<u64> {
        if values.is_empty() {
            return Err(CacheError::InvalidArgument(
                "Redis LPUSH requires at least one value".into(),
            ));
        }
        self.execute("LPUSH", self.client.lpush(key, values)).await
    }

    pub(super) async fn rpush(&self, key: &str, values: Vec<Bytes>) -> CacheResult<u64> {
        if values.is_empty() {
            return Err(CacheError::InvalidArgument(
                "Redis RPUSH requires at least one value".into(),
            ));
        }
        self.execute("RPUSH", self.client.rpush(key, values)).await
    }

    pub(super) async fn lpop(&self, key: &str, count: Option<u64>) -> CacheResult<Vec<Bytes>> {
        if let Some(count) = count {
            let count = usize::try_from(count)
                .map_err(|_| CacheError::InvalidArgument("Redis LPOP count is too large".into()))?;
            self.execute("LPOP", self.client.lpop(key, Some(count)))
                .await
        } else {
            let value: Option<Bytes> = self.execute("LPOP", self.client.lpop(key, None)).await?;
            Ok(value.into_iter().collect())
        }
    }

    pub(super) async fn rpop(&self, key: &str, count: Option<u64>) -> CacheResult<Vec<Bytes>> {
        if let Some(count) = count {
            let count = usize::try_from(count)
                .map_err(|_| CacheError::InvalidArgument("Redis RPOP count is too large".into()))?;
            self.execute("RPOP", self.client.rpop(key, Some(count)))
                .await
        } else {
            let value: Option<Bytes> = self.execute("RPOP", self.client.rpop(key, None)).await?;
            Ok(value.into_iter().collect())
        }
    }

    pub(super) async fn llen(&self, key: &str) -> CacheResult<u64> {
        self.execute("LLEN", self.client.llen(key)).await
    }

    pub(super) async fn lrange(&self, key: &str, start: i64, stop: i64) -> CacheResult<Vec<Bytes>> {
        self.execute("LRANGE", self.client.lrange(key, start, stop))
            .await
    }

    pub(super) async fn lindex(&self, key: &str, index: i64) -> CacheResult<Option<Bytes>> {
        self.execute("LINDEX", self.client.lindex(key, index)).await
    }

    pub(super) async fn lset(&self, key: &str, index: i64, value: Bytes) -> CacheResult<()> {
        self.execute("LSET", self.client.lset(key, index, value))
            .await
    }

    pub(super) async fn ltrim(&self, key: &str, start: i64, stop: i64) -> CacheResult<()> {
        self.execute("LTRIM", self.client.ltrim(key, start, stop))
            .await
    }

    pub(super) async fn lrem(&self, key: &str, count: i64, value: Bytes) -> CacheResult<u64> {
        self.execute("LREM", self.client.lrem(key, count, value))
            .await
    }

    pub(super) async fn linsert(&self, request: ListInsertRequest) -> CacheResult<i64> {
        let position = match request.position {
            ListInsertPosition::Before => ListLocation::Before,
            ListInsertPosition::After => ListLocation::After,
        };
        self.execute(
            "LINSERT",
            self.client
                .linsert(request.key, position, request.pivot, request.value),
        )
        .await
    }

    pub(super) async fn lmove(&self, request: ListMoveRequest) -> CacheResult<Option<Bytes>> {
        self.require_same_slot(
            &[request.source.clone(), request.destination.clone()],
            "LMOVE",
        )?;
        self.execute(
            "LMOVE",
            self.client.lmove(
                request.source,
                request.destination,
                list_direction(request.source_direction),
                list_direction(request.destination_direction),
            ),
        )
        .await
    }

    pub(super) async fn execute_script(
        &self,
        script_id: &str,
        lua: &'static str,
        keys: Vec<String>,
        args: Vec<Bytes>,
    ) -> CacheResult<ScriptValue> {
        self.require_same_slot(&keys, "EVALSHA")?;
        let cached_sha = {
            let scripts = self.scripts.read().await;
            scripts.get(script_id).cloned()
        };
        let sha = match cached_sha {
            Some(sha) => sha,
            None => self.load_script(script_id, lua).await?,
        };
        match self.evalsha(&sha, keys.clone(), args.clone()).await {
            Err(CacheError::NoScript(_)) => {
                let sha = self.load_script(script_id, lua).await?;
                self.evalsha(&sha, keys, args).await
            }
            result => result,
        }
    }

    async fn load_script(&self, script_id: &str, lua: &'static str) -> CacheResult<String> {
        let sha: String = if self.deployment_mode == RedisDeploymentMode::Cluster {
            self.execute("SCRIPT LOAD", self.client.script_load_cluster(lua))
                .await?
        } else {
            self.execute("SCRIPT LOAD", self.client.script_load(lua))
                .await?
        };
        self.scripts
            .write()
            .await
            .insert(script_id.to_string(), sha.clone());
        Ok(sha)
    }

    async fn evalsha(
        &self,
        sha: &str,
        keys: Vec<String>,
        args: Vec<Bytes>,
    ) -> CacheResult<ScriptValue> {
        let value: Value = self
            .execute("EVALSHA", self.client.evalsha(sha, keys, args))
            .await?;
        fred_value_to_script_value(value)
    }

    #[cfg(test)]
    pub(super) async fn script_flush(&self) -> CacheResult<()> {
        if self.deployment_mode == RedisDeploymentMode::Cluster {
            self.execute("SCRIPT FLUSH", self.client.script_flush_cluster(false))
                .await
        } else {
            self.execute("SCRIPT FLUSH", self.client.script_flush(false))
                .await
        }
    }

    pub(super) async fn close(&self) -> CacheResult<()> {
        if self.closed.swap(true, Ordering::AcqRel) {
            return Ok(());
        }
        let permits = Arc::clone(&self.concurrency)
            .acquire_many_owned(self.concurrency_limit)
            .await
            .map_err(|_| CacheError::Closed)?;
        let task = self.connection_task.lock().await.take();
        let result = match task {
            Some(task) => terminate_connection_task(&self.client, task, self.command_timeout).await,
            None => Ok(()),
        };
        drop(permits);
        result
    }

    fn require_same_slot(&self, keys: &[String], operation: &str) -> CacheResult<()> {
        if self.deployment_mode == RedisDeploymentMode::Cluster && !keys_share_slot(keys) {
            Err(CacheError::CrossSlot(format!(
                "Redis {operation} keys do not share one cluster slot"
            )))
        } else {
            Ok(())
        }
    }
}

async fn terminate_connection_task(
    client: &Client,
    task: ConnectHandle,
    timeout_duration: Duration,
) -> CacheResult<()> {
    let quit_result = match tokio::time::timeout(timeout_duration, client.quit()).await {
        Ok(result) => result.map_err(|error| map_fred_error("QUIT", error)),
        Err(_) => Err(CacheError::Timeout(format!(
            "Redis QUIT exceeded {} ms",
            timeout_duration.as_millis()
        ))),
    };

    task.abort();
    let task_result = match task.await {
        Err(error) if error.is_cancelled() => Ok(()),
        Err(error) => Err(CacheError::Internal(format!("Redis task failed: {error}"))),
        Ok(result) => result.map_err(|error| map_fred_error("connection task", error)),
    };

    quit_result.and(task_result)
}

fn fred_config(
    config: &RedisProviderConfig,
    deployment_mode: RedisDeploymentMode,
) -> CacheResult<Config> {
    let hosts = config
        .endpoints
        .iter()
        .map(|endpoint| parse_endpoint(endpoint).map(|(host, port, _)| Server::new(host, port)))
        .collect::<CacheResult<Vec<_>>>()?;
    let server = match deployment_mode {
        RedisDeploymentMode::Standalone => ServerConfig::Centralized {
            server: hosts[0].clone(),
        },
        RedisDeploymentMode::Cluster => ServerConfig::Clustered {
            hosts,
            policy: ClusterDiscoveryPolicy::ConfigEndpoint,
        },
        RedisDeploymentMode::Sentinel => ServerConfig::Sentinel {
            hosts,
            service_name: config.master_name.clone().expect("validated master_name"),
            username: non_empty(&config.sentinel_username),
            password: resolve_secret(&config.sentinel_password_env, &config.sentinel_password)?,
        },
    };
    let mut result = Config {
        server,
        username: non_empty(&config.username),
        password: resolve_secret(&config.password_env, &config.password)?,
        database: Some(config.db as u8),
        ..Config::default()
    };
    if config.uses_tls()? {
        let mut connector = native_tls::TlsConnector::builder();
        if config.tls_insecure_skip_verify {
            connector.danger_accept_invalid_certs(true);
            connector.danger_accept_invalid_hostnames(true);
        }
        let connector = TlsConnector::try_from(connector)
            .map_err(|error| map_fred_error("TLS configuration", error))?;
        result.tls = Some(TlsConfig {
            connector,
            hostnames: TlsHostMapping::None,
        });
    }
    Ok(result)
}

fn non_empty(value: &str) -> Option<String> {
    (!value.trim().is_empty()).then(|| value.to_string())
}

fn resolve_secret(variable: &str, plaintext: &str) -> CacheResult<Option<String>> {
    if variable.trim().is_empty() {
        return Ok(non_empty(plaintext));
    }
    env::var(variable).map(Some).map_err(|_| {
        CacheError::InvalidArgument(format!(
            "Redis secret environment variable {variable} is not set"
        ))
    })
}

fn fred_expiration(
    options: SetOptions,
    default_ttl: Option<Duration>,
) -> CacheResult<Option<FredExpiration>> {
    if options.keep_ttl {
        return Ok(Some(FredExpiration::KEEPTTL));
    }
    options
        .expiration
        .map(|expiration| match expiration {
            Expiration::After(duration) => duration_to_expiration(duration),
        })
        .transpose()
        .and_then(|expiration| match expiration {
            Some(expiration) => Ok(Some(expiration)),
            None => default_ttl.map(duration_to_expiration).transpose(),
        })
}

fn duration_to_expiration(duration: Duration) -> CacheResult<FredExpiration> {
    let millis = i64::try_from(duration.as_millis())
        .map_err(|_| CacheError::InvalidArgument("Redis TTL is too large".into()))?;
    if millis <= 0 {
        return Err(CacheError::InvalidArgument(
            "Redis TTL must be greater than zero".into(),
        ));
    }
    Ok(FredExpiration::PX(millis))
}

fn list_direction(direction: ListDirection) -> LMoveDirection {
    match direction {
        ListDirection::Left => LMoveDirection::Left,
        ListDirection::Right => LMoveDirection::Right,
    }
}

fn keys_share_slot(keys: &[String]) -> bool {
    let Some(first) = keys.first() else {
        return true;
    };
    let slot = fred::util::redis_keyslot(first.as_bytes());
    keys.iter()
        .skip(1)
        .all(|key| fred::util::redis_keyslot(key.as_bytes()) == slot)
}

fn fred_value_to_script_value(value: Value) -> CacheResult<ScriptValue> {
    match value {
        Value::Null => Ok(ScriptValue::Null),
        Value::Integer(value) => Ok(ScriptValue::Integer(value)),
        Value::Bytes(value) => Ok(ScriptValue::Bytes(value.to_vec())),
        Value::String(value) => Ok(ScriptValue::Bytes(value.as_bytes().to_vec())),
        Value::Array(values) => values
            .into_iter()
            .map(fred_value_to_script_value)
            .collect::<CacheResult<Vec<_>>>()
            .map(ScriptValue::Array),
        Value::Boolean(value) => Ok(ScriptValue::Boolean(value)),
        other => Err(CacheError::InvalidData(format!(
            "unsupported Redis script result: {other:?}"
        ))),
    }
}

fn map_fred_error(operation: &str, error: FredError) -> CacheError {
    let details = error.details().to_ascii_uppercase();
    if details.contains("NOSCRIPT") {
        return CacheError::NoScript(format!("Redis {operation}: {error}"));
    }
    if details.contains("CROSSSLOT") {
        return CacheError::CrossSlot(format!("Redis {operation}: {error}"));
    }
    if details.contains("READONLY") {
        return CacheError::ReadOnly(format!("Redis {operation}: {error}"));
    }
    if details.contains("NOPERM") {
        return CacheError::PermissionDenied(format!("Redis {operation}: {error}"));
    }
    match error.kind() {
        ErrorKind::Timeout => CacheError::Timeout(format!("Redis {operation}: {error}")),
        ErrorKind::Auth => CacheError::Authentication(format!("Redis {operation}: {error}")),
        ErrorKind::IO | ErrorKind::Routing | ErrorKind::Cluster | ErrorKind::Sentinel => {
            CacheError::Unavailable(format!("Redis {operation}: {error}"))
        }
        ErrorKind::InvalidArgument | ErrorKind::Config | ErrorKind::Url => {
            CacheError::InvalidArgument(format!("Redis {operation}: {error}"))
        }
        ErrorKind::Protocol | ErrorKind::Parse | ErrorKind::NotFound => {
            CacheError::InvalidData(format!("Redis {operation}: {error}"))
        }
        _ => CacheError::Internal(format!("Redis {operation}: {error}")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::net::TcpListener;

    #[test]
    fn repeated_connect_timeouts_release_connection_tasks() {
        let runtime = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(1)
            .enable_all()
            .build()
            .unwrap();
        runtime.block_on(async {
            let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
            let address = listener.local_addr().unwrap();
            let accept_task = tokio::spawn(async move {
                let mut sockets = Vec::new();
                while let Ok((socket, _)) = listener.accept().await {
                    sockets.push(socket);
                }
            });
            tokio::task::yield_now().await;
            let baseline = tokio::runtime::Handle::current()
                .metrics()
                .num_alive_tasks();
            let config = RedisProviderConfig {
                endpoints: vec![format!("redis://{address}")],
                connect_timeout_ms: 25,
                command_timeout_ms: 10_000,
                ..RedisProviderConfig::default()
            };

            for _ in 0..3 {
                let error = match RedisClient::connect(&config).await {
                    Ok(_) => panic!("Redis connection unexpectedly succeeded"),
                    Err(error) => error,
                };
                assert!(matches!(error, CacheError::Timeout(_)));
                tokio::time::sleep(Duration::from_millis(10)).await;
                assert_eq!(
                    tokio::runtime::Handle::current()
                        .metrics()
                        .num_alive_tasks(),
                    baseline
                );
            }

            accept_task.abort();
        });
    }
}
