//! Queue Backend Abstraction
//!
//! This module provides a pluggable backend system for QueueFS, allowing different
//! storage implementations (memory, SQLite, etc.) while maintaining a consistent interface.

use crate::core::errors::{Error, Result};
use chrono::{DateTime, SecondsFormat, Utc};
use rusqlite::{params, types::ValueRef, Connection, Row};
use serde::{Deserialize, Deserializer, Serialize};
use std::collections::{HashMap, HashSet, VecDeque};
use std::sync::atomic::{AtomicI64, Ordering};
use std::sync::Mutex;
use std::time::SystemTime;
use std::time::{Duration, UNIX_EPOCH};
use uuid::Uuid;

/// A message in the queue
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Message {
    /// Unique identifier for the message
    pub id: String,
    /// Message data
    pub data: Vec<u8>,
    /// Timestamp when the message was enqueued
    pub timestamp: SystemTime,
    /// Whether `timestamp` came from a real persisted enqueue time.
    pub timestamp_valid: bool,
    /// Scheduling metadata, separate from the opaque payload.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub group_key: Option<String>,
    /// At most one unacknowledged delivery for this key.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub serial_key: Option<String>,
}

impl Message {
    /// Create a new message with the given data
    pub fn new(data: Vec<u8>) -> Self {
        Self {
            id: Uuid::new_v4().to_string(),
            data,
            timestamp: SystemTime::now(),
            timestamp_valid: true,
            group_key: None,
            serial_key: None,
        }
    }

    pub(super) fn validate_keys(&self) -> Result<()> {
        if (self.group_key.is_some() && self.serial_key.is_some())
            || self
                .group_key
                .iter()
                .chain(self.serial_key.iter())
                .any(|key| !(1..=4096).contains(&key.len()))
        {
            return Err(Error::InvalidOperation(
                "group_key and serial_key are mutually exclusive and must contain 1..=4096 UTF-8 bytes"
                    .to_string(),
            ));
        }
        Ok(())
    }
}

/// Current queue occupancy.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub struct QueueState {
    /// Messages available for dequeue.
    pub pending: usize,
    /// Messages dequeued but not acknowledged.
    pub processing: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub(super) struct StoredMessage {
    id: String,
    #[serde(deserialize_with = "deserialize_stored_message_data")]
    data: Vec<u8>,
    #[serde(default)]
    timestamp: Option<serde_json::Value>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    group_key: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    serial_key: Option<String>,
}

impl StoredMessage {
    pub(super) fn from_message(msg: &Message) -> Self {
        Self {
            id: msg.id.clone(),
            data: msg.data.clone(),
            group_key: msg.group_key.clone(),
            serial_key: msg.serial_key.clone(),
            // Older readers already accept RFC3339 strings; nanoseconds preserve latency accuracy.
            timestamp: Some(serde_json::Value::String(
                DateTime::<Utc>::from(msg.timestamp).to_rfc3339_opts(SecondsFormat::Nanos, true),
            )),
        }
    }

    pub(super) fn into_message(self) -> Message {
        let (timestamp, timestamp_valid) = parse_stored_timestamp(self.timestamp);
        Message {
            id: self.id,
            data: self.data,
            timestamp,
            timestamp_valid,
            group_key: self.group_key,
            serial_key: self.serial_key,
        }
    }
}

fn deserialize_stored_message_data<'de, D>(
    deserializer: D,
) -> std::result::Result<Vec<u8>, D::Error>
where
    D: Deserializer<'de>,
{
    let value = serde_json::Value::deserialize(deserializer)?;
    match value {
        // Legacy queue.db rows stored message bytes as a JSON string.
        serde_json::Value::String(data) => Ok(data.into_bytes()),
        // New rows keep arbitrary message bytes lossless as a JSON byte array.
        serde_json::Value::Array(bytes) => bytes
            .into_iter()
            .map(|byte| {
                byte.as_u64()
                    .and_then(|value| u8::try_from(value).ok())
                    .ok_or_else(|| {
                        serde::de::Error::custom(
                            "stored queue message data byte must be an integer in 0..=255",
                        )
                    })
            })
            .collect(),
        other => Err(serde::de::Error::custom(format!(
            "stored queue message data must be a string or byte array, got {other}"
        ))),
    }
}

fn parse_stored_timestamp(raw: Option<serde_json::Value>) -> (SystemTime, bool) {
    match raw {
        Some(serde_json::Value::String(ts)) => DateTime::parse_from_rfc3339(&ts)
            .ok()
            .and_then(|dt| timestamp_from_parts(dt.timestamp(), dt.timestamp_subsec_nanos()))
            .map(|timestamp| (timestamp, true))
            .unwrap_or_else(|| (SystemTime::now(), false)),
        Some(serde_json::Value::Number(num)) => num
            .as_i64()
            .and_then(|secs| timestamp_from_parts(secs, 0))
            .map(|timestamp| (timestamp, true))
            .unwrap_or_else(|| (SystemTime::now(), false)),
        _ => (SystemTime::now(), false),
    }
}

fn timestamp_from_parts(secs: i64, nanos: u32) -> Option<SystemTime> {
    (secs > 0)
        .then(|| Duration::new(secs as u64, nanos))
        .and_then(|duration| UNIX_EPOCH.checked_add(duration))
}

fn unix_secs(time: SystemTime) -> i64 {
    time.duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs() as i64
}

fn read_sqlite_text(row: &Row<'_>, idx: usize) -> rusqlite::Result<String> {
    match row.get_ref(idx)? {
        ValueRef::Text(bytes) => Ok(String::from_utf8_lossy(bytes).into_owned()),
        ValueRef::Blob(bytes) => Ok(String::from_utf8_lossy(bytes).into_owned()),
        ValueRef::Null => Ok(String::new()),
        other => Err(rusqlite::Error::InvalidColumnType(
            idx,
            row.as_ref()
                .column_name(idx)
                .map(|s| s.to_string())
                .unwrap_or_else(|_| format!("column_{idx}")),
            other.data_type(),
        )),
    }
}

/// Queue backend trait for pluggable storage implementations
pub trait QueueBackend: Send + Sync {
    /// Create a new queue with the given name
    fn create_queue(&mut self, name: &str) -> Result<()>;

    /// Remove a queue and all its messages
    fn remove_queue(&mut self, name: &str) -> Result<()>;

    /// Check if a queue exists
    fn queue_exists(&self, name: &str) -> bool;

    /// List all queues with the given prefix
    /// If prefix is empty, returns all queues
    fn list_queues(&self, prefix: &str) -> Vec<String>;

    /// Add a message to the queue
    fn enqueue(&mut self, queue_name: &str, msg: Message) -> Result<()>;

    /// Remove and return the first message from the queue
    fn dequeue(&mut self, queue_name: &str) -> Result<Option<Message>>;

    /// View the first message without removing it
    fn peek(&self, queue_name: &str) -> Result<Option<Message>>;

    /// Get the number of messages in the queue
    fn size(&self, queue_name: &str) -> Result<usize>;

    /// Get pending and processing message counts from one backend snapshot
    fn status(&self, queue_name: &str) -> Result<QueueState>;

    /// List every unacknowledged message without changing queue state
    fn list_unacked(&self, queue_name: &str) -> Result<Vec<Message>>;

