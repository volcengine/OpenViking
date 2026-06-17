use crate::MooncakeStoreError;

/// Replication options applied to each Mooncake object write.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct MooncakeReplicateConfig {
    /// Number of replicas to create.
    pub replica_num: usize,
    /// Prefer a replica local to the client.
    pub with_soft_pin: bool,
    /// Prevent eviction of the object.
    pub with_hard_pin: bool,
    /// Preferred Mooncake segment names.
    pub preferred_segments: Vec<String>,
}

/// Synchronous object operations exposed by Mooncake's Rust binding.
///
/// The asynchronous client executes every method on Tokio's blocking pool.
pub trait MooncakeObjectStore: Send + Sync + 'static {
    /// Check that Mooncake services are reachable.
    fn health_check(&self) -> Result<(), MooncakeStoreError>;
    /// Return whether an object exists.
    fn is_exist(&self, key: &str) -> Result<bool, MooncakeStoreError>;
    /// Read a complete object.
    fn get(&self, key: &str) -> Result<Vec<u8>, MooncakeStoreError>;
    /// Store a complete object.
    fn put(
        &self,
        key: &str,
        value: &[u8],
        replicate: &MooncakeReplicateConfig,
    ) -> Result<(), MooncakeStoreError>;
    /// Remove an object.
    fn remove(&self, key: &str, force: bool) -> Result<(), MooncakeStoreError>;
}
