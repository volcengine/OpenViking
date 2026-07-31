use crate::YuanrongStoreError;

/// Synchronous Yuanrong KV operations used by the async provider.
///
/// The production implementation delegates to the Yuanrong C++ SDK. Tests use
/// this boundary to exercise the provider without requiring a worker process.
pub trait YuanrongKvStore: Send + Sync + 'static {
    /// Check that the connected worker is healthy.
    fn health_check(&self) -> Result<(), YuanrongStoreError>;
    /// Read one complete value. A missing key returns `Ok(None)`.
    fn get(&self, key: &str) -> Result<Option<Vec<u8>>, YuanrongStoreError>;
    /// Store one complete value.
    fn set(&self, key: &str, value: &[u8]) -> Result<(), YuanrongStoreError>;
    /// Delete one key. Missing keys are treated as success.
    fn delete(&self, key: &str) -> Result<(), YuanrongStoreError>;
    /// Check whether one key exists.
    fn exists(&self, key: &str) -> Result<bool, YuanrongStoreError>;
    /// Read multiple values while preserving input order.
    fn batch_get(&self, keys: &[String]) -> Result<Vec<Option<Vec<u8>>>, YuanrongStoreError>;
    /// Store multiple values.
    fn batch_set(&self, entries: &[(String, Vec<u8>)]) -> Result<(), YuanrongStoreError>;
    /// Delete multiple keys.
    fn batch_delete(&self, keys: &[String]) -> Result<(), YuanrongStoreError>;
    /// Shut down the SDK client.
    fn shutdown(&self) -> Result<(), YuanrongStoreError>;
}
