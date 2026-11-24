//! Multi-backend config validation and normalization helpers.

use std::collections::{HashMap, HashSet};

use serde_json::Value;

use crate::core::errors::{Error, Result};
use crate::core::types::{BackendsConfig, ConfigValue, PluginConfig};

/// Convert one nested backup `params` object into plugin config values.
pub fn item_params_to_config_values(value: &Value) -> Result<HashMap<String, ConfigValue>> {
    if value.is_null() {
        return Ok(HashMap::new());
    }
    let obj = value.as_object().ok_or_else(|| {
        Error::config(format!(
            "backup item params must be a JSON object, got: {:?}",
            value
        ))
    })?;
    let mut params = HashMap::new();
    for (k, v) in obj {
        let cv = match v {
            Value::String(s) => ConfigValue::String(s.clone()),
            Value::Number(n) => {
                if let Some(i) = n.as_i64() {
                    ConfigValue::Int(i)
                } else {
                    ConfigValue::String(n.to_string())
                }
            }
            Value::Bool(b) => ConfigValue::Bool(*b),
            Value::Array(arr) => ConfigValue::StringList(
                arr.iter()
                    .map(|item| match item {
                        Value::String(s) => s.clone(),
                        other => other.to_string(),
                    })
                    .collect(),
            ),
            Value::Object(_) => ConfigValue::Json(v.clone()),
            _ => ConfigValue::String(v.to_string()),
        };
        params.insert(k.clone(), cv);
    }
    Ok(params)
}

/// Validate encryption flags on the primary mount config.
pub fn validate_primary_encryption_flags(
    config: &PluginConfig,
    global_encryption_enabled: bool,
) -> Result<()> {
    if global_encryption_enabled && !config.server_encryption_enabled {
        return Err(Error::config(
            "server_encryption_enabled must be true when global encryption is configured"
                .to_string(),
        ));
    }
    if global_encryption_enabled && !config.primary_encryption_enabled {
        return Err(Error::config(
            "primary_encryption_enabled cannot be false when global encryption is enabled",
        ));
    }
    Ok(())
}

/// Validate V2 backup settings.
pub fn validate_backups_config(bc: &BackendsConfig) -> Result<()> {
    if !matches!(bc.provider.as_str(), "filesystem" | "cache") {
        return Err(Error::config(
            "backups.provider must be 'filesystem' or 'cache'".to_string(),
        ));
    }

    let mut names = HashSet::new();
    for item in &bc.items {
        let name = item.name.trim();
        if name.is_empty() {
            return Err(Error::config("backup name must not be empty".to_string()));
        }
        if name == "primary" {
            return Err(Error::config(
                "backup backend name 'primary' is reserved".to_string(),
            ));
        }
        if !names.insert(name) {
            return Err(Error::config(format!(
                "duplicate backup name '{}'",
                item.name
            )));
        }
    }

    Ok(())
}
