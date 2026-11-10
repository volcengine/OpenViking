use super::backend::{Message, QueueState, StoredMessage};
use super::cache_protocol::{
    heartbeat_key, instance_key_prefix, last_enqueue_time_from_pending_payloads, queue_key_prefix,
    queue_names_key, recovery_leader_key, unix_secs, QueueKeys, ACK_SCRIPT,
    ACQUIRE_RECOVERY_LEADER_SCRIPT, CLEAR_SCRIPT, CREATE_QUEUE_SCRIPT, DEQUEUE_SCRIPT,
    ENQUEUE_SCRIPT, HEARTBEAT_INTERVAL_SECS, HEARTBEAT_TTL_SECS, LIST_UNACKED_SCRIPT, PEEK_SCRIPT,
    RECOVERY_INTERVAL_SECS, RECOVERY_LEADER_TTL_SECS, RECOVER_STALE_SCRIPT,
    RELEASE_RECOVERY_LEADER_SCRIPT, REMOVE_QUEUE_SCRIPT, STATUS_SCRIPT,
};
use crate::cache_runtime::{
    CacheError, CacheOperation, CacheRuntime, Expiration, ScriptDefinition, ScriptRequest,
    ScriptValue, SetOptions,
};
use crate::core::errors::{Error, Result};
use bytes::Bytes;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tokio::sync::{watch, Mutex};
use tokio::task::JoinHandle;
use uuid::Uuid;

const CREATE_QUEUE_ID: &str = "queuefs.create_queue.v1";
const REMOVE_QUEUE_ID: &str = "queuefs.remove_queue.v1";
const ENQUEUE_ID: &str = "queuefs.enqueue.v1";
const DEQUEUE_ID: &str = "queuefs.dequeue.v1";
const PEEK_ID: &str = "queuefs.peek.v1";
const STATUS_ID: &str = "queuefs.status.v1";
const LIST_UNACKED_ID: &str = "queuefs.list_unacked.v1";
const ACK_ID: &str = "queuefs.ack.v1";
const CLEAR_ID: &str = "queuefs.clear.v1";
const RECOVER_STALE_ID: &str = "queuefs.recover_stale.v1";
const ACQUIRE_RECOVERY_LEADER_ID: &str = "queuefs.acquire_recovery_leader.v1";
const RELEASE_RECOVERY_LEADER_ID: &str = "queuefs.release_recovery_leader.v1";

const SCRIPT_DEFINITIONS: &[ScriptDefinition] = &[
    ScriptDefinition {
        id: CREATE_QUEUE_ID,
        redis_lua: CREATE_QUEUE_SCRIPT,
    },
    ScriptDefinition {
        id: REMOVE_QUEUE_ID,
        redis_lua: REMOVE_QUEUE_SCRIPT,
    },
    ScriptDefinition {
        id: ENQUEUE_ID,
        redis_lua: ENQUEUE_SCRIPT,
    },
    ScriptDefinition {
        id: DEQUEUE_ID,
        redis_lua: DEQUEUE_SCRIPT,
    },
    ScriptDefinition {
        id: PEEK_ID,
        redis_lua: PEEK_SCRIPT,
    },
    ScriptDefinition {
        id: STATUS_ID,
        redis_lua: STATUS_SCRIPT,
    },
    ScriptDefinition {
        id: LIST_UNACKED_ID,
        redis_lua: LIST_UNACKED_SCRIPT,
    },
    ScriptDefinition {
        id: ACK_ID,
        redis_lua: ACK_SCRIPT,
    },
    ScriptDefinition {
        id: CLEAR_ID,
        redis_lua: CLEAR_SCRIPT,
    },
    ScriptDefinition {
        id: RECOVER_STALE_ID,
        redis_lua: RECOVER_STALE_SCRIPT,
    },
    ScriptDefinition {
        id: ACQUIRE_RECOVERY_LEADER_ID,
        redis_lua: ACQUIRE_RECOVERY_LEADER_SCRIPT,
    },
    ScriptDefinition {
        id: RELEASE_RECOVERY_LEADER_ID,
        redis_lua: RELEASE_RECOVERY_LEADER_SCRIPT,
    },
];

