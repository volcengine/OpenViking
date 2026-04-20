//! QueueFS Plugin
//!
//! A filesystem-based message queue with multi-queue support where operations are performed
//! through control files within each queue directory:
//! - `/queue_name/enqueue` - Write to this file to add a message to the queue
//! - `/queue_name/dequeue` - Read from this file to remove and return the first message
//! - `/queue_name/peek` - Read from this file to view the first message without removing it
//! - `/queue_name/size` - Read from this file to get the current queue size
//! - `/queue_name/clear` - Write to this file to clear all messages from the queue
//! - `/queue_name/ack` - Write message ID to this file to acknowledge and delete it

mod backend;

use crate::core::{
    errors::{Error, Result},
    filesystem::FileSystem,
    plugin::ServicePlugin,
    types::{ConfigParameter, FileInfo, PluginConfig, WriteFlag},
};
use async_trait::async_trait;
use backend::{MemoryBackend, Message, QueueBackend, SQLiteQueueBackend, SQLiteQueueOptions};
use serde::Serialize;
use std::sync::Arc;
use std::time::SystemTime;
use tokio::sync::Mutex;

#[derive(Clone, Copy)]
struct ControlFileSpec {
    name: &'static str,
    mode: u32,
}

// Single source of truth for control files (names + permissions).
const CONTROL_FILES: &[ControlFileSpec] = &[
    ControlFileSpec {
        name: "enqueue",
        mode: 0o222,
    },
    ControlFileSpec {
        name: "dequeue",
        mode: 0o444,
    },
    ControlFileSpec {
        name: "peek",
        mode: 0o444,
    },
    ControlFileSpec {
        name: "size",
        mode: 0o444,
    },
    ControlFileSpec {
        name: "clear",
        mode: 0o222,
    },
    ControlFileSpec {
        name: "ack",
        mode: 0o222,
    },
];

/// Dequeue response format (matches Go libagfsbinding format)
#[derive(Debug, Serialize)]
struct QueueMessage {
    id: String,
    data: String,
}

#[derive(Debug, Clone, Copy)]
enum BackendKind {
    Memory,
    Sqlite,
}

#[derive(Debug, Clone)]
struct ParsedBackendConfig {
    kind: BackendKind,
    sqlite_db_path: Option<String>,
    sqlite_options: SQLiteQueueOptions,
}

/// Parsed path information
struct ParsedPath {
    queue_name: Option<String>,
    operation: Option<String>,
    is_dir: bool,
}

/// QueueFS - A filesystem-based message queue with multi-queue support
pub struct QueueFileSystem {
    /// The queue backend
    backend: Arc<Mutex<Box<dyn QueueBackend>>>,
}

impl QueueFileSystem {
    /// Create a new QueueFileSystem with memory backend
    pub fn new() -> Self {
        Self::with_backend(Box::new(MemoryBackend::new()))
    }

    /// Create a QueueFileSystem with a specific backend implementation.
    pub fn with_backend(backend: Box<dyn QueueBackend>) -> Self {
        Self {
            backend: Arc::new(Mutex::new(backend)),
        }
    }

    /// Check if a name is a control operation
    fn is_control_operation(name: &str) -> bool {
        CONTROL_FILES.iter().any(|spec| spec.name == name)
    }

    fn control_file_mode(name: &str) -> Option<u32> {
        CONTROL_FILES
            .iter()
            .find(|spec| spec.name == name)
            .map(|spec| spec.mode)
    }

    fn leaf_control_files(now: SystemTime) -> Vec<FileInfo> {
        CONTROL_FILES
            .iter()
            .map(|spec| FileInfo {
                name: spec.name.to_string(),
                size: 0,
                mode: spec.mode,
                mod_time: now,
                is_dir: false,
            })
            .collect()
    }

    /// Normalize path by removing trailing slashes and ensuring it starts with /
    fn normalize_path(path: &str) -> String {
        let path = path.trim_end_matches('/');
        if path.is_empty() || path == "/" {
            "/".to_string()
        } else if !path.starts_with('/') {
            format!("/{}", path)
        } else {
            path.to_string()
        }
    }