    /// Clear all messages from the queue
    fn clear(&mut self, queue_name: &str) -> Result<()>;

    /// Get the last enqueue time for the queue
    fn get_last_enqueue_time(&self, queue_name: &str) -> Result<SystemTime>;

    /// Acknowledge (delete) a message by ID
    fn ack(&mut self, queue_name: &str, msg_id: &str) -> Result<bool>;

    /// Return a processing message to pending, retaining its identity and serial priority.
    fn retry(&mut self, queue_name: &str, msg_id: &str) -> Result<bool>;
}

/// Options for SQLite queue backend.
#[derive(Debug, Clone, Copy)]
pub struct SQLiteQueueOptions {
    pub recover_stale_sec: i64,
    pub busy_timeout_ms: u64,
}

impl Default for SQLiteQueueOptions {
    fn default() -> Self {
        Self {
            recover_stale_sec: 0,
            busy_timeout_ms: 5_000,
        }
    }
}

/// A single queue with its messages
struct Queue {
    messages: VecDeque<Message>,
    processing: VecDeque<Message>,
    /// IDs stay reserved across dequeue/retry until final ACK or clear.
    unacked_ids: HashSet<String>,
    retry_serial: HashMap<String, String>,
    last_enqueue_time: SystemTime,
}

impl Queue {
    fn new() -> Self {
        Self {
            messages: VecDeque::new(),
            processing: VecDeque::new(),
            unacked_ids: HashSet::new(),
            retry_serial: HashMap::new(),
            last_enqueue_time: SystemTime::UNIX_EPOCH,
        }
    }
}

/// In-memory queue backend using HashMap
pub struct MemoryBackend {
    queues: HashMap<String, Queue>,
}

impl MemoryBackend {
    /// Create a new memory backend
    pub fn new() -> Self {
        Self {
            queues: HashMap::new(),
        }
    }
}

impl QueueBackend for MemoryBackend {
    fn create_queue(&mut self, name: &str) -> Result<()> {
        if self.queues.contains_key(name) {
            return Err(Error::AlreadyExists(format!(
                "queue '{}' already exists",
                name
            )));
        }
        self.queues.insert(name.to_string(), Queue::new());
        Ok(())
    }

    fn remove_queue(&mut self, name: &str) -> Result<()> {
        if self.queues.remove(name).is_none() {
            return Err(Error::NotFound(format!("queue '{}' not found", name)));
        }
        Ok(())
    }

    fn queue_exists(&self, name: &str) -> bool {
        self.queues.contains_key(name)
    }

    fn list_queues(&self, prefix: &str) -> Vec<String> {
        if prefix.is_empty() {
            self.queues.keys().cloned().collect()
        } else {
            self.queues
                .keys()
                .filter(|name| name.starts_with(prefix))
                .cloned()
                .collect()
        }
    }

    fn enqueue(&mut self, queue_name: &str, msg: Message) -> Result<()> {
        let queue = self
            .queues
            .get_mut(queue_name)
            .ok_or_else(|| Error::NotFound(format!("queue '{}' not found", queue_name)))?;

        msg.validate_keys()?;
        if !queue.unacked_ids.insert(msg.id.clone()) {
            return Err(Error::AlreadyExists(format!(
                "message '{}' already exists",
                msg.id
            )));
        }
        queue.last_enqueue_time = SystemTime::now();
        queue.messages.push_back(msg);
        Ok(())
    }

    fn dequeue(&mut self, queue_name: &str) -> Result<Option<Message>> {
        let queue = self
            .queues
            .get_mut(queue_name)
            .ok_or_else(|| Error::NotFound(format!("queue '{}' not found", queue_name)))?;

        // MemoryBackend itself is the sole consumer instance. Node affinity is
        // therefore implicit; serial_key additionally excludes active keys.
        let position = queue.messages.iter().position(|message| {
            message.serial_key.as_ref().is_none_or(|key| {
                queue
                    .retry_serial
                    .get(key)
                    .is_none_or(|id| id == &message.id)
                    && !queue
                        .processing
                        .iter()
                        .any(|active| active.serial_key == message.serial_key)
            })
        });
        let message = position.and_then(|position| queue.messages.remove(position));
        if let Some(message) = &message {
            queue.processing.push_back(message.clone());
        }
        Ok(message)
    }

    fn peek(&self, queue_name: &str) -> Result<Option<Message>> {
        let queue = self
            .queues
            .get(queue_name)
            .ok_or_else(|| Error::NotFound(format!("queue '{}' not found", queue_name)))?;

        Ok(queue.messages.front().cloned())
    }

    fn size(&self, queue_name: &str) -> Result<usize> {
        let queue = self
            .queues
            .get(queue_name)
            .ok_or_else(|| Error::NotFound(format!("queue '{}' not found", queue_name)))?;

        Ok(queue.messages.len())
    }

    fn status(&self, queue_name: &str) -> Result<QueueState> {
        let queue = self
            .queues
            .get(queue_name)
            .ok_or_else(|| Error::NotFound(format!("queue '{}' not found", queue_name)))?;
        Ok(QueueState {
            pending: queue.messages.len(),
            processing: queue.processing.len(),
        })
    }

    fn list_unacked(&self, queue_name: &str) -> Result<Vec<Message>> {
        let queue = self
            .queues
            .get(queue_name)
            .ok_or_else(|| Error::NotFound(format!("queue '{}' not found", queue_name)))?;
        Ok(queue
            .messages
            .iter()
            .chain(queue.processing.iter())
            .cloned()
            .collect())
    }

    fn clear(&mut self, queue_name: &str) -> Result<()> {
        let queue = self
            .queues
            .get_mut(queue_name)
            .ok_or_else(|| Error::NotFound(format!("queue '{}' not found", queue_name)))?;

        queue.messages.clear();
        queue.processing.clear();
        queue.unacked_ids.clear();
        queue.retry_serial.clear();
        Ok(())
    }

    fn get_last_enqueue_time(&self, queue_name: &str) -> Result<SystemTime> {
        let queue = self
            .queues
            .get(queue_name)
            .ok_or_else(|| Error::NotFound(format!("queue '{}' not found", queue_name)))?;

        Ok(queue.last_enqueue_time)
    }

    fn ack(&mut self, queue_name: &str, msg_id: &str) -> Result<bool> {
        let queue = self
            .queues
            .get_mut(queue_name)
            .ok_or_else(|| Error::NotFound(format!("queue '{}' not found", queue_name)))?;

        let Some(position) = queue.processing.iter().position(|msg| msg.id == msg_id) else {
            return Ok(false);
        };
        let message = queue.processing.remove(position).unwrap();
        queue.unacked_ids.remove(&message.id);
        if let Some(key) = message.serial_key {
            if queue.retry_serial.get(&key).is_some_and(|id| id == msg_id) {
                queue.retry_serial.remove(&key);
            }
        }
        Ok(true)
    }

    fn retry(&mut self, queue_name: &str, msg_id: &str) -> Result<bool> {
        let queue = self
            .queues
            .get_mut(queue_name)
            .ok_or_else(|| Error::NotFound(format!("queue '{}' not found", queue_name)))?;
        let Some(position) = queue.processing.iter().position(|msg| msg.id == msg_id) else {
            return Ok(false);
        };
        let message = queue.processing.remove(position).unwrap();
        if let Some(key) = &message.serial_key {
            queue.retry_serial.insert(key.clone(), message.id.clone());
        }
        queue.messages.push_back(message);
        Ok(true)
    }
}

