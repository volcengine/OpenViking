use std::sync::Arc;

use crate::core::errors::{Error, Result};
use crate::core::FileSystem;
use crate::crypto;

use super::manifest::StorageShape;
use super::probe::{detect_legacy_shape, probe_shape_guard_storage, write_shape_guard};

/// Compute the expected backend shape from encryption settings.
pub fn expected_shape(encrypted: bool, provider_type: Option<u8>) -> Result<StorageShape> {
    if encrypted {
        let provider_type = provider_type
            .ok_or_else(|| Error::config("encrypted backend shape requires provider_type"))?;
        Ok(StorageShape::Encrypted {
            provider_type,
            envelope_version: crypto::VERSION,
        })
    } else {
        Ok(StorageShape::Plaintext)
    }
}

/// Validate that one backend matches the expected storage shape.
pub async fn ensure_backend_shape(
    raw_fs: &Arc<dyn FileSystem>,
    backend_type: &str,
    encrypted: bool,
    provider_type: Option<u8>,
    root_key: Option<[u8; 32]>,
) -> Result<()> {
    let expected = expected_shape(encrypted, provider_type)?;
    if let Some(observed) = probe_shape_guard_storage(raw_fs).await? {
        if observed != expected {
            return Err(Error::config(format!(
                "backend storage shape mismatch for '{}': expected {:?}, found {:?}",
                backend_type, expected, observed
            )));
        }
        return Ok(());
    }

    let detected = detect_legacy_shape(raw_fs).await?;
    match detected {
        None => write_shape_guard(raw_fs, &expected, root_key.as_ref()).await,
        Some(found) if found == expected => {
            write_shape_guard(raw_fs, &found, root_key.as_ref()).await
        }
        Some(found) => Err(Error::config(format!(
            "backend storage shape mismatch for '{}': expected {:?}, found {:?}",
            backend_type, expected, found
        ))),
    }
}