    /// Parse a queue path into its components
    fn parse_queue_path(path: &str) -> Result<ParsedPath> {
        let path = Self::normalize_path(path);
        let path = path.trim_start_matches('/');

        // Root directory
        if path.is_empty() {
            return Ok(ParsedPath {
                queue_name: None,
                operation: None,
                is_dir: true,
            });
        }

        let parts: Vec<&str> = path.split('/').collect();
        let last = parts[parts.len() - 1];

        // Check if last part is a control operation
        if Self::is_control_operation(last) {
            if parts.len() == 1 {
                return Err(Error::InvalidOperation(
                    "operation without queue name".to_string(),
                ));
            }
            let queue_name = parts[..parts.len() - 1].join("/");
            return Ok(ParsedPath {
                queue_name: Some(queue_name),
                operation: Some(last.to_string()),
                is_dir: false,
            });
        }

        // It's a directory (queue or parent)
        Ok(ParsedPath {
            queue_name: Some(parts.join("/")),
            operation: None,
            is_dir: true,
        })
    }
}

#[async_trait]
impl FileSystem for QueueFileSystem {
    async fn create(&self, path: &str) -> Result<()> {
        let parsed = Self::parse_queue_path(path)?;
        if !parsed.is_dir && parsed.operation.is_some() {
            // Control files always exist
            Ok(())
        } else {
            Err(Error::InvalidOperation(
                "QueueFS only supports control files".to_string(),
            ))
        }
    }

    async fn mkdir(&self, path: &str, _mode: u32) -> Result<()> {
        let parsed = Self::parse_queue_path(path)?;
        if !parsed.is_dir {
            return Err(Error::InvalidOperation(
                "not a directory path".to_string(),
            ));
        }
        if let Some(queue_name) = parsed.queue_name {
            self.backend.lock().await.create_queue(&queue_name)?;
            Ok(())
        } else {
            // Root directory always exists
            Ok(())
        }
    }

    async fn read(&self, path: &str, _offset: u64, _size: u64) -> Result<Vec<u8>> {
        let parsed = Self::parse_queue_path(path)?;

        let queue_name = parsed
            .queue_name
            .ok_or_else(|| Error::InvalidOperation("no queue specified".to_string()))?;
        let operation = parsed
            .operation
            .ok_or_else(|| Error::InvalidOperation("no operation specified".to_string()))?;

        let mut backend = self.backend.lock().await;

        match operation.as_str() {
            "dequeue" => {
                let Some(msg) = backend.dequeue(&queue_name)? else {
                    return Ok(b"{}".to_vec());
                };
                // Return in Go libagfsbinding format: {"id": "...", "data": "..."}
                let data_str = String::from_utf8_lossy(&msg.data).to_string();
                let response = QueueMessage {
                    id: msg.id,
                    data: data_str,
                };
                Ok(serde_json::to_vec(&response)?)
            }
            "peek" => {
                let Some(msg) = backend.peek(&queue_name)? else {
                    return Ok(b"{}".to_vec());
                };
                // Return in Go libagfsbinding format: {"id": "...", "data": "..."}
                let data_str = String::from_utf8_lossy(&msg.data).to_string();
                let response = QueueMessage {
                    id: msg.id.clone(),
                    data: data_str,
                };
                Ok(serde_json::to_vec(&response)?)
            }
            "size" => {
                let size = backend.size(&queue_name)?;
                Ok(size.to_string().into_bytes())
            }
            _ => Err(Error::InvalidOperation(format!(
                "Cannot read from '{}'. Use dequeue, peek, or size",
                operation
            ))),
        }
    }