#[derive(Clone, Copy)]
struct BackgroundTiming {
    heartbeat_interval: Duration,
    heartbeat_ttl: Duration,
    recovery_interval: Duration,
    recovery_leader_ttl: Duration,
}

impl Default for BackgroundTiming {
    fn default() -> Self {
        Self {
            heartbeat_interval: Duration::from_secs(HEARTBEAT_INTERVAL_SECS),
            heartbeat_ttl: Duration::from_secs(HEARTBEAT_TTL_SECS),
            recovery_interval: Duration::from_secs(RECOVERY_INTERVAL_SECS),
            recovery_leader_ttl: Duration::from_secs(RECOVERY_LEADER_TTL_SECS),
        }
    }
}

pub(super) struct CacheQueueStorage {
    runtime: Arc<CacheRuntime>,
    key_prefix: String,
    instance_id: String,
    heartbeat_stop: watch::Sender<bool>,
    heartbeat_task: Mutex<Option<JoinHandle<()>>>,
    recovery_stop: watch::Sender<bool>,
    recovery_task: Mutex<Option<JoinHandle<()>>>,
    closed: AtomicBool,
}

impl CacheQueueStorage {
    pub(super) async fn open(runtime: Arc<CacheRuntime>, key_prefix: String) -> Result<Self> {
        Self::open_with_timing(runtime, key_prefix, BackgroundTiming::default()).await
    }

    async fn open_with_timing(
        runtime: Arc<CacheRuntime>,
        key_prefix: String,
        timing: BackgroundTiming,
    ) -> Result<Self> {
        runtime
            .require_operations(&[
                CacheOperation::Set,
                CacheOperation::Del,
                CacheOperation::Mget,
                CacheOperation::Sismember,
                CacheOperation::Smembers,
                CacheOperation::Llen,
                CacheOperation::Lrange,
                CacheOperation::ExecuteScript,
            ])
            .map_err(|error| cache_error("validate provider operations", error))?;
        for definition in SCRIPT_DEFINITIONS {
            runtime
                .register_script(*definition)
                .map_err(|error| cache_error("register script", error))?;
        }
        let instance_id = Uuid::new_v4().to_string();
        let heartbeat = heartbeat_key(&key_prefix, &instance_id);
        refresh_heartbeat(&runtime, &heartbeat, timing.heartbeat_ttl).await?;

        let (heartbeat_stop, heartbeat_receiver) = watch::channel(false);
        let heartbeat_task = tokio::spawn(run_heartbeat(
            Arc::clone(&runtime),
            heartbeat,
            heartbeat_receiver,
            timing,
        ));
        let (recovery_stop, recovery_receiver) = watch::channel(false);
        let recovery_task = tokio::spawn(run_continuous_recovery(
            Arc::clone(&runtime),
            key_prefix.clone(),
            instance_id.clone(),
            recovery_receiver,
            timing,
        ));

        Ok(Self {
            runtime,
            key_prefix,
            instance_id,
            heartbeat_stop,
            heartbeat_task: Mutex::new(Some(heartbeat_task)),
            recovery_stop,
            recovery_task: Mutex::new(Some(recovery_task)),
            closed: AtomicBool::new(false),
        })
    }

    pub(super) async fn shutdown(&self) -> Result<()> {
        if self.closed.swap(true, Ordering::AcqRel) {
            return Ok(());
        }
        let _ = self.heartbeat_stop.send(true);
        let _ = self.recovery_stop.send(true);

        let mut task_error = None;
        for task in [
            self.heartbeat_task.lock().await.take(),
            self.recovery_task.lock().await.take(),
        ]
        .into_iter()
        .flatten()
        {
            if let Err(error) = task.await {
                task_error.get_or_insert_with(|| {
                    Error::internal(format!("queuefs cache background task failed: {error}"))
                });
            }
        }

        let key = heartbeat_key(&self.key_prefix, &self.instance_id);
        let delete_result = self
            .runtime
            .del(&[key])
            .await
            .map(|_| ())
            .map_err(|error| cache_error("shutdown heartbeat cleanup", error));
        match task_error {
            Some(error) => Err(error),
            None => delete_result,
        }
    }

