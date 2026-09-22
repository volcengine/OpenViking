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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn debug_output_redacts_provider_params() {
        let config = DynamicProviderConfig {
            library_path: PathBuf::from("/opt/openviking/libprovider.so"),
            params_json: r#"{"password":"secret-value"}"#.to_string(),
        };

        let output = format!("{config:?}");

        assert!(output.contains("/opt/openviking/libprovider.so"));
        assert!(!output.contains("secret-value"));
        assert!(output.contains("<redacted>"));
    }
}
