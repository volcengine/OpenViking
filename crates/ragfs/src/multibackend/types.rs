//! Shared build-time types for multi-backend assembly.

use std::sync::Arc;

use crate::lock::PathLockManager;

/// Encryption context used while assembling one multi-backend mount.
#[derive(Clone)]
pub struct MultiBackendBuildContext {
    /// Global root key, if server-side encryption is enabled.
    pub enc_root_key: Option<[u8; 32]>,
    /// Global provider type, if server-side encryption is enabled.
    pub enc_provider_type: Option<u8>,
    /// Shared manager used by encrypted backends for dual-path locking.
    pub pathlock_manager: Arc<PathLockManager>,
    /// Mount prefix used to restore manager-visible backend paths.
    pub backend_prefix: String,
}

impl MultiBackendBuildContext {
    /// Return whether global encryption is enabled for this mount.
    pub fn global_encryption_enabled(&self) -> bool {
        self.enc_root_key.is_some() && self.enc_provider_type.is_some()
    }
}