    async fn execute(
        &self,
        operation: &str,
        script_id: &str,
        keys: Vec<String>,
        args: Vec<Bytes>,
    ) -> Result<ScriptValue> {
        self.runtime
            .execute_script(ScriptRequest {
                script_id: script_id.to_string(),
                keys,
                args,
            })
            .await
            .and_then(|result| result.decode())
            .map_err(|error| cache_error(operation, error))
    }

    pub(super) async fn create_queue(&self, name: &str) -> Result<()> {
        let keys = QueueKeys::new(&self.key_prefix, name);
        let created = integer(
            self.execute(
                "create_queue",
                CREATE_QUEUE_ID,
                vec![queue_names_key(&self.key_prefix), keys.meta],
                vec![bytes(name), bytes(unix_secs(SystemTime::now()).to_string())],
            )
            .await?,
        )?;
        if created == 0 {
            return Err(Error::AlreadyExists(format!(
                "queue '{}' already exists",
                name
            )));
        }
        Ok(())
    }

    pub(super) async fn remove_queue(&self, name: &str) -> Result<()> {
        let removed = integer(
            self.execute(
                "remove_queue",
                REMOVE_QUEUE_ID,
                vec![queue_names_key(&self.key_prefix)],
                vec![bytes(name), bytes(queue_key_prefix(&self.key_prefix))],
            )
            .await?,
        )?;
        if removed == 0 {
            return Err(Error::NotFound(format!("queue '{}' not found", name)));
        }
        Ok(())
    }

    async fn queue_exists_result(&self, name: &str) -> Result<bool> {
        self.runtime
            .sismember(&queue_names_key(&self.key_prefix), name.as_bytes())
            .await
            .map_err(|error| cache_error("queue_exists", error))
    }

    pub(super) async fn queue_exists(&self, name: &str) -> Result<bool> {
        self.queue_exists_result(name).await
    }

    pub(super) async fn list_queues(&self, prefix: &str) -> Result<Vec<String>> {
        let mut queues = self
            .runtime
            .smembers(&queue_names_key(&self.key_prefix))
            .await
            .map_err(|error| cache_error("list_queues", error))
            .and_then(bytes_array_to_strings)?;
        queues.retain(|queue| queue.starts_with(prefix));
        queues.sort();
        Ok(queues)
    }

    async fn require_queue(&self, queue_name: &str) -> Result<()> {
        if self.queue_exists_result(queue_name).await? {
            Ok(())
        } else {
            Err(Error::NotFound(format!("queue '{}' not found", queue_name)))
        }
    }

    pub(super) async fn enqueue(&self, queue_name: &str, msg: Message) -> Result<()> {
        let keys = QueueKeys::new(&self.key_prefix, queue_name);
        let payload = serde_json::to_string(&StoredMessage::from_message(&msg))?;
        let enqueued = integer(
            self.execute(
                "enqueue",
                ENQUEUE_ID,
                vec![
                    queue_names_key(&self.key_prefix),
                    keys.message(&msg.id),
                    keys.pending,
                    keys.meta,
                ],
                vec![
                    bytes(queue_name),
                    bytes(&msg.id),
                    bytes(payload),
                    bytes(unix_secs(SystemTime::now()).to_string()),
                ],
            )
            .await?,
        )?;
        if enqueued == 0 {
            return Err(Error::NotFound(format!("queue '{}' not found", queue_name)));
        }
        Ok(())
    }

