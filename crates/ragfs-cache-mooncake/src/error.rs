/// Errors returned by the synchronous Mooncake object API.
#[derive(Debug, thiserror::Error)]
pub enum MooncakeStoreError {
    /// The requested object does not exist.
    #[error("object not found")]
    NotFound,
    /// Mooncake or one of its required services is unavailable.
    #[error("Mooncake unavailable: {0}")]
    Unavailable(String),
    /// Mooncake rejected an argument.
    #[error("invalid Mooncake argument: {0}")]
    InvalidArgument(String),
    /// Mooncake returned an unspecified operation failure.
    #[error("Mooncake operation failed: {0}")]
    Internal(String),
}
