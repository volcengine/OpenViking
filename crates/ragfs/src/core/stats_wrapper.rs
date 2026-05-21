//! Statistics wrapper for FileSystem
//!
//! This module provides a wrapper around any FileSystem implementation
//! that automatically collects operation statistics.

use async_trait::async_trait;
use std::sync::Arc;

use super::{
    FileInfo, FileSystem, FsOperation, GrepResult, OperationTimer, Result, StatsCollector,
    WriteFlag,
};

/// A wrapper around FileSystem that automatically collects operation statistics
pub struct StatsWrappedFS {
    inner: Box<dyn FileSystem>,
    stats: Arc<StatsCollector>,
}

impl StatsWrappedFS {
    /// Create a new statistics-wrapped filesystem
    pub fn new(inner: Box<dyn FileSystem>) -> Self {
        Self {
            inner,
            stats: Arc::new(StatsCollector::new()),
        }
    }

    /// Create a new statistics-wrapped filesystem with an existing collector
    pub fn with_collector(inner: Box<dyn FileSystem>, stats: Arc<StatsCollector>) -> Self {
        Self { inner, stats }
    }

    /// Get a reference to the statistics collector
    pub fn stats_collector(&self) -> Arc<StatsCollector> {
        Arc::clone(&self.stats)
    }

    /// Get the inner filesystem (for testing)
    #[cfg(test)]
    pub fn into_inner(self) -> Box<dyn FileSystem> {
        self.inner
    }
}

#[async_trait]
impl FileSystem for StatsWrappedFS {
    async fn create(&self, path: &str) -> Result<()> {
        let timer = OperationTimer::start(FsOperation::Create, Arc::clone(&self.stats));
        let result = self.inner.create(path).await;
        timer.finish().await;
        result
    }

    async fn mkdir(&self, path: &str, mode: u32) -> Result<()> {
        let timer = OperationTimer::start(FsOperation::Mkdir, Arc::clone(&self.stats));
        let result = self.inner.mkdir(path, mode).await;
        timer.finish().await;
        result
    }

    async fn remove(&self, path: &str) -> Result<()> {
        let timer = OperationTimer::start(FsOperation::Remove, Arc::clone(&self.stats));
        let result = self.inner.remove(path).await;
        timer.finish().await;
        result
    }

    async fn remove_all(&self, path: &str) -> Result<()> {
        let timer = OperationTimer::start(FsOperation::RemoveAll, Arc::clone(&self.stats));
        let result = self.inner.remove_all(path).await;
        timer.finish().await;
        result
    }

    async fn read(&self, path: &str, offset: u64, size: u64) -> Result<Vec<u8>> {
        let timer = OperationTimer::start(FsOperation::Read, Arc::clone(&self.stats));
        let result = self.inner.read(path, offset, size).await;
        timer.finish().await;
        result
    }

    async fn write(&self, path: &str, data: &[u8], offset: u64, flags: WriteFlag) -> Result<u64> {
        let timer = OperationTimer::start(FsOperation::Write, Arc::clone(&self.stats));
        let result = self.inner.write(path, data, offset, flags).await;
        timer.finish().await;
        result
    }

    async fn read_dir(&self, path: &str) -> Result<Vec<FileInfo>> {
        let timer = OperationTimer::start(FsOperation::ReadDir, Arc::clone(&self.stats));
        let result = self.inner.read_dir(path).await;
        timer.finish().await;
        result
    }

    async fn stat(&self, path: &str) -> Result<FileInfo> {
        let timer = OperationTimer::start(FsOperation::Stat, Arc::clone(&self.stats));
        let result = self.inner.stat(path).await;
        timer.finish().await;
        result
    }

    async fn rename(&self, old_path: &str, new_path: &str) -> Result<()> {
        let timer = OperationTimer::start(FsOperation::Rename, Arc::clone(&self.stats));
        let result = self.inner.rename(old_path, new_path).await;
        timer.finish().await;
        result
    }

    async fn chmod(&self, path: &str, mode: u32) -> Result<()> {
        let timer = OperationTimer::start(FsOperation::Chmod, Arc::clone(&self.stats));
        let result = self.inner.chmod(path, mode).await;
        timer.finish().await;
        result
    }

    async fn truncate(&self, path: &str, size: u64) -> Result<()> {
        let timer = OperationTimer::start(FsOperation::Truncate, Arc::clone(&self.stats));
        let result = self.inner.truncate(path, size).await;
        timer.finish().await;
        result
    }

    async fn ensure_parent_dirs(&self, path: &str, mode: u32) -> Result<()> {
        let timer = OperationTimer::start(FsOperation::EnsureParentDirs, Arc::clone(&self.stats));
        let result = self.inner.ensure_parent_dirs(path, mode).await;
        timer.finish().await;
        result
    }

    async fn exists(&self, path: &str) -> bool {
        let timer = OperationTimer::start(FsOperation::Exists, Arc::clone(&self.stats));
        let result = self.inner.exists(path).await;
        timer.finish().await;
        result
    }

    async fn grep(
        &self,
        path: &str,
        pattern: &str,
        recursive: bool,
        case_insensitive: bool,
        node_limit: Option<usize>,
        exclude_path: Option<&str>,
        level_limit: Option<usize>,
    ) -> Result<GrepResult> {
        let timer = OperationTimer::start(FsOperation::Grep, Arc::clone(&self.stats));
        let result = self
            .inner
            .grep(
                path,
                pattern,
                recursive,
                case_insensitive,
                node_limit,
                exclude_path,
                level_limit,
            )
            .await;
        timer.finish().await;
        result
    }
}