    async fn write(
        &self,
        path: &str,
        data: &[u8],
        _offset: u64,
        _flags: WriteFlag,
    ) -> Result<u64> {
        let parsed = Self::parse_queue_path(path)?;

        let queue_name = parsed
            .queue_name
            .ok_or_else(|| Error::InvalidOperation("no queue specified".to_string()))?;
        let operation = parsed
            .operation
            .ok_or_else(|| Error::InvalidOperation("no operation specified".to_string()))?;

        let mut backend = self.backend.lock().await;

        match operation.as_str() {
            "enqueue" => {
                let msg = Message::new(data.to_vec());
                let len = data.len() as u64;
                backend.enqueue(&queue_name, msg)?;
                Ok(len)
            }
            "clear" => {
                backend.clear(&queue_name)?;
                Ok(0)
            }
            "ack" => {
                let msg_id = String::from_utf8_lossy(data).trim().to_string();
                backend.ack(&queue_name, &msg_id)?;
                Ok(0)
            }
            _ => Err(Error::InvalidOperation(format!(
                "Cannot write to '{}'. Use enqueue, clear, or ack",
                operation
            ))),
        }
    }

    async fn read_dir(&self, path: &str) -> Result<Vec<FileInfo>> {
        let parsed = Self::parse_queue_path(path)?;

        if !parsed.is_dir {
            return Err(Error::NotADirectory(path.to_string()));
        }

        let backend = self.backend.lock().await;
        let now = SystemTime::now();

        // Root directory: list all top-level queues
        if parsed.queue_name.is_none() {
            let queues = backend.list_queues("");
            let mut top_level = std::collections::HashSet::new();

            for q in queues {
                if let Some(first) = q.split('/').next() {
                    top_level.insert(first.to_string());
                }
            }

            return Ok(top_level
                .into_iter()
                .map(|name| FileInfo {
                    name,
                    size: 0,
                    mode: 0o755,
                    mod_time: now,
                    is_dir: true,
                })
                .collect());
        }

        // Queue directory: check if it has nested queues
        let queue_name = parsed.queue_name.unwrap();
        let all_queues = backend.list_queues(&queue_name);

        let has_nested = all_queues
            .iter()
            .any(|q| q.starts_with(&format!("{}/", queue_name)));

        if has_nested {
            // Return subdirectories
            let prefix = format!("{}/", queue_name);
            let mut subdirs = std::collections::HashSet::new();

            for q in all_queues {
                if let Some(remainder) = q.strip_prefix(&prefix) {
                    if let Some(first) = remainder.split('/').next() {
                        subdirs.insert(first.to_string());
                    }
                }
            }

            return Ok(subdirs
                .into_iter()
                .map(|name| FileInfo {
                    name,
                    size: 0,
                    mode: 0o755,
                    mod_time: now,
                    is_dir: true,
                })
                .collect());
        }

        // Leaf queue: return control files
        if !backend.queue_exists(&queue_name) {
            return Err(Error::NotFound(format!(
                "queue not found: {}",
                queue_name
            )));
        }

        Ok(Self::leaf_control_files(now))
    }

    async fn stat(&self, path: &str) -> Result<FileInfo> {
        let parsed = Self::parse_queue_path(path)?;

        // Root directory
        if parsed.queue_name.is_none() {
            return Ok(FileInfo {
                name: "/".to_string(),
                size: 0,
                mode: 0o755,
                mod_time: SystemTime::now(),
                is_dir: true,
            });
        }

        let backend = self.backend.lock().await;

        if parsed.is_dir {
            // Queue directory
            let queue_name = parsed.queue_name.unwrap();
            if backend.queue_exists(&queue_name) {
                Ok(FileInfo {
                    name: queue_name.split('/').last().unwrap_or(&queue_name).to_string(),
                    size: 0,
                    mode: 0o755,
                    mod_time: SystemTime::now(),
                    is_dir: true,
                })
            } else {
                Err(Error::NotFound(format!("queue not found: {}", queue_name)))
            }
        } else {
            // Control file
            let operation = parsed.operation.as_ref().unwrap();
            Ok(FileInfo {
                name: operation.clone(),
                size: 0,
                mode: Self::control_file_mode(operation)
                    .ok_or_else(|| Error::NotFound(format!("control file not found: {}", operation)))?,
                mod_time: SystemTime::now(),
                is_dir: false,
            })
        }
    }

