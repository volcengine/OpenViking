//! ServerInfoFS plugin - Server metadata and information
//!
//! This plugin provides runtime information about RAGFS server.

use async_trait::async_trait;
use std::time::{Duration, Instant, UNIX_EPOCH};

use crate::core::errors::{Error, Result};
use crate::core::filesystem::FileSystem;
use crate::core::plugin::ServicePlugin;
use crate::core::types::{ConfigParameter, FileInfo, PluginConfig, WriteFlag};

/// ServerInfoFS - Server metadata filesystem
pub struct ServerInfoFileSystem {
    /// Server start time
    start_time: Instant,
    /// Server version
    version: String,
}

impl ServerInfoFileSystem {
    /// Create a new ServerInfoFileSystem
    pub fn new(version: &str) -> Self {
        Self {
            start_time: Instant::now(),
            version: version.to_string(),
        }
    }

    /// Check if path is valid
    fn is_valid_path(path: &str) -> bool {
        matches!(
            path,
            "/" | "/server_info" | "/uptime" | "/version" | "/stats" | "/README"
        )
    }

    /// Get server info as JSON
    fn get_server_info(&self) -> String {
        let uptime = self.start_time.elapsed();
        let uptime_secs = uptime.as_secs();

        format!(
            r#"{{
  "version": "{}",
  "uptime": "{}",
  "start_time": "{}",
  "rust_version": "{}"
}}"#,
            self.version,
            format_duration(uptime),
            format_timestamp(UNIX_EPOCH.elapsed().unwrap_or(Duration::from_secs(0)).as_secs() - uptime_secs),
            env!("CARGO_PKG_RUST_VERSION")
        )
    }

    /// Get uptime string
    fn get_uptime(&self) -> String {
        format_duration(self.start_time.elapsed())
    }

    /// Get stats as JSON
    fn get_stats(&self) -> String {
        format!(
            r#"{{
  "uptime_seconds": {},
  "uptime": "{}"
}}"#,
            self.start_time.elapsed().as_secs(),
            format_duration(self.start_time.elapsed())
        )
    }

    /// Get readme content
    fn get_readme(&self) -> String {
        format!(
            r#"ServerInfoFS Plugin - Server Metadata and Information

This plugin provides runtime information about RAGFS server.

USAGE:
  View server version:
    cat /serverinfofs/version

  View server uptime:
    cat /serverinfofs/uptime

  View server info:
    cat /serverinfofs/server_info

  View runtime stats:
    cat /serverinfofs/stats

FILES:
  /server_info  - Complete server information (JSON)
  /uptime       - Server uptime since start
  /version      - Server version
  /stats        - Runtime statistics
  /README       - This file

EXAMPLES:
  # Check server version
  agfs:/> cat /serverinfofs/version
  {}

  # Check uptime
  agfs:/> cat /serverinfofs/uptime
  {}

  # Get complete info
  agfs:/> cat /serverinfofs/server_info
  {{
    "version": "{}",
    "uptime": "{}",
    ...
  }}

VERSION: 1.0.0
"#,
            self.version,
            format_duration(self.start_time.elapsed()),
            self.version,
            format_duration(self.start_time.elapsed())
        )
    }
}

#[async_trait]
impl FileSystem for ServerInfoFileSystem {
    async fn create(&self, _path: &str) -> Result<()> {
        Err(Error::plugin("operation not permitted: serverinfofs is read-only".to_string()))
    }

    async fn mkdir(&self, _path: &str, _mode: u32) -> Result<()> {
        Err(Error::plugin("operation not permitted: serverinfofs is read-only".to_string()))
    }

    async fn remove(&self, _path: &str) -> Result<()> {
        Err(Error::plugin("operation not permitted: serverinfofs is read-only".to_string()))
    }

    async fn remove_all(&self, _path: &str) -> Result<()> {
        Err(Error::plugin("operation not permitted: serverinfofs is read-only".to_string()))
    }

    async fn read(&self, path: &str, offset: u64, size: u64) -> Result<Vec<u8>> {
        if !Self::is_valid_path(path) {
            return Err(Error::NotFound(path.to_string()));
        }

        if path == "/" {
            return Err(Error::plugin("is a directory: /".to_string()));
        }

        let data = match path {
            "/server_info" => self.get_server_info(),
            "/uptime" => self.get_uptime(),
            "/version" => self.version.clone(),
            "/stats" => self.get_stats(),
            "/README" => self.get_readme(),
            _ => return Err(Error::NotFound(path.to_string())),
        };

        // Add newline if not present
        let data = if data.ends_with('\n') {
            data
        } else {
            format!("{}\n", data)
        };

        // Apply offset and size
        let bytes = data.as_bytes();
        let file_size = bytes.len() as u64;
        let start = offset.min(file_size) as usize;
        let end = if size == 0 {
            bytes.len()
        } else {
            (offset + size).min(file_size) as usize
        };

        if start >= bytes.len() {
            Ok(vec![])
        } else {
            Ok(bytes[start..end].to_vec())
        }
    }

