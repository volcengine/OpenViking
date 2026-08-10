//! QueueFS Plugin
//!
//! A filesystem-based message queue with multi-queue support where operations are performed
//! through control files within each queue directory:
//! - `/queue_name/enqueue` - Write to this file to add a message to the queue
//! - `/queue_name/dequeue` - Read from this file to remove and return the first message
//! - `/queue_name/peek` - Read from this file to view the first message without removing it
//! - `/queue_name/size` - Read from this file to get the current queue size
//! - `/queue_name/messages` - Read all unacknowledged messages without changing queue state
//! - `/queue_name/clear` - Write to this file to clear all messages from the queue
//! - `/queue_name/ack` - Write message ID to this file to acknowledge and delete it

mod backend;
mod redis_backend;

use crate::core::{
    errors::{Error, Result},
    filesystem::FileSystem,
    plugin::ServicePlugin,
    types::{ConfigParameter, FileInfo, PluginConfig, WriteFlag},
};
use async_trait::async_trait;
use backend::{MemoryBackend, Message, QueueBackend, SQLiteQueueBackend, SQLiteQueueOptions};
use redis_backend::RedisQueueBackend;
use serde::{Deserialize, Serialize};
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
        name: "messages",
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
    Redis,
}

#[derive(Debug, Clone)]
struct ParsedBackendConfig {
    kind: BackendKind,
    sqlite_db_path: Option<String>,
    sqlite_options: SQLiteQueueOptions,
    redis_options: Option<RedisQueueOptions>,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "lowercase")]
enum RedisMode {
    #[default]
    Singleton,
    Cluster,
    Sentinel,
}

#[derive(Clone, Deserialize)]
#[serde(default, deny_unknown_fields)]
struct RedisQueueOptions {
    mode: RedisMode,
    endpoints: Vec<String>,
    master_name: Option<String>,
    username: Option<String>,
    password: Option<String>,
    sentinel_username: Option<String>,
    sentinel_password: Option<String>,
    db: i64,
    connect_timeout_ms: u64,
    command_timeout_ms: u64,
    key_prefix: String,
    tls_enabled: bool,
    tls_insecure_skip_verify: bool,
}

impl std::fmt::Debug for RedisQueueOptions {
    /// Format Redis settings without exposing the configured password.
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("RedisQueueOptions")
            .field("mode", &self.mode)
            .field("endpoints", &self.endpoints)
            .field("master_name", &self.master_name)
            .field("username", &self.username)
            .field("password_configured", &self.password.is_some())
            .field(
                "sentinel_username_configured",
                &self.sentinel_username.is_some(),
            )
            .field(
                "sentinel_password_configured",
                &self.sentinel_password.is_some(),
            )
            .field("db", &self.db)
            .field("connect_timeout_ms", &self.connect_timeout_ms)
            .field("command_timeout_ms", &self.command_timeout_ms)
            .field("key_prefix", &self.key_prefix)
            .field("tls_enabled", &self.tls_enabled)
            .field(
                "tls_insecure_skip_verify",
                &self.tls_insecure_skip_verify,
            )
            .finish()
    }
}

impl Default for RedisQueueOptions {
    /// Return the default standalone Redis connection settings.
    fn default() -> Self {
        Self {
            mode: RedisMode::Singleton,
            endpoints: vec!["redis://127.0.0.1:6379".to_string()],
            master_name: None,
            username: None,
            password: None,
            sentinel_username: None,
            sentinel_password: None,
            db: 0,
            connect_timeout_ms: 3_000,
            command_timeout_ms: 3_000,
            key_prefix: "default".to_string(),
            tls_enabled: false,
            tls_insecure_skip_verify: false,
        }
    }
}