    async fn rename(&self, _old_path: &str, _new_path: &str) -> Result<()> {
        Err(Error::InvalidOperation(
            "QueueFS does not support rename".to_string(),
        ))
    }

    async fn chmod(&self, _path: &str, _mode: u32) -> Result<()> {
        Err(Error::InvalidOperation(
            "QueueFS does not support chmod".to_string(),
        ))
    }

    async fn remove(&self, _path: &str) -> Result<()> {
        Err(Error::InvalidOperation(
            "QueueFS does not support remove".to_string(),
        ))
    }

    async fn remove_all(&self, path: &str) -> Result<()> {
        let parsed = Self::parse_queue_path(path)?;

        if !parsed.is_dir {
            return Err(Error::InvalidOperation(
                "not a directory".to_string(),
            ));
        }

        if let Some(queue_name) = parsed.queue_name {
            self.backend.lock().await.remove_queue(&queue_name)?;
            Ok(())
        } else {
            Err(Error::InvalidOperation(
                "cannot remove root directory".to_string(),
            ))
        }
    }

    async fn truncate(&self, _path: &str, _size: u64) -> Result<()> {
        Err(Error::InvalidOperation(
            "QueueFS does not support truncate".to_string(),
        ))
    }
}

/// QueueFS Plugin
pub struct QueueFSPlugin {
    config_params: Vec<ConfigParameter>,
}

impl QueueFSPlugin {
    /// Create a new queuefs plugin with supported configuration metadata.
    pub fn new() -> Self {
        Self {
            config_params: vec![
                ConfigParameter::optional(
                    "backend",
                    "string",
                    "memory",
                    "Queue backend (memory, sqlite, sqlite3)",
                ),
                ConfigParameter::optional(
                    "db_path",
                    "string",
                    "",
                    "SQLite database file path when backend=sqlite or backend=sqlite3",
                ),
                ConfigParameter::optional(
                    "recover_stale_sec",
                    "int",
                    "0",
                    "Recover processing messages older than this many seconds on startup (0 = recover all)",
                ),
                ConfigParameter::optional(
                    "busy_timeout_ms",
                    "int",
                    "5000",
                    "SQLite busy timeout in milliseconds",
                ),
            ],
        }
    }

    fn get_string_param<'a>(config: &'a PluginConfig, key: &str) -> Option<&'a str> {
        config.params.get(key).and_then(|v| v.as_string())
    }

    fn get_int_param(config: &PluginConfig, key: &str) -> Option<i64> {
        config.params.get(key).and_then(|v| v.as_int())
    }

    fn parse_backend_config(config: &PluginConfig) -> Result<ParsedBackendConfig> {
        let backend_name = Self::get_string_param(config, "backend").unwrap_or("memory");
        let valid_backends = ["memory", "sqlite", "sqlite3"];
        if !valid_backends.contains(&backend_name) {
            return Err(Error::config(format!(
                "unsupported queue backend: {} (valid: {})",
                backend_name,
                valid_backends.join(", ")
            )));
        }

        let kind = match backend_name {
            "memory" => BackendKind::Memory,
            "sqlite" | "sqlite3" => BackendKind::Sqlite,
            _ => {
                return Err(Error::config(format!(
                    "unsupported queue backend: {}",
                    backend_name
                )))
            }
        };

        let recover_stale_sec = Self::get_int_param(config, "recover_stale_sec").unwrap_or(0);
        let busy_timeout_ms = Self::get_int_param(config, "busy_timeout_ms")
            .unwrap_or(5_000)
            .max(0) as u64;

        // Ensure typed config values are valid when present (avoid validate/initialize drift).
        for (key, label) in [
            ("recover_stale_sec", "recover_stale_sec"),
            ("busy_timeout_ms", "busy_timeout_ms"),
        ] {
            if let Some(value) = config.params.get(key) {
                value
                    .as_int()
                    .ok_or_else(|| Error::config(format!("{} must be an integer", label)))?;
            }
        }

        let sqlite_db_path = match kind {
            BackendKind::Memory => None,
            BackendKind::Sqlite => {
                let db_path = Self::get_string_param(config, "db_path").unwrap_or("");
                if db_path.trim().is_empty() {
                    return Err(Error::config(format!(
                        "queuefs db_path is required when backend={} or backend={}",
                        "sqlite", "sqlite3"
                    )));
                }
                Some(db_path.to_string())
            }
        };

        Ok(ParsedBackendConfig {
            kind,
            sqlite_db_path,
            sqlite_options: SQLiteQueueOptions {
                recover_stale_sec,
                busy_timeout_ms,
            },
        })
    }
}

