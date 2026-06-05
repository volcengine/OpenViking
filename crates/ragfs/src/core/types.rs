//! Core types for RAGFS
//!
//! This module defines the fundamental data structures used throughout RAGFS,
//! including file metadata, write flags, and configuration types.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::time::SystemTime;

/// Grep match result
///
/// Represents a single match found during a grep operation.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GrepMatch {
    /// File path where the match was found
    pub file: String,

    /// Line number (1-based)
    pub line: u64,

    /// Content of the matched line
    pub content: String,
}

/// Grep operation result
///
/// Contains all matches found during a grep operation.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GrepResult {
    /// List of matches
    pub matches: Vec<GrepMatch>,

    /// Total number of matches
    pub count: usize,
}

/// Tree traversal entry.
///
/// Represents one flattened node in a recursive directory traversal.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TreeEntry {
    /// Internal trait contract: plugin-root-relative absolute path,
    /// e.g. "/a/file.txt".
    ///
    /// External bindings contract: after `MountableFS.tree_directory()`
    /// rewrites the mount prefix back, Python-visible `path` must be a
    /// global AGFS absolute path such as "/local/{account}/resources/a/file.txt".
    pub path: String,

    /// Path relative to traversal root, e.g. "a/file.txt"
    pub rel_path: String,

    /// File metadata for this node.
    pub info: FileInfo,

    /// Backend-specific fields required for zero-regression original output.
    pub extra: HashMap<String, serde_json::Value>,
}

impl GrepResult {
    /// Create a new empty GrepResult
    pub fn new() -> Self {
        Self {
            matches: Vec::new(),
            count: 0,
        }
    }

    /// Create a GrepResult from a list of matches
    pub fn from_matches(matches: Vec<GrepMatch>) -> Self {
        let count = matches.len();
        Self { matches, count }
    }

    /// Add a match to the result
    pub fn add_match(&mut self, file: String, line: u64, content: String) {
        self.matches.push(GrepMatch {
            file,
            line,
            content,
        });
        self.count += 1;
    }

    /// Limit the number of matches
    pub fn limit(&mut self, max_count: usize) {
        if self.matches.len() > max_count {
            self.matches.truncate(max_count);
            self.count = max_count;
        }
    }
}

impl Default for GrepResult {
    fn default() -> Self {
        Self::new()
    }
}

/// File metadata information
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FileInfo {
    /// File name (without path)
    pub name: String,

    /// File size in bytes
    pub size: u64,

    /// File mode/permissions (Unix-style)
    pub mode: u32,

    /// Last modification time
    #[serde(with = "systemtime_serde")]
    pub mod_time: SystemTime,

    /// Whether this is a directory
    pub is_dir: bool,
}

impl FileInfo {
    /// Create a new FileInfo for a file
    pub fn new_file(name: String, size: u64, mode: u32) -> Self {
        Self {
            name,
            size,
            mode,
            mod_time: SystemTime::now(),
            is_dir: false,
        }
    }

    /// Create a new FileInfo for a directory
    pub fn new_dir(name: String, mode: u32) -> Self {
        Self {
            name,
            size: 0,
            mode,
            mod_time: SystemTime::now(),
            is_dir: true,
        }
    }

    /// Create a new FileInfo with all parameters
    pub fn new(name: String, size: u64, mode: u32, mod_time: SystemTime, is_dir: bool) -> Self {
        Self {
            name,
            size,
            mode,
            mod_time,
            is_dir,
        }
    }
}

/// Write operation flags
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum WriteFlag {
    /// Create new file or truncate existing
    Create,

    /// Append to existing file
    Append,

    /// Truncate file before writing
    Truncate,

    /// Write at specific offset (default)
    None,
}

impl Default for WriteFlag {
    fn default() -> Self {
        Self::None
    }
}