    pub(super) async fn dequeue(&self, queue_name: &str) -> Result<Option<Message>> {
        self.require_queue(queue_name).await?;
        let keys = QueueKeys::new(&self.key_prefix, queue_name);
        match self
            .execute(
                "dequeue",
                DEQUEUE_ID,
                vec![keys.pending, keys.processing],
                vec![
                    bytes(keys.message_prefix),
                    bytes(&self.instance_id),
                    bytes(unix_secs(SystemTime::now()).to_string()),
                ],
            )
            .await?
        {
            ScriptValue::Null => Ok(None),
            ScriptValue::Array(values) => values
                .get(1)
                .ok_or_else(|| Error::internal("redis dequeue returned no payload"))
                .and_then(string_value)
                .and_then(Self::decode_message)
                .map(Some),
            other => Err(invalid_result("dequeue", other)),
        }
    }

    pub(super) async fn peek(&self, queue_name: &str) -> Result<Option<Message>> {
        self.require_queue(queue_name).await?;
        let keys = QueueKeys::new(&self.key_prefix, queue_name);
        match self
            .execute(
                "peek",
                PEEK_ID,
                vec![keys.pending],
                vec![bytes(keys.message_prefix)],
            )
            .await?
        {
            ScriptValue::Null => Ok(None),
            value => string_value(&value)
                .and_then(Self::decode_message)
                .map(Some),
        }
    }

    pub(super) async fn size(&self, queue_name: &str) -> Result<usize> {
        self.require_queue(queue_name).await?;
        let keys = QueueKeys::new(&self.key_prefix, queue_name);
        usize::try_from(
            self.runtime
                .llen(&keys.pending)
                .await
                .map_err(|error| cache_error("size", error))?,
        )
        .map_err(|_| Error::internal("redis size returned an invalid value"))
    }

    pub(super) async fn status(&self, queue_name: &str) -> Result<QueueState> {
        let keys = QueueKeys::new(&self.key_prefix, queue_name);
        let value = self
            .execute(
                "status",
                STATUS_ID,
                vec![
                    queue_names_key(&self.key_prefix),
                    keys.pending,
                    keys.processing,
                ],
                vec![bytes(queue_name)],
            )
            .await?;
        if matches!(value, ScriptValue::Null) {
            return Err(Error::NotFound(format!("queue '{}' not found", queue_name)));
        }
        let ScriptValue::Array(values) = value else {
            return Err(invalid_result("queue status", value));
        };
        if values.len() != 2 {
            return Err(Error::internal(
                "redis queue status returned an invalid value",
            ));
        }
        Ok(QueueState {
            pending: usize::try_from(integer(values[0].clone())?)
                .map_err(|_| Error::internal("redis pending count is invalid"))?,
            processing: usize::try_from(integer(values[1].clone())?)
                .map_err(|_| Error::internal("redis processing count is invalid"))?,
        })
    }

    pub(super) async fn list_unacked(&self, queue_name: &str) -> Result<Vec<Message>> {
        self.require_queue(queue_name).await?;
        let keys = QueueKeys::new(&self.key_prefix, queue_name);
        string_array(
            self.execute(
                "list_unacked",
                LIST_UNACKED_ID,
                vec![keys.pending, keys.processing],
                vec![bytes(keys.message_prefix)],
            )
            .await?,
        )?
        .iter()
        .map(|payload| Self::decode_message(payload))
        .collect()
    }

    pub(super) async fn clear(&self, queue_name: &str) -> Result<()> {
        self.require_queue(queue_name).await?;
        let keys = QueueKeys::new(&self.key_prefix, queue_name);
        self.execute(
            "clear",
            CLEAR_ID,
            vec![keys.pending, keys.processing],
            vec![bytes(keys.message_prefix)],
        )
        .await?;
        Ok(())
    }

    pub(super) async fn ack(&self, queue_name: &str, msg_id: &str) -> Result<bool> {
        self.require_queue(queue_name).await?;
        let keys = QueueKeys::new(&self.key_prefix, queue_name);
        let message_key = keys.message(msg_id);
        boolean(
            self.execute(
                "ack",
                ACK_ID,
                vec![keys.processing, message_key],
                vec![bytes(msg_id)],
            )
            .await?,
        )
    }