impl Default for QueueFSPlugin {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl ServicePlugin for QueueFSPlugin {
    fn name(&self) -> &str {
        "queuefs"
    }

    fn readme(&self) -> &str {
        "QueueFS - A filesystem-based message queue with multi-queue support\n\
         \n\
         Usage:\n\
         1. Create a queue:\n\
            mkdir /queuefs/Embedding\n\
         \n\
         2. Enqueue messages:\n\
            echo 'message data' > /queuefs/Embedding/enqueue\n\
         \n\
         3. Dequeue messages:\n\
            cat /queuefs/Embedding/dequeue\n\
         \n\
         4. Peek at messages:\n\
            cat /queuefs/Embedding/peek\n\
         \n\
         5. Check queue size:\n\
            cat /queuefs/Embedding/size\n\
         \n\
         6. Clear queue:\n\
            echo '' > /queuefs/Embedding/clear\n\
         \n\
         7. Ack a message:\n\
            echo '<message_id>' > /queuefs/Embedding/ack\n\
         \n\
         Control files per queue:\n\
         - enqueue: Write to add a message to the queue\n\
         - dequeue: Read to remove and return the first message\n\
         - peek: Read to view the first message without removing it\n\
         - size: Read to get the current queue size\n\
         - clear: Write to clear all messages from the queue\n\
         - ack: Write message id to acknowledge and delete it\n\
         \n\
         Supports nested queues:\n\
            mkdir /queuefs/logs/errors\n\
            echo 'error message' > /queuefs/logs/errors/enqueue"
    }

    async fn validate(&self, config: &PluginConfig) -> Result<()> {
        Self::parse_backend_config(config)?;
        Ok(())
    }

    async fn initialize(&self, config: PluginConfig) -> Result<Box<dyn FileSystem>> {
        let parsed = Self::parse_backend_config(&config)?;

        let backend: Box<dyn QueueBackend> = match parsed.kind {
            BackendKind::Memory => Box::new(MemoryBackend::new()),
            BackendKind::Sqlite => Box::new(SQLiteQueueBackend::open(
                parsed
                    .sqlite_db_path
                    .as_deref()
                    .expect("sqlite db_path is validated"),
                parsed.sqlite_options,
            )?),
        };

        Ok(Box::new(QueueFileSystem::with_backend(backend)))
    }