/// Plugin configuration parameter metadata
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ConfigParameter {
    /// Parameter name
    pub name: String,

    /// Parameter type: "string", "int", "bool", "string_list"
    #[serde(rename = "type")]
    pub param_type: String,

    /// Whether this parameter is required
    pub required: bool,

    /// Default value (if not required)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub default: Option<String>,

    /// Human-readable description
    pub description: String,
}

impl ConfigParameter {
    /// Create a required string parameter
    pub fn required_string(name: impl Into<String>, description: impl Into<String>) -> Self {
        Self {
            name: name.into(),
            param_type: "string".to_string(),
            required: true,
            default: None,
            description: description.into(),
        }
    }

    /// Create an optional parameter with default
    pub fn optional(
        name: impl Into<String>,
        param_type: impl Into<String>,
        default: impl Into<String>,
        description: impl Into<String>,
    ) -> Self {
        Self {
            name: name.into(),
            param_type: param_type.into(),
            required: false,
            default: Some(default.into()),
            description: description.into(),
        }
    }
}

/// Plugin configuration
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct PluginConfig {
    /// Plugin name
    pub name: String,

    /// Mount path
    pub mount_path: String,

    /// Configuration parameters
    pub params: HashMap<String, ConfigValue>,

    /// Multi-write backups config (None = single backend mode)
    #[serde(default)]
    pub backups: Option<BackendsConfig>,

    /// Global encryption enabled (server.encryption.enabled)
    #[serde(default)]
    pub server_encryption_enabled: bool,

    /// Primary encryption enabled (follows global, not independently configurable)
    #[serde(default)]
    pub primary_encryption_enabled: bool,

    /// Primary redirect policies
    #[serde(default)]
    pub primary_redirects: Vec<RedirectPolicy>,
}

/// Configuration value types
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(untagged)]
pub enum ConfigValue {
    /// String value
    String(String),

    /// Integer value
    Int(i64),

    /// Boolean value
    Bool(bool),

    /// List of strings
    StringList(Vec<String>),

    /// Nested JSON value (for complex config like backups)
    Json(serde_json::Value),
}

impl ConfigValue {
    /// Try to get as string
    pub fn as_string(&self) -> Option<&str> {
        match self {
            ConfigValue::String(s) => Some(s),
            _ => None,
        }
    }

    /// Try to get as integer
    pub fn as_int(&self) -> Option<i64> {
        match self {
            ConfigValue::Int(i) => Some(*i),
            _ => None,
        }
    }

    /// Try to get as boolean
    pub fn as_bool(&self) -> Option<bool> {
        match self {
            ConfigValue::Bool(b) => Some(*b),
            _ => None,
        }
    }

    /// Try to get as string list
    pub fn as_string_list(&self) -> Option<&[String]> {
        match self {
            ConfigValue::StringList(list) => Some(list),
            _ => None,
        }
    }
}

// ── Multi-write configuration types ──

/// Multi-write backends container configuration
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BackendsConfig {
    /// Sync type: "sync" or "async", default async
    #[serde(default = "default_sync_type")]
    pub sync_type: String,
    /// Minimum backup ack count for sync mode
    pub write_ack_count: Option<usize>,
    /// Timeout for waiting backup ack in sync mode (ms)
    pub write_ack_timeout_ms: Option<u64>,
    /// Max concurrent async writes
    pub write_concurrency: Option<usize>,
    /// Backup items
    pub items: Vec<BackendItemConfig>,
}

fn default_sync_type() -> String {
    "async".to_string()
}

/// Single backup backend item configuration
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BackendItemConfig {
    /// Logical name, globally unique
    pub name: String,
    /// Plugin type (local/s3/memfs/kvfs/...)
    pub backend: String,
    /// Plugin-specific params (nested JSON)
    #[serde(default)]
    pub params: serde_json::Value,
    /// Timeout in seconds
    pub timeout: Option<u64>,
    /// Encryption config for this backup
    pub encryption: Option<EncryptionConfig>,
    /// Operations this backup participates in
    pub operations: Option<Vec<OperationItemConfig>>,
    /// Exclude policies
    pub excludes: Option<Vec<RedirectPolicy>>,
}