    #[allow(dead_code)]
    pub(super) async fn get_last_enqueue_time(&self, queue_name: &str) -> Result<SystemTime> {
        self.require_queue(queue_name).await?;
        let keys = QueueKeys::new(&self.key_prefix, queue_name);
        let pending_key = keys.pending.clone();
        let pending_ids = self
            .runtime
            .lrange(&pending_key, 0, -1)
            .await
            .map_err(|error| cache_error("get_last_enqueue_time list pending", error))
            .and_then(bytes_array_to_strings)?;
        if pending_ids.is_empty() {
            return Ok(UNIX_EPOCH);
        }
        let message_keys = pending_ids
            .iter()
            .map(|id| keys.message(id))
            .collect::<Vec<_>>();
        let payloads = self
            .runtime
            .mget(&message_keys)
            .await
            .map_err(|error| cache_error("get_last_enqueue_time load payloads", error))?
            .into_iter()
            .enumerate()
            .map(|(index, payload)| {
                payload
                    .ok_or_else(|| {
                        Error::internal(format!(
                            "redis get_last_enqueue_time missing payload for message {}",
                            pending_ids[index]
                        ))
                    })
                    .and_then(|payload| {
                        String::from_utf8(payload.to_vec()).map_err(|error| {
                            Error::Serialization(format!("invalid queue payload: {error}"))
                        })
                    })
            })
            .collect::<Result<Vec<_>>>()?;
        last_enqueue_time_from_pending_payloads(&payloads)
    }

    fn decode_message(payload: &str) -> Result<Message> {
        serde_json::from_str::<StoredMessage>(payload)
            .map(StoredMessage::into_message)
            .map_err(|error| Error::Serialization(format!("invalid queue payload: {error}")))
    }
}

impl Drop for CacheQueueStorage {
    fn drop(&mut self) {
        if self.closed.swap(true, Ordering::AcqRel) {
            return;
        }
        let _ = self.heartbeat_stop.send(true);
        let _ = self.recovery_stop.send(true);
        if let Ok(mut task) = self.heartbeat_task.try_lock() {
            if let Some(task) = task.take() {
                task.abort();
            }
        }
        if let Ok(mut task) = self.recovery_task.try_lock() {
            if let Some(task) = task.take() {
                task.abort();
            }
        }
        let key = heartbeat_key(&self.key_prefix, &self.instance_id);
        if let Ok(handle) = tokio::runtime::Handle::try_current() {
            let runtime = Arc::clone(&self.runtime);
            handle.spawn(async move {
                let _ = runtime.del(&[key]).await;
            });
        } else if let Err(error) = self.runtime.sync_facade().del(&[key]) {
            tracing::warn!("queuefs cache heartbeat cleanup failed: {error}");
        }
    }
}

async fn run_heartbeat(
    runtime: Arc<CacheRuntime>,
    key: String,
    mut stop: watch::Receiver<bool>,
    timing: BackgroundTiming,
) {
    let mut interval = tokio::time::interval(timing.heartbeat_interval);
    interval.tick().await;
    loop {
        tokio::select! {
            _ = interval.tick() => {
                if let Err(error) = refresh_heartbeat(&runtime, &key, timing.heartbeat_ttl).await {
                    tracing::warn!("queuefs cache heartbeat failed: {error}");
                }
            }
            changed = stop.changed() => {
                if changed.is_err() || *stop.borrow() {
                    break;
                }
            }
        }
    }
}

async fn refresh_heartbeat(runtime: &CacheRuntime, key: &str, ttl: Duration) -> Result<()> {
    runtime
        .set(
            key,
            Bytes::from_static(b"1"),
            SetOptions {
                expiration: Some(Expiration::After(ttl)),
                ..SetOptions::default()
            },
        )
        .await
        .map(|_| ())
        .map_err(|error| cache_error("heartbeat", error))
}

