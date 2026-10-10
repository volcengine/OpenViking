//! Multi-backend runtime assembly from validated mount configuration.

use std::collections::HashMap;
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
    item_params_to_config_values, validate_backups_config, validate_primary_encryption_flags,
};
use crate::multibackend::meta::{MetadataStore, RelativePathFsContextResolver};
#[cfg(feature = "cache")]
use crate::multibackend::provider::CacheProvider;
use crate::multibackend::provider::{FilesystemProvider, MultiWriteProvider};
use crate::multibackend::types::MultiBackendBuildContext;
use crate::shape::validate::ensure_backend_shape;

/// Return whether one backend may be wrapped by EncryptionWrappedFS.
///
/// Keep the check local to the wrapper creation entrypoint for this minimal
/// fix: encrypted publish requires overwrite-on-publish semantics
/// (`replace(temp, final)`), so unsupported backends must be rejected before
/// the encrypted wrapper is built.
fn supports_encrypted_publish(backend_name: &str) -> bool {
    matches!(backend_name, "localfs" | "s3fs" | "memfs")
}

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

/// Build the multi-backend wrapper without starting background work.
pub(crate) async fn build_inactive_multi_write_fs(
    registry: &Arc<RwLock<HashMap<String, Arc<dyn ServicePlugin>>>>,
    config: &PluginConfig,
    bc: &BackendsConfig,
    build_ctx: MultiBackendBuildContext,
) -> Result<MultiWriteWrappedFS> {
    let global_encryption_enabled = build_ctx.global_encryption_enabled();
    validate_backups_config(bc)?;
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
        if !supports_encrypted_publish(&config.name) {
            return Err(Error::config(format!(
                "encrypted backend '{}' must support replace() semantics",
                config.name
            )));
        }
        Arc::new(EncryptionWrappedFS::new(
            primary_raw.clone(),
            build_ctx
                .enc_root_key
                .expect("global encryption validated before building primary backend"),
            build_ctx
                .enc_provider_type
                .expect("global encryption validated before building primary backend"),
            build_ctx.pathlock_manager.clone(),
            build_ctx.backend_prefix.clone(),
        ))
    } else {
        primary_raw.clone()
    };

    let metadata_store = Arc::new(MetadataStore::new(
        primary_backend.clone(),
        build_ctx.pathlock_manager.clone(),
        &build_ctx.backend_prefix,
    )?);
    let metadata_provider: Arc<dyn MultiWriteProvider> = match bc.provider.as_str() {
        "filesystem" => Arc::new(FilesystemProvider::new(metadata_store.clone())),
        "cache" => {
            #[cfg(feature = "cache")]
            {
                let runtime = build_ctx.cache_runtime.clone().ok_or_else(|| {
                    Error::config("backups.provider = 'cache' requires a top-level CacheRuntime")
                })?;
                Arc::new(CacheProvider::new(
                    metadata_store.clone(),
                    runtime,
                    bc.namespace.clone(),
                )?)
            }
            #[cfg(not(feature = "cache"))]
            {
                return Err(Error::config(
                    "backups.provider = 'cache' requires the ragfs cache feature",
                ));
            }
        }
        _ => unreachable!("backup provider validated before construction"),
    };

    let mut backup_entries: Vec<BackendEntry> = Vec::new();
    for item in &bc.items {
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
            if !supports_encrypted_publish(&item.backend) {
                return Err(Error::config(format!(
                    "encrypted backend '{}' must support replace() semantics",
                    item.backend
                )));
            }
            Arc::new(EncryptionWrappedFS::new(
                backup_raw,
                build_ctx
                    .enc_root_key
                    .expect("global encryption validated before building backup backend"),
                build_ctx
                    .enc_provider_type
                    .expect("global encryption validated before building backup backend"),
                build_ctx.pathlock_manager.clone(),
                build_ctx.backend_prefix.clone(),
            ))
        } else {
            backup_raw
        };

        backup_entries.push(BackendEntry {
            name: item.name.clone(),
            role: BackendRole::Backup,
            backend: backup_backend,
            raw_backend: None,
        });
    }

    MultiWriteWrappedFS::builder(primary_backend)
        .with_primary_raw_backend(primary_raw)
        .with_metadata_store(metadata_store)
        .with_metadata_provider(metadata_provider)
        .with_backups(backup_entries)
        .initial_partitions(bc.initial_partitions)
        .checkpoint_interval(Duration::from_secs(bc.checkpoint_interval_secs))
        .ctx_resolver(Arc::new(RelativePathFsContextResolver))
        .build_inactive()
        .await
}
