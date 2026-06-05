use std::sync::Arc;

use crate::core::errors::{Error, Result};
use crate::core::filesystem::FileSystem;
use crate::core::WriteFlag;
use crate::crypto;

use super::manifest::{
    BackendShapeManifest, StorageShape, SHAPE_MANIFEST_PATH, SHAPE_MANIFEST_VERSION,
};

fn normalize_shape_path(path: &str) -> String {
    let mut normalized = path.trim().to_string();
    if !normalized.starts_with('/') {
        normalized.insert(0, '/');
    }
    if normalized.len() > 1 && normalized.ends_with('/') {
        normalized.pop();
    }
    normalized
}

/// Read the persisted backend-shape manifest.
pub async fn read_shape_manifest(
    raw_fs: &Arc<dyn FileSystem>,
) -> Result<Option<BackendShapeManifest>> {
    match raw_fs.read(SHAPE_MANIFEST_PATH, 0, 0).await {
        Ok(bytes) => {
            let manifest: BackendShapeManifest = serde_json::from_slice(&bytes)?;
            Ok(Some(manifest))
        }
        Err(Error::NotFound(_)) => Ok(None),
        Err(err) => Err(err),
    }
}

/// Persist the current backend-shape manifest.
pub async fn write_shape_manifest(
    raw_fs: &Arc<dyn FileSystem>,
    backend_type: &str,
    shape: &StorageShape,
) -> Result<()> {
    let manifest = BackendShapeManifest {
        version: SHAPE_MANIFEST_VERSION,
        backend_type: backend_type.to_string(),
        shape: shape.clone(),
    };
    let bytes = serde_json::to_vec_pretty(&manifest)?;
    raw_fs
        .write(SHAPE_MANIFEST_PATH, &bytes, 0, WriteFlag::Create)
        .await?;
    Ok(())
}

/// Probe existing backend files to infer the legacy storage shape.
pub async fn detect_legacy_shape(raw_fs: &Arc<dyn FileSystem>) -> Result<Option<StorageShape>> {
    let entries = raw_fs.tree_directory("/", true, None, None).await?;
    let mut detected: Option<StorageShape> = None;

    for entry in entries {
        if entry.info.is_dir {
            continue;
        }

        let normalized = normalize_shape_path(&entry.path);
        if normalized == SHAPE_MANIFEST_PATH {
            continue;
        }

        let header = raw_fs.read(&normalized, 0, 6).await?;
        let current = if crypto::is_encrypted(&header) {
            if header.len() < 6 {
                return Err(Error::config(format!(
                    "encrypted file '{}' is too short to inspect shape",
                    normalized
                )));
            }
            StorageShape::Encrypted {
                envelope_version: header[4],
                provider_type: header[5],
            }
        } else {
            StorageShape::Plaintext
        };

        match &detected {
            None => detected = Some(current),
            Some(existing) if existing == &current => {}
            Some(existing) => {
                return Err(Error::config(format!(
                    "backend contains mixed storage shapes: previously {:?}, found {:?} at '{}'",
                    existing, current, normalized
                )));
            }
        }
    }

    Ok(detected)
}