/// Per-operation priority config
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OperationItemConfig {
    /// Operation type: "read" | "write"
    pub operation: String,
    /// Priority (smaller = higher priority)
    pub priority: u32,
}

/// Encryption on/off config for a backend
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EncryptionConfig {
    /// Whether encryption is enabled for this backend
    pub enabled: bool,
}

/// Redirect / exclude policy (shared trait)
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type")]
pub enum RedirectPolicy {
    /// Redirect/exclude files exceeding a size threshold
    #[serde(rename = "FileOverSizePolicy")]
    FileOverSizePolicy {
        /// Max file size in MB
        max_size_mb: u64,
        /// Target backend names (redirect only)
        target: Option<Vec<String>>,
    },
    /// Redirect/exclude files matching extension patterns
    #[serde(rename = "FileExtensionPolicy")]
    FileExtensionPolicy {
        /// Regex patterns for file extensions
        extensions: Vec<String>,
        /// Target backend names (redirect only)
        target: Option<Vec<String>>,
    },
}

/// Strongly typed multi-write operation stored in `.sync_log.json`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "type")]
pub enum SyncOp {
    /// Create an empty file.
    Create,
    /// Write the captured request bytes with the original offset and flags.
    Write {
        /// Original write offset.
        offset: u64,
        /// Original write flags.
        flags: WriteFlag,
        /// File size used by retry/exclude policy decisions.
        size: u64,
        /// Captured request payload.
        content: Vec<u8>,
    },
    /// Truncate to size, with a post-primary snapshot for missing lagging replicas.
    Truncate {
        /// Requested target size.
        size: u64,
        /// Post-truncate primary snapshot.
        content: Vec<u8>,
    },
    /// Create a directory.
    Mkdir {
        /// Directory mode.
        mode: u32,
    },
    /// Change file mode.
    Chmod {
        /// File mode.
        mode: u32,
    },
    /// Remove one file.
    Remove,
    /// Remove a file tree.
    RemoveAll,
    /// Rename to another path.
    Rename {
        /// Rename target path.
        to: String,
    },
}

/// Per-backend sync state
#[derive(Debug, Clone, Serialize, Deserialize, Default, PartialEq, Eq)]
pub struct BackendSyncState {
    /// Acknowledged sequence number
    pub acked_seq: u64,
    /// Consecutive retry failures for the current operation.
    #[serde(default)]
    pub retry_failures: u32,
    /// Whether this backend/path pair is quarantined for manual intervention.
    #[serde(default)]
    pub quarantined: bool,
}

impl BackendSyncState {
    /// Create an acknowledged backend sync state.
    pub fn acked(acked_seq: u64) -> Self {
        Self {
            acked_seq,
            retry_failures: 0,
            quarantined: false,
        }
    }

    /// Mark this backend as acknowledged and clear retry state.
    pub fn mark_acked(&mut self, acked_seq: u64) {
        self.acked_seq = acked_seq;
        self.retry_failures = 0;
        self.quarantined = false;
    }

    /// Record one retry failure and quarantine after the configured threshold.
    pub fn mark_retry_failed(&mut self, quarantine_after_failures: u32) {
        self.retry_failures = self.retry_failures.saturating_add(1);
        if self.retry_failures >= quarantine_after_failures {
            self.quarantined = true;
        }
    }
}

/// Sync log entry for a single file path.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct SyncLogEntry {
    /// Latest monotonic sequence number.
    pub latest_seq: u64,
    /// Strongly typed operation payload.
    pub op: SyncOp,
    /// Per-backend acked sequence numbers.
    pub backends: std::collections::HashMap<String, BackendSyncState>,
}

