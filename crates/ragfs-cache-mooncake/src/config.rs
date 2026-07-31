use ragfs::cache::{CacheError, CacheResult};

/// Connection and execution settings for a Mooncake provider.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MooncakeConfig {
    /// Hostname or IP address registered for the local Mooncake client.
    pub local_hostname: String,
    /// Metadata service URL.
    pub metadata_server: String,
    /// Mooncake Master service address.
    pub master_server_addr: String,
    /// Transfer protocol, such as `tcp` or `rdma`.
    pub protocol: String,
    /// Optional transport device name.
    pub device_name: String,
    /// Size of the global Mooncake segment in bytes.
    pub global_segment_size: u64,
    /// Size of the local transfer buffer in bytes.
    pub local_buffer_size: u64,
    /// Number of object replicas requested for writes.
    pub replica_num: usize,
    /// Maximum number of concurrent synchronous SDK operations.
    pub sdk_concurrency: usize,
    /// Timeout applied while waiting for each SDK operation.
    pub operation_timeout_ms: u64,
}

impl MooncakeConfig {
    pub(crate) fn validate(&self) -> CacheResult<()> {
        for (name, value) in [
            ("local_hostname", self.local_hostname.as_str()),
            ("metadata_server", self.metadata_server.as_str()),
            ("master_server_addr", self.master_server_addr.as_str()),
            ("protocol", self.protocol.as_str()),
        ] {
            if value.trim().is_empty() {
                return Err(CacheError::InvalidArgument(format!(
                    "Mooncake {name} must not be empty"
                )));
            }
        }
        if !matches!(
            self.protocol.as_str(),
            "tcp" | "rdma" | "ascend" | "cxl" | "nvlink" | "barex"
        ) {
            return Err(CacheError::InvalidArgument(format!(
                "unsupported Mooncake protocol: {}",
                self.protocol
            )));
        }
        if self.global_segment_size == 0 {
            return Err(CacheError::InvalidArgument(
                "Mooncake global_segment_size must be greater than zero".into(),
            ));
        }
        if self.local_buffer_size == 0 {
            return Err(CacheError::InvalidArgument(
                "Mooncake local_buffer_size must be greater than zero".into(),
            ));
        }
        if self.sdk_concurrency == 0 || self.sdk_concurrency > u32::MAX as usize {
            return Err(CacheError::InvalidArgument(
                "Mooncake sdk_concurrency must be between 1 and u32::MAX".into(),
            ));
        }
        if self.operation_timeout_ms == 0 {
            return Err(CacheError::InvalidArgument(
                "Mooncake operation_timeout_ms must be greater than zero".into(),
            ));
        }
        Ok(())
    }
}
