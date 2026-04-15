//! Core types for RAGFS
//!
//! This module defines the fundamental data structures used throughout RAGFS,
//! including file metadata, write flags, and configuration types.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::time::SystemTime;

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
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
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
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PluginConfig {
    /// Plugin name
    pub name: String,

    /// Mount path
    pub mount_path: String,

    /// Configuration parameters
    pub params: HashMap<String, ConfigValue>,
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