    async fn write(&self, _path: &str, _data: &[u8], _offset: u64, _flags: WriteFlag) -> Result<u64> {
        Err(Error::plugin("operation not permitted: serverinfofs is read-only".to_string()))
    }

    async fn read_dir(&self, path: &str) -> Result<Vec<FileInfo>> {
        if path != "/" {
            return Err(Error::plugin(format!("not a directory: {}", path)));
        }

        let now = std::time::SystemTime::now();

        // Generate content for each file to get accurate sizes
        let server_info = self.get_server_info();
        let uptime = self.get_uptime();
        let version = self.version.clone();
        let stats = self.get_stats();
        let readme = self.get_readme();

        Ok(vec![
            FileInfo::new("README".to_string(), readme.len() as u64, 0o444, now, false),
            FileInfo::new("server_info".to_string(), server_info.len() as u64, 0o444, now, false),
            FileInfo::new("uptime".to_string(), uptime.len() as u64, 0o444, now, false),
            FileInfo::new("version".to_string(), version.len() as u64, 0o444, now, false),
            FileInfo::new("stats".to_string(), stats.len() as u64, 0o444, now, false),
        ])
    }

    async fn stat(&self, path: &str) -> Result<FileInfo> {
        if !Self::is_valid_path(path) {
            return Err(Error::NotFound(path.to_string()));
        }

        let now = std::time::SystemTime::now();

        if path == "/" {
            return Ok(FileInfo::new("/".to_string(), 0, 0o555, now, true));
        }

        // For files, read content to get size
        let data = match path {
            "/server_info" => self.get_server_info(),
            "/uptime" => self.get_uptime(),
            "/version" => self.version.clone(),
            "/stats" => self.get_stats(),
            "/README" => self.get_readme(),
            _ => return Err(Error::NotFound(path.to_string())),
        };

        let name = path.strip_prefix('/').unwrap_or(path);
        Ok(FileInfo::new(name.to_string(), data.len() as u64, 0o444, now, false))
    }

    async fn rename(&self, _old_path: &str, _new_path: &str) -> Result<()> {
        Err(Error::plugin("operation not permitted: serverinfofs is read-only".to_string()))
    }

    async fn chmod(&self, _path: &str, _mode: u32) -> Result<()> {
        Err(Error::plugin("operation not permitted: serverinfofs is read-only".to_string()))
    }
}

/// ServerInfoFS plugin
pub struct ServerInfoFSPlugin {
    config_params: Vec<ConfigParameter>,
}

impl ServerInfoFSPlugin {
    /// Create a new ServerInfoFS plugin
    pub fn new() -> Self {
        Self {
            config_params: vec![],
        }
    }
}

#[async_trait]
impl ServicePlugin for ServerInfoFSPlugin {
    fn name(&self) -> &str {
        "serverinfofs"
    }

    fn readme(&self) -> &str {
        r#"ServerInfoFS Plugin - Server Metadata and Information

This plugin provides runtime information about RAGFS server.

USAGE:
  View server version:
    cat /serverinfofs/version

  View server uptime:
    cat /serverinfofs/uptime

  View server info:
    cat /serverinfofs/server_info

  View runtime stats:
    cat /serverinfofs/stats

FILES:
  /server_info  - Complete server information (JSON)
  /uptime       - Server uptime since start
  /version      - Server version
  /stats        - Runtime statistics
  /README       - This file

VERSION: 1.0.0
"#
    }

    async fn validate(&self, _config: &PluginConfig) -> Result<()> {
        // No validation needed
        Ok(())
    }

    async fn initialize(&self, _config: PluginConfig) -> Result<Box<dyn FileSystem>> {
        let fs = ServerInfoFileSystem::new(env!("CARGO_PKG_VERSION"));
        Ok(Box::new(fs))
    }

    fn config_params(&self) -> &[ConfigParameter] {
        &self.config_params
    }
}

/// Format duration as human-readable string
fn format_duration(duration: Duration) -> String {
    let secs = duration.as_secs();
    let days = secs / 86400;
    let hours = (secs % 86400) / 3600;
    let minutes = (secs % 3600) / 60;
    let seconds = secs % 60;

    if days > 0 {
        format!("{}d{}h{}m{}s", days, hours, minutes, seconds)
    } else if hours > 0 {
        format!("{}h{}m{}s", hours, minutes, seconds)
    } else if minutes > 0 {
        format!("{}m{}s", minutes, seconds)
    } else {
        format!("{}s", seconds)
    }
}

/// Format timestamp as RFC3339 string
fn format_timestamp(secs: u64) -> String {
    let s = secs;
    let days = s / 86400;
    let time_of_day = s % 86400;
    let h = time_of_day / 3600;
    let m = (time_of_day % 3600) / 60;
    let sec = time_of_day % 60;

    let (year, month, day) = days_to_ymd(days);
    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}Z",
        year, month, day, h, m, sec
    )
}

/// Convert days since Unix epoch to (year, month, day)
fn days_to_ymd(days: u64) -> (u64, u64, u64) {
    let z = days + 719468;
    let era = z / 146097;
    let doe = z - era * 146097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    (y, m, d)
}
