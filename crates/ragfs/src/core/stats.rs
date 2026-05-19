//! Filesystem operation statistics
//!
//! This module provides functionality to track filesystem operations,
//! including operation counts and latency statistics.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::sync::RwLock;

/// Type of filesystem operation
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum FsOperation {
    /// Create a file
    Create,
    /// Create a directory
    Mkdir,
    /// Remove a file
    Remove,
    /// Remove recursively
    RemoveAll,
    /// Read a file
    Read,
    /// Write a file
    Write,
    /// List directory contents
    ReadDir,
    /// Get file metadata
    Stat,
    /// Rename a file/directory
    Rename,
    /// Change file permissions
    Chmod,
    /// Truncate a file
    Truncate,
    /// Check if a path exists
    Exists,
    /// Grep operation
    Grep,
    /// Ensure parent directories exist
    EnsureParentDirs,
}

impl FsOperation {
    /// Get all operation types
    pub fn all() -> &'static [FsOperation] {
        &[
            FsOperation::Create,
            FsOperation::Mkdir,
            FsOperation::Remove,
            FsOperation::RemoveAll,
            FsOperation::Read,
            FsOperation::Write,
            FsOperation::ReadDir,
            FsOperation::Stat,
            FsOperation::Rename,
            FsOperation::Chmod,
            FsOperation::Truncate,
            FsOperation::Exists,
            FsOperation::Grep,
            FsOperation::EnsureParentDirs,
        ]
    }

    /// Get the operation name as string
    pub fn as_str(&self) -> &'static str {
        match self {
            FsOperation::Create => "create",
            FsOperation::Mkdir => "mkdir",
            FsOperation::Remove => "remove",
            FsOperation::RemoveAll => "remove_all",
            FsOperation::Read => "read",
            FsOperation::Write => "write",
            FsOperation::ReadDir => "read_dir",
            FsOperation::Stat => "stat",
            FsOperation::Rename => "rename",
            FsOperation::Chmod => "chmod",
            FsOperation::Truncate => "truncate",
            FsOperation::Exists => "exists",
            FsOperation::Grep => "grep",
            FsOperation::EnsureParentDirs => "ensure_parent_dirs",
        }
    }
}

/// Statistics for a single operation type
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OperationStats {
    /// Number of times the operation was called
    pub count: u64,
    /// Total time spent on this operation (microseconds)
    pub total_time_us: u64,
    /// Minimum time spent (microseconds)
    pub min_time_us: u64,
    /// Maximum time spent (microseconds)
    pub max_time_us: u64,
}

impl Default for OperationStats {
    fn default() -> Self {
        Self {
            count: 0,
            total_time_us: 0,
            min_time_us: u64::MAX,
            max_time_us: 0,
        }
    }
}

impl OperationStats {
    /// Record an operation duration
    pub fn record(&mut self, duration: Duration) {
        let us = duration.as_micros() as u64;
        self.count += 1;
        self.total_time_us += us;
        if us < self.min_time_us {
            self.min_time_us = us;
        }
        if us > self.max_time_us {
            self.max_time_us = us;
        }
    }

    /// Get average time per operation (microseconds)
    pub fn avg_time_us(&self) -> f64 {
        if self.count == 0 {
            0.0
        } else {
            self.total_time_us as f64 / self.count as f64
        }
    }
}

/// Complete filesystem statistics
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FilesystemStats {
    /// Per-operation statistics
    pub operations: HashMap<FsOperation, OperationStats>,
}

impl Default for FilesystemStats {
    fn default() -> Self {
        let mut operations = HashMap::new();
        for op in FsOperation::all() {
            operations.insert(*op, OperationStats::default());
        }
        Self { operations }
    }
}

impl FilesystemStats {
    /// Get statistics for a specific operation
    pub fn get(&self, op: FsOperation) -> &OperationStats {
        self.operations.get(&op).unwrap()
    }

    /// Get total number of operations
    pub fn total_operations(&self) -> u64 {
        self.operations.values().map(|s| s.count).sum()
    }

    /// Reset all statistics
    pub fn reset(&mut self) {
        *self = Self::default();
    }
}

/// Thread-safe statistics collector
pub struct StatsCollector {
    stats: Arc<RwLock<FilesystemStats>>,
}

impl Default for StatsCollector {
    fn default() -> Self {
        Self::new()
    }
}

impl StatsCollector {
    /// Create a new statistics collector
    pub fn new() -> Self {
        Self {
            stats: Arc::new(RwLock::new(FilesystemStats::default())),
        }
    }

    /// Record an operation
    pub async fn record(&self, op: FsOperation, duration: Duration) {
        let mut stats = self.stats.write().await;
        stats.operations.entry(op).or_default().record(duration);
    }

    /// Get a snapshot of current statistics
    pub async fn snapshot(&self) -> FilesystemStats {
        self.stats.read().await.clone()
    }

    /// Reset all statistics
    pub async fn reset(&self) {
        let mut stats = self.stats.write().await;
        stats.reset();
    }
}

/// Timer for measuring operation duration
pub struct OperationTimer {
    op: FsOperation,
    start: Instant,
    collector: Arc<StatsCollector>,
}

impl OperationTimer {
    /// Create a new timer for an operation
    pub fn start(op: FsOperation, collector: Arc<StatsCollector>) -> Self {
        Self {
            op,
            start: Instant::now(),
            collector,
        }
    }

    /// Finish the timer and record the duration
    pub async fn finish(self) {
        let duration = self.start.elapsed();
        self.collector.record(self.op, duration).await;
    }
}
