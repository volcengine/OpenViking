//! Multi-backend config validation and normalization helpers.

use std::collections::HashMap;

use serde_json::Value;

use crate::core::errors::{Error, Result};
use crate::core::multibackend_wrapper::{BackendEntry, SyncMode};
use crate::core::types::{BackendsConfig, ConfigValue, PluginConfig, RedirectPolicy};

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

/// Validate redirect targets against the assembled backup entries.
pub fn validate_redirect_targets(
    redirects: &[RedirectPolicy],
    backup_entries: &[BackendEntry],
) -> Result<()> {
    for policy in redirects {
        let targets = match policy {
            RedirectPolicy::FileOverSizePolicy { target, .. } => target.as_ref(),
            RedirectPolicy::FileExtensionPolicy { target, .. } => target.as_ref(),
        };
        let target_names = targets
            .ok_or_else(|| Error::config("redirect policy target must not be empty".to_string()))?;
        if target_names.is_empty() {
            return Err(Error::config(
                "redirect policy target must not be empty".to_string(),
            ));
        }
        for name in target_names {
            if !backup_entries.iter().any(|be| &be.name == name) {
                return Err(Error::config(format!(
                    "redirect target '{}' not found in backup entries",
                    name
                )));
            }
        }
    }
    Ok(())
}

/// Validate that exclude policies do not silently carry redirect targets.
pub fn validate_backup_excludes(bc: &BackendsConfig) -> Result<()> {
    for item in &bc.items {
        for policy in item.excludes.as_deref().unwrap_or(&[]) {
            match policy {
                RedirectPolicy::FileOverSizePolicy { target, .. }
                | RedirectPolicy::FileExtensionPolicy { target, .. } => {
                    if target.is_some() {
                        return Err(Error::config(format!(
                            "exclude policy for backup '{}' must not contain target",
                            item.name
                        )));
                    }
                }
            }
        }
    }
    Ok(())
}

/// Convert config sync settings into the runtime sync mode.
pub fn sync_mode_from_config(bc: &BackendsConfig) -> SyncMode {
    match bc.sync_type.as_str() {
        "sync" => SyncMode::Sync {
            ack_count: bc.write_ack_count.unwrap_or(usize::MAX),
            timeout_ms: bc.write_ack_timeout_ms.unwrap_or(0),
        },
        _ => SyncMode::Async,
    }
}