async fn run_continuous_recovery(
    runtime: Arc<CacheRuntime>,
    key_prefix: String,
    leader_token: String,
    mut stop: watch::Receiver<bool>,
    timing: BackgroundTiming,
) {
    let mut interval = tokio::time::interval(timing.recovery_interval);
    loop {
        tokio::select! {
            _ = interval.tick() => {
                match acquire_or_renew_recovery_leader(
                    &runtime,
                    &key_prefix,
                    &leader_token,
                    timing.recovery_leader_ttl,
                ).await {
                    Ok(true) => {
                        if let Err(error) = recover_stale(
                            &runtime,
                            &key_prefix,
                            &leader_token,
                            timing.recovery_leader_ttl,
                        ).await {
                            tracing::warn!("queuefs cache recover_stale failed: {error}");
                        }
                    }
                    Ok(false) => {}
                    Err(error) => tracing::warn!("queuefs cache recovery leader lease failed: {error}"),
                }
            }
            changed = stop.changed() => {
                if changed.is_err() || *stop.borrow() {
                    break;
                }
            }
        }
    }

    if let Err(error) = release_recovery_leader(&runtime, &key_prefix, &leader_token).await {
        tracing::warn!("queuefs cache recovery leader release failed: {error}");
    }
}

async fn recover_stale(
    runtime: &CacheRuntime,
    key_prefix: &str,
    leader_token: &str,
    leader_ttl: Duration,
) -> Result<usize> {
    let queues = runtime
        .smembers(&queue_names_key(key_prefix))
        .await
        .map_err(|error| cache_error("recover_stale list queues", error))
        .and_then(bytes_array_to_strings)?;
    let mut recovered = 0;
    for queue in queues {
        if !acquire_or_renew_recovery_leader(runtime, key_prefix, leader_token, leader_ttl).await? {
            break;
        }
        let keys = QueueKeys::new(key_prefix, &queue);
        recovered += usize::try_from(integer(
            execute_runtime(
                runtime,
                "recover_stale",
                RECOVER_STALE_ID,
                vec![keys.processing, keys.pending],
                vec![bytes(instance_key_prefix(key_prefix))],
            )
            .await?,
        )?)
        .map_err(|_| Error::internal("redis recover_stale returned an invalid value"))?;
    }
    Ok(recovered)
}

async fn acquire_or_renew_recovery_leader(
    runtime: &CacheRuntime,
    key_prefix: &str,
    leader_token: &str,
    ttl: Duration,
) -> Result<bool> {
    boolean(
        execute_runtime(
            runtime,
            "acquire recovery leader",
            ACQUIRE_RECOVERY_LEADER_ID,
            vec![recovery_leader_key(key_prefix)],
            vec![bytes(leader_token), bytes(ttl.as_millis().to_string())],
        )
        .await?,
    )
}

async fn release_recovery_leader(
    runtime: &CacheRuntime,
    key_prefix: &str,
    leader_token: &str,
) -> Result<bool> {
    boolean(
        execute_runtime(
            runtime,
            "release recovery leader",
            RELEASE_RECOVERY_LEADER_ID,
            vec![recovery_leader_key(key_prefix)],
            vec![bytes(leader_token)],
        )
        .await?,
    )
}

async fn execute_runtime(
    runtime: &CacheRuntime,
    operation: &str,
    script_id: &str,
    keys: Vec<String>,
    args: Vec<Bytes>,
) -> Result<ScriptValue> {
    runtime
        .execute_script(ScriptRequest {
            script_id: script_id.to_string(),
            keys,
            args,
        })
        .await
        .and_then(|result| result.decode())
        .map_err(|error| cache_error(operation, error))
}

fn bytes(value: impl AsRef<[u8]>) -> Bytes {
    Bytes::copy_from_slice(value.as_ref())
}

fn integer(value: ScriptValue) -> Result<i64> {
    match value {
        ScriptValue::Integer(value) => Ok(value),
        other => Err(invalid_result("integer", other)),
    }
}

fn boolean(value: ScriptValue) -> Result<bool> {
    match value {
        ScriptValue::Boolean(value) => Ok(value),
        ScriptValue::Integer(value) => Ok(value != 0),
        other => Err(invalid_result("boolean", other)),
    }
}

fn string_value(value: &ScriptValue) -> Result<&str> {
    match value {
        ScriptValue::Bytes(value) => std::str::from_utf8(value)
            .map_err(|error| Error::Serialization(format!("invalid queue payload: {error}"))),
        other => Err(invalid_result("string", other.clone())),
    }
}

