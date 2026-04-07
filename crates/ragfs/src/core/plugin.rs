//! Plugin system for RAGFS
//!
//! This module defines the ServicePlugin trait that all plugins must implement.
//! Plugins provide filesystem implementations that can be dynamically mounted
//! at different paths.

use async_trait::async_trait;
use std::collections::HashMap;
use std::sync::Arc;

use super::errors::Result;
use super::filesystem::FileSystem;
use super::types::{ConfigParameter, PluginConfig};

/// Service plugin trait
///
/// All filesystem plugins must implement this trait to be registered
/// and used within RAGFS. The plugin is responsible for validating
/// configuration and creating filesystem instances.
#[async_trait]
pub trait ServicePlugin: Send + Sync {
    /// Get the unique name of this plugin
    ///
    /// This name is used to identify the plugin in configuration
    /// and mount operations.
    fn name(&self) -> &str;

    /// Get the plugin version
    fn version(&self) -> &str {
        "0.1.0"
    }

    /// Get a brief description of the plugin
    fn description(&self) -> &str {
        ""
    }

    /// Get the README documentation for this plugin
    ///
    /// This should include usage examples, configuration parameters,
    /// and any special considerations.
    fn readme(&self) -> &str;

    /// Validate plugin configuration
    ///
    /// This is called before initialize() to ensure the configuration
    /// is valid. Should check for required parameters, valid values, etc.
    ///
    /// # Arguments
    /// * `config` - The configuration to validate
    ///
    /// # Errors
    /// Returns an error if the configuration is invalid
    async fn validate(&self, config: &PluginConfig) -> Result<()>;

    /// Initialize the plugin and return a filesystem instance
    ///
    /// This is called after validate() succeeds. The plugin should
    /// create and return a new filesystem instance configured according
    /// to the provided configuration.
    ///
    /// # Arguments
    /// * `config` - The validated configuration
    ///
    /// # Returns
    /// A boxed FileSystem implementation
    ///
    /// # Errors
    /// Returns an error if initialization fails
    async fn initialize(&self, config: PluginConfig) -> Result<Box<dyn FileSystem>>;

    /// Shutdown the plugin
    ///
    /// This is called when the plugin is being unmounted or the server
    /// is shutting down. The plugin should clean up any resources.
    async fn shutdown(&self) -> Result<()> {
        Ok(())
    }

    /// Get the configuration parameters supported by this plugin
    ///
    /// Returns a list of parameter definitions that describe what
    /// configuration this plugin accepts.
    fn config_params(&self) -> &[ConfigParameter];

    /// Health check for the plugin
    ///
    /// Returns whether the plugin is healthy and operational.
    async fn health_check(&self) -> Result<HealthStatus> {
        Ok(HealthStatus::Healthy)
    }
}

/// Health status of a plugin
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum HealthStatus {
    /// Plugin is healthy and operational
    Healthy,

    /// Plugin is degraded but still functional
    Degraded(String),

    /// Plugin is unhealthy and not functional
    Unhealthy(String),
}

/// Plugin registry
///
/// Manages all registered plugins and provides lookup functionality.
pub struct PluginRegistry {
    plugins: HashMap<String, Arc<dyn ServicePlugin>>,
}

impl PluginRegistry {
    /// Create a new empty plugin registry
    pub fn new() -> Self {
        Self {
            plugins: HashMap::new(),
        }
    }

