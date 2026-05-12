use serde::{Deserialize, Serialize};
use std::path::PathBuf;

use crate::error::{Error, Result};

const OPENVIKING_CLI_CONFIG_ENV: &str = "OPENVIKING_CLI_CONFIG_FILE";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UploadConfig {
    pub ignore_dirs: Option<String>,
    pub include: Option<String>,
    pub exclude: Option<String>,
}

impl Default for UploadConfig {
    fn default() -> Self {
        Self {
            ignore_dirs: None,
            include: None,
            exclude: None,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Config {
    #[serde(default = "default_url")]
    pub url: String,
    pub api_key: Option<String>,
    pub root_api_key: Option<String>,
    #[serde(alias = "account_id")]
    pub account: Option<String>,
    #[serde(alias = "user_id")]
    pub user: Option<String>,
    pub agent_id: Option<String>,
    #[serde(default = "default_timeout")]
    pub timeout: f64,
    #[serde(default = "default_output_format")]
    pub output: String,
    #[serde(default = "default_echo_command")]
    pub echo_command: bool,
    #[serde(default = "default_show_progress")]
    pub show_progress: bool,
    #[serde(default = "default_verbose")]
    pub verbose: bool,
    #[serde(default)]
    pub upload: UploadConfig,
    #[serde(default, alias = "extra_header")]
    pub extra_headers: Option<std::collections::HashMap<String, String>>,
}

fn default_url() -> String {
    "http://localhost:1933".to_string()
}

fn default_timeout() -> f64 {
    60.0
}

fn default_output_format() -> String {
    "table".to_string()
}

fn default_echo_command() -> bool {
    true
}

fn default_show_progress() -> bool {
    false
}

fn default_verbose() -> bool {
    false
}

impl Default for Config {
    fn default() -> Self {
        Self {
            url: "http://localhost:1933".to_string(),
            api_key: None,
            root_api_key: None,
            account: None,
            user: None,
            agent_id: None,
            timeout: 60.0,
            output: "table".to_string(),
            echo_command: true,
            show_progress: false,
            verbose: false,
            upload: UploadConfig::default(),
            extra_headers: None,
        }
    }
}

fn normalize_csv_option(value: Option<String>) -> Vec<String> {
    value
        .unwrap_or_default()
        .split(',')
        .map(str::trim)
        .filter(|token| !token.is_empty())
        .map(ToString::to_string)
        .collect()
}

pub fn merge_csv_options(
    config_value: Option<String>,
    cli_value: Option<String>,
) -> Option<String> {
    let mut merged = normalize_csv_option(config_value);
    merged.extend(normalize_csv_option(cli_value));

    if merged.is_empty() {
        None
    } else {
        Some(merged.join(","))
    }
}

impl Config {
    /// Load config from default location or create default
    pub fn load() -> Result<Self> {
        Self::load_default()
    }

    pub fn load_default() -> Result<Self> {
        // Resolution order: env var > default path
        if let Ok(env_path) = std::env::var(OPENVIKING_CLI_CONFIG_ENV) {
            let p = PathBuf::from(env_path);
            if p.exists() {
                return Self::from_file(&p.to_string_lossy());
            }
        }

        let config_path = default_config_path()?;
        if config_path.exists() {
            Self::from_file(&config_path.to_string_lossy())
        } else {
            Ok(Self::default())
        }
    }

    pub fn from_file(path: &str) -> Result<Self> {
        let content = std::fs::read_to_string(path)
            .map_err(|e| Error::Config(format!("Failed to read config file: {}", e)))?;
        let config: Config = serde_json::from_str(&content)
            .map_err(|e| Error::Config(format!("Failed to parse config file: {}", e)))?;
        Ok(config)
    }

    pub fn save_default(&self) -> Result<()> {
        let config_path = default_config_path()?;
        if let Some(parent) = config_path.parent() {
            std::fs::create_dir_all(parent)
                .map_err(|e| Error::Config(format!("Failed to create config directory: {}", e)))?;
        }
        let content = serde_json::to_string_pretty(self)
            .map_err(|e| Error::Config(format!("Failed to serialize config: {}", e)))?;
        std::fs::write(&config_path, content)
            .map_err(|e| Error::Config(format!("Failed to write config file: {}", e)))?;
        Ok(())
    }
}

pub fn default_config_path() -> Result<PathBuf> {
    let home = dirs::home_dir()
        .ok_or_else(|| Error::Config("Could not determine home directory".to_string()))?;
    Ok(home.join(".openviking").join("ovcli.conf"))
}

/// Get a unique machine ID using machine-uid crate.
///
/// Uses the system's machine ID, falls back to "default" if unavailable.
pub fn get_or_create_machine_id() -> Result<String> {
    match machine_uid::get() {
        Ok(id) => Ok(id),
        Err(_) => Ok("default".to_string()),
    }
}

#[cfg(test)]
mod tests {
    use super::{Config, merge_csv_options};

    #[test]
    fn config_deserializes_account_and_user_fields() {
        let config: Config = serde_json::from_str(
            r#"{
                "url": "http://localhost:1933",
                "api_key": "test-key",
                "account": "acme",
                "user": "alice",
                "agent_id": "assistant-1"
            }"#,
        )
        .expect("config should deserialize");

        assert_eq!(config.account.as_deref(), Some("acme"));
        assert_eq!(config.user.as_deref(), Some("alice"));
        assert_eq!(config.agent_id.as_deref(), Some("assistant-1"));
        assert!(config.upload.ignore_dirs.is_none());
        assert!(config.upload.include.is_none());
        assert!(config.upload.exclude.is_none());
    }

    #[test]
    fn config_deserializes_root_api_key() {
        let config: Config = serde_json::from_str(
            r#"{
                "url": "http://localhost:1933",
                "api_key": "user-key",
                "root_api_key": "root-key"
            }"#,
        )
        .expect("config should deserialize with root_api_key");

        assert_eq!(config.api_key.as_deref(), Some("user-key"));
        assert_eq!(config.root_api_key.as_deref(), Some("root-key"));
    }

    #[test]
    fn config_deserializes_account_id_and_user_id_aliases() {
        let config: Config = serde_json::from_str(
            r#"{
                "url": "http://localhost:1933",
                "account_id": "acme",
                "user_id": "alice"
            }"#,
        )
        .expect("config should deserialize aliases");

        assert_eq!(config.account.as_deref(), Some("acme"));
        assert_eq!(config.user.as_deref(), Some("alice"));
    }

