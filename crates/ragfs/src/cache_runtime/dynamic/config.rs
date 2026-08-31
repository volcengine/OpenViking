use std::path::PathBuf;

/// Configuration for one dynamically loaded cache provider.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DynamicProviderConfig {
    /// Absolute or process-resolvable path to the provider library.
    pub library_path: PathBuf,
    /// Provider-owned JSON configuration passed unchanged to `init`.
    pub params_json: String,
}
