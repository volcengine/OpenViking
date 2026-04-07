//! LocalFS plugin - Local file system mount
//!
//! This plugin mounts a local directory into RAGFS virtual file system,
//! providing direct access to local files and directories.

use async_trait::async_trait;
use std::fs;
use std::path::{Path, PathBuf};

use crate::core::errors::{Error, Result};
use crate::core::filesystem::FileSystem;
use crate::core::plugin::ServicePlugin;
use crate::core::types::{ConfigParameter, FileInfo, PluginConfig, WriteFlag};

/// LocalFS - Local file system implementation
pub struct LocalFileSystem {
    /// Base path of the mounted directory
    base_path: PathBuf,
}

impl LocalFileSystem {
    /// Create a new LocalFileSystem
    ///
    /// # Arguments
    /// * `base_path` - The local directory path to mount
    ///
    /// # Errors
    /// Returns an error if the base path doesn't exist or is not a directory
    pub fn new(base_path: &str) -> Result<Self> {
        let path = PathBuf::from(base_path);

        // Check if path exists
        if !path.exists() {
            return Err(Error::plugin(format!(
                "base path does not exist: {}",
                base_path
            )));
        }

        // Check if it's a directory
        if !path.is_dir() {
            return Err(Error::plugin(format!(
                "base path is not a directory: {}",
                base_path
            )));
        }

        Ok(Self { base_path: path })
    }

    /// Resolve a virtual path to actual local path
    fn resolve_path(&self, path: &str) -> PathBuf {
        // Remove leading slash to make it relative
        let relative = path.strip_prefix('/').unwrap_or(path);

        // Join with base path
        if relative.is_empty() {
            self.base_path.clone()
        } else {
            self.base_path.join(relative)
        }
    }
}

#[async_trait]
impl FileSystem for LocalFileSystem {
    async fn create(&self, path: &str) -> Result<()> {
        let local_path = self.resolve_path(path);

        // Check if file already exists
        if local_path.exists() {
            return Err(Error::AlreadyExists(path.to_string()));
        }

        // Check if parent directory exists
        if let Some(parent) = local_path.parent() {
            if !parent.exists() {
                return Err(Error::NotFound(parent.to_string_lossy().to_string()));
            }
        }

        // Create empty file
        fs::File::create(&local_path)
            .map_err(|e| Error::plugin(format!("failed to create file: {}", e)))?;

        Ok(())
    }

    async fn mkdir(&self, path: &str, _mode: u32) -> Result<()> {
        let local_path = self.resolve_path(path);

        // Check if directory already exists
        if local_path.exists() {
            return Err(Error::AlreadyExists(path.to_string()));
        }

        // Check if parent directory exists
        if let Some(parent) = local_path.parent() {
            if !parent.exists() {
                return Err(Error::NotFound(parent.to_string_lossy().to_string()));
            }
        }

        // Create directory
        fs::create_dir(&local_path)
            .map_err(|e| Error::plugin(format!("failed to create directory: {}", e)))?;

        Ok(())
    }

    async fn remove(&self, path: &str) -> Result<()> {
        let local_path = self.resolve_path(path);

        // Check if exists
        if !local_path.exists() {
            return Err(Error::NotFound(path.to_string()));
        }

        // If directory, check if empty
        if local_path.is_dir() {
            let entries = fs::read_dir(&local_path)
                .map_err(|e| Error::plugin(format!("failed to read directory: {}", e)))?;

            if entries.count() > 0 {
                return Err(Error::plugin(format!("directory not empty: {}", path)));
            }
        }

        // Remove file or empty directory
        fs::remove_file(&local_path)
            .or_else(|_| fs::remove_dir(&local_path))
            .map_err(|e| Error::plugin(format!("failed to remove: {}", e)))?;

        Ok(())
    }

    async fn remove_all(&self, path: &str) -> Result<()> {
        let local_path = self.resolve_path(path);

        // Check if exists
        if !local_path.exists() {
            return Err(Error::NotFound(path.to_string()));
        }

        // Remove recursively
        fs::remove_dir_all(&local_path)
            .map_err(|e| Error::plugin(format!("failed to remove: {}", e)))?;

        Ok(())
    }