    #[test]
    fn config_deserializes_upload_fields() {
        let config: Config = serde_json::from_str(
            r#"{
                "url": "http://localhost:1933",
                "upload": {
                    "ignore_dirs": "node_modules,dist",
                    "include": "*.md,*.pdf",
                    "exclude": "*.tmp,*.log"
                }
            }"#,
        )
        .expect("config should deserialize");

        assert_eq!(
            config.upload.ignore_dirs.as_deref(),
            Some("node_modules,dist")
        );
        assert_eq!(config.upload.include.as_deref(), Some("*.md,*.pdf"));
        assert_eq!(config.upload.exclude.as_deref(), Some("*.tmp,*.log"));
    }

    #[test]
    fn merge_csv_options_config_only() {
        assert_eq!(
            merge_csv_options(Some("node_modules,dist".to_string()), None),
            Some("node_modules,dist".to_string())
        );
    }

    #[test]
    fn merge_csv_options_cli_only() {
        assert_eq!(
            merge_csv_options(None, Some("*.md,*.pdf".to_string())),
            Some("*.md,*.pdf".to_string())
        );
    }

    #[test]
    fn merge_csv_options_additive_merge() {
        assert_eq!(
            merge_csv_options(
                Some("node_modules,dist".to_string()),
                Some("build,out".to_string())
            ),
            Some("node_modules,dist,build,out".to_string())
        );
    }

    #[test]
    fn merge_csv_options_trims_and_drops_empty_tokens() {
        assert_eq!(
            merge_csv_options(
                Some(" node_modules , , dist ,".to_string()),
                Some(" ,*.tmp,  *.log  ,".to_string())
            ),
            Some("node_modules,dist,*.tmp,*.log".to_string())
        );
    }

    #[test]
    fn merge_csv_options_returns_none_when_empty() {
        assert_eq!(
            merge_csv_options(Some("  ,  , ".to_string()), Some("".to_string())),
            None
        );
        assert_eq!(merge_csv_options(None, None), None);
    }

    #[test]
    fn config_deserializes_extra_headers() {
        let config: Config = serde_json::from_str(
            r#"{
                "url": "http://localhost:1933",
                "extra_headers": {
                    "X-Custom-Header": "custom-value",
                    "Authorization": "Bearer token"
                }
            }"#,
        )
        .expect("config should deserialize with extra_headers");

        let headers = config.extra_headers.expect("extra_headers should be present");
        assert_eq!(headers.get("X-Custom-Header"), Some(&"custom-value".to_string()));
        assert_eq!(headers.get("Authorization"), Some(&"Bearer token".to_string()));
    }

    #[test]
    fn config_deserializes_extra_headers_none_when_missing() {
        let config: Config = serde_json::from_str(
            r#"{
                "url": "http://localhost:1933"
            }"#,
        )
        .expect("config should deserialize");

        assert!(config.extra_headers.is_none());
    }

    #[test]
    fn config_deserializes_extra_header_alias() {
        let config: Config = serde_json::from_str(
            r#"{
                "url": "http://localhost:1933",
                "extra_header": {
                    "X-Custom-Header": "custom-value",
                    "Authorization": "Bearer token"
                }
            }"#,
        )
        .expect("config should deserialize with alias");

        let headers = config.extra_headers.expect("extra_headers should be present");
        assert_eq!(headers.get("X-Custom-Header"), Some(&"custom-value".to_string()));
        assert_eq!(headers.get("Authorization"), Some(&"Bearer token".to_string()));
    }
}
