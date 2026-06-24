use ragfs::cache::{CacheError, CacheResult};

/// Connection and execution settings for a Yuanrong provider.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct YuanrongConfig {
    /// Yuanrong worker host.
    pub host: String,
    /// Yuanrong worker port.
    pub port: u16,
    /// Timeout used while connecting to the worker.
    pub connect_timeout_ms: u64,
    /// Timeout applied to each KV operation.
    pub request_timeout_ms: u64,
    /// Maximum number of concurrent blocking tasks allowed by the Rust client.
    ///
    /// The current native bridge creates one native client handle per
    /// `YuanrongProvider`; calls through that handle are serialized in C++.
    /// Therefore `sdk_concurrency > 1` does not provide true Yuanrong backend
    /// concurrency for a single native provider instance.
    pub sdk_concurrency: usize,
}

impl YuanrongConfig {
    pub(crate) fn validate(&self) -> CacheResult<()> {
        if self.host.trim().is_empty() {
            return Err(CacheError::InvalidArgument(
                "Yuanrong host must not be empty".into(),
            ));
        }
        if self.port == 0 {
            return Err(CacheError::InvalidArgument(
                "Yuanrong port must be greater than zero".into(),
            ));
        }
        if self.connect_timeout_ms == 0 || self.connect_timeout_ms > i32::MAX as u64 {
            return Err(CacheError::InvalidArgument(
                "Yuanrong connect_timeout_ms must be between 1 and i32::MAX".into(),
            ));
        }
        if self.request_timeout_ms == 0 || self.request_timeout_ms > i32::MAX as u64 {
            return Err(CacheError::InvalidArgument(
                "Yuanrong request_timeout_ms must be between 1 and i32::MAX".into(),
            ));
        }
        if self.sdk_concurrency == 0 || self.sdk_concurrency > u32::MAX as usize {
            return Err(CacheError::InvalidArgument(
                "Yuanrong sdk_concurrency must be between 1 and u32::MAX".into(),
            ));
        }
        Ok(())
    }
}
