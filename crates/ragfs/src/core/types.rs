//! Core types for RAGFS
//!
//! This module defines the fundamental data structures used throughout RAGFS,
//! including file metadata, write flags, and configuration types.

use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::time::SystemTime;

/// Supported directory listing sort fields.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ListSortBy {
    /// Sort entries by name.
    Name,
    /// Sort entries by modification time.
    Mtime,
}

/// Supported directory listing sort directions.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SortOrder {
    /// Sort values in ascending order.
    Asc,
    /// Sort values in descending order.
    Desc,
}

/// Options for a grep operation.
#[derive(Debug, Clone, Copy)]
pub struct GrepOptions<'a> {
    /// Whether to search recursively in subdirectories.
    pub recursive: bool,
    /// Whether matching is case-insensitive.
    pub case_insensitive: bool,
    /// Maximum number of matches to return.
    pub node_limit: Option<usize>,
    /// Optional path prefix to exclude.
    pub exclude_path: Option<&'a str>,
    /// Optional maximum depth relative to the query root.
    pub level_limit: Option<usize>,
    /// Number of lines to include before each match.
    pub before_context: usize,
    /// Number of lines to include after each match.
    pub after_context: usize,
}

impl Default for GrepOptions<'_> {
    fn default() -> Self {
        Self {
            recursive: false,
            case_insensitive: false,
            node_limit: None,
            exclude_path: None,
            level_limit: None,
            before_context: 0,
            after_context: 0,
        }
    }
}

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

    /// Lines immediately before the match
    #[serde(skip_serializing_if = "Option::is_none")]
    pub before_context: Option<Vec<GrepContextLine>>,

    /// Lines immediately after the match
    #[serde(skip_serializing_if = "Option::is_none")]
    pub after_context: Option<Vec<GrepContextLine>>,
}

/// One line included as grep context.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GrepContextLine {
    /// Line number (1-based)
    pub line: u64,

    /// Line content
    pub content: String,
}

impl GrepMatch {
    /// Build a grep match and slice optional context from already-read lines.
    pub fn from_lines(
        file: String,
        lines: &[&str],
        line_index: usize,
        before_context: usize,
        after_context: usize,
    ) -> Self {
        let before = (before_context > 0).then(|| {
            let start = line_index.saturating_sub(before_context);
            (start..line_index)
                .map(|index| GrepContextLine {
                    line: (index + 1) as u64,
                    content: lines[index].to_string(),
                })
                .collect()
        });
        let after = (after_context > 0).then(|| {
            let end = lines.len().min(line_index + after_context + 1);
            (line_index + 1..end)
                .map(|index| GrepContextLine {
                    line: (index + 1) as u64,
                    content: lines[index].to_string(),
                })
                .collect()
        });

        Self {
            file,
            line: (line_index + 1) as u64,
            content: lines[line_index].to_string(),
            before_context: before,
            after_context: after,
        }
    }
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

/// Flat glob match entry.
///
/// Represents one path matched by `glob_directory`, preserving enough metadata
/// for Python-side visibility and URI alias handling without reconstructing a
/// full `TreeEntry`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GlobEntry {
    /// Plugin-root-relative absolute path, matching the `TreeEntry.path`
    /// contract after mount rewriting.
    pub path: String,

    /// Path relative to the glob query root.
    pub rel_path: String,

    /// Final path component.
    pub name: String,

    /// Whether the matched entry is a directory.
    pub is_dir: bool,
}

/// One page of glob results.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GlobPage {
    /// Matched entries for this page.
    pub entries: Vec<GlobEntry>,

    /// Opaque continuation token for the next page.
    pub next_token: Option<String>,
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
            before_context: None,
            after_context: None,
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
    /// Create new file or truncate existing.
    ///
    /// Follows POSIX `O_CREAT` / Rust `OpenOptions::create(true)` convention:
    /// create-or-open (non-exclusive). For exclusive create-if-absent, use
    /// `CreateNew` (POSIX `O_CREAT | O_EXCL` / Rust `create_new(true)`).
    Create,

    /// Create new file, fail if already exists
    CreateNew,

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
#[derive(Debug, Clone, Serialize, Deserialize)]
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
}

impl Default for PluginConfig {
    /// Build an empty plugin config with all optional multi-write fields disabled.
    fn default() -> Self {
        Self {
            name: String::new(),
            mount_path: String::new(),
            params: HashMap::new(),
            backups: None,
            server_encryption_enabled: false,
            primary_encryption_enabled: false,
        }
    }
}

impl PluginConfig {
    /// Build one single-backend plugin config with defaulted optional fields.
    pub fn single_backend(
        name: impl Into<String>,
        mount_path: impl Into<String>,
        params: HashMap<String, ConfigValue>,
    ) -> Self {
        Self {
            name: name.into(),
            mount_path: mount_path.into(),
            params,
            ..Self::default()
        }
    }

    /// Build one plugin config from raw params, extracting multi-write fields in one place.
    pub fn from_raw_parts(
        name: impl Into<String>,
        mount_path: impl Into<String>,
        mut params: HashMap<String, ConfigValue>,
    ) -> crate::core::Result<Self> {
        let backups = Self::take_optional_json("backups", &mut params)?;
        let server_encryption_enabled =
            Self::take_bool_with_default("server_encryption_enabled", &mut params, false)?;
        let primary_encryption_enabled =
            Self::take_bool_with_default("primary_encryption_enabled", &mut params, false)?;

        Ok(Self {
            name: name.into(),
            mount_path: mount_path.into(),
            params,
            backups,
            server_encryption_enabled,
            primary_encryption_enabled,
        })
    }