/// SQLite queue backend with at-least-once delivery semantics.
pub struct SQLiteQueueBackend {
    conn: Mutex<Connection>,
    next_schedule_seq: AtomicI64,
}

impl SQLiteQueueBackend {
    pub fn open(db_path: &str, options: SQLiteQueueOptions) -> Result<Self> {
        let conn = Connection::open(db_path)
            .map_err(|e| Error::internal(format!("sqlite connection error: {}", e)))?;

        if options.busy_timeout_ms > 0 {
            conn.busy_timeout(Duration::from_millis(options.busy_timeout_ms))
                .map_err(|e| Error::internal(format!("sqlite busy_timeout error: {}", e)))?;
        }

        if Self::is_new_database(&conn)? {
            conn.execute_batch(
                r#"
                PRAGMA auto_vacuum=FULL;
                VACUUM;
                "#,
            )
            .map_err(|e| Error::internal(format!("sqlite auto_vacuum pragma error: {}", e)))?;
        }

        conn.execute_batch(
            r#"
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=NORMAL;
            "#,
        )
        .map_err(|e| Error::internal(format!("sqlite pragma error: {}", e)))?;

        conn.execute_batch(
            r#"
            CREATE TABLE IF NOT EXISTS queue_metadata (
                queue_name TEXT PRIMARY KEY,
                created_at INTEGER DEFAULT (strftime('%s', 'now')),
                last_updated INTEGER DEFAULT (strftime('%s', 'now'))
            );

            CREATE TABLE IF NOT EXISTS queue_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                queue_name TEXT NOT NULL,
                message_id TEXT NOT NULL,
                data TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                processing_started_at INTEGER,
                created_at INTEGER DEFAULT (strftime('%s', 'now'))
            );

            "#,
        )
        .map_err(|e| Error::internal(format!("sqlite schema init error: {}", e)))?;

        let backend = Self {
            conn: Mutex::new(conn),
            next_schedule_seq: AtomicI64::new(1),
        };
        backend.run_migrations()?;
        let next = backend
            .conn
            .lock()
            .map_err(|e| Error::internal(e.to_string()))?
            .query_row(
                "SELECT COALESCE(MAX(schedule_seq), 0) + 1 FROM queue_messages",
                [],
                |row| row.get::<_, i64>(0),
            )
            .map_err(|e| Error::internal(format!("sqlite sequence init error: {e}")))?;
        backend.next_schedule_seq.store(next, Ordering::Relaxed);
        backend.recover_stale(options.recover_stale_sec)?;
        Ok(backend)
    }

    fn is_new_database(conn: &Connection) -> Result<bool> {
        let table_count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'",
                [],
                |row| row.get(0),
            )
            .map_err(|e| Error::internal(format!("sqlite schema probe error: {}", e)))?;
        Ok(table_count == 0)
    }

    fn run_migrations(&self) -> Result<()> {
        let conn = self
            .conn
            .lock()
            .map_err(|e| Error::internal(format!("sqlite mutex poisoned: {}", e)))?;

        for stmt in [
            "ALTER TABLE queue_messages ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'",
            "ALTER TABLE queue_messages ADD COLUMN processing_started_at INTEGER",
            "ALTER TABLE queue_messages ADD COLUMN serial_key TEXT",
            "ALTER TABLE queue_messages ADD COLUMN schedule_seq INTEGER",
            "CREATE INDEX IF NOT EXISTS idx_queue_message_id ON queue_messages(queue_name, message_id)",
            "CREATE INDEX IF NOT EXISTS idx_queue_serial ON queue_messages(queue_name, serial_key, id)",
        ] {
            if let Err(err) = conn.execute(stmt, []) {
                let text = err.to_string();
                if !text.contains("duplicate column name") && !text.contains("already exists") {
                    return Err(Error::internal(format!(
                        "sqlite migration failed for '{}': {}",
                        stmt, text
                    )));
                }
            }
        }

        // Keep row IDs immutable for same-key ordering. Only scheduling moves to
        // the tail on retry; replace the existing status index rather than adding one.
        conn.execute(
            "UPDATE queue_messages SET schedule_seq = id WHERE schedule_seq IS NULL",
            [],
        )
        .map_err(|e| Error::internal(format!("sqlite sequence migration error: {e}")))?;
        let columns = conn
            .prepare("PRAGMA index_info(idx_queue_status)")
            .and_then(|mut stmt| {
                stmt.query_map([], |row| row.get::<_, String>(2))?
                    .collect::<rusqlite::Result<Vec<_>>>()
            })
            .map_err(|e| Error::internal(format!("sqlite index probe error: {e}")))?;
        if columns != ["queue_name", "status", "schedule_seq"] {
            conn.execute_batch(
                "DROP INDEX IF EXISTS idx_queue_status;
                 CREATE INDEX idx_queue_status ON queue_messages(queue_name, status, schedule_seq);"
            ).map_err(|e| Error::internal(format!("sqlite schedule index migration error: {e}")))?;
        }

        // Backfill queue metadata for legacy databases that only persisted queue_messages.
        conn.execute(
            "INSERT OR IGNORE INTO queue_metadata (queue_name)
             SELECT DISTINCT queue_name
             FROM queue_messages
             WHERE queue_name IS NOT NULL AND queue_name != ''",
            [],
        )
        .map_err(|e| {
            Error::internal(format!(
                "sqlite migration backfill queue metadata error: {}",
                e
            ))
        })?;

        Ok(())
    }

    fn recover_stale(&self, stale_sec: i64) -> Result<usize> {
        let cutoff = Utc::now().timestamp() - stale_sec.max(0);
        let conn = self
            .conn
            .lock()
            .map_err(|e| Error::internal(format!("sqlite mutex poisoned: {}", e)))?;

        let changed = if stale_sec <= 0 {
            conn.execute(
                "UPDATE queue_messages SET status = 'pending', processing_started_at = NULL WHERE status = 'processing'",
                [],
            )
        } else {
            conn.execute(
                "UPDATE queue_messages SET status = 'pending', processing_started_at = NULL WHERE status = 'processing' AND processing_started_at <= ?1",
                params![cutoff],
            )
        }
        .map_err(|e| Error::internal(format!("sqlite recover stale error: {}", e)))?;

        Ok(changed)
    }

    fn queue_known(conn: &Connection, queue_name: &str) -> Result<bool> {
        let metadata_count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM queue_metadata WHERE queue_name = ?1",
                params![queue_name],
                |row| row.get(0),
            )
            .map_err(|e| Error::internal(format!("sqlite queue metadata lookup error: {}", e)))?;

        if metadata_count > 0 {
            return Ok(true);
        }

        let message_count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM queue_messages WHERE queue_name = ?1",
                params![queue_name],
                |row| row.get(0),
            )
            .map_err(|e| Error::internal(format!("sqlite queue messages lookup error: {}", e)))?;

        Ok(message_count > 0)
    }

    fn require_queue_exists(conn: &Connection, queue_name: &str) -> Result<()> {
        if Self::queue_known(conn, queue_name)? {
            Ok(())
        } else {
            Err(Error::NotFound(format!("queue '{}' not found", queue_name)))
        }
    }
}