    fn config_params(&self) -> &[ConfigParameter] {
        &self.config_params
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde::Deserialize;

    /// Helper struct to deserialize queue messages in tests
    #[derive(Debug, Deserialize)]
    struct TestQueueMessage {
        id: String,
        data: String,
    }

    #[tokio::test]
    async fn test_queuefs_enqueue_dequeue() {
        let fs = QueueFileSystem::new();

        // Create a queue first
        fs.mkdir("/test", 0o755).await.unwrap();

        // Enqueue messages
        let data1 = b"message 1";
        let data2 = b"message 2";

        fs.write("/test/enqueue", data1, 0, WriteFlag::None)
            .await
            .unwrap();
        fs.write("/test/enqueue", data2, 0, WriteFlag::None)
            .await
            .unwrap();

        // Dequeue messages
        let result1 = fs.read("/test/dequeue", 0, 0).await.unwrap();
        let msg1: TestQueueMessage = serde_json::from_slice(&result1).unwrap();
        assert!(!msg1.id.is_empty());
        assert_eq!(msg1.data.as_bytes(), data1);

        let result2 = fs.read("/test/dequeue", 0, 0).await.unwrap();
        let msg2: TestQueueMessage = serde_json::from_slice(&result2).unwrap();
        assert!(!msg2.id.is_empty());
        assert_eq!(msg2.data.as_bytes(), data2);

        // Queue should be empty
        let result = fs.read("/test/dequeue", 0, 0).await.unwrap();
        assert_eq!(result, b"{}");
    }

    #[tokio::test]
    async fn test_queuefs_peek() {
        let fs = QueueFileSystem::new();

        // Create a queue first
        fs.mkdir("/test", 0o755).await.unwrap();

        // Enqueue a message
        let data = b"test message";
        fs.write("/test/enqueue", data, 0, WriteFlag::None)
            .await
            .unwrap();

        // Peek should return the message without removing it
        let result1 = fs.read("/test/peek", 0, 0).await.unwrap();
        let msg1: TestQueueMessage = serde_json::from_slice(&result1).unwrap();
        assert_eq!(msg1.data.as_bytes(), data);

        let result2 = fs.read("/test/peek", 0, 0).await.unwrap();
        let msg2: TestQueueMessage = serde_json::from_slice(&result2).unwrap();
        assert_eq!(msg2.data.as_bytes(), data);

        // Dequeue should still work
        let result3 = fs.read("/test/dequeue", 0, 0).await.unwrap();
        let msg3: TestQueueMessage = serde_json::from_slice(&result3).unwrap();
        assert_eq!(msg3.data.as_bytes(), data);
    }

    #[tokio::test]
    async fn test_queuefs_size() {
        let fs = QueueFileSystem::new();

        // Create a queue first
        fs.mkdir("/test", 0o755).await.unwrap();

        // Initially empty
        let size = fs.read("/test/size", 0, 0).await.unwrap();
        assert_eq!(String::from_utf8(size).unwrap(), "0");

        // Add messages
        fs.write("/test/enqueue", b"msg1", 0, WriteFlag::None)
            .await
            .unwrap();
        fs.write("/test/enqueue", b"msg2", 0, WriteFlag::None)
            .await
            .unwrap();

        let size = fs.read("/test/size", 0, 0).await.unwrap();
        assert_eq!(String::from_utf8(size).unwrap(), "2");

        // Dequeue one
        fs.read("/test/dequeue", 0, 0).await.unwrap();

        let size = fs.read("/test/size", 0, 0).await.unwrap();
        assert_eq!(String::from_utf8(size).unwrap(), "1");
    }

    #[tokio::test]
    async fn test_queuefs_clear() {
        let fs = QueueFileSystem::new();

        // Create a queue first
        fs.mkdir("/test", 0o755).await.unwrap();

        // Add messages
        fs.write("/test/enqueue", b"msg1", 0, WriteFlag::None)
            .await
            .unwrap();
        fs.write("/test/enqueue", b"msg2", 0, WriteFlag::None)
            .await
            .unwrap();

        // Clear the queue
        fs.write("/test/clear", b"", 0, WriteFlag::None)
            .await
            .unwrap();

        // Queue should be empty
        let size = fs.read("/test/size", 0, 0).await.unwrap();
        assert_eq!(String::from_utf8(size).unwrap(), "0");

        let result = fs.read("/test/dequeue", 0, 0).await;
        assert_eq!(result.unwrap(), b"{}");
    }

    #[tokio::test]
    async fn test_queuefs_read_dir() {
        let fs = QueueFileSystem::new();

        // Create a queue
        fs.mkdir("/test", 0o755).await.unwrap();

        // Root should list the queue
        let entries = fs.read_dir("/").await.unwrap();
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].name, "test");
        assert!(entries[0].is_dir);