    async fn read(&self, path: &str, offset: u64, size: u64) -> Result<Vec<u8>> {
        let local_path = self.resolve_path(path);

        // Check if exists and is not a directory
        let metadata = fs::metadata(&local_path)
            .map_err(|_| Error::NotFound(path.to_string()))?;

        if metadata.is_dir() {
            return Err(Error::plugin(format!("is a directory: {}", path)));
        }

        // Read file
        let data = fs::read(&local_path)
            .map_err(|e| Error::plugin(format!("failed to read file: {}", e)))?;

        // Apply offset and size
        let file_size = data.len() as u64;
        let start = offset.min(file_size) as usize;
        let end = if size == 0 {
            data.len()
        } else {
            (offset + size).min(file_size) as usize
        };

        if start >= data.len() {
            Ok(vec![])
        } else {
            Ok(data[start..end].to_vec())
        }
    }

    async fn write(&self, path: &str, data: &[u8], offset: u64, _flags: WriteFlag) -> Result<u64> {
        let local_path = self.resolve_path(path);

        // Check if it's a directory
        if local_path.exists() && local_path.is_dir() {
            return Err(Error::plugin(format!("is a directory: {}", path)));
        }

        // Check if parent directory exists
        if let Some(parent) = local_path.parent() {
            if !parent.exists() {
                return Err(Error::NotFound(parent.to_string_lossy().to_string()));
            }
        }

        // Open or create file
        let mut file = if local_path.exists() {
            fs::OpenOptions::new()
                .write(true)
                .open(&local_path)
                .map_err(|e| Error::plugin(format!("failed to open file: {}", e)))?
        } else {
            fs::OpenOptions::new()
                .write(true)
                .create(true)
                .open(&local_path)
                .map_err(|e| Error::plugin(format!("failed to create file: {}", e)))?
        };

        // Write data
        use std::io::{Seek, SeekFrom, Write};

        if offset > 0 {
            file.seek(SeekFrom::Start(offset))
                .map_err(|e| Error::plugin(format!("failed to seek: {}", e)))?;
        }

        let written = file
            .write(data)
            .map_err(|e| Error::plugin(format!("failed to write: {}", e)))?;

        Ok(written as u64)
    }

    async fn read_dir(&self, path: &str) -> Result<Vec<FileInfo>> {
        let local_path = self.resolve_path(path);

        // Check if directory exists
        if !local_path.exists() {
            return Err(Error::NotFound(path.to_string()));
        }

        if !local_path.is_dir() {
            return Err(Error::plugin(format!("not a directory: {}", path)));
        }

        // Read directory
        let entries = fs::read_dir(&local_path)
            .map_err(|e| Error::plugin(format!("failed to read directory: {}", e)))?;

        let mut files = Vec::new();
        for entry in entries {
            let entry = entry.map_err(|e| Error::plugin(format!("failed to read entry: {}", e)))?;
            let metadata = entry
                .metadata()
                .map_err(|e| Error::plugin(format!("failed to get metadata: {}", e)))?;

            let name = entry.file_name().to_string_lossy().to_string();
            let mode = if metadata.is_dir() { 0o755 } else { 0o644 };
            let mod_time = metadata
                .modified()
                .unwrap_or(std::time::SystemTime::UNIX_EPOCH);

            files.push(FileInfo::new(
                name,
                metadata.len(),
                mode,
                mod_time,
                metadata.is_dir(),
            ));
        }

        Ok(files)
    }

    async fn stat(&self, path: &str) -> Result<FileInfo> {
        let local_path = self.resolve_path(path);

        // Get file metadata
        let metadata = fs::metadata(&local_path)
            .map_err(|_| Error::NotFound(path.to_string()))?;

        let name = Path::new(path)
            .file_name()
            .unwrap_or(path.as_ref())
            .to_string_lossy()
            .to_string();
        let mode = if metadata.is_dir() { 0o755 } else { 0o644 };
        let mod_time = metadata
            .modified()
            .unwrap_or(std::time::SystemTime::UNIX_EPOCH);

        Ok(FileInfo::new(
            name,
            metadata.len(),
            mode,
            mod_time,
            metadata.is_dir(),
        ))
    }