    /// Remove one optional bool config value and type-check it.
    fn take_bool_with_default(
        field_name: &str,
        params: &mut HashMap<String, ConfigValue>,
        default: bool,
    ) -> crate::core::Result<bool> {
        match params.remove(field_name) {
            None => Ok(default),
            Some(ConfigValue::Bool(value)) => Ok(value),
            Some(other) => Err(crate::core::Error::config(format!(
                "'{}' must be a boolean, got {}",
                field_name,
                Self::config_value_kind(&other)
            ))),
        }
    }

    /// Remove one optional JSON config value and deserialize it when present.
    fn take_optional_json<T: DeserializeOwned>(
        field_name: &str,
        params: &mut HashMap<String, ConfigValue>,
    ) -> crate::core::Result<Option<T>> {
        match params.remove(field_name) {
            None => Ok(None),
            Some(ConfigValue::Json(value)) => {
                let parsed = serde_json::from_value(value).map_err(|err| {
                    crate::core::Error::config(format!("invalid '{}': {}", field_name, err))
                })?;
                Ok(Some(parsed))
            }
            Some(other) => Err(crate::core::Error::config(format!(
                "'{}' must be a JSON object/array, got {}",
                field_name,
                Self::config_value_kind(&other)
            ))),
        }
    }

    /// Return a human-readable config value kind for error messages.
    fn config_value_kind(value: &ConfigValue) -> &'static str {
        match value {
            ConfigValue::String(_) => "string",
            ConfigValue::Int(_) => "integer",
            ConfigValue::Bool(_) => "boolean",
            ConfigValue::StringList(_) => "string_list",
            ConfigValue::Json(_) => "json",
        }
    }
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
    /// Cache key namespace used by cache-backed metadata.
    #[serde(default = "default_backups_namespace")]
    pub namespace: String,
    /// Initial number of metadata partitions.
    #[serde(default = "default_initial_partitions")]
    pub initial_partitions: u32,
    /// Checkpoint interval in seconds.
    #[serde(default = "default_checkpoint_interval_secs")]
    pub checkpoint_interval_secs: u64,
    /// Metadata storage provider.
    #[serde(default = "default_backups_provider")]
    pub provider: String,
    /// Backup items
    #[serde(default)]
    pub items: Vec<BackendItemConfig>,
}

/// Return the default cache-backed metadata namespace.
fn default_backups_namespace() -> String {
    "default".to_string()
}

/// Return the default metadata partition count.
fn default_initial_partitions() -> u32 {
    16
}

/// Return the default checkpoint interval in seconds.
fn default_checkpoint_interval_secs() -> u64 {
    86_400
}

/// Return the default metadata provider.
fn default_backups_provider() -> String {
    "filesystem".to_string()
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
    /// Encryption config for this backup
    pub encryption: Option<EncryptionConfig>,
}

/// Encryption on/off config for a backend
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EncryptionConfig {
    /// Whether encryption is enabled for this backend
    pub enabled: bool,
}

/// Backend role
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BackendRole {
    /// Primary backend (authoritative source)
    Primary,
    /// Backup backend (replica)
    Backup,
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

    #[test]
    fn test_plugin_config_from_raw_parts_handles_multiwrite_shapes() {
        let mut valid_params = HashMap::new();
        valid_params.insert(
            "root_path".to_string(),
            ConfigValue::String("/tmp/data".to_string()),
        );
        valid_params.insert(
            "backups".to_string(),
            ConfigValue::Json(serde_json::json!({
                "items": [{"name": "backup1", "backend": "memory"}]
            })),
        );
        valid_params.insert(
            "server_encryption_enabled".to_string(),
            ConfigValue::Bool(true),
        );
        valid_params.insert(
            "primary_encryption_enabled".to_string(),
            ConfigValue::Bool(true),
        );
        let config = PluginConfig::from_raw_parts("localfs", "/local", valid_params).unwrap();
        assert!(config.backups.is_some());
        assert!(config.server_encryption_enabled);
        assert!(config.primary_encryption_enabled);
        assert_eq!(
            config.params.get("root_path"),
            Some(&ConfigValue::String("/tmp/data".to_string()))
        );
        assert!(!config.params.contains_key("backups"));
        assert!(!config.params.contains_key("server_encryption_enabled"));
        assert!(!config.params.contains_key("primary_encryption_enabled"));

        for (params, expected_message) in [
            (
                HashMap::from([(
                    "backups".to_string(),
                    ConfigValue::Json(serde_json::json!({"items": "invalid"})),
                )]),
                "invalid 'backups'",
            ),
            (
                HashMap::from([(
                    "server_encryption_enabled".to_string(),
                    ConfigValue::String("true".to_string()),
                )]),
                "'server_encryption_enabled' must be a boolean",
            ),
        ] {
            let err = PluginConfig::from_raw_parts("localfs", "/local", params).unwrap_err();
            assert!(matches!(err, crate::core::Error::Config(_)));
            assert!(err.to_string().contains(expected_message));
        }
    }
}
