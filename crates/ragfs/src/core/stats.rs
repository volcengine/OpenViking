//! Filesystem operation statistics
//!
//! This module provides functionality to track filesystem operations,
//! including operation counts and latency statistics.

use std::collections::HashMap;
use std::sync::Arc;
use std::time::{Duration, Instant};
use opentelemetry::KeyValue;
use serde::{Deserialize, Serialize};
use tokio::sync::RwLock;

use crate::metrics::duration::DurationMetrics;

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
    /// Tree directory operation
    TreeDir,
    /// Glob directory operation
    GlobDir,
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
            FsOperation::TreeDir,
            FsOperation::GlobDir,
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
            FsOperation::TreeDir => "tree_dir",
            FsOperation::GlobDir => "glob_dir",
        }
    }
}

/// Statistics for a single operation type
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OperationStats {
    /// Number of times the operation was called
    pub count: u64,
    /// Number of successful operations.
    pub success_count: u64,
    /// Number of failed operations.
    pub error_count: u64,
    /// Total time spent on this operation (nanoseconds).
    pub total_time_ns: u64,
    /// Minimum time spent (nanoseconds); u64::MAX when count is zero.
    pub min_time_ns: u64,
    /// Maximum time spent (nanoseconds).
    pub max_time_ns: u64,
    /// Inclusive duration boundaries returned by the SDK, in nanoseconds.
    pub duration_bucket_bounds_ns: Vec<u64>,
    /// Noncumulative duration counts; the last bucket holds overflow samples.
    pub duration_bucket_counts: Vec<u64>,
}

impl Default for OperationStats {
    /// Return empty statistics with no inputs and an unset minimum duration.
    fn default() -> Self {
        Self {
            count: 0,
            success_count: 0,
            error_count: 0,
            total_time_ns: 0,
            min_time_ns: u64::MAX,
            max_time_ns: 0,
            duration_bucket_bounds_ns: Vec::new(),
            duration_bucket_counts: Vec::new(),
        }
    }
}

impl OperationStats {
    /// Return this operation's average nanoseconds, or zero when no samples exist.
    pub fn avg_time_ns(&self) -> f64 {
        if self.count == 0 {
            0.0
        } else {
            self.total_time_ns as f64 / self.count as f64
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

}

/// Thread-safe statistics collector
pub struct StatsCollector {
    durations: RwLock<DurationMetrics>,
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
            durations: RwLock::new(DurationMetrics::default()),
        }
    }

    /// Record the supplied operation, duration, and success flag; return unit.
    pub async fn record(&self, op: FsOperation, duration: Duration, success: bool) {
        self.durations.read().await.record(
            duration,
            &[
                KeyValue::new("operation", op.as_str()),
                KeyValue::new("status", if success { "success" } else { "error" }),
            ],
        );
    }

    /// Get a snapshot of current statistics
    pub async fn snapshot(&self) -> FilesystemStats {
        let mut snapshot = FilesystemStats::default();
        for point in self.durations.read().await.snapshot() {
            let operation = point
                .attributes()
                .find(|attribute| attribute.key.as_str() == "operation")
                .expect("filesystem duration has an operation attribute");
            let operation = FsOperation::all()
                .iter()
                .find(|op| op.as_str() == operation.value.as_str())
                .expect("filesystem duration uses a known operation");
            let success = point.attributes().any(|attribute| {
                attribute.key.as_str() == "status" && attribute.value.as_str() == "success"
            });
            let stats = snapshot.operations.get_mut(operation).unwrap();
            stats.count = stats.count.saturating_add(point.count());
            if success {
                stats.success_count = point.count();
            } else {
                stats.error_count = point.count();
            }
            stats.total_time_ns = stats.total_time_ns.saturating_add(point.sum());
            stats.min_time_ns = stats.min_time_ns.min(point.min().unwrap_or(u64::MAX));
            stats.max_time_ns = stats.max_time_ns.max(point.max().unwrap_or(0));
            if stats.duration_bucket_counts.is_empty() {
                stats.duration_bucket_bounds_ns = point.bounds().map(|bound| bound as u64).collect();
                stats.duration_bucket_counts = point.bucket_counts().collect();
            } else {
                for (count, incoming) in stats
                    .duration_bucket_counts
                    .iter_mut()
                    .zip(point.bucket_counts())
                {
                    *count = count.saturating_add(incoming);
                }
            }
        }
        snapshot
    }

    /// Reset all statistics
    pub async fn reset(&self) {
        *self.durations.write().await = DurationMetrics::default();
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

    /// Consume this timer and record elapsed time with the supplied outcome; return unit.
    pub async fn finish(self, success: bool) {
        let duration = self.start.elapsed();
        self.collector.record(self.op, duration, success).await;
    }
}