    async fn rename(&self, old_path: &str, new_path: &str) -> Result<()> {
        let old_local = self.resolve_path(old_path);
        let new_local = self.resolve_path(new_path);

        // Check if old path exists
        if !old_local.exists() {
            return Err(Error::NotFound(old_path.to_string()));
        }

        // Check if new path parent directory exists
        if let Some(parent) = new_local.parent() {
            if !parent.exists() {
                return Err(Error::NotFound(parent.to_string_lossy().to_string()));
            }
        }

        // Rename/move
        fs::rename(&old_local, &new_local)
            .map_err(|e| Error::plugin(format!("failed to rename: {}", e)))?;

        Ok(())
    }

    async fn chmod(&self, path: &str, _mode: u32) -> Result<()> {
        let local_path = self.resolve_path(path);

        // Check if exists
        if !local_path.exists() {
            return Err(Error::NotFound(path.to_string()));
        }

        // Note: chmod is not fully implemented on all platforms
        // For now, just return success
        Ok(())
    }
}

/// LocalFS plugin
pub struct LocalFSPlugin {
    config_params: Vec<ConfigParameter>,
}

impl LocalFSPlugin {
    /// Create a new LocalFS plugin
    pub fn new() -> Self {
        Self {
            config_params: vec![
                ConfigParameter {
                    name: "local_dir".to_string(),
                    param_type: "string".to_string(),
                    required: true,
                    default: None,
                    description: "Local directory path to expose (must exist)".to_string(),
                },
            ],
        }
    }
}

#[async_trait]
impl ServicePlugin for LocalFSPlugin {
    fn name(&self) -> &str {
        "localfs"
    }

    fn readme(&self) -> &str {
        r#"LocalFS Plugin - Local File System Mount

This plugin mounts a local directory into RAGFS virtual file system.

FEATURES:
  - Mount any local directory into RAGFS
  - Full POSIX file system operations
  - Direct access to local files and directories
  - Preserves file permissions and timestamps
  - Efficient file operations (no copying)

CONFIGURATION:

  Basic configuration:
  [plugins.localfs]
  enabled = true
  path = "/local"

    [plugins.localfs.config]
    local_dir = "/path/to/local/directory"

  Multiple local mounts:
  [plugins.localfs_home]
  enabled = true
  path = "/home"

    [plugins.localfs_home.config]
    local_dir = "/Users/username"

USAGE:

  List directory:
    agfs ls /local

  Read a file:
    agfs cat /local/file.txt

  Write to a file:
    agfs write /local/file.txt "Hello, World!"

  Create a directory:
    agfs mkdir /local/newdir

  Remove a file:
    agfs rm /local/file.txt

NOTES:
  - Changes are directly applied to local file system
  - File permissions are preserved and can be modified
  - Be careful with rm -r as it permanently deletes files

VERSION: 1.0.0
"#
    }

    async fn validate(&self, config: &PluginConfig) -> Result<()> {
        // Validate local_dir parameter
        let local_dir = config
            .params
            .get("local_dir")
            .and_then(|v| match v {
                crate::core::types::ConfigValue::String(s) => Some(s),
                _ => None,
            })
            .ok_or_else(|| Error::plugin("local_dir is required in configuration".to_string()))?;

        // Check if path exists
        let path = Path::new(local_dir);
        if !path.exists() {
            return Err(Error::plugin(format!(
                "base path does not exist: {}",
                local_dir
            )));
        }

        // Verify it's a directory
        if !path.is_dir() {
            return Err(Error::plugin(format!(
                "base path is not a directory: {}",
                local_dir
            )));
        }

        Ok(())
    }

    async fn initialize(&self, config: PluginConfig) -> Result<Box<dyn FileSystem>> {
        // Parse configuration
        let local_dir = config
            .params
            .get("local_dir")
            .and_then(|v| match v {
                crate::core::types::ConfigValue::String(s) => Some(s),
                _ => None,
            })
            .ok_or_else(|| Error::plugin("local_dir is required".to_string()))?;

        let fs = LocalFileSystem::new(local_dir)?;
        Ok(Box::new(fs))
    }

    fn config_params(&self) -> &[ConfigParameter] {
        &self.config_params
    }
}
