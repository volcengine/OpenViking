use std::fmt;
use std::path::PathBuf;

/// Configuration for one dynamically loaded cache provider.
#[derive(Clone, PartialEq, Eq)]
pub struct DynamicProviderConfig {
    /// Absolute path to the provider library.
    pub library_path: PathBuf,
    /// Provider-owned JSON configuration passed unchanged to `create`.
    pub params_json: String,
}

impl fmt::Debug for DynamicProviderConfig {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("DynamicProviderConfig")
            .field("library_path", &self.library_path)
            .field("params_json", &"<redacted>")
            .field("params_length", &self.params_json.len())
            .finish()
    }
}
