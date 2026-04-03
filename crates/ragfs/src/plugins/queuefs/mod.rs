//! QueueFS Plugin
//!
//! A filesystem-based message queue where operations are performed through control files:
//! - `/enqueue` - Write to this file to add a message to the queue
//! - `/dequeue` - Read from this file to remove and return the first message
//! - `/peek` - Read from this file to view the first message without removing it
//! - `/size` - Read from this file to get the current queue size
//! - `/clear` - Write to this file to clear all messages from the queue

use crate::core::{
    errors::{Error, Result},
    filesystem::FileSystem,
    plugin::ServicePlugin,
    types::{ConfigParameter, FileInfo, PluginConfig, WriteFlag},
};
use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use std::collections::VecDeque;
use std::sync::Arc;
use std::time::SystemTime;
use tokio::sync::Mutex;
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
    fn new(data: Vec<u8>) -> Self {
        Self {
            id: Uuid::new_v4().to_string(),
            data,
            timestamp: SystemTime::now(),
        }
    }
}

/// QueueFS - A filesystem-based message queue
pub struct QueueFileSystem {
    /// The message queue
    queue: Arc<Mutex<VecDeque<Message>>>,
}

impl QueueFileSystem {
    /// Create a new QueueFileSystem
    pub fn new() -> Self {
        Self {
            queue: Arc::new(Mutex::new(VecDeque::new())),
        }
    }

    /// Check if a path is a control file
    fn is_control_file(path: &str) -> bool {
        matches!(
            path,
            "/enqueue" | "/dequeue" | "/peek" | "/size" | "/clear"
        )
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
}

#[async_trait]
impl FileSystem for QueueFileSystem {
    async fn create(&self, path: &str) -> Result<()> {
        let path = Self::normalize_path(path);
        if Self::is_control_file(&path) {
            // Control files always exist
            Ok(())
        } else {
            Err(Error::InvalidOperation(
                "QueueFS only supports control files".to_string(),
            ))
        }
    }

    async fn mkdir(&self, path: &str, _mode: u32) -> Result<()> {
        let path = Self::normalize_path(path);
        if path == "/" {
            Ok(())
        } else {
            Err(Error::InvalidOperation(
                "QueueFS does not support directories".to_string(),
            ))
        }
    }