        // Queue directory should list control files
        let entries = fs.read_dir("/test").await.unwrap();
        assert_eq!(entries.len(), 6);

        let names: Vec<String> = entries.iter().map(|e| e.name.clone()).collect();
        assert!(names.contains(&"enqueue".to_string()));
        assert!(names.contains(&"dequeue".to_string()));
        assert!(names.contains(&"peek".to_string()));
        assert!(names.contains(&"size".to_string()));
        assert!(names.contains(&"clear".to_string()));
        assert!(names.contains(&"ack".to_string()));
    }

    #[tokio::test]
    async fn test_queuefs_stat() {
        let fs = QueueFileSystem::new();

        // Create a queue
        fs.mkdir("/test", 0o755).await.unwrap();

        // Stat root
        let info = fs.stat("/").await.unwrap();
        assert!(info.is_dir);

        // Stat queue directory
        let info = fs.stat("/test").await.unwrap();
        assert!(info.is_dir);

        // Stat control files
        let info = fs.stat("/test/enqueue").await.unwrap();
        assert!(!info.is_dir);
        assert_eq!(info.name, "enqueue");

        // Stat non-existent queue
        let result = fs.stat("/nonexistent").await;
        assert!(result.is_err());
    }

    #[tokio::test]
    async fn test_queuefs_invalid_operations() {
        let fs = QueueFileSystem::new();

        // Create a queue
        fs.mkdir("/test", 0o755).await.unwrap();

        // Cannot read from enqueue
        let result = fs.read("/test/enqueue", 0, 0).await;
        assert!(result.is_err());

        // Cannot write to dequeue
        let result = fs.write("/test/dequeue", b"data", 0, WriteFlag::None).await;
        assert!(result.is_err());

        // Cannot rename
        let result = fs.rename("/test/enqueue", "/test/enqueue2").await;
        assert!(result.is_err());

        // Cannot remove control files
        let result = fs.remove("/test/enqueue").await;
        assert!(result.is_err());
    }

    #[tokio::test]
    async fn test_queuefs_concurrent_access() {
        let fs = Arc::new(QueueFileSystem::new());

        // Create a queue
        fs.mkdir("/test", 0o755).await.unwrap();

        // Spawn multiple tasks to enqueue messages
        let mut handles = vec![];
        for i in 0..10 {
            let fs_clone = fs.clone();
            let handle = tokio::spawn(async move {
                let data = format!("message {}", i);
                fs_clone
                    .write("/test/enqueue", data.as_bytes(), 0, WriteFlag::None)
                    .await
                    .unwrap();
            });
            handles.push(handle);
        }

        // Wait for all tasks to complete
        for handle in handles {
            handle.await.unwrap();
        }

        // Check size
        let size = fs.read("/test/size", 0, 0).await.unwrap();
        assert_eq!(String::from_utf8(size).unwrap(), "10");

        // Dequeue all messages
        for _ in 0..10 {
            fs.read("/test/dequeue", 0, 0).await.unwrap();
        }

        // Queue should be empty
        let size = fs.read("/test/size", 0, 0).await.unwrap();
        assert_eq!(String::from_utf8(size).unwrap(), "0");
    }

    #[tokio::test]
    async fn test_queuefs_plugin() {
        let plugin = QueueFSPlugin::new();

        assert_eq!(plugin.name(), "queuefs");
        assert!(!plugin.readme().is_empty());
        assert_eq!(plugin.config_params().len(), 4);

        let config = PluginConfig {
            name: "queuefs".to_string(),
            mount_path: "/queue".to_string(),
            params: std::collections::HashMap::new(),
        };

        plugin.validate(&config).await.unwrap();
        let fs = plugin.initialize(config).await.unwrap();

        // Create a queue
        fs.mkdir("/test", 0o755).await.unwrap();

        // Test basic operation
        fs.write("/test/enqueue", b"test", 0, WriteFlag::None)
            .await
            .unwrap();
        let result = fs.read("/test/dequeue", 0, 0).await.unwrap();
        let msg: TestQueueMessage = serde_json::from_slice(&result).unwrap();
        assert_eq!(msg.data, "test");
    }