impl SyncLogEntry {
    /// Create a new sync log entry for a sequenced operation.
    pub fn new(latest_seq: u64, op: SyncOp) -> Self {
        Self {
            latest_seq,
            op,
            backends: std::collections::HashMap::new(),
        }
    }

    /// Return the backend sync state if present.
    pub fn backend_state(&self, backend_name: &str) -> Option<&BackendSyncState> {
        self.backends.get(backend_name)
    }

    /// Return the acknowledged sequence for a backend, or 0 if unknown.
    pub fn acked_seq(&self, backend_name: &str) -> u64 {
        self.backend_state(backend_name)
            .map(|state| state.acked_seq)
            .unwrap_or(0)
    }

    /// Return whether the backend is quarantined.
    pub fn is_quarantined(&self, backend_name: &str) -> bool {
        self.backend_state(backend_name)
            .map(|state| state.quarantined)
            .unwrap_or(false)
    }

    /// Return whether the backend has acknowledged the latest operation.
    pub fn is_in_sync(&self, backend_name: &str) -> bool {
        self.acked_seq(backend_name) >= self.latest_seq
    }
}

/// Sync log file content
#[derive(Debug, Clone, Serialize, Deserialize, Default, PartialEq, Eq)]
pub struct SyncLogMeta {
    /// File entries keyed by file name (current directory)
    pub entries: std::collections::HashMap<String, SyncLogEntry>,
}

/// Redirect metadata file content
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct RedirectMeta {
    /// Schema version
    #[serde(default = "default_redirect_version")]
    pub version: u32,
    /// Redirect entries keyed by file name
    #[serde(default)]
    pub entries: std::collections::HashMap<String, RedirectEntry>,
}

fn default_redirect_version() -> u32 {
    1
}

impl Default for RedirectMeta {
    fn default() -> Self {
        Self {
            version: 1,
            entries: HashMap::new(),
        }
    }
}

/// Single redirect entry
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct RedirectEntry {
    /// Target backend names
    pub targets: Vec<String>,
}

/// Backend role
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BackendRole {
    /// Primary backend (authoritative source)
    Primary,
    /// Backup backend (replica)
    Backup,
}

/// Sync type enum
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SyncType {
    /// Synchronous: wait for backup ack before returning
    Sync,
    /// Asynchronous: return after primary write, sync in background
    Async,
}

/// Custom serde module for SystemTime
mod systemtime_serde {
    use serde::{Deserialize, Deserializer, Serialize, Serializer};
    use std::time::{SystemTime, UNIX_EPOCH};

    pub fn serialize<S>(time: &SystemTime, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        let duration = time
            .duration_since(UNIX_EPOCH)
            .map_err(serde::ser::Error::custom)?;
        duration.as_secs().serialize(serializer)
    }

    pub fn deserialize<'de, D>(deserializer: D) -> Result<SystemTime, D::Error>
    where
        D: Deserializer<'de>,
    {
        let secs = u64::deserialize(deserializer)?;
        Ok(UNIX_EPOCH + std::time::Duration::from_secs(secs))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_file_info_creation() {
        let file = FileInfo::new_file("test.txt".to_string(), 1024, 0o644);
        assert_eq!(file.name, "test.txt");
        assert_eq!(file.size, 1024);
        assert!(!file.is_dir);

        let dir = FileInfo::new_dir("testdir".to_string(), 0o755);
        assert_eq!(dir.name, "testdir");
        assert!(dir.is_dir);
    }

    #[test]
    fn test_config_value() {
        let val = ConfigValue::String("test".to_string());
        assert_eq!(val.as_string(), Some("test"));
        assert_eq!(val.as_int(), None);

        let val = ConfigValue::Int(42);
        assert_eq!(val.as_int(), Some(42));
        assert_eq!(val.as_string(), None);
    }

    #[test]
    fn test_config_parameter() {
        let param = ConfigParameter::required_string("host", "Database host");
        assert_eq!(param.name, "host");
        assert!(param.required);
        assert_eq!(param.param_type, "string");
    }
}