    async fn read(&self, path: &str, _offset: u64, _size: u64) -> Result<Vec<u8>> {
        let path = Self::normalize_path(path);

        match path.as_str() {
            "/dequeue" => {
                let mut queue = self.queue.lock().await;
                let msg = queue
                    .pop_front()
                    .ok_or_else(|| Error::NotFound("queue is empty".to_string()))?;
                Ok(msg.data)
            }
            "/peek" => {
                let queue = self.queue.lock().await;
                let msg = queue
                    .front()
                    .ok_or_else(|| Error::NotFound("queue is empty".to_string()))?;
                Ok(msg.data.clone())
            }
            "/size" => {
                let queue = self.queue.lock().await;
                let size = queue.len();
                Ok(size.to_string().into_bytes())
            }
            _ => Err(Error::InvalidOperation(format!(
                "Cannot read from '{}'. Use /dequeue, /peek, or /size",
                path
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
        let path = Self::normalize_path(path);

        match path.as_str() {
            "/enqueue" => {
                let msg = Message::new(data.to_vec());
                let len = data.len() as u64;
                self.queue.lock().await.push_back(msg);
                Ok(len)
            }
            "/clear" => {
                self.queue.lock().await.clear();
                Ok(0)
            }
            _ => Err(Error::InvalidOperation(format!(
                "Cannot write to '{}'. Use /enqueue or /clear",
                path
            ))),
        }
    }

    async fn read_dir(&self, path: &str) -> Result<Vec<FileInfo>> {
        let path = Self::normalize_path(path);

        if path != "/" {
            return Err(Error::NotFound(format!("directory not found: {}", path)));
        }

        let now = SystemTime::now();
        Ok(vec![
            FileInfo {
                name: "enqueue".to_string(),
                size: 0,
                mode: 0o666,
                mod_time: now,
                is_dir: false,
            },
            FileInfo {
                name: "dequeue".to_string(),
                size: 0,
                mode: 0o666,
                mod_time: now,
                is_dir: false,
            },
            FileInfo {
                name: "peek".to_string(),
                size: 0,
                mode: 0o666,
                mod_time: now,
                is_dir: false,
            },
            FileInfo {
                name: "size".to_string(),
                size: 0,
                mode: 0o666,
                mod_time: now,
                is_dir: false,
            },
            FileInfo {
                name: "clear".to_string(),
                size: 0,
                mode: 0o666,
                mod_time: now,
                is_dir: false,
            },
        ])
    }

    async fn stat(&self, path: &str) -> Result<FileInfo> {
        let path = Self::normalize_path(path);

        if path == "/" {
            return Ok(FileInfo {
                name: "/".to_string(),
                size: 0,
                mode: 0o755,
                mod_time: SystemTime::now(),
                is_dir: true,
            });
        }

        if Self::is_control_file(&path) {
            let name = path.trim_start_matches('/').to_string();
            Ok(FileInfo {
                name,
                size: 0,
                mode: 0o666,
                mod_time: SystemTime::now(),
                is_dir: false,
            })
        } else {
            Err(Error::NotFound(format!("file not found: {}", path)))
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

    async fn remove_all(&self, _path: &str) -> Result<()> {
        Err(Error::InvalidOperation(
            "QueueFS does not support remove_all".to_string(),
        ))
    }

    async fn truncate(&self, _path: &str, _size: u64) -> Result<()> {
        Err(Error::InvalidOperation(
            "QueueFS does not support truncate".to_string(),
        ))
    }
}

/// QueueFS Plugin
pub struct QueueFSPlugin;

#[async_trait]
impl ServicePlugin for QueueFSPlugin {
    fn name(&self) -> &str {
        "queuefs"
    }

    fn readme(&self) -> &str {
        "QueueFS - A filesystem-based message queue\n\
         \n\
         Control files:\n\
         - /enqueue: Write to add a message to the queue\n\
         - /dequeue: Read to remove and return the first message\n\
         - /peek: Read to view the first message without removing it\n\
         - /size: Read to get the current queue size\n\
         - /clear: Write to clear all messages from the queue"
    }

    async fn validate(&self, _config: &PluginConfig) -> Result<()> {
        // No configuration parameters required
        Ok(())
    }

    async fn initialize(&self, _config: PluginConfig) -> Result<Box<dyn FileSystem>> {
        Ok(Box::new(QueueFileSystem::new()))
    }

    fn config_params(&self) -> &[ConfigParameter] {
        &[]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_queuefs_enqueue_dequeue() {
        let fs = QueueFileSystem::new();

        // Enqueue messages
        let data1 = b"message 1";
        let data2 = b"message 2";

        fs.write("/enqueue", data1, 0, WriteFlag::None)
            .await
            .unwrap();
        fs.write("/enqueue", data2, 0, WriteFlag::None)
            .await
            .unwrap();

        // Dequeue messages
        let result1 = fs.read("/dequeue", 0, 0).await.unwrap();
        assert_eq!(result1, data1);

        let result2 = fs.read("/dequeue", 0, 0).await.unwrap();
        assert_eq!(result2, data2);

        // Queue should be empty
        let result = fs.read("/dequeue", 0, 0).await;
        assert!(result.is_err());
    }

    #[tokio::test]
    async fn test_queuefs_peek() {
        let fs = QueueFileSystem::new();

        // Enqueue a message
        let data = b"test message";
        fs.write("/enqueue", data, 0, WriteFlag::None)
            .await
            .unwrap();

        // Peek should return the message without removing it
        let result1 = fs.read("/peek", 0, 0).await.unwrap();
        assert_eq!(result1, data);

        let result2 = fs.read("/peek", 0, 0).await.unwrap();
        assert_eq!(result2, data);

        // Dequeue should still work
        let result3 = fs.read("/dequeue", 0, 0).await.unwrap();
        assert_eq!(result3, data);
    }

    #[tokio::test]
    async fn test_queuefs_size() {
        let fs = QueueFileSystem::new();

        // Initially empty
        let size = fs.read("/size", 0, 0).await.unwrap();
        assert_eq!(String::from_utf8(size).unwrap(), "0");

        // Add messages
        fs.write("/enqueue", b"msg1", 0, WriteFlag::None)
            .await
            .unwrap();
        fs.write("/enqueue", b"msg2", 0, WriteFlag::None)
            .await
            .unwrap();

        let size = fs.read("/size", 0, 0).await.unwrap();
        assert_eq!(String::from_utf8(size).unwrap(), "2");

        // Dequeue one
        fs.read("/dequeue", 0, 0).await.unwrap();

        let size = fs.read("/size", 0, 0).await.unwrap();
        assert_eq!(String::from_utf8(size).unwrap(), "1");
    }

    #[tokio::test]
    async fn test_queuefs_clear() {
        let fs = QueueFileSystem::new();

        // Add messages
        fs.write("/enqueue", b"msg1", 0, WriteFlag::None)
            .await
            .unwrap();
        fs.write("/enqueue", b"msg2", 0, WriteFlag::None)
            .await
            .unwrap();

        // Clear the queue
        fs.write("/clear", b"", 0, WriteFlag::None).await.unwrap();

        // Queue should be empty
        let size = fs.read("/size", 0, 0).await.unwrap();
        assert_eq!(String::from_utf8(size).unwrap(), "0");

        let result = fs.read("/dequeue", 0, 0).await;
        assert!(result.is_err());
    }

    #[tokio::test]
    async fn test_queuefs_read_dir() {
        let fs = QueueFileSystem::new();

        let entries = fs.read_dir("/").await.unwrap();
        assert_eq!(entries.len(), 5);

        let names: Vec<String> = entries.iter().map(|e| e.name.clone()).collect();
        assert!(names.contains(&"enqueue".to_string()));
        assert!(names.contains(&"dequeue".to_string()));
        assert!(names.contains(&"peek".to_string()));
        assert!(names.contains(&"size".to_string()));
        assert!(names.contains(&"clear".to_string()));
    }

    #[tokio::test]
    async fn test_queuefs_stat() {
        let fs = QueueFileSystem::new();

        // Stat root
        let info = fs.stat("/").await.unwrap();
        assert!(info.is_dir);

        // Stat control files
        let info = fs.stat("/enqueue").await.unwrap();
        assert!(!info.is_dir);
        assert_eq!(info.name, "enqueue");

        // Stat non-existent file
        let result = fs.stat("/nonexistent").await;
        assert!(result.is_err());
    }

    #[tokio::test]
    async fn test_queuefs_invalid_operations() {
        let fs = QueueFileSystem::new();

        // Cannot read from enqueue
        let result = fs.read("/enqueue", 0, 0).await;
        assert!(result.is_err());

        // Cannot write to dequeue
        let result = fs.write("/dequeue", b"data", 0, WriteFlag::None).await;
        assert!(result.is_err());

        // Cannot rename
        let result = fs.rename("/enqueue", "/enqueue2").await;
        assert!(result.is_err());

        // Cannot remove
        let result = fs.remove("/enqueue").await;
        assert!(result.is_err());
    }

    #[tokio::test]
    async fn test_queuefs_concurrent_access() {
        let fs = Arc::new(QueueFileSystem::new());

        // Spawn multiple tasks to enqueue messages
        let mut handles = vec![];
        for i in 0..10 {
            let fs_clone = fs.clone();
            let handle = tokio::spawn(async move {
                let data = format!("message {}", i);
                fs_clone
                    .write("/enqueue", data.as_bytes(), 0, WriteFlag::None)
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
        let size = fs.read("/size", 0, 0).await.unwrap();
        assert_eq!(String::from_utf8(size).unwrap(), "10");

        // Dequeue all messages
        for _ in 0..10 {
            fs.read("/dequeue", 0, 0).await.unwrap();
        }

        // Queue should be empty
        let size = fs.read("/size", 0, 0).await.unwrap();
        assert_eq!(String::from_utf8(size).unwrap(), "0");
    }

    #[tokio::test]
    async fn test_queuefs_plugin() {
        let plugin = QueueFSPlugin;

        assert_eq!(plugin.name(), "queuefs");
        assert!(!plugin.readme().is_empty());
        assert_eq!(plugin.config_params().len(), 0);

        let config = PluginConfig {
            name: "queuefs".to_string(),
            mount_path: "/queue".to_string(),
            params: std::collections::HashMap::new(),
        };

        plugin.validate(&config).await.unwrap();
        let fs = plugin.initialize(config).await.unwrap();

        // Test basic operation
        fs.write("/enqueue", b"test", 0, WriteFlag::None)
            .await
            .unwrap();
        let result = fs.read("/dequeue", 0, 0).await.unwrap();
        assert_eq!(result, b"test");
    }
}
