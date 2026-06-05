use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

use crate::error::{Error, Result};

const OPENVIKING_CLI_CONFIG_ENV: &str = "OPENVIKING_CLI_CONFIG_FILE";
pub const DEFAULT_SELF_MANAGED_PORT: &str = "1933";
pub const DEFAULT_SELF_MANAGED_URL: &str = "http://127.0.0.1:1933";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UploadConfig {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ignore_dirs: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub include: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub exclude: Option<String>,
}

impl UploadConfig {
    fn is_default(&self) -> bool {
        self.ignore_dirs.is_none() && self.include.is_none() && self.exclude.is_none()
    }
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
    #[serde(default = "default_url", skip_serializing_if = "is_default_url")]
    pub url: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub api_key: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub root_api_key: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none", alias = "account_id")]
    pub account: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none", alias = "user_id")]
    pub user: Option<String>,
    #[serde(
        default = "default_timeout",
        skip_serializing_if = "is_default_timeout"
    )]
    pub timeout: f64,
    #[serde(
        default = "default_output_format",
        skip_serializing_if = "is_default_output"
    )]
    pub output: String,
    #[serde(
        default = "default_echo_command",
        skip_serializing_if = "is_default_echo_command"
    )]
    pub echo_command: bool,
    #[serde(
        default = "default_show_progress",
        skip_serializing_if = "is_default_show_progress"
    )]
    pub show_progress: bool,
    #[serde(
        default = "default_verbose",
        skip_serializing_if = "is_default_verbose"
    )]
    pub verbose: bool,
    #[serde(default, skip_serializing_if = "is_default_profile")]
    pub profile: bool,
    #[serde(default, skip_serializing_if = "UploadConfig::is_default")]
    pub upload: UploadConfig,
    #[serde(
        default,
        alias = "extra_header",
        skip_serializing_if = "Option::is_none"
    )]
    pub extra_headers: Option<std::collections::HashMap<String, String>>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct EffectiveAuth {
    pub api_key: Option<String>,
    pub account: Option<String>,
    pub user: Option<String>,
}

