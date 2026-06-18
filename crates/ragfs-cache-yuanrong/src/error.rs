/// Errors returned by the synchronous Yuanrong KV boundary.
#[derive(Debug, thiserror::Error)]
pub enum YuanrongStoreError {
    /// The worker or client connection is unavailable.
    #[error("Yuanrong unavailable: {0}")]
    Unavailable(String),
    /// A Yuanrong SDK operation timed out.
    #[error("Yuanrong operation timed out: {0}")]
    Timeout(String),
    /// Yuanrong rejected an argument.
    #[error("invalid Yuanrong argument: {0}")]
    InvalidArgument(String),
    /// Yuanrong returned an unspecified operation failure.
    #[error("Yuanrong operation failed: {0}")]
    Internal(String),
}
