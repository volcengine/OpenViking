use serde::{Deserialize, Serialize};
use std::path::PathBuf;

use crate::error::{Error, Result};

const OPENVIKING_CLI_CONFIG_ENV: &str = "OPENVIKING_CLI_CONFIG_FILE";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Config {
    #[serde(default = "default_url")]
    pub url: String,
    pub api_key: Option<String>,
    pub agent_id: Option<String>,
    #[serde(default = "default_timeout")]
    pub timeout: f64,
    #[serde(default = "default_output_format")]
    pub output: String,
    #[serde(default = "default_echo_command")]
    pub echo_command: bool,
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

impl Default for Config {
    fn default() -> Self {
        Self {
            url: "http://localhost:1933".to_string(),
            api_key: None,
            agent_id: None,
            timeout: 60.0,
            output: "table".to_string(),
            echo_command: true,
        }
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