fn default_url() -> String {
    DEFAULT_SELF_MANAGED_URL.to_string()
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

fn is_default_url(value: &str) -> bool {
    value == default_url()
}

fn is_default_timeout(value: &f64) -> bool {
    (*value - default_timeout()).abs() < f64::EPSILON
}

fn is_default_output(value: &str) -> bool {
    value == default_output_format()
}

fn is_default_echo_command(value: &bool) -> bool {
    *value == default_echo_command()
}

fn is_default_show_progress(value: &bool) -> bool {
    *value == default_show_progress()
}

fn is_default_verbose(value: &bool) -> bool {
    *value == default_verbose()
}

fn is_default_profile(value: &bool) -> bool {
    !*value
}

impl Default for Config {
    fn default() -> Self {
        Self {
            url: DEFAULT_SELF_MANAGED_URL.to_string(),
            api_key: None,
            root_api_key: None,
            account: None,
            user: None,
            timeout: 60.0,
            output: "table".to_string(),
            echo_command: true,
            show_progress: false,
            verbose: false,
            profile: false,
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

    pub fn load_required() -> Result<Self> {
        // Resolution order: env var > default path
        if let Ok(env_path) = std::env::var(OPENVIKING_CLI_CONFIG_ENV) {
            return Self::load_required_from_path(&PathBuf::from(env_path));
        }

        let config_path = default_config_path()?;
        Self::load_required_from_path(&config_path)
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
        Self::load_default_from_path(&config_path)
    }

    pub fn load_required_from_path(path: &Path) -> Result<Self> {
        if path.exists() {
            Self::from_file(&path.to_string_lossy())
        } else {
            Err(Error::MissingConfig)
        }
    }

    pub fn load_default_from_path(path: &Path) -> Result<Self> {
        if path.exists() {
            Self::from_file(&path.to_string_lossy())
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

    pub(crate) fn effective_auth(&self, sudo: bool) -> EffectiveAuth {
        self.effective_auth_with_overrides(None, None, None, sudo)
    }

    pub(crate) fn effective_auth_with_overrides(
        &self,
        api_key_override: Option<String>,
        account_override: Option<String>,
        user_override: Option<String>,
        sudo: bool,
    ) -> EffectiveAuth {
        let api_key = if sudo {
            self.root_api_key.clone()
        } else {
            api_key_override.or_else(|| self.api_key.clone())
        };
        let account = account_override.or_else(|| self.account.clone());
        let user = user_override.or_else(|| self.user.clone());

        let send_identity = if sudo {
            true
        } else {
            api_key.is_none() || api_key.as_deref() == self.root_api_key.as_deref()
        };

        EffectiveAuth {
            api_key,
            account: send_identity.then_some(account).flatten(),
            user: send_identity.then_some(user).flatten(),
        }
    }
}

pub fn default_config_path() -> Result<PathBuf> {
    let home = dirs::home_dir()
        .ok_or_else(|| Error::Config("Could not determine home directory".to_string()))?;
    Ok(home.join(".openviking").join("ovcli.conf"))
}

pub fn display_config_home() -> String {
    let path = default_config_path()
        .ok()
        .and_then(|path| path.parent().map(|parent| parent.to_path_buf()));
    let Some(path) = path else {
        return "~/.openviking".to_string();
    };
    let Some(home) = dirs::home_dir() else {
        return path.display().to_string();
    };
    if let Ok(stripped) = path.strip_prefix(&home) {
        return format!("~/{}", stripped.display());
    }
    path.display().to_string()
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
    use crate::error::Error;

    use super::{Config, merge_csv_options};

    #[test]
    fn load_required_from_path_reports_missing_cli_config() {
        let dir = tempfile::tempdir().expect("tempdir should be created");
        let path = dir.path().join("missing-ovcli.conf");

        let error = Config::load_required_from_path(&path)
            .expect_err("missing required config should fail");

        assert!(matches!(error, Error::MissingConfig));
        let message = error.to_string();
        assert!(message.contains("No ovcli.conf detected"));
        assert!(message.contains("ov config"));
        assert!(!message.contains("setup-cli"));
    }

    #[test]
    fn load_default_from_path_keeps_default_fallback() {
        let dir = tempfile::tempdir().expect("tempdir should be created");
        let path = dir.path().join("missing-ovcli.conf");

        let config = Config::load_default_from_path(&path)
            .expect("default-loading path should still fall back");

        assert_eq!(config.url, "http://127.0.0.1:1933");
    }

    #[test]
    fn config_deserializes_account_and_user_fields() {
        let config: Config = serde_json::from_str(
            r#"{
                "url": "http://127.0.0.1:1933",
                "api_key": "test-key",
                "account": "acme",
                "user": "alice"
            }"#,
        )
        .expect("config should deserialize");

        assert_eq!(config.account.as_deref(), Some("acme"));
        assert_eq!(config.user.as_deref(), Some("alice"));
        assert!(config.upload.ignore_dirs.is_none());
        assert!(config.upload.include.is_none());
        assert!(config.upload.exclude.is_none());
    }

    #[test]
    fn config_deserializes_root_api_key() {
        let config: Config = serde_json::from_str(
            r#"{
                "url": "http://127.0.0.1:1933",
                "api_key": "user-key",
                "root_api_key": "root-key"
            }"#,
        )
        .expect("config should deserialize with root_api_key");

        assert_eq!(config.api_key.as_deref(), Some("user-key"));
        assert_eq!(config.root_api_key.as_deref(), Some("root-key"));
    }

    #[test]
    fn effective_auth_omits_stale_identity_for_regular_user_key() {
        let config = Config {
            api_key: Some("user-key".to_string()),
            root_api_key: Some("root-key".to_string()),
            account: Some("stale-account".to_string()),
            user: Some("stale-user".to_string()),
            ..Config::default()
        };

        let auth = config.effective_auth(false);

        assert_eq!(auth.api_key.as_deref(), Some("user-key"));
        assert!(auth.account.is_none());
        assert!(auth.user.is_none());
    }

    #[test]
    fn effective_auth_sends_identity_for_root_as_normal() {
        let config = Config {
            api_key: Some("root-key".to_string()),
            root_api_key: Some("root-key".to_string()),
            account: Some("acme".to_string()),
            user: Some("alice".to_string()),
            ..Config::default()
        };

        let auth = config.effective_auth(false);

        assert_eq!(auth.api_key.as_deref(), Some("root-key"));
        assert_eq!(auth.account.as_deref(), Some("acme"));
        assert_eq!(auth.user.as_deref(), Some("alice"));
    }

    #[test]
    fn effective_auth_sends_identity_for_no_key_configs() {
        let config = Config {
            account: Some("default".to_string()),
            user: Some("default".to_string()),
            ..Config::default()
        };

        let auth = config.effective_auth(false);

        assert!(auth.api_key.is_none());
        assert_eq!(auth.account.as_deref(), Some("default"));
        assert_eq!(auth.user.as_deref(), Some("default"));
    }

    #[test]
    fn effective_auth_uses_root_key_and_identity_for_sudo() {
        let config = Config {
            api_key: Some("user-key".to_string()),
            root_api_key: Some("root-key".to_string()),
            account: Some("acme".to_string()),
            user: Some("alice".to_string()),
            ..Config::default()
        };

        let auth = config.effective_auth(true);

        assert_eq!(auth.api_key.as_deref(), Some("root-key"));
        assert_eq!(auth.account.as_deref(), Some("acme"));
        assert_eq!(auth.user.as_deref(), Some("alice"));
    }

    #[test]
    fn config_deserializes_account_id_and_user_id_aliases() {
        let config: Config = serde_json::from_str(
            r#"{
                "url": "http://127.0.0.1:1933",
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
                "url": "http://127.0.0.1:1933",
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
                "url": "http://127.0.0.1:1933",
                "extra_headers": {
                    "X-Custom-Header": "custom-value",
                    "Authorization": "Bearer token"
                }
            }"#,
        )
        .expect("config should deserialize with extra_headers");

        let headers = config
            .extra_headers
            .expect("extra_headers should be present");
        assert_eq!(
            headers.get("X-Custom-Header"),
            Some(&"custom-value".to_string())
        );
        assert_eq!(
            headers.get("Authorization"),
            Some(&"Bearer token".to_string())
        );
    }

    #[test]
    fn config_deserializes_extra_headers_none_when_missing() {
        let config: Config = serde_json::from_str(
            r#"{
                "url": "http://127.0.0.1:1933"
            }"#,
        )
        .expect("config should deserialize");

        assert!(config.extra_headers.is_none());
    }

    #[test]
    fn config_deserializes_profile_flag() {
        let config: Config = serde_json::from_str(
            r#"{
                "url": "http://127.0.0.1:1933",
                "profile": true
            }"#,
        )
        .expect("config should deserialize with profile");

        assert!(config.profile);
    }

    #[test]
    fn config_deserializes_extra_header_alias() {
        let config: Config = serde_json::from_str(
            r#"{
                "url": "http://127.0.0.1:1933",
                "extra_header": {
                    "X-Custom-Header": "custom-value",
                    "Authorization": "Bearer token"
                }
            }"#,
        )
        .expect("config should deserialize with alias");

        let headers = config
            .extra_headers
            .expect("extra_headers should be present");
        assert_eq!(
            headers.get("X-Custom-Header"),
            Some(&"custom-value".to_string())
        );
        assert_eq!(
            headers.get("Authorization"),
            Some(&"Bearer token".to_string())
        );
    }
}