impl RedisQueueOptions {
    /// Validate Redis endpoint and timeout settings.
    fn validate(&self) -> Result<()> {
        if self.endpoints.is_empty() || self.endpoints.iter().any(|value| value.trim().is_empty()) {
            return Err(Error::config(
                "queuefs redis endpoints must not be empty".to_string(),
            ));
        }
        for value in &self.endpoints {
            if !value.starts_with("redis://") && !value.starts_with("rediss://") {
                return Err(Error::config(
                    "queuefs redis endpoints must use valid redis:// or rediss:// URLs".to_string(),
                ));
            }
            let info = redis::IntoConnectionInfo::into_connection_info(value.as_str()).map_err(|_| {
                Error::config("queuefs redis endpoints must use valid redis:// or rediss:// URLs".to_string())
            })?;
            let endpoint = value
                .split_once("://")
                .map(|(_, endpoint)| endpoint)
                .unwrap_or_default();
            let options_start = endpoint
                .find(|character| matches!(character, '/' | '?' | '#'))
                .unwrap_or(endpoint.len());
            if endpoint[..options_start].contains('@')
                || !matches!(&endpoint[options_start..], "" | "/")
            {
                return Err(Error::config(
                    "queuefs redis endpoints must not include credentials, database paths, query parameters, or fragments; use dedicated redis fields".to_string(),
                ));
            }
            if matches!(
                info.addr(),
                redis::ConnectionAddr::Tcp(_, 0) | redis::ConnectionAddr::TcpTls { port: 0, .. }
            ) {
                return Err(Error::config("queuefs redis endpoint port is invalid".to_string()));
            }
        }
        if self.mode == RedisMode::Singleton && self.endpoints.len() != 1 {
            return Err(Error::config(
                "queuefs redis singleton mode requires exactly one endpoint".to_string(),
            ));
        }
        if self.mode == RedisMode::Cluster && self.db != 0 {
            return Err(Error::config(
                "queuefs redis cluster mode requires db=0".to_string(),
            ));
        }
        if self.mode == RedisMode::Sentinel
            && self
                .master_name
                .as_deref()
                .is_none_or(|value| value.trim().is_empty())
        {
            return Err(Error::config(
                "queuefs redis sentinel mode requires master_name".to_string(),
            ));
        }
        if self.db < 0 {
            return Err(Error::config("queuefs redis db must be >= 0".to_string()));
        }
        if self.connect_timeout_ms == 0 {
            return Err(Error::config(
                "queuefs redis connect_timeout_ms must be > 0".to_string(),
            ));
        }
        if self.command_timeout_ms == 0 {
            return Err(Error::config(
                "queuefs redis command_timeout_ms must be > 0".to_string(),
            ));
        }
        if self.key_prefix.trim().is_empty() {
            return Err(Error::config(
                "queuefs redis key_prefix must not be empty".to_string(),
            ));
        }
        if self.key_prefix.contains(['{', '}']) {
            return Err(Error::config(
                "queuefs redis key_prefix must not contain '{' or '}'".to_string(),
            ));
        }
        if self.tls_insecure_skip_verify && !self.tls_enabled {
            return Err(Error::config(
                "queuefs redis tls_insecure_skip_verify requires tls_enabled=true".to_string(),
            ));
        }
        Ok(())
    }
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
            return Err(Error::InvalidOperation("not a directory path".to_string()));
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
            "messages" => {
                let messages = backend
                    .list_unacked(&queue_name)?
                    .into_iter()
                    .map(|msg| QueueMessage {
                        id: msg.id,
                        data: String::from_utf8_lossy(&msg.data).to_string(),
                    })
                    .collect::<Vec<_>>();
                Ok(serde_json::to_vec(&messages)?)
            }
            _ => Err(Error::InvalidOperation(format!(
                "Cannot read from '{}'. Use dequeue, peek, size, or messages",
                operation
            ))),
        }
    }

    async fn write(&self, path: &str, data: &[u8], _offset: u64, _flags: WriteFlag) -> Result<u64> {
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
            return Err(Error::NotFound(format!("queue not found: {}", queue_name)));
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
                    name: queue_name
                        .split('/')
                        .last()
                        .unwrap_or(&queue_name)
                        .to_string(),
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
                mode: Self::control_file_mode(operation).ok_or_else(|| {
                    Error::NotFound(format!("control file not found: {}", operation))
                })?,
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
            return Err(Error::InvalidOperation("not a directory".to_string()));
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
                    "Queue backend (memory, sqlite, sqlite3, redis)",
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
                ConfigParameter::optional(
                    "redis",
                    "json",
                    "{}",
                    "Redis connection settings when backend=redis",
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
        let valid_backends = ["memory", "sqlite", "sqlite3", "redis"];
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
            "redis" => BackendKind::Redis,
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
            BackendKind::Memory | BackendKind::Redis => None,
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
        let redis_options = match kind {
            BackendKind::Redis => {
                let options = match config.params.get("redis") {
                    Some(crate::core::types::ConfigValue::Json(value)) => {
                        serde_json::from_value(value.clone()).map_err(|error| {
                            Error::config(format!("invalid queuefs redis config: {error}"))
                        })?
                    }
                    Some(_) => {
                        return Err(Error::config(
                            "queuefs redis config must be a JSON object".to_string(),
                        ))
                    }
                    None => RedisQueueOptions::default(),
                };
                options.validate()?;
                Some(options)
            }
            BackendKind::Memory | BackendKind::Sqlite => None,
        };

        Ok(ParsedBackendConfig {
            kind,
            sqlite_db_path,
            sqlite_options: SQLiteQueueOptions {
                recover_stale_sec,
                busy_timeout_ms,
            },
            redis_options,
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
            BackendKind::Redis => {
                Box::new(RedisQueueBackend::open(
                    parsed
                        .redis_options
                        .expect("redis options are validated for redis backend"),
                )?)
            }
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

    /// Build a QueueFS plugin config containing a nested Redis object.
    fn redis_plugin_config(redis: serde_json::Value) -> PluginConfig {
        let mut params = std::collections::HashMap::new();
        params.insert(
            "backend".to_string(),
            crate::core::types::ConfigValue::String("redis".to_string()),
        );
        params.insert(
            "redis".to_string(),
            crate::core::types::ConfigValue::Json(redis),
        );
        PluginConfig::single_backend("queuefs", "/queue", params)
    }

    /// Create a queue filesystem with one initialized queue.
    async fn queuefs_with(queue: &str) -> QueueFileSystem {
        let fs = QueueFileSystem::new();
        fs.mkdir(&format!("/{queue}"), 0o755).await.unwrap();
        fs
    }

    /// Enqueue one message into a test queue.
    async fn enqueue(fs: &dyn FileSystem, queue: &str, data: &[u8]) {
        fs.write(&format!("/{queue}/enqueue"), data, 0, WriteFlag::None)
            .await
            .unwrap();
    }

    /// Dequeue and deserialize one message from a test queue.
    async fn dequeue_msg(fs: &dyn FileSystem, queue: &str) -> TestQueueMessage {
        let result = fs.read(&format!("/{queue}/dequeue"), 0, 0).await.unwrap();
        serde_json::from_slice(&result).unwrap()
    }

    /// Read the queue size control file as usize.
    async fn queue_size(fs: &dyn FileSystem, queue: &str) -> usize {
        let size = fs.read(&format!("/{queue}/size"), 0, 0).await.unwrap();
        String::from_utf8(size).unwrap().parse().unwrap()
    }

    #[tokio::test]
    async fn test_queuefs_enqueue_dequeue() {
        let fs = queuefs_with("test").await;

        // Enqueue messages
        let data1 = b"message 1";
        let data2 = b"message 2";
        enqueue(&fs, "test", data1).await;
        enqueue(&fs, "test", data2).await;

        // Dequeue messages
        let msg1 = dequeue_msg(&fs, "test").await;
        assert!(!msg1.id.is_empty());
        assert_eq!(msg1.data.as_bytes(), data1);

        let msg2 = dequeue_msg(&fs, "test").await;
        assert!(!msg2.id.is_empty());
        assert_eq!(msg2.data.as_bytes(), data2);

        // Queue should be empty
        let result = fs.read("/test/dequeue", 0, 0).await.unwrap();
        assert_eq!(result, b"{}");
    }

    #[tokio::test]
    async fn test_queuefs_peek() {
        let fs = queuefs_with("test").await;

        // Enqueue a message
        let data = b"test message";
        enqueue(&fs, "test", data).await;

        // Peek should return the message without removing it
        let result1 = fs.read("/test/peek", 0, 0).await.unwrap();
        let msg1: TestQueueMessage = serde_json::from_slice(&result1).unwrap();
        assert_eq!(msg1.data.as_bytes(), data);

        let result2 = fs.read("/test/peek", 0, 0).await.unwrap();
        let msg2: TestQueueMessage = serde_json::from_slice(&result2).unwrap();
        assert_eq!(msg2.data.as_bytes(), data);

        // Dequeue should still work
        let msg3 = dequeue_msg(&fs, "test").await;
        assert_eq!(msg3.data.as_bytes(), data);
    }

    #[tokio::test]
    async fn test_queuefs_size() {
        let fs = queuefs_with("test").await;

        // Initially empty
        assert_eq!(queue_size(&fs, "test").await, 0);

        // Add messages
        enqueue(&fs, "test", b"msg1").await;
        enqueue(&fs, "test", b"msg2").await;

        assert_eq!(queue_size(&fs, "test").await, 2);

        // Dequeue one
        fs.read("/test/dequeue", 0, 0).await.unwrap();

        assert_eq!(queue_size(&fs, "test").await, 1);
    }

    #[tokio::test]
    async fn test_queuefs_clear() {
        let fs = queuefs_with("test").await;

        // Add messages
        enqueue(&fs, "test", b"msg1").await;
        enqueue(&fs, "test", b"msg2").await;

        // Clear the queue
        fs.write("/test/clear", b"", 0, WriteFlag::None)
            .await
            .unwrap();

        // Queue should be empty
        assert_eq!(queue_size(&fs, "test").await, 0);

        let result = fs.read("/test/dequeue", 0, 0).await;
        assert_eq!(result.unwrap(), b"{}");
    }

    #[tokio::test]
    async fn test_queuefs_read_dir() {
        let fs = queuefs_with("test").await;

        // Root should list the queue
        let entries = fs.read_dir("/").await.unwrap();
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].name, "test");
        assert!(entries[0].is_dir);

        // Queue directory should list control files
        let entries = fs.read_dir("/test").await.unwrap();
        assert_eq!(entries.len(), 7);

        let names: Vec<String> = entries.iter().map(|e| e.name.clone()).collect();
        assert!(names.contains(&"enqueue".to_string()));
        assert!(names.contains(&"dequeue".to_string()));
        assert!(names.contains(&"peek".to_string()));
        assert!(names.contains(&"size".to_string()));
        assert!(names.contains(&"messages".to_string()));
        assert!(names.contains(&"clear".to_string()));
        assert!(names.contains(&"ack".to_string()));
    }

    #[tokio::test]
    async fn test_queuefs_stat() {
        let fs = queuefs_with("test").await;

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
        let fs = queuefs_with("test").await;

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
        assert_eq!(queue_size(fs.as_ref(), "test").await, 10);

        // Dequeue all messages
        for _ in 0..10 {
            fs.read("/test/dequeue", 0, 0).await.unwrap();
        }

        // Queue should be empty
        assert_eq!(queue_size(fs.as_ref(), "test").await, 0);
    }

    #[tokio::test]
    async fn test_queuefs_plugin() {
        let plugin = QueueFSPlugin::new();

        assert_eq!(plugin.name(), "queuefs");
        assert!(!plugin.readme().is_empty());
        assert_eq!(plugin.config_params().len(), 5);

        let config =
            PluginConfig::single_backend("queuefs", "/queue", std::collections::HashMap::new());

        plugin.validate(&config).await.unwrap();
        let fs = plugin.initialize(config).await.unwrap();

        // Create a queue
        fs.mkdir("/test", 0o755).await.unwrap();

        // Test basic operation
        enqueue(fs.as_ref(), "test", b"test").await;
        let msg = dequeue_msg(fs.as_ref(), "test").await;
        assert_eq!(msg.data, "test");
    }

    #[test]
    fn test_parse_redis_backend_config() {
        let parsed = QueueFSPlugin::parse_backend_config(&redis_plugin_config(
            serde_json::json!({
                "mode": "singleton",
                "endpoints": ["redis://127.0.0.1:6379"],
                "master_name": null,
                "username": "queue-user",
                "password": "secret",
                "sentinel_username": null,
                "sentinel_password": null,
                "db": 2,
                "connect_timeout_ms": 1500,
                "command_timeout_ms": 2500,
                "key_prefix": "tenant-a",
                "tls_enabled": false,
                "tls_insecure_skip_verify": false
            }),
        ))
        .unwrap();

        assert!(matches!(parsed.kind, BackendKind::Redis));
        let options = parsed.redis_options.unwrap();
        assert_eq!(options.endpoints, vec!["redis://127.0.0.1:6379"]);
        assert_eq!(options.username.as_deref(), Some("queue-user"));
        assert_eq!(options.password.as_deref(), Some("secret"));
        assert_eq!(options.db, 2);
        assert_eq!(options.connect_timeout_ms, 1500);
        assert_eq!(options.command_timeout_ms, 2500);
        assert_eq!(options.key_prefix, "tenant-a");
    }

    #[test]
    /// Accept valid Cluster and Sentinel topology configurations.
    fn test_parse_redis_backend_config_accepts_high_availability_modes() {
        let cluster = QueueFSPlugin::parse_backend_config(&redis_plugin_config(
            serde_json::json!({
                "mode": "cluster",
                "endpoints": ["redis://cluster-1:6379", "redis://cluster-2:6379"],
                "db": 0
            }),
        ));
        assert!(cluster.is_ok());

        let sentinel = QueueFSPlugin::parse_backend_config(&redis_plugin_config(
            serde_json::json!({
                "mode": "sentinel",
                "endpoints": ["redis://sentinel-1:26379", "redis://sentinel-2:26379"],
                "master_name": "mymaster"
            }),
        ));
        assert!(sentinel.is_ok());
    }

    #[test]
    /// Reject topology settings that violate the selected Redis mode.
    fn test_parse_redis_backend_config_rejects_invalid_mode_settings() {
        let cases = [
            (serde_json::json!({"mode": "invalid"}), "invalid"),
            (
                serde_json::json!({
                    "mode": "singleton",
                    "endpoints": ["redis://redis-1:6379", "redis://redis-2:6379"]
                }),
                "singleton",
            ),
            (
                serde_json::json!({"mode": "cluster", "endpoints": []}),
                "endpoints",
            ),
            (serde_json::json!({"mode": "cluster", "db": 1}), "db"),
            (
                serde_json::json!({
                    "mode": "sentinel",
                    "endpoints": [],
                    "master_name": "mymaster"
                }),
                "endpoints",
            ),
            (
                serde_json::json!({"mode": "sentinel", "master_name": ""}),
                "master_name",
            ),
            (
                serde_json::json!({"key_prefix": "invalid{tag}"}),
                "key_prefix",
            ),
        ];

        for (redis, expected) in cases {
            let error = QueueFSPlugin::parse_backend_config(&redis_plugin_config(redis))
                .unwrap_err()
                .to_string();
            assert!(
                error.contains(expected),
                "expected {expected:?} in {error:?}"
            );
        }
    }

    #[test]
    fn test_parse_redis_backend_config_rejects_empty_endpoints() {
        let mut params = std::collections::HashMap::new();
        params.insert(
            "backend".to_string(),
            crate::core::types::ConfigValue::String("redis".to_string()),
        );
        params.insert(
            "redis".to_string(),
            crate::core::types::ConfigValue::Json(serde_json::json!({
                "endpoints": []
            })),
        );
        let config = PluginConfig::single_backend("queuefs", "/queue", params);

        assert!(QueueFSPlugin::parse_backend_config(&config)
            .unwrap_err()
            .to_string()
            .contains("endpoints"));
    }

    #[test]
    fn test_parse_redis_backend_config_rejects_invalid_endpoint() {
        let mut params = std::collections::HashMap::new();
        params.insert(
            "backend".to_string(),
            crate::core::types::ConfigValue::String("redis".to_string()),
        );
        params.insert(
            "redis".to_string(),
            crate::core::types::ConfigValue::Json(serde_json::json!({
                "endpoints": ["http://127.0.0.1:6379"]
            })),
        );
        let config = PluginConfig::single_backend("queuefs", "/queue", params);

        assert!(QueueFSPlugin::parse_backend_config(&config)
            .unwrap_err()
            .to_string()
            .contains("redis:// or rediss://"));
    }

    #[test]
    /// Reject an empty Redis namespace prefix.
    fn test_parse_redis_backend_config_rejects_empty_key_prefix() {
        let mut params = std::collections::HashMap::new();
        params.insert(
            "backend".to_string(),
            crate::core::types::ConfigValue::String("redis".to_string()),
        );
        params.insert(
            "redis".to_string(),
            crate::core::types::ConfigValue::Json(serde_json::json!({
                "key_prefix": ""
            })),
        );
        let config = PluginConfig::single_backend("queuefs", "/queue", params);

        assert!(QueueFSPlugin::parse_backend_config(&config)
            .unwrap_err()
            .to_string()
            .contains("key_prefix"));
    }

    #[test]
    /// Reject the removed Redis connection pool setting.
    fn test_parse_redis_backend_config_rejects_removed_pool_max_size() {
        let mut params = std::collections::HashMap::new();
        params.insert(
            "backend".to_string(),
            crate::core::types::ConfigValue::String("redis".to_string()),
        );
        params.insert(
            "redis".to_string(),
            crate::core::types::ConfigValue::Json(serde_json::json!({
                "pool_max_size": 16
            })),
        );
        let config = PluginConfig::single_backend("queuefs", "/queue", params);

        assert!(QueueFSPlugin::parse_backend_config(&config)
            .unwrap_err()
            .to_string()
            .contains("pool_max_size"));
    }

    #[tokio::test]
    async fn test_multi_queue() {
        let fs = QueueFileSystem::new();

        // Create two queues
        fs.mkdir("/Embedding", 0o755).await.unwrap();
        fs.mkdir("/Semantic", 0o755).await.unwrap();

        // Enqueue to both
        enqueue(&fs, "Embedding", b"embed1").await;
        enqueue(&fs, "Semantic", b"semantic1").await;

        // Verify isolation
        assert_eq!(queue_size(&fs, "Embedding").await, 1);
        assert_eq!(queue_size(&fs, "Semantic").await, 1);

        // Dequeue from specific queue
        let msg = dequeue_msg(&fs, "Embedding").await;
        assert_eq!(msg.data, "embed1");

        // Other queue unaffected
        assert_eq!(queue_size(&fs, "Semantic").await, 1);
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
        enqueue(&fs, "logs/errors", b"error1").await;
        let msg = dequeue_msg(&fs, "logs/errors").await;
        assert_eq!(msg.data, "error1");
    }

    #[tokio::test]
    async fn test_queue_lifecycle() {
        let fs = QueueFileSystem::new();

        // Create queue
        fs.mkdir("/temp", 0o755).await.unwrap();
        enqueue(&fs, "temp", b"data").await;

        // Verify exists
        assert_eq!(queue_size(&fs, "temp").await, 1);

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