    /// Register a plugin
    ///
    /// # Arguments
    /// * `plugin` - The plugin to register
    ///
    /// # Panics
    /// Panics if a plugin with the same name is already registered
    pub fn register<P: ServicePlugin + 'static>(&mut self, plugin: P) {
        let name = plugin.name().to_string();
        if self.plugins.contains_key(&name) {
            panic!("Plugin '{}' is already registered", name);
        }
        self.plugins.insert(name, Arc::new(plugin));
    }

    /// Get a plugin by name
    ///
    /// # Arguments
    /// * `name` - The name of the plugin to retrieve
    ///
    /// # Returns
    /// An Arc to the plugin, or None if not found
    pub fn get(&self, name: &str) -> Option<Arc<dyn ServicePlugin>> {
        self.plugins.get(name).cloned()
    }

    /// List all registered plugin names
    pub fn list(&self) -> Vec<&str> {
        self.plugins.keys().map(|s| s.as_str()).collect()
    }

    /// Get the number of registered plugins
    pub fn len(&self) -> usize {
        self.plugins.len()
    }

    /// Check if the registry is empty
    pub fn is_empty(&self) -> bool {
        self.plugins.is_empty()
    }
}

impl Default for PluginRegistry {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // Mock plugin for testing
    struct MockPlugin;

    #[async_trait]
    impl ServicePlugin for MockPlugin {
        fn name(&self) -> &str {
            "mock"
        }

        fn readme(&self) -> &str {
            "Mock plugin for testing"
        }

        async fn validate(&self, _config: &PluginConfig) -> Result<()> {
            Ok(())
        }

        async fn initialize(&self, _config: PluginConfig) -> Result<Box<dyn FileSystem>> {
            use crate::core::filesystem::FileSystem;
            use crate::core::types::{FileInfo, WriteFlag};

            struct MockFS;

            #[async_trait]
            impl FileSystem for MockFS {
                async fn create(&self, _path: &str) -> Result<()> {
                    Ok(())
                }
                async fn mkdir(&self, _path: &str, _mode: u32) -> Result<()> {
                    Ok(())
                }
                async fn remove(&self, _path: &str) -> Result<()> {
                    Ok(())
                }
                async fn remove_all(&self, _path: &str) -> Result<()> {
                    Ok(())
                }
                async fn read(&self, _path: &str, _offset: u64, _size: u64) -> Result<Vec<u8>> {
                    Ok(vec![])
                }
                async fn write(&self, _path: &str, _data: &[u8], _offset: u64, _flags: WriteFlag) -> Result<u64> {
                    Ok(_data.len() as u64)
                }
                async fn read_dir(&self, _path: &str) -> Result<Vec<FileInfo>> {
                    Ok(vec![])
                }
                async fn stat(&self, _path: &str) -> Result<FileInfo> {
                    Ok(FileInfo::new_file("test".to_string(), 0, 0o644))
                }
                async fn rename(&self, _old_path: &str, _new_path: &str) -> Result<()> {
                    Ok(())
                }
                async fn chmod(&self, _path: &str, _mode: u32) -> Result<()> {
                    Ok(())
                }
            }

            Ok(Box::new(MockFS))
        }

        fn config_params(&self) -> &[ConfigParameter] {
            &[]
        }
    }

    #[test]
    fn test_plugin_registry() {
        let mut registry = PluginRegistry::new();
        assert!(registry.is_empty());

        registry.register(MockPlugin);
        assert_eq!(registry.len(), 1);
        assert!(registry.get("mock").is_some());
        assert!(registry.get("nonexistent").is_none());

        let names = registry.list();
        assert_eq!(names, vec!["mock"]);
    }

    #[tokio::test]
    async fn test_plugin_lifecycle() {
        let plugin = MockPlugin;

        let config = PluginConfig {
            name: "mock".to_string(),
            mount_path: "/mock".to_string(),
            params: HashMap::new(),
        };

        assert!(plugin.validate(&config).await.is_ok());
        assert!(plugin.initialize(config).await.is_ok());
        assert!(plugin.shutdown().await.is_ok());
    }

    #[test]
    fn test_health_status() {
        let healthy = HealthStatus::Healthy;
        assert_eq!(healthy, HealthStatus::Healthy);

        let degraded = HealthStatus::Degraded("slow".to_string());
        assert!(matches!(degraded, HealthStatus::Degraded(_)));
    }
}
