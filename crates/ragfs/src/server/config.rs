//! Server configuration module
//!
//! This module handles server configuration including address binding,
//! logging levels, and other runtime settings.

use clap::Parser;
use serde::{Deserialize, Serialize};
use std::net::SocketAddr;

/// Server configuration
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ServerConfig {
    /// Server bind address
    pub address: String,

    /// Log level (trace, debug, info, warn, error)
    pub log_level: String,

    /// Enable CORS
    pub enable_cors: bool,
}

impl Default for ServerConfig {
    fn default() -> Self {
        Self {
            address: "0.0.0.0:8080".to_string(),
            log_level: "info".to_string(),
            enable_cors: true,
        }
    }
}

impl ServerConfig {
    /// Parse server address into SocketAddr
    pub fn socket_addr(&self) -> Result<SocketAddr, std::io::Error> {
        self.address.parse().map_err(|e| {
            std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                format!("Invalid address '{}': {}", self.address, e),
            )
        })
    }
}

/// Command-line arguments
#[derive(Debug, Parser)]
#[command(name = "ragfs-server")]
#[command(about = "RAGFS HTTP Server", long_about = None)]
pub struct Args {
    /// Server bind address
    #[arg(short, long, default_value = "0.0.0.0:8080", env = "RAGFS_ADDRESS")]
    pub address: String,

    /// Log level
    #[arg(short, long, default_value = "info", env = "RAGFS_LOG_LEVEL")]
    pub log_level: String,

    /// Configuration file path (optional)
    #[arg(short, long, env = "RAGFS_CONFIG")]
    pub config: Option<String>,

    /// Enable CORS
    #[arg(long, default_value = "true", env = "RAGFS_ENABLE_CORS")]
    pub enable_cors: bool,
}

impl Args {
    /// Convert Args to ServerConfig
    pub fn to_config(&self) -> ServerConfig {
        ServerConfig {
            address: self.address.clone(),
            log_level: self.log_level.clone(),
            enable_cors: self.enable_cors,
        }
    }

    /// Load configuration from file if specified, otherwise use CLI args
    pub fn load_config(&self) -> Result<ServerConfig, Box<dyn std::error::Error>> {
        if let Some(config_path) = &self.config {
            // Load from YAML file
            let content = std::fs::read_to_string(config_path)?;
            let config: ServerConfig = serde_yaml::from_str(&content)?;
            Ok(config)
        } else {
            // Use CLI args
            Ok(self.to_config())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_default_config() {
        let config = ServerConfig::default();
        assert_eq!(config.address, "0.0.0.0:8080");
        assert_eq!(config.log_level, "info");
        assert!(config.enable_cors);
    }

    #[test]
    fn test_socket_addr_parsing() {
        let config = ServerConfig {
            address: "127.0.0.1:3000".to_string(),
            log_level: "debug".to_string(),
            enable_cors: false,
        };

        let addr = config.socket_addr().unwrap();
        assert_eq!(addr.port(), 3000);
    }

    #[test]
    fn test_invalid_socket_addr() {
        let config = ServerConfig {
            address: "invalid".to_string(),
            log_level: "info".to_string(),
            enable_cors: true,
        };

        assert!(config.socket_addr().is_err());
    }
}