impl QueueBackend for SQLiteQueueBackend {
    fn create_queue(&mut self, name: &str) -> Result<()> {
        let conn = self
            .conn
            .lock()
            .map_err(|e| Error::internal(format!("sqlite mutex poisoned: {}", e)))?;

        let changed = conn
            .execute(
                "INSERT OR IGNORE INTO queue_metadata (queue_name) VALUES (?1)",
                params![name],
            )
            .map_err(|e| Error::internal(format!("sqlite create queue error: {}", e)))?;
        if changed == 0 {
            return Err(Error::AlreadyExists(format!(
                "queue '{}' already exists",
                name
            )));
        }
        Ok(())
    }

    fn remove_queue(&mut self, name: &str) -> Result<()> {
        let conn = self
            .conn
            .lock()
            .map_err(|e| Error::internal(format!("sqlite mutex poisoned: {}", e)))?;

        let pattern = format!("{}/%", name);
        let deleted_messages = conn
            .execute(
                "DELETE FROM queue_messages WHERE queue_name = ?1 OR queue_name LIKE ?2",
                params![name, pattern],
            )
            .map_err(|e| Error::internal(format!("sqlite remove messages error: {}", e)))?;
        let deleted_meta = conn
            .execute(
                "DELETE FROM queue_metadata WHERE queue_name = ?1 OR queue_name LIKE ?2",
                params![name, pattern],
            )
            .map_err(|e| Error::internal(format!("sqlite remove metadata error: {}", e)))?;

        if deleted_messages == 0 && deleted_meta == 0 {
            return Err(Error::NotFound(format!("queue '{}' not found", name)));
        }
        Ok(())
    }

    fn queue_exists(&self, name: &str) -> bool {
        let conn = match self.conn.lock() {
            Ok(conn) => conn,
            Err(_) => return false,
        };
        Self::queue_known(&conn, name).unwrap_or(false)
    }

    fn list_queues(&self, prefix: &str) -> Vec<String> {
        let conn = match self.conn.lock() {
            Ok(conn) => conn,
            Err(_) => return Vec::new(),
        };

        if prefix.is_empty() {
            let mut stmt =
                match conn.prepare("SELECT queue_name FROM queue_metadata ORDER BY queue_name") {
                    Ok(stmt) => stmt,
                    Err(_) => return Vec::new(),
                };
            let rows = match stmt.query_map([], |row| row.get::<_, String>(0)) {
                Ok(rows) => rows,
                Err(_) => return Vec::new(),
            };
            return rows.filter_map(|row| row.ok()).collect();
        }

        let mut stmt = match conn.prepare(
            "SELECT queue_name FROM queue_metadata
             WHERE queue_name = ?1 OR queue_name LIKE ?2
             ORDER BY queue_name",
        ) {
            Ok(stmt) => stmt,
            Err(_) => return Vec::new(),
        };
        let pattern = format!("{}/%", prefix);
        let rows = match stmt.query_map(params![prefix, pattern], |row| row.get::<_, String>(0)) {
            Ok(rows) => rows,
            Err(_) => return Vec::new(),
        };
        rows.filter_map(|row| row.ok()).collect()
    }

    fn enqueue(&mut self, queue_name: &str, msg: Message) -> Result<()> {
        msg.validate_keys()?;
        let conn = self
            .conn
            .lock()
            .map_err(|e| Error::internal(format!("sqlite mutex poisoned: {}", e)))?;

        Self::require_queue_exists(&conn, queue_name)?;
        let stored = serde_json::to_string(&StoredMessage::from_message(&msg))?;
        let changed = conn
            .execute(
                "INSERT INTO queue_messages (queue_name, message_id, data, timestamp, serial_key, schedule_seq, status)
             SELECT ?1, ?2, ?3, ?4, ?5, ?6, 'pending'
             WHERE NOT EXISTS (
                 SELECT 1 FROM queue_messages WHERE queue_name = ?1 AND message_id = ?2
             )",
                params![queue_name, msg.id, stored, unix_secs(msg.timestamp), msg.serial_key,
                    self.next_schedule_seq.fetch_add(1, Ordering::Relaxed)],
            )
            .map_err(|e| Error::internal(format!("sqlite enqueue error: {}", e)))?;
        if changed == 0 {
            return Err(Error::AlreadyExists(format!(
                "message '{}' already exists",
                msg.id
            )));
        }
        Ok(())
    }

    fn dequeue(&mut self, queue_name: &str) -> Result<Option<Message>> {
        let mut conn = self
            .conn
            .lock()
            .map_err(|e| Error::internal(format!("sqlite mutex poisoned: {}", e)))?;

        Self::require_queue_exists(&conn, queue_name)?;

        let tx = conn
            .transaction()
            .map_err(|e| Error::internal(format!("sqlite transaction begin error: {}", e)))?;

        let row = tx.query_row(
            "SELECT candidate.id, candidate.data FROM queue_messages AS candidate
             WHERE candidate.queue_name = ?1 AND candidate.status = 'pending'
               AND (candidate.serial_key IS NULL OR NOT EXISTS (
                   SELECT 1 FROM queue_messages AS earlier
                   WHERE earlier.queue_name = candidate.queue_name
                     AND earlier.serial_key = candidate.serial_key
                     AND earlier.id < candidate.id
               ))
             ORDER BY candidate.schedule_seq LIMIT 1",
            params![queue_name],
            |row| Ok((row.get::<_, i64>(0)?, read_sqlite_text(row, 1)?)),
        );

        let (id, raw_data) = match row {
            Ok(row) => row,
            Err(rusqlite::Error::QueryReturnedNoRows) => return Ok(None),
            Err(e) => {
                return Err(Error::internal(format!(
                    "sqlite dequeue query error: {}",
                    e
                )))
            }
        };

        tx.execute(
            "UPDATE queue_messages
             SET status = 'processing', processing_started_at = ?1
             WHERE id = ?2",
            params![Utc::now().timestamp(), id],
        )
        .map_err(|e| Error::internal(format!("sqlite mark processing error: {}", e)))?;

        tx.commit()
            .map_err(|e| Error::internal(format!("sqlite transaction commit error: {}", e)))?;

        let stored: StoredMessage =
            serde_json::from_str(&raw_data).map_err(|e| Error::Serialization(e.to_string()))?;
        Ok(Some(stored.into_message()))
    }

    fn peek(&self, queue_name: &str) -> Result<Option<Message>> {
        let conn = self
            .conn
            .lock()
            .map_err(|e| Error::internal(format!("sqlite mutex poisoned: {}", e)))?;

        Self::require_queue_exists(&conn, queue_name)?;

        let raw_data = match conn.query_row(
            "SELECT data FROM queue_messages
             WHERE queue_name = ?1 AND status = 'pending'
             ORDER BY schedule_seq LIMIT 1",
            params![queue_name],
            |row| read_sqlite_text(row, 0),
        ) {
            Ok(data) => data,
            Err(rusqlite::Error::QueryReturnedNoRows) => return Ok(None),
            Err(e) => return Err(Error::internal(format!("sqlite peek query error: {}", e))),
        };

        let stored: StoredMessage = serde_json::from_str(&raw_data)?;
        Ok(Some(stored.into_message()))
    }

