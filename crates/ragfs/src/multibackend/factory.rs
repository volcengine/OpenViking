//! Multi-backend runtime assembly from validated mount configuration.

use std::collections::{HashMap, HashSet};
use std::sync::Arc;
use std::time::Duration;

use tokio::sync::RwLock;

use crate::core::encryption_wrapper::EncryptionWrappedFS;
use crate::core::errors::{Error, Result};
use crate::core::filesystem::FileSystem;
use crate::core::multibackend_wrapper::{BackendEntry, MultiWriteWrappedFS};
use crate::core::plugin::ServicePlugin;
use crate::core::types::{BackendRole, BackendsConfig, ConfigValue, PluginConfig};
use crate::multibackend::config::{
    item_params_to_config_values, sync_mode_from_config, validate_backup_excludes,
    validate_primary_encryption_flags, validate_redirect_targets,
};
use crate::multibackend::meta::MountRootFsContextResolver;
use crate::multibackend::types::MultiBackendBuildContext;
use crate::shape::validate::ensure_backend_shape;

/// Initialize one backend plugin instance from config params.
pub async fn init_backend_plugin(
    registry: &Arc<RwLock<HashMap<String, Arc<dyn ServicePlugin>>>>,
    plugin_name: &str,
    params: &HashMap<String, ConfigValue>,
) -> Result<Arc<dyn FileSystem>> {
    let plugin = {
        let registry = registry.read().await;
        registry
            .get(plugin_name)
            .cloned()
            .ok_or_else(|| Error::plugin(format!("Plugin '{}' not registered", plugin_name)))?
    };

    let plugin_config = PluginConfig::single_backend(plugin_name, String::new(), params.clone());

    plugin.validate(&plugin_config).await?;
    let fs = plugin.initialize(plugin_config).await?;
    Ok(Arc::from(fs))
}

/// Build the multi-backend wrapper from primary and backup configuration.
pub async fn build_multi_write_fs(
    registry: &Arc<RwLock<HashMap<String, Arc<dyn ServicePlugin>>>>,
    config: &PluginConfig,
    bc: &BackendsConfig,
    build_ctx: MultiBackendBuildContext,
) -> Result<MultiWriteWrappedFS> {
    let global_encryption_enabled = build_ctx.global_encryption_enabled();
    validate_primary_encryption_flags(config, global_encryption_enabled)?;

    let primary_raw = init_backend_plugin(registry, &config.name, &config.params).await?;
    ensure_backend_shape(
        &primary_raw,
        &config.name,
        global_encryption_enabled,
        build_ctx.enc_provider_type,
        build_ctx.enc_root_key,
    )
    .await?;
    let primary_backend: Arc<dyn FileSystem> = if global_encryption_enabled {
        Arc::new(EncryptionWrappedFS::new(
            primary_raw.clone(),
            build_ctx
                .enc_root_key
                .expect("global encryption validated before building primary backend"),
            build_ctx
                .enc_provider_type
                .expect("global encryption validated before building primary backend"),
        ))
    } else {
        primary_raw.clone()
    };

    let mut backup_entries: Vec<BackendEntry> = Vec::new();
    let mut seen_names: HashSet<String> = HashSet::new();

    for item in &bc.items {
        if item.name == "primary" {
            return Err(Error::config(
                "backup backend name 'primary' is reserved".to_string(),
            ));
        }
        if !seen_names.insert(item.name.clone()) {
            return Err(Error::config(format!(
                "duplicate backup name '{}'",
                item.name
            )));
        }

        let backup_params = item_params_to_config_values(&item.params)?;
        let backup_raw = init_backend_plugin(registry, &item.backend, &backup_params).await?;
        let backup_encrypted = global_encryption_enabled
            && item
                .encryption
                .as_ref()
                .map(|encryption| encryption.enabled)
                .unwrap_or(true);
        ensure_backend_shape(
            &backup_raw,
            &item.backend,
            backup_encrypted,
            if backup_encrypted {
                build_ctx.enc_provider_type
            } else {
                None
            },
            if backup_encrypted {
                build_ctx.enc_root_key
            } else {
                None
            },
        )
        .await?;

        let backup_backend: Arc<dyn FileSystem> = if backup_encrypted {
            Arc::new(EncryptionWrappedFS::new(
                backup_raw,
                build_ctx
                    .enc_root_key
                    .expect("global encryption validated before building backup backend"),
                build_ctx
                    .enc_provider_type
                    .expect("global encryption validated before building backup backend"),
            ))
        } else {
            backup_raw
        };

        backup_entries.push(BackendEntry {
            name: item.name.clone(),
            role: BackendRole::Backup,
            backend: backup_backend,
            raw_backend: None,
            operations: item.operations.clone().unwrap_or_default(),
            excludes: item.excludes.clone().unwrap_or_default(),
        });
    }

    validate_redirect_targets(&config.primary_redirects, &backup_entries)?;
    validate_backup_excludes(bc)?;

    MultiWriteWrappedFS::builder(primary_backend)
        .with_primary_raw_backend(primary_raw)
        .with_backups(backup_entries)
        .with_redirects(config.primary_redirects.clone())
        .sync_mode(sync_mode_from_config(bc))
        .write_concurrency(bc.write_concurrency)
        .retry_interval(Duration::from_millis(
            bc.retry_interval_ms.unwrap_or(30_000),
        ))
        .retry_backoff_base_ms(bc.retry_backoff_base_ms.unwrap_or(1_000))
        .max_retry_per_round(bc.retry_max_retries_per_round.unwrap_or(3))
        .quarantine_after_failures(bc.retry_quarantine_after_failures.unwrap_or(9))
        .ctx_resolver(Arc::new(MountRootFsContextResolver::new(
            &config.mount_path,
        )))
        .build()
}
