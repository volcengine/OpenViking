//! Queue Backend Abstraction
//!
//! This module provides a pluggable backend system for QueueFS, allowing different
//! storage implementations (memory, SQLite, etc.) while maintaining a consistent interface.

use crate::core::errors::{Error, Result};
use chrono::{DateTime, Utc};
use rusqlite::{params, types::ValueRef, Connection, Row};
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, VecDeque};
use std::sync::Mutex;
use std::time::{Duration, UNIX_EPOCH};
use std::time::SystemTime;
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
}

impl Message {
    /// Create a new message with the given data
    pub fn new(data: Vec<u8>) -> Self {
        Self {
            id: Uuid::new_v4().to_string(),
            data,
            timestamp: SystemTime::now(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct StoredMessage {
    id: String,
    data: String,
    #[serde(default)]
    timestamp: Option<serde_json::Value>,
}

impl StoredMessage {
    fn from_message(msg: &Message) -> Self {
        Self {
            id: msg.id.clone(),
            data: String::from_utf8_lossy(&msg.data).to_string(),
            // Prefer unix seconds for compatibility with older queue.db producers.
            timestamp: Some(serde_json::Value::Number(unix_secs(msg.timestamp).into())),
        }
    }

    fn into_message(self) -> Message {
        Message {
            id: self.id,
            data: self.data.into_bytes(),
            timestamp: parse_stored_timestamp(self.timestamp),
        }
    }
}

fn parse_stored_timestamp(raw: Option<serde_json::Value>) -> SystemTime {
    match raw {
        Some(serde_json::Value::String(ts)) => DateTime::parse_from_rfc3339(&ts)
            .map(|dt| {
                let secs = dt.timestamp();
                let secs_u64 = if secs <= 0 { 0 } else { secs as u64 };
                UNIX_EPOCH + Duration::from_secs(secs_u64)
            })
            .unwrap_or_else(|_| SystemTime::now()),
        Some(serde_json::Value::Number(num)) => num
            .as_i64()
            .map(|secs| {
                let secs_u64 = if secs <= 0 { 0 } else { secs as u64 };
                UNIX_EPOCH + Duration::from_secs(secs_u64)
            })
            .unwrap_or_else(SystemTime::now),
        _ => SystemTime::now(),
    }
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

    /// Clear all messages from the queue
    fn clear(&mut self, queue_name: &str) -> Result<()>;

    /// Get the last enqueue time for the queue
    fn get_last_enqueue_time(&self, queue_name: &str) -> Result<SystemTime>;

    /// Acknowledge (delete) a message by ID
    fn ack(&mut self, queue_name: &str, msg_id: &str) -> Result<bool>;
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
    last_enqueue_time: SystemTime,
}

impl Queue {
    fn new() -> Self {
        Self {
            messages: VecDeque::new(),
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
            return Err(Error::AlreadyExists(format!("queue '{}' already exists", name)));
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
        let queue = self.queues.get_mut(queue_name).ok_or_else(|| {
            Error::NotFound(format!("queue '{}' not found", queue_name))
        })?;

        queue.last_enqueue_time = SystemTime::now();
        queue.messages.push_back(msg);
        Ok(())
    }

    fn dequeue(&mut self, queue_name: &str) -> Result<Option<Message>> {
        let queue = self.queues.get_mut(queue_name).ok_or_else(|| {
            Error::NotFound(format!("queue '{}' not found", queue_name))
        })?;

        Ok(queue.messages.pop_front())
    }

    fn peek(&self, queue_name: &str) -> Result<Option<Message>> {
        let queue = self.queues.get(queue_name).ok_or_else(|| {
            Error::NotFound(format!("queue '{}' not found", queue_name))
        })?;

        Ok(queue.messages.front().cloned())
    }

    fn size(&self, queue_name: &str) -> Result<usize> {
        let queue = self.queues.get(queue_name).ok_or_else(|| {
            Error::NotFound(format!("queue '{}' not found", queue_name))
        })?;

        Ok(queue.messages.len())
    }

    fn clear(&mut self, queue_name: &str) -> Result<()> {
        let queue = self.queues.get_mut(queue_name).ok_or_else(|| {
            Error::NotFound(format!("queue '{}' not found", queue_name))
        })?;

        queue.messages.clear();
        Ok(())
    }

    fn get_last_enqueue_time(&self, queue_name: &str) -> Result<SystemTime> {
        let queue = self.queues.get(queue_name).ok_or_else(|| {
            Error::NotFound(format!("queue '{}' not found", queue_name))
        })?;

        Ok(queue.last_enqueue_time)
    }

    fn ack(&mut self, queue_name: &str, msg_id: &str) -> Result<bool> {
        let queue = self.queues.get_mut(queue_name).ok_or_else(|| {
            Error::NotFound(format!("queue '{}' not found", queue_name))
        })?;

        // Find and remove message by ID
        let original_len = queue.messages.len();
        queue.messages.retain(|msg| msg.id != msg_id);
        Ok(queue.messages.len() != original_len)
    }
}

/// SQLite queue backend with at-least-once delivery semantics.
pub struct SQLiteQueueBackend {
    conn: Mutex<Connection>,
}

impl SQLiteQueueBackend {
    pub fn open(db_path: &str, options: SQLiteQueueOptions) -> Result<Self> {
        let conn = Connection::open(db_path)
            .map_err(|e| Error::internal(format!("sqlite connection error: {}", e)))?;

        if options.busy_timeout_ms > 0 {
            conn.busy_timeout(Duration::from_millis(options.busy_timeout_ms))
                .map_err(|e| Error::internal(format!("sqlite busy_timeout error: {}", e)))?;
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

            CREATE INDEX IF NOT EXISTS idx_queue_status
                ON queue_messages(queue_name, status, id);
            CREATE INDEX IF NOT EXISTS idx_queue_message_id
                ON queue_messages(queue_name, message_id);
            "#,
        )
        .map_err(|e| Error::internal(format!("sqlite schema init error: {}", e)))?;

        let backend = Self {
            conn: Mutex::new(conn),
        };
        backend.run_migrations()?;
        backend.recover_stale(options.recover_stale_sec)?;
        Ok(backend)
    }

    fn run_migrations(&self) -> Result<()> {
        let conn = self
            .conn
            .lock()
            .map_err(|e| Error::internal(format!("sqlite mutex poisoned: {}", e)))?;

        for stmt in [
            "ALTER TABLE queue_messages ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'",
            "ALTER TABLE queue_messages ADD COLUMN processing_started_at INTEGER",
            "CREATE INDEX IF NOT EXISTS idx_queue_status ON queue_messages(queue_name, status, id)",
            "CREATE INDEX IF NOT EXISTS idx_queue_message_id ON queue_messages(queue_name, message_id)",
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

        // Backfill queue metadata for legacy databases that only persisted queue_messages.
        conn.execute(
            "INSERT OR IGNORE INTO queue_metadata (queue_name)
             SELECT DISTINCT queue_name
             FROM queue_messages
             WHERE queue_name IS NOT NULL AND queue_name != ''",
            [],
        )
        .map_err(|e| Error::internal(format!("sqlite migration backfill queue metadata error: {}", e)))?;

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

    fn ensure_queue_exists(conn: &Connection, queue_name: &str) -> Result<()> {
        conn.execute(
            "INSERT OR IGNORE INTO queue_metadata (queue_name) VALUES (?1)",
            params![queue_name],
        )
        .map_err(|e| Error::internal(format!("sqlite ensure queue error: {}", e)))?;
        Ok(())
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
            return Err(Error::AlreadyExists(format!("queue '{}' already exists", name)));
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
            let mut stmt = match conn.prepare("SELECT queue_name FROM queue_metadata ORDER BY queue_name") {
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
        let conn = self
            .conn
            .lock()
            .map_err(|e| Error::internal(format!("sqlite mutex poisoned: {}", e)))?;

        Self::require_queue_exists(&conn, queue_name)?;
        let stored = serde_json::to_string(&StoredMessage::from_message(&msg))?;
        conn.execute(
            "INSERT INTO queue_messages (queue_name, message_id, data, timestamp, status)
             VALUES (?1, ?2, ?3, ?4, 'pending')",
            params![queue_name, msg.id, stored, unix_secs(msg.timestamp)],
        )
        .map_err(|e| Error::internal(format!("sqlite enqueue error: {}", e)))?;
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
            "SELECT id, data FROM queue_messages
             WHERE queue_name = ?1 AND status = 'pending'
             ORDER BY id LIMIT 1",
            params![queue_name],
            |row| Ok((row.get::<_, i64>(0)?, read_sqlite_text(row, 1)?)),
        );

        let (id, raw_data) = match row {
            Ok(row) => row,
            Err(rusqlite::Error::QueryReturnedNoRows) => return Ok(None),
            Err(e) => return Err(Error::internal(format!("sqlite dequeue query error: {}", e))),
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
             ORDER BY id LIMIT 1",
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
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

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
        let mut backend = MemoryBackend::new();

        backend.create_queue("test").unwrap();
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
        let mut backend = MemoryBackend::new();
        backend.create_queue("test").unwrap();

        let msg1 = Message::new(b"message 1".to_vec());
        let msg2 = Message::new(b"message 2".to_vec());

        backend.enqueue("test", msg1.clone()).unwrap();
        backend.enqueue("test", msg2.clone()).unwrap();

        assert_eq!(backend.size("test").unwrap(), 2);

        let dequeued1 = backend.dequeue("test").unwrap().unwrap();
        assert_eq!(dequeued1.data, b"message 1");

        let dequeued2 = backend.dequeue("test").unwrap().unwrap();
        assert_eq!(dequeued2.data, b"message 2");

        assert_eq!(backend.size("test").unwrap(), 0);
        assert!(backend.dequeue("test").unwrap().is_none());
    }

    #[test]
    fn test_peek() {
        let mut backend = MemoryBackend::new();
        backend.create_queue("test").unwrap();

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
        let mut backend = MemoryBackend::new();
        backend.create_queue("test").unwrap();

        backend.enqueue("test", Message::new(b"msg1".to_vec())).unwrap();
        backend.enqueue("test", Message::new(b"msg2".to_vec())).unwrap();

        assert_eq!(backend.size("test").unwrap(), 2);

        backend.clear("test").unwrap();
        assert_eq!(backend.size("test").unwrap(), 0);
    }

    #[test]
    fn test_multi_queue_isolation() {
        let mut backend = MemoryBackend::new();
        backend.create_queue("queue1").unwrap();
        backend.create_queue("queue2").unwrap();

        backend.enqueue("queue1", Message::new(b"msg1".to_vec())).unwrap();
        backend.enqueue("queue2", Message::new(b"msg2".to_vec())).unwrap();

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

        assert!(backend.enqueue("nonexistent", Message::new(b"data".to_vec())).is_err());
        assert!(backend.dequeue("nonexistent").is_err());
        assert!(backend.peek("nonexistent").is_err());
        assert!(backend.size("nonexistent").is_err());
        assert!(backend.clear("nonexistent").is_err());
    }

    #[test]
    fn test_sqlite_backend_basic_flow() {
        let dir = tempdir().unwrap();
        let db_path = dir.path().join("queue.db");
        let mut backend =
            SQLiteQueueBackend::open(db_path.to_str().unwrap(), SQLiteQueueOptions::default())
                .unwrap();

        backend.create_queue("test").unwrap();
        let msg1 = Message::new(b"message 1".to_vec());
        let msg2 = Message::new(b"message 2".to_vec());
        let msg1_id = msg1.id.clone();

        backend.enqueue("test", msg1).unwrap();
        backend.enqueue("test", msg2).unwrap();

        let first = backend.dequeue("test").unwrap().unwrap();
        assert_eq!(first.data, b"message 1");
        assert_eq!(backend.size("test").unwrap(), 1);
        assert!(backend.ack("test", &msg1_id).unwrap());

        let second = backend.dequeue("test").unwrap().unwrap();
        assert_eq!(second.data, b"message 2");
    }

    #[test]
    fn test_sqlite_backend_recover_stale() {
        let dir = tempdir().unwrap();
        let db_path = dir.path().join("queue.db");
        let db_path_str = db_path.to_str().unwrap().to_string();

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
        let dir = tempdir().unwrap();
        let db_path = dir.path().join("queue.db");
        let db_path_str = db_path.to_str().unwrap().to_string();

        let mut backend =
            SQLiteQueueBackend::open(&db_path_str, SQLiteQueueOptions::default()).unwrap();
        backend.create_queue("Semantic").unwrap();
        drop(backend);

        let conn = Connection::open(&db_path_str).unwrap();
        conn.execute(
            "INSERT INTO queue_messages (queue_name, message_id, data, timestamp, status)
             VALUES (?1, ?2, ?3, ?4, 'pending')",
            params![
                "Semantic",
                "legacy-msg-id",
                r#"{"id":"legacy-msg-id","data":"{\"id\":\"semantic-inner\",\"uri\":\"viking://resources/demo\",\"context_type\":\"resource\",\"status\":\"pending\",\"timestamp\":1776411350,\"recursive\":true,\"account_id\":\"default\",\"user_id\":\"default\",\"agent_id\":\"default\",\"role\":\"root\",\"skip_vectorization\":false,\"telemetry_id\":\"tm_demo\",\"target_uri\":null,\"lifecycle_lock_handle_id\":\"lock-demo\",\"is_code_repo\":false,\"changes\":null}","timestamp":"2026-04-17T15:37:39.287855+08:00"}"#,
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
    }

    #[test]
    fn test_sqlite_backend_operations_on_nonexistent_queue() {
        let dir = tempdir().unwrap();
        let db_path = dir.path().join("queue.db");
        let mut backend =
            SQLiteQueueBackend::open(db_path.to_str().unwrap(), SQLiteQueueOptions::default())
                .unwrap();

        assert!(backend.enqueue("nonexistent", Message::new(b"data".to_vec())).is_err());
        assert!(backend.dequeue("nonexistent").is_err());
        assert!(backend.peek("nonexistent").is_err());
        assert!(backend.size("nonexistent").is_err());
        assert!(backend.clear("nonexistent").is_err());
        assert!(backend.get_last_enqueue_time("nonexistent").is_err());
        assert!(backend.ack("nonexistent", "msg-id").is_err());
    }
}