    fn size(&self, queue_name: &str) -> Result<usize> {
        let conn = self
            .conn
            .lock()
            .map_err(|e| Error::internal(format!("sqlite mutex poisoned: {}", e)))?;

        Self::require_queue_exists(&conn, queue_name)?;

        let count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM queue_messages
                 WHERE queue_name = ?1 AND status = 'pending'",
                params![queue_name],
                |row| row.get(0),
            )
            .map_err(|e| Error::internal(format!("sqlite size query error: {}", e)))?;
        Ok(count as usize)
    }

    fn status(&self, queue_name: &str) -> Result<QueueState> {
        let conn = self
            .conn
            .lock()
            .map_err(|e| Error::internal(format!("sqlite mutex poisoned: {}", e)))?;

        Self::require_queue_exists(&conn, queue_name)?;

        let (pending, processing): (i64, i64) = conn
            .query_row(
                "SELECT
                    COALESCE(SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END), 0),
                    COALESCE(SUM(CASE WHEN status = 'processing' THEN 1 ELSE 0 END), 0)
                 FROM queue_messages WHERE queue_name = ?1",
                params![queue_name],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .map_err(|e| Error::internal(format!("sqlite status query error: {}", e)))?;
        Ok(QueueState {
            pending: pending as usize,
            processing: processing as usize,
        })
    }

    fn list_unacked(&self, queue_name: &str) -> Result<Vec<Message>> {
        let conn = self
            .conn
            .lock()
            .map_err(|e| Error::internal(format!("sqlite mutex poisoned: {}", e)))?;

        Self::require_queue_exists(&conn, queue_name)?;
        let mut stmt = conn
            .prepare(
                "SELECT data FROM queue_messages
                 WHERE queue_name = ?1 AND status IN ('pending', 'processing')
                 ORDER BY id",
            )
            .map_err(|e| Error::internal(format!("sqlite list unacked prepare error: {}", e)))?;
        let rows = stmt
            .query_map(params![queue_name], |row| read_sqlite_text(row, 0))
            .map_err(|e| Error::internal(format!("sqlite list unacked query error: {}", e)))?;

        let mut messages = Vec::new();
        for row in rows {
            let raw_data =
                row.map_err(|e| Error::internal(format!("sqlite list unacked row error: {}", e)))?;
            let stored: StoredMessage = serde_json::from_str(&raw_data)?;
            messages.push(stored.into_message());
        }
        Ok(messages)
    }

    fn clear(&mut self, queue_name: &str) -> Result<()> {
        let conn = self
            .conn
            .lock()
            .map_err(|e| Error::internal(format!("sqlite mutex poisoned: {}", e)))?;

        Self::require_queue_exists(&conn, queue_name)?;

        conn.execute(
            "DELETE FROM queue_messages WHERE queue_name = ?1",
            params![queue_name],
        )
        .map_err(|e| Error::internal(format!("sqlite clear error: {}", e)))?;
        Ok(())
    }

    fn get_last_enqueue_time(&self, queue_name: &str) -> Result<SystemTime> {
        let conn = self
            .conn
            .lock()
            .map_err(|e| Error::internal(format!("sqlite mutex poisoned: {}", e)))?;

        Self::require_queue_exists(&conn, queue_name)?;

        let timestamp: Option<i64> = conn
            .query_row(
                "SELECT MAX(timestamp) FROM queue_messages
                 WHERE queue_name = ?1 AND status = 'pending'",
                params![queue_name],
                |row| row.get(0),
            )
            .map_err(|e| Error::internal(format!("sqlite last enqueue query error: {}", e)))?;

        Ok(timestamp
            .map(|secs| {
                let secs_u64 = if secs <= 0 { 0 } else { secs as u64 };
                UNIX_EPOCH + Duration::from_secs(secs_u64)
            })
            .unwrap_or(SystemTime::UNIX_EPOCH))
    }

    fn ack(&mut self, queue_name: &str, msg_id: &str) -> Result<bool> {
        let conn = self
            .conn
            .lock()
            .map_err(|e| Error::internal(format!("sqlite mutex poisoned: {}", e)))?;

        Self::require_queue_exists(&conn, queue_name)?;

        let changed = conn
            .execute(
                "DELETE FROM queue_messages
                 WHERE queue_name = ?1 AND message_id = ?2 AND status = 'processing'",
                params![queue_name, msg_id],
            )
            .map_err(|e| Error::internal(format!("sqlite ack error: {}", e)))?;
        Ok(changed > 0)
    }

    fn retry(&mut self, queue_name: &str, msg_id: &str) -> Result<bool> {
        let conn = self
            .conn
            .lock()
            .map_err(|e| Error::internal(format!("sqlite mutex poisoned: {e}")))?;
        Self::require_queue_exists(&conn, queue_name)?;
        let changed = conn
            .execute(
                "UPDATE queue_messages SET status = 'pending', processing_started_at = NULL,
                 schedule_seq = ?3
             WHERE queue_name = ?1 AND message_id = ?2 AND status = 'processing'",
                params![
                    queue_name,
                    msg_id,
                    self.next_schedule_seq.fetch_add(1, Ordering::Relaxed)
                ],
            )
            .map_err(|e| Error::internal(format!("sqlite retry error: {e}")))?;
        Ok(changed > 0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;
    use tempfile::TempDir;

    fn keyed_message(id: &str, serial: bool, key: &str) -> Message {
        let mut message = Message::new(id.as_bytes().to_vec());
        message.id = id.to_string();
        if serial {
            message.serial_key = Some(key.to_string());
        } else {
            message.group_key = Some(key.to_string());
        }
        message
    }

    #[test]
    fn test_memory_duplicate_ids_follow_ack_retry_and_clear() {
        for message in [
            Message::new(b"plain".to_vec()),
            keyed_message("group", false, "A"),
            keyed_message("serial", true, "A"),
        ] {
            let mut backend = memory_backend_with_queue("test");
            backend.create_queue("other").unwrap();
            backend.enqueue("test", message.clone()).unwrap();
            backend.enqueue("other", message.clone()).unwrap();

            assert!(matches!(
                backend.enqueue("test", message.clone()),
                Err(Error::AlreadyExists(_))
            ));
            assert!(!backend.ack("test", &message.id).unwrap());
            assert!(backend.enqueue("test", message.clone()).is_err());

            let delivery = backend.dequeue("test").unwrap().unwrap();
            assert_eq!(delivery.id, message.id);
            assert!(backend.enqueue("test", message.clone()).is_err());
            assert!(backend.retry("test", &message.id).unwrap());
            assert!(!backend.retry("test", &message.id).unwrap());
            assert!(!backend.ack("test", &message.id).unwrap());
            assert!(backend.enqueue("test", message.clone()).is_err());

            let retried = backend.dequeue("test").unwrap().unwrap();
            assert_eq!(retried.id, message.id);
            assert_eq!(retried.data, message.data);
            assert_eq!(retried.timestamp, message.timestamp);
            assert!(backend.ack("test", &message.id).unwrap());
            backend.enqueue("test", message.clone()).unwrap();
            assert!(!backend.ack("test", &message.id).unwrap());
            assert!(backend.enqueue("test", message.clone()).is_err());

            backend.dequeue("test").unwrap().unwrap();
            backend
                .enqueue("test", Message::new(b"pending".to_vec()))
                .unwrap();
            backend.clear("test").unwrap();
            assert!(backend.list_unacked("test").unwrap().is_empty());
            backend.enqueue("test", message.clone()).unwrap();
            backend.dequeue("test").unwrap().unwrap();
            backend.remove_queue("test").unwrap();
            backend.create_queue("test").unwrap();
            backend.enqueue("test", message.clone()).unwrap();
            assert!(backend.enqueue("other", message).is_err());
        }
    }

    #[test]
    fn test_memory_invalid_enqueue_does_not_reserve_message_id() {
        let mut backend = memory_backend_with_queue("test");
        let message = Message::new(b"valid".to_vec());
        for (group_key, serial_key) in [
            (Some("A".to_string()), Some("A".to_string())),
            (None, Some(String::new())),
            (Some("A".repeat(4097)), None),
        ] {
            let mut invalid = message.clone();
            invalid.group_key = group_key;
            invalid.serial_key = serial_key;
            assert!(backend.enqueue("test", invalid).is_err());
        }
        backend.enqueue("test", message.clone()).unwrap();
        assert_eq!(backend.dequeue("test").unwrap().unwrap().id, message.id);
    }

    #[test]
    fn test_backends_mix_message_keys_and_release_only_on_ack() {
        let (_directory, _path, sqlite) = sqlite_backend();
        for mut backend in [
            Box::new(MemoryBackend::new()) as Box<dyn QueueBackend>,
            Box::new(sqlite),
        ] {
            backend.create_queue("mixed").unwrap();
            backend.create_queue("other").unwrap();
            for message in [
                keyed_message("s1", true, "用户:A"),
                keyed_message("s2", true, "用户:A"),
                keyed_message("g1", false, "用户:A"),
                keyed_message("g2", false, "用户:A"),
                Message::new(b"plain".to_vec()),
                keyed_message("different", true, "B"),
            ] {
                backend.enqueue("mixed", message).unwrap();
            }
            backend
                .enqueue("other", keyed_message("s1", true, "用户:A"))
                .unwrap();
            assert_eq!(backend.dequeue("other").unwrap().unwrap().id, "s1");
            for (expected, group, serial) in [
                ("s1", None, Some("用户:A")),
                ("g1", Some("用户:A"), None),
                ("g2", Some("用户:A"), None),
                ("plain", None, None),
                ("different", None, Some("B")),
            ] {
                let message = backend.dequeue("mixed").unwrap().unwrap();
                assert_eq!(message.data, expected.as_bytes());
                assert_eq!(message.group_key.as_deref(), group);
                assert_eq!(message.serial_key.as_deref(), serial);
            }
            assert_eq!(
                backend.status("mixed").unwrap(),
                QueueState {
                    pending: 1,
                    processing: 5
                }
            );
            assert_eq!(backend.peek("mixed").unwrap().unwrap().id, "s2");
            assert_eq!(backend.list_unacked("mixed").unwrap().len(), 6);
            assert!(backend.dequeue("mixed").unwrap().is_none());
            assert!(!backend.ack("mixed", "s2").unwrap());
            assert!(backend.ack("mixed", "g1").unwrap());
            assert!(backend.dequeue("mixed").unwrap().is_none());
            assert!(backend.ack("mixed", "s1").unwrap());
            let second = backend.dequeue("mixed").unwrap().unwrap();
            assert_eq!(second.id, "s2");
            backend
                .enqueue("mixed", keyed_message("s3", true, "用户:A"))
                .unwrap();
            assert!(!backend.ack("mixed", "s1").unwrap());
            assert!(backend.dequeue("mixed").unwrap().is_none());
            for message in [
                keyed_message("s2", true, "用户:A"),
                keyed_message("empty", false, ""),
                keyed_message("long", true, &"a".repeat(4097)),
            ] {
                assert!(backend.enqueue("mixed", message).is_err());
            }
            let mut both = keyed_message("both", true, "A");
            both.group_key = Some("A".to_string());
            assert!(backend.enqueue("mixed", both).is_err());
            backend.clear("mixed").unwrap();
            assert_eq!(
                backend.status("mixed").unwrap(),
                QueueState {
                    pending: 0,
                    processing: 0
                }
            );
            backend
                .enqueue("mixed", keyed_message("s1", true, "用户:A"))
                .unwrap();
            assert_eq!(backend.dequeue("mixed").unwrap().unwrap().id, "s1");
            backend.remove_queue("mixed").unwrap();
            backend.create_queue("mixed").unwrap();
            assert!(backend.list_unacked("mixed").unwrap().is_empty());
        }
    }

    #[test]
    fn test_sqlite_serial_restart_replays_original_before_later_messages() {
        let (_directory, path, mut backend) = sqlite_backend();
        backend.create_queue("mixed").unwrap();
        for message in [
            keyed_message("original", true, "A"),
            keyed_message("later", true, "A"),
            keyed_message("group", false, "A"),
        ] {
            backend.enqueue("mixed", message).unwrap();
        }
        assert_eq!(backend.dequeue("mixed").unwrap().unwrap().id, "original");
        assert_eq!(backend.dequeue("mixed").unwrap().unwrap().id, "group");
        assert!(backend.dequeue("mixed").unwrap().is_none());
        drop(backend);
        let mut backend = SQLiteQueueBackend::open(&path, SQLiteQueueOptions::default()).unwrap();
        let original = backend.dequeue("mixed").unwrap().unwrap();
        assert_eq!(original.id, "original");
        assert_eq!(original.serial_key.as_deref(), Some("A"));
        assert_eq!(backend.dequeue("mixed").unwrap().unwrap().id, "group");
        assert!(backend.dequeue("mixed").unwrap().is_none());
        backend.ack("mixed", "original").unwrap();
        assert_eq!(backend.dequeue("mixed").unwrap().unwrap().id, "later");
        assert!(!backend.ack("mixed", "original").unwrap());
    }

    /// Create an in-memory backend with a test queue.
    fn memory_backend_with_queue(queue: &str) -> MemoryBackend {
        let mut backend = MemoryBackend::new();
        backend.create_queue(queue).unwrap();
        backend
    }

    /// Create a SQLite backend backed by a temporary database file.
    fn sqlite_backend() -> (TempDir, String, SQLiteQueueBackend) {
        let dir = tempdir().unwrap();
        let db_path = dir.path().join("queue.db");
        let db_path_str = db_path.to_str().unwrap().to_string();
        let backend =
            SQLiteQueueBackend::open(&db_path_str, SQLiteQueueOptions::default()).unwrap();
        (dir, db_path_str, backend)
    }

    #[test]
    fn test_create_queue() {
        let mut backend = MemoryBackend::new();

        backend.create_queue("test").unwrap();
        assert!(backend.queue_exists("test"));

        // Creating duplicate should fail
        let result = backend.create_queue("test");
        assert!(result.is_err());
    }

    #[test]
    fn test_remove_queue() {
        let mut backend = memory_backend_with_queue("test");
        backend.remove_queue("test").unwrap();
        assert!(!backend.queue_exists("test"));

        // Removing non-existent queue should fail
        let result = backend.remove_queue("test");
        assert!(result.is_err());
    }

    #[test]
    fn test_list_queues() {
        let mut backend = MemoryBackend::new();

        backend.create_queue("queue1").unwrap();
        backend.create_queue("queue2").unwrap();
        backend.create_queue("logs/errors").unwrap();

        let all = backend.list_queues("");
        assert_eq!(all.len(), 3);

        let logs = backend.list_queues("logs");
        assert_eq!(logs.len(), 1);
        assert_eq!(logs[0], "logs/errors");
    }

    #[test]
    fn test_enqueue_dequeue() {
        let mut backend = memory_backend_with_queue("test");

        let msg1 = Message::new(b"message 1".to_vec());
        let msg2 = Message::new(b"message 2".to_vec());

        backend.enqueue("test", msg1.clone()).unwrap();
        backend.enqueue("test", msg2.clone()).unwrap();

        assert_eq!(backend.size("test").unwrap(), 2);

        let dequeued1 = backend.dequeue("test").unwrap().unwrap();
        assert_eq!(dequeued1.data, b"message 1");
        assert_eq!(
            backend.status("test").unwrap(),
            QueueState {
                pending: 1,
                processing: 1,
            }
        );
        assert!(backend.ack("test", &dequeued1.id).unwrap());

        let dequeued2 = backend.dequeue("test").unwrap().unwrap();
        assert_eq!(dequeued2.data, b"message 2");
        assert!(backend.ack("test", &dequeued2.id).unwrap());

        assert_eq!(backend.size("test").unwrap(), 0);
        assert_eq!(
            backend.status("test").unwrap(),
            QueueState {
                pending: 0,
                processing: 0,
            }
        );
        assert!(backend.dequeue("test").unwrap().is_none());
    }

    #[test]
    fn test_peek() {
        let mut backend = memory_backend_with_queue("test");

        let msg = Message::new(b"test message".to_vec());
        backend.enqueue("test", msg.clone()).unwrap();

        let peeked1 = backend.peek("test").unwrap().unwrap();
        assert_eq!(peeked1.data, b"test message");

        let peeked2 = backend.peek("test").unwrap().unwrap();
        assert_eq!(peeked2.data, b"test message");

        // Size should still be 1
        assert_eq!(backend.size("test").unwrap(), 1);
    }

    #[test]
    fn test_clear() {
        let mut backend = memory_backend_with_queue("test");

        backend
            .enqueue("test", Message::new(b"msg1".to_vec()))
            .unwrap();
        backend
            .enqueue("test", Message::new(b"msg2".to_vec()))
            .unwrap();

        assert_eq!(backend.size("test").unwrap(), 2);

        backend.clear("test").unwrap();
        assert_eq!(backend.size("test").unwrap(), 0);
    }

    #[test]
    fn test_multi_queue_isolation() {
        let mut backend = MemoryBackend::new();
        backend.create_queue("queue1").unwrap();
        backend.create_queue("queue2").unwrap();

        backend
            .enqueue("queue1", Message::new(b"msg1".to_vec()))
            .unwrap();
        backend
            .enqueue("queue2", Message::new(b"msg2".to_vec()))
            .unwrap();

        assert_eq!(backend.size("queue1").unwrap(), 1);
        assert_eq!(backend.size("queue2").unwrap(), 1);

        let msg1 = backend.dequeue("queue1").unwrap().unwrap();
        assert_eq!(msg1.data, b"msg1");

        // queue2 should be unaffected
        assert_eq!(backend.size("queue2").unwrap(), 1);
    }

    #[test]
    fn test_operations_on_nonexistent_queue() {
        let mut backend = MemoryBackend::new();

        assert!(backend
            .enqueue("nonexistent", Message::new(b"data".to_vec()))
            .is_err());
        assert!(backend.dequeue("nonexistent").is_err());
        assert!(backend.peek("nonexistent").is_err());
        assert!(backend.size("nonexistent").is_err());
        assert!(backend.status("nonexistent").is_err());
        assert!(backend.clear("nonexistent").is_err());
    }

    #[test]
    fn test_sqlite_backend_basic_flow() {
        let (_dir, _db_path, mut backend) = sqlite_backend();

        backend.create_queue("test").unwrap();
        let msg1 = Message::new(b"message 1".to_vec());
        let msg2 = Message::new(b"message 2".to_vec());
        let msg1_id = msg1.id.clone();

        backend.enqueue("test", msg1).unwrap();
        backend.enqueue("test", msg2).unwrap();
        assert_eq!(
            backend.status("test").unwrap(),
            QueueState {
                pending: 2,
                processing: 0,
            }
        );

        let first = backend.dequeue("test").unwrap().unwrap();
        assert_eq!(first.data, b"message 1");
        assert_eq!(backend.size("test").unwrap(), 1);
        assert_eq!(
            backend.status("test").unwrap(),
            QueueState {
                pending: 1,
                processing: 1,
            }
        );
        let unacked = backend.list_unacked("test").unwrap();
        assert_eq!(unacked.len(), 2);
        assert_eq!(unacked[0].id, msg1_id);
        assert!(backend.ack("test", &msg1_id).unwrap());
        assert_eq!(
            backend.status("test").unwrap(),
            QueueState {
                pending: 1,
                processing: 0,
            }
        );

        let second = backend.dequeue("test").unwrap().unwrap();
        assert_eq!(second.data, b"message 2");
    }

    #[test]
    fn test_sqlite_backend_preserves_non_utf8_payload_bytes() {
        let (_dir, _db_path, mut backend) = sqlite_backend();

        backend.create_queue("test").unwrap();
        let payload = vec![0xff, 0x00, 0x80, b'a'];
        backend
            .enqueue("test", Message::new(payload.clone()))
            .unwrap();

        let peeked = backend.peek("test").unwrap().unwrap();
        assert_eq!(peeked.data, payload);

        let dequeued = backend.dequeue("test").unwrap().unwrap();
        assert_eq!(dequeued.data, payload);
    }

    #[test]
    fn test_stored_message_preserves_timestamp_validity() {
        let missing = StoredMessage {
            id: "missing".to_string(),
            data: b"payload".to_vec(),
            timestamp: None,
            group_key: None,
            serial_key: None,
        }
        .into_message();
        assert!(!missing.timestamp_valid);

        let invalid = StoredMessage {
            id: "invalid".to_string(),
            data: b"payload".to_vec(),
            timestamp: Some(serde_json::Value::String("not-a-time".to_string())),
            group_key: None,
            serial_key: None,
        }
        .into_message();
        assert!(!invalid.timestamp_valid);

        let valid = StoredMessage {
            id: "valid".to_string(),
            data: b"payload".to_vec(),
            timestamp: Some(serde_json::Value::Number(1234.into())),
            group_key: None,
            serial_key: None,
        }
        .into_message();
        assert!(valid.timestamp_valid);
        assert_eq!(unix_secs(valid.timestamp), 1234);

        let precise_timestamp = UNIX_EPOCH + Duration::new(1_234, 567_890_123);
        let precise = StoredMessage::from_message(&Message {
            id: "precise".to_string(),
            data: b"payload".to_vec(),
            timestamp: precise_timestamp,
            timestamp_valid: true,
            group_key: None,
            serial_key: None,
        })
        .into_message();
        assert!(precise.timestamp_valid);
        assert_eq!(precise.timestamp, precise_timestamp);
    }

    #[test]
    fn test_sqlite_backend_preserves_subsecond_enqueue_timestamp() {
        let (_dir, _db_path, mut backend) = sqlite_backend();
        backend.create_queue("test").unwrap();

        let timestamp = UNIX_EPOCH + Duration::new(1_700_000_000, 987_654_321);
        backend
            .enqueue(
                "test",
                Message {
                    id: "precise".to_string(),
                    data: b"payload".to_vec(),
                    timestamp,
                    timestamp_valid: true,
                    group_key: None,
                    serial_key: None,
                },
            )
            .unwrap();

        let dequeued = backend.dequeue("test").unwrap().unwrap();
        assert_eq!(dequeued.timestamp, timestamp);
    }

    #[test]
    fn test_sqlite_backend_recover_stale() {
        let (_dir, db_path_str, _backend) = sqlite_backend();

        let msg_id = {
            let mut backend =
                SQLiteQueueBackend::open(&db_path_str, SQLiteQueueOptions::default()).unwrap();
            backend.create_queue("test").unwrap();
            let msg = Message::new(b"recover me".to_vec());
            let msg_id = msg.id.clone();
            backend.enqueue("test", msg).unwrap();
            let dequeued = backend.dequeue("test").unwrap().unwrap();
            assert_eq!(dequeued.id, msg_id);
            msg_id
        };

        let mut reopened =
            SQLiteQueueBackend::open(&db_path_str, SQLiteQueueOptions::default()).unwrap();
        let recovered = reopened.dequeue("test").unwrap().unwrap();
        assert_eq!(recovered.id, msg_id);
        assert_eq!(recovered.data, b"recover me");
    }

    #[test]
    fn test_sqlite_backend_dequeue_legacy_go_row() {
        let (_dir, db_path_str, mut backend) = sqlite_backend();

        backend.create_queue("Semantic").unwrap();
        drop(backend);

        let conn = Connection::open(&db_path_str).unwrap();
        conn.execute(
            "INSERT INTO queue_messages (queue_name, message_id, data, timestamp, status)
             VALUES (?1, ?2, ?3, ?4, 'pending')",
            params![
                "Semantic",
                "legacy-msg-id",
                r#"{"id":"legacy-msg-id","data":"{\"id\":\"semantic-inner\",\"uri\":\"viking://resources/demo\",\"context_type\":\"resource\",\"status\":\"pending\",\"timestamp\":1776411350,\"recursive\":true,\"account_id\":\"default\",\"user_id\":\"default\",\"peer_id\":\"default\",\"role\":\"root\",\"skip_vectorization\":false,\"telemetry_id\":\"tm_demo\",\"target_uri\":null,\"lifecycle_lock_handle_id\":\"lock-demo\",\"is_code_repo\":false,\"changes\":null}","timestamp":"2026-04-17T15:37:39.287855+08:00"}"#,
                1776411459_i64,
            ],
        )
        .unwrap();
        drop(conn);

        let mut reopened =
            SQLiteQueueBackend::open(&db_path_str, SQLiteQueueOptions::default()).unwrap();
        let msg = reopened.dequeue("Semantic").unwrap().unwrap();
        let payload = String::from_utf8(msg.data).unwrap();
        assert!(payload.contains("\"uri\":\"viking://resources/demo\""));
    }

    #[test]
    fn test_sqlite_backend_backfills_queue_metadata_from_legacy_rows() {
        let dir = tempdir().unwrap();
        let db_path = dir.path().join("legacy.db");
        let db_path_str = db_path.to_str().unwrap().to_string();

        let conn = Connection::open(&db_path_str).unwrap();
        conn.execute_batch(
            r#"
            CREATE TABLE queue_metadata (
                queue_name TEXT PRIMARY KEY,
                created_at INTEGER DEFAULT (strftime('%s', 'now')),
                last_updated INTEGER DEFAULT (strftime('%s', 'now'))
            );
            CREATE TABLE queue_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                queue_name TEXT NOT NULL,
                message_id TEXT NOT NULL,
                data TEXT NOT NULL,
                timestamp INTEGER NOT NULL
            );
            INSERT INTO queue_messages (queue_name, message_id, data, timestamp)
            VALUES ('legacy/semantic', 'legacy-id', '{"id":"legacy-id","data":"payload"}', 1776411459);
            "#,
        )
        .unwrap();
        drop(conn);

        let backend =
            SQLiteQueueBackend::open(&db_path_str, SQLiteQueueOptions::default()).unwrap();
        assert!(backend.queue_exists("legacy/semantic"));
        assert_eq!(backend.list_queues("legacy"), vec!["legacy/semantic"]);
        drop(backend);

        let conn = Connection::open(&db_path_str).unwrap();
        let migrated_indexes = conn
            .prepare(
                "SELECT name FROM sqlite_master
                 WHERE type = 'index'
                   AND name IN ('idx_queue_message_id', 'idx_queue_status')
                 ORDER BY name",
            )
            .unwrap()
            .query_map([], |row| row.get::<_, String>(0))
            .unwrap()
            .collect::<std::result::Result<Vec<_>, _>>()
            .unwrap();
        assert_eq!(
            migrated_indexes,
            vec![
                "idx_queue_message_id".to_string(),
                "idx_queue_status".to_string()
            ]
        );
    }

    #[test]
    fn test_sqlite_backend_enables_full_auto_vacuum_for_new_databases() {
        let (_dir, db_path, _backend) = sqlite_backend();

        let conn = Connection::open(db_path).unwrap();
        let auto_vacuum: i64 = conn
            .query_row("PRAGMA auto_vacuum", [], |row| row.get(0))
            .unwrap();
        assert_eq!(auto_vacuum, 1);
    }

    #[test]
    fn test_sqlite_backend_does_not_rewrite_existing_auto_vacuum_mode() {
        let dir = tempdir().unwrap();
        let db_path = dir.path().join("legacy-auto-vacuum.db");
        let db_path_str = db_path.to_str().unwrap().to_string();

        let conn = Connection::open(&db_path_str).unwrap();
        conn.execute_batch(
            r#"
            PRAGMA auto_vacuum=NONE;
            CREATE TABLE queue_metadata (
                queue_name TEXT PRIMARY KEY,
                created_at INTEGER DEFAULT (strftime('%s', 'now')),
                last_updated INTEGER DEFAULT (strftime('%s', 'now'))
            );
            CREATE TABLE queue_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                queue_name TEXT NOT NULL,
                message_id TEXT NOT NULL,
                data TEXT NOT NULL,
                timestamp INTEGER NOT NULL
            );
            "#,
        )
        .unwrap();
        drop(conn);

        let _backend =
            SQLiteQueueBackend::open(&db_path_str, SQLiteQueueOptions::default()).unwrap();

        let conn = Connection::open(&db_path_str).unwrap();
        let auto_vacuum: i64 = conn
            .query_row("PRAGMA auto_vacuum", [], |row| row.get(0))
            .unwrap();
        assert_eq!(auto_vacuum, 0);
    }

    #[test]
    fn test_sqlite_backend_operations_on_nonexistent_queue() {
        let (_dir, _db_path, mut backend) = sqlite_backend();

        assert!(backend
            .enqueue("nonexistent", Message::new(b"data".to_vec()))
            .is_err());
        assert!(backend.dequeue("nonexistent").is_err());
        assert!(backend.peek("nonexistent").is_err());
        assert!(backend.size("nonexistent").is_err());
        assert!(backend.clear("nonexistent").is_err());
        assert!(backend.get_last_enqueue_time("nonexistent").is_err());
        assert!(backend.ack("nonexistent", "msg-id").is_err());
    }
}