    #[tokio::test]
    async fn test_multi_queue() {
        let fs = QueueFileSystem::new();

        // Create two queues
        fs.mkdir("/Embedding", 0o755).await.unwrap();
        fs.mkdir("/Semantic", 0o755).await.unwrap();

        // Enqueue to both
        fs.write("/Embedding/enqueue", b"embed1", 0, WriteFlag::None)
            .await
            .unwrap();
        fs.write("/Semantic/enqueue", b"semantic1", 0, WriteFlag::None)
            .await
            .unwrap();

        // Verify isolation
        let size1 = fs.read("/Embedding/size", 0, 0).await.unwrap();
        let size2 = fs.read("/Semantic/size", 0, 0).await.unwrap();
        assert_eq!(String::from_utf8(size1).unwrap(), "1");
        assert_eq!(String::from_utf8(size2).unwrap(), "1");

        // Dequeue from specific queue
        let msg = fs.read("/Embedding/dequeue", 0, 0).await.unwrap();
        let msg: TestQueueMessage = serde_json::from_slice(&msg).unwrap();
        assert_eq!(msg.data, "embed1");

        // Other queue unaffected
        let size2 = fs.read("/Semantic/size", 0, 0).await.unwrap();
        assert_eq!(String::from_utf8(size2).unwrap(), "1");
    }

    #[tokio::test]
    async fn test_nested_queues() {
        let fs = QueueFileSystem::new();

        // Create nested structure
        fs.mkdir("/logs", 0o755).await.unwrap();
        fs.mkdir("/logs/errors", 0o755).await.unwrap();
        fs.mkdir("/logs/warnings", 0o755).await.unwrap();

        // List /logs should show subdirectories
        let entries = fs.read_dir("/logs").await.unwrap();
        assert_eq!(entries.len(), 2);
        let names: Vec<_> = entries.iter().map(|e| e.name.as_str()).collect();
        assert!(names.contains(&"errors"));
        assert!(names.contains(&"warnings"));

        // Can enqueue to nested queue
        fs.write("/logs/errors/enqueue", b"error1", 0, WriteFlag::None)
            .await
            .unwrap();
        let msg = fs.read("/logs/errors/dequeue", 0, 0).await.unwrap();
        let msg: TestQueueMessage = serde_json::from_slice(&msg).unwrap();
        assert_eq!(msg.data, "error1");
    }

    #[tokio::test]
    async fn test_queue_lifecycle() {
        let fs = QueueFileSystem::new();

        // Create queue
        fs.mkdir("/temp", 0o755).await.unwrap();
        fs.write("/temp/enqueue", b"data", 0, WriteFlag::None)
            .await
            .unwrap();

        // Verify exists
        let size = fs.read("/temp/size", 0, 0).await.unwrap();
        assert_eq!(String::from_utf8(size).unwrap(), "1");

        // Delete queue
        fs.remove_all("/temp").await.unwrap();

        // Verify deleted
        let result = fs.stat("/temp").await;
        assert!(result.is_err());
    }

    #[tokio::test]
    async fn test_path_parsing() {
        let fs = QueueFileSystem::new();

        // Create queue
        fs.mkdir("/test", 0o755).await.unwrap();

        // Various path formats should work
        fs.write("/test/enqueue", b"msg1", 0, WriteFlag::None)
            .await
            .unwrap();
        fs.write("/test/enqueue/", b"msg2", 0, WriteFlag::None)
            .await
            .unwrap();

        let size = fs.read("/test/size", 0, 0).await.unwrap();
        assert_eq!(String::from_utf8(size).unwrap(), "2");
    }
}