fn string_array(value: ScriptValue) -> Result<Vec<String>> {
    match value {
        ScriptValue::Array(values) => values
            .iter()
            .map(string_value)
            .map(|value| value.map(str::to_string))
            .collect(),
        other => Err(invalid_result("array", other)),
    }
}

fn bytes_array_to_strings(values: Vec<Bytes>) -> Result<Vec<String>> {
    values
        .into_iter()
        .map(|value| {
            String::from_utf8(value.to_vec())
                .map_err(|error| Error::Serialization(format!("invalid queue payload: {error}")))
        })
        .collect()
}

fn invalid_result(expected: &str, value: ScriptValue) -> Error {
    Error::internal(format!(
        "cache script returned {value:?}, expected {expected}"
    ))
}

fn cache_error(operation: &str, error: CacheError) -> Error {
    match error {
        CacheError::Timeout(message) => Error::Timeout(format!("cache {operation}: {message}")),
        CacheError::Unavailable(message) => Error::Network(format!("cache {operation}: {message}")),
        CacheError::Closed => Error::Network(format!("cache {operation}: runtime is closed")),
        other => Error::internal(format!("cache {operation}: {other}")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn lifecycle_test_runtime() -> Arc<CacheRuntime> {
        let provider = Arc::new(
            crate::cache_runtime::MemoryMockProvider::new().with_script_handler(|_| {
                crate::cache_runtime::ScriptResult::encode(&ScriptValue::Integer(1))
            }),
        );
        CacheRuntime::memory_with_provider(provider)
    }

    #[tokio::test]
    async fn open_rejects_provider_without_script_support() {
        let error = CacheQueueStorage::open(CacheRuntime::memory(), "unsupported".to_string())
            .await
            .err()
            .expect("memory provider must be rejected");

        assert!(matches!(
            error,
            Error::Internal(message)
                if message.contains("unsupported cache operation: execute_script")
        ));
    }

    #[test]
    fn default_background_timing_matches_recovery_contract() {
        let timing = BackgroundTiming::default();
        assert_eq!(
            timing.heartbeat_interval,
            Duration::from_secs(HEARTBEAT_INTERVAL_SECS)
        );
        assert_eq!(
            timing.heartbeat_ttl,
            Duration::from_secs(HEARTBEAT_TTL_SECS)
        );
        assert_eq!(
            timing.recovery_interval,
            Duration::from_secs(RECOVERY_INTERVAL_SECS)
        );
        assert_eq!(
            timing.recovery_leader_ttl,
            Duration::from_secs(RECOVERY_LEADER_TTL_SECS)
        );
    }

    #[test]
    fn drop_outside_tokio_runtime_removes_heartbeat() {
        let runtime = lifecycle_test_runtime();
        let tokio_runtime = tokio::runtime::Runtime::new().unwrap();
        let storage = tokio_runtime
            .block_on(CacheQueueStorage::open(
                Arc::clone(&runtime),
                "drop-test".to_string(),
            ))
            .unwrap();
        let heartbeat = heartbeat_key("drop-test", &storage.instance_id);
        let sync = runtime.sync_facade();

        assert!(sync.get(&heartbeat).unwrap().is_some());
        drop(storage);
        assert!(sync.get(&heartbeat).unwrap().is_none());
    }

    #[tokio::test]
    async fn shutdown_removes_heartbeat_before_returning() {
        let runtime = lifecycle_test_runtime();
        let storage =
            CacheQueueStorage::open(Arc::clone(&runtime), "async-shutdown-test".to_string())
                .await
                .unwrap();
        let heartbeat = heartbeat_key("async-shutdown-test", &storage.instance_id);

        assert!(runtime.get(&heartbeat).await.unwrap().is_some());
        storage.shutdown().await.unwrap();
        assert!(runtime.get(&heartbeat).await.unwrap().is_none());
    }

    #[tokio::test]
    async fn graceful_shutdown_allows_immediate_processing_recovery() {
        let Ok(endpoint) = std::env::var("REDIS_URL") else {
            return;
        };
        let runtime = CacheRuntime::redis(crate::cache_runtime::RedisProviderConfig {
            endpoints: vec![endpoint],
            connect_timeout_ms: 5_000,
            command_timeout_ms: 1_000,
            default_ttl_seconds: 60,
            ..crate::cache_runtime::RedisProviderConfig::default()
        })
        .await
        .unwrap();
        let prefix = format!("queuefs-shutdown-test:{}", Uuid::new_v4());
        let first = CacheQueueStorage::open(Arc::clone(&runtime), prefix.clone())
            .await
            .unwrap();
        first.create_queue("jobs").await.unwrap();
        let message = Message::new(b"payload".to_vec());
        let message_id = message.id.clone();
        first.enqueue("jobs", message).await.unwrap();
        assert_eq!(first.dequeue("jobs").await.unwrap().unwrap().id, message_id);

        first.shutdown().await.unwrap();
        let second = CacheQueueStorage::open_with_timing(
            Arc::clone(&runtime),
            prefix.clone(),
            fast_background_timing(),
        )
        .await
        .unwrap();
        let recovered = tokio::time::timeout(Duration::from_secs(2), async {
            loop {
                if let Some(message) = second.dequeue("jobs").await.unwrap() {
                    break message;
                }
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .unwrap();

        assert_eq!(recovered.id, message_id);
        second.remove_queue("jobs").await.unwrap();
        second.shutdown().await.unwrap();
        runtime.del(&[queue_names_key(&prefix)]).await.unwrap();
        runtime.close().await.unwrap();
    }

    #[tokio::test]
    async fn running_node_recovers_processing_after_peer_crash() {
        let Ok(endpoint) = std::env::var("REDIS_URL") else {
            return;
        };
        let runtime = CacheRuntime::redis(crate::cache_runtime::RedisProviderConfig {
            endpoints: vec![endpoint],
            connect_timeout_ms: 5_000,
            command_timeout_ms: 1_000,
            default_ttl_seconds: 60,
            ..crate::cache_runtime::RedisProviderConfig::default()
        })
        .await
        .unwrap();
        let prefix = format!("queuefs-continuous-recovery-test:{}", Uuid::new_v4());
        let survivor = CacheQueueStorage::open_with_timing(
            Arc::clone(&runtime),
            prefix.clone(),
            fast_background_timing(),
        )
        .await
        .unwrap();
        let failed = CacheQueueStorage::open_with_timing(
            Arc::clone(&runtime),
            prefix.clone(),
            fast_background_timing(),
        )
        .await
        .unwrap();
        failed.create_queue("jobs").await.unwrap();
        let message = Message::new(b"payload".to_vec());
        let message_id = message.id.clone();
        failed.enqueue("jobs", message).await.unwrap();
        assert_eq!(
            failed.dequeue("jobs").await.unwrap().unwrap().id,
            message_id
        );

        let heartbeat = heartbeat_key(&prefix, &failed.instance_id);
        for task in [
            failed.heartbeat_task.lock().await.take(),
            failed.recovery_task.lock().await.take(),
        ]
        .into_iter()
        .flatten()
        {
            task.abort();
        }
        runtime.del(&[heartbeat]).await.unwrap();
        drop(failed);

        let recovered = tokio::time::timeout(Duration::from_secs(2), async {
            loop {
                if let Some(message) = survivor.dequeue("jobs").await.unwrap() {
                    break message;
                }
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .unwrap();

        assert_eq!(recovered.id, message_id);
        survivor.remove_queue("jobs").await.unwrap();
        survivor.shutdown().await.unwrap();
        runtime.del(&[queue_names_key(&prefix)]).await.unwrap();
        runtime.close().await.unwrap();
    }

    fn fast_background_timing() -> BackgroundTiming {
        BackgroundTiming {
            heartbeat_interval: Duration::from_millis(20),
            heartbeat_ttl: Duration::from_millis(100),
            recovery_interval: Duration::from_millis(20),
            recovery_leader_ttl: Duration::from_millis(100),
        }
    }
}
