use crate::{
    MooncakeConfig, MooncakeObjectStore, MooncakeProvider, MooncakeReplicateConfig,
    MooncakeStoreError,
};
use mooncake_store::{MooncakeStore, ReplicateConfig as NativeReplicateConfig, StoreError};
use ragfs::cache::{CacheError, CacheResult};
use std::sync::Arc;
use std::time::Duration;

struct NativeMooncakeStore {
    store: MooncakeStore,
}

impl NativeMooncakeStore {
    fn connect(config: &MooncakeConfig) -> Result<Self, MooncakeStoreError> {
        let store = MooncakeStore::new().map_err(map_initialization_error)?;
        store
            .setup(
                &config.local_hostname,
                &config.metadata_server,
                config.global_segment_size,
                config.local_buffer_size,
                &config.protocol,
                &config.device_name,
                &config.master_server_addr,
            )
            .map_err(map_initialization_error)?;
        Ok(Self { store })
    }
}

impl MooncakeObjectStore for NativeMooncakeStore {
    fn health_check(&self) -> Result<(), MooncakeStoreError> {
        self.store.health_check().map_err(map_initialization_error)
    }

    fn is_exist(&self, key: &str) -> Result<bool, MooncakeStoreError> {
        self.store.is_exist(key).map_err(map_operation_error)
    }

    fn get(&self, key: &str) -> Result<Vec<u8>, MooncakeStoreError> {
        self.store.get(key).map_err(map_operation_error)
    }

    fn put(
        &self,
        key: &str,
        value: &[u8],
        replicate: &MooncakeReplicateConfig,
    ) -> Result<(), MooncakeStoreError> {
        let native = NativeReplicateConfig {
            replica_num: replicate.replica_num,
            with_soft_pin: replicate.with_soft_pin,
            with_hard_pin: replicate.with_hard_pin,
            preferred_segments: replicate.preferred_segments.clone(),
        };
        self.store
            .put(key, value, Some(&native))
            .map_err(map_operation_error)
    }

    fn remove(&self, key: &str, force: bool) -> Result<(), MooncakeStoreError> {
        self.store.remove(key, force).map_err(map_operation_error)
    }
}

impl MooncakeProvider {
    /// Connect to Mooncake through the pinned official Rust binding.
    pub async fn connect(config: MooncakeConfig) -> CacheResult<Self> {
        config.validate()?;
        let setup_config = config.clone();
        let setup_timeout = Duration::from_millis(config.operation_timeout_ms);
        let store = tokio::time::timeout(
            setup_timeout,
            tokio::task::spawn_blocking(move || NativeMooncakeStore::connect(&setup_config)),
        )
        .await
        .map_err(|_| {
            CacheError::Timeout(format!(
                "Mooncake setup exceeded {} ms",
                setup_timeout.as_millis()
            ))
        })?
        .map_err(|error| CacheError::Internal(format!("Mooncake setup task failed: {error}")))?
        .map_err(map_cache_error)?;

        Self::from_store(config, Arc::new(store)).await
    }
}

fn map_initialization_error(error: StoreError) -> MooncakeStoreError {
    match error {
        StoreError::InvalidString(error) => MooncakeStoreError::InvalidArgument(error.to_string()),
        StoreError::InvalidArgument(message) => MooncakeStoreError::InvalidArgument(message),
        StoreError::NullHandle | StoreError::OperationFailed(_) | StoreError::NotFound => {
            MooncakeStoreError::Unavailable(error.to_string())
        }
    }
}

fn map_operation_error(error: StoreError) -> MooncakeStoreError {
    match error {
        StoreError::NotFound => MooncakeStoreError::NotFound,
        StoreError::InvalidString(error) => MooncakeStoreError::InvalidArgument(error.to_string()),
        StoreError::InvalidArgument(message) => MooncakeStoreError::InvalidArgument(message),
        StoreError::NullHandle | StoreError::OperationFailed(_) => {
            MooncakeStoreError::Internal(error.to_string())
        }
    }
}

fn map_cache_error(error: MooncakeStoreError) -> CacheError {
    match error {
        MooncakeStoreError::NotFound => {
            CacheError::Unavailable("Mooncake setup object not found".into())
        }
        MooncakeStoreError::Unavailable(message) => CacheError::Unavailable(message),
        MooncakeStoreError::InvalidArgument(message) => CacheError::InvalidArgument(message),
        MooncakeStoreError::Internal(message) => CacheError::Internal(message),
    }
}
