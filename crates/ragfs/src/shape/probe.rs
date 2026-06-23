use std::sync::Arc;

use crate::core::errors::{Error, Result};
use crate::core::filesystem::FileSystem;
use crate::core::WriteFlag;
use crate::crypto;
use futures::stream::{self, StreamExt};

use super::manifest::{StorageShape, SHAPE_MANIFEST_PATH};

const SYSTEM_ACCOUNT_ID: &[u8] = b"_system";
const PATH_LOCK_FILE: &str = ".path.ovlock";
const EXACT_LOCK_FILE_PREFIX: &str = ".exact.ovlock.";

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

fn is_persistent_task_record_path(path: &str) -> bool {
    let parts: Vec<&str> = path.trim_matches('/').split('/').collect();
    if let ["_system", "tasks", user_id, _record, ..] = parts.as_slice() {
        return !user_id.is_empty();
    }
    if let [account_id, "_system", "tasks", user_id, _record, ..] = parts.as_slice() {
        return !account_id.is_empty() && !user_id.is_empty();
    }
    false
}

fn is_path_lock_record_path(path: &str) -> bool {
    let name = path.rsplit('/').next().unwrap_or("");
    name == PATH_LOCK_FILE || name.starts_with(EXACT_LOCK_FILE_PREFIX)
}

/// Read the raw guard-file bytes from the backend root.
async fn read_shape_guard_raw(raw_fs: &Arc<dyn FileSystem>) -> Result<Option<Vec<u8>>> {
    match raw_fs.read(SHAPE_MANIFEST_PATH, 0, 0).await {
        Ok(bytes) => Ok(Some(bytes)),
        Err(Error::NotFound(_)) => Ok(None),
        Err(err) => Err(err),
    }
}

/// Infer one file's physical storage shape from its raw bytes.
fn shape_from_raw_bytes(path: &str, bytes: &[u8]) -> Result<StorageShape> {
    if crypto::is_encrypted(bytes) {
        if bytes.len() < 6 {
            return Err(Error::config(format!(
                "encrypted file '{}' is too short to inspect shape",
                path
            )));
        }
        Ok(StorageShape::Encrypted {
            envelope_version: bytes[4],
            provider_type: bytes[5],
        })
    } else {
        Ok(StorageShape::Plaintext)
    }
}

/// Encrypt one empty guard payload so its physical shape matches the backend.
fn encrypt_shape_guard_bytes(root_key: &[u8; 32], provider_type: u8) -> Result<Vec<u8>> {
    let account_key = crypto::hkdf_sha256(root_key, SYSTEM_ACCOUNT_ID);
    let file_key: [u8; 32] = rand::random();
    let key_iv: [u8; 12] = rand::random();
    let data_iv: [u8; 12] = rand::random();
    let ciphertext = crypto::aes_gcm_encrypt(&file_key, &data_iv, &[])?;
    let enc_key = crypto::aes_gcm_encrypt(&account_key, &key_iv, &file_key)?;
    Ok(crypto::build_envelope(
        provider_type,
        &enc_key,
        &key_iv,
        &data_iv,
        &ciphertext,
    ))
}

/// Probe the physical shape of the persisted backend-shape guard file.
pub async fn probe_shape_guard_storage(
    raw_fs: &Arc<dyn FileSystem>,
) -> Result<Option<StorageShape>> {
    let Some(bytes) = read_shape_guard_raw(raw_fs).await? else {
        return Ok(None);
    };
    Ok(Some(shape_from_raw_bytes(SHAPE_MANIFEST_PATH, &bytes)?))
}

/// Persist the backend-shape guard file in the same physical shape as the backend.
pub async fn write_shape_guard(
    raw_fs: &Arc<dyn FileSystem>,
    shape: &StorageShape,
    root_key: Option<&[u8; 32]>,
) -> Result<()> {
    let payload = match shape {
        StorageShape::Plaintext => Vec::new(),
        StorageShape::Encrypted { provider_type, .. } => {
            let root_key = root_key.ok_or_else(|| {
                Error::config("encrypted backend_meta.json requires a root key to be written")
            })?;
            encrypt_shape_guard_bytes(root_key, *provider_type)?
        }
    };
    raw_fs
        .write(SHAPE_MANIFEST_PATH, &payload, 0, WriteFlag::Create)
        .await?;
    Ok(())
}

/// Probe existing backend files to infer the legacy storage shape.
pub async fn detect_legacy_shape(raw_fs: &Arc<dyn FileSystem>) -> Result<Option<StorageShape>> {
    let entries = raw_fs.tree_directory("/", true, None, None).await?;
    let mut candidates = Vec::new();

    for entry in entries {
        // Zero-byte files are a legacy plaintext artifact in the current implementation, but they
        // do not carry enough signal to classify backend shape reliably. Skip them here instead of
        // forcing a plaintext verdict during legacy shape inference.
        if entry.info.is_dir || entry.info.size == 0 {
            continue;
        }

        let normalized = normalize_shape_path(&entry.path);
        if normalized == SHAPE_MANIFEST_PATH
            || is_persistent_task_record_path(&normalized)
            || is_path_lock_record_path(&normalized)
        {
            continue;
        }
        candidates.push(normalized);
    }

    let mut detected: Option<StorageShape> = None;
    let mut probes = stream::iter(candidates.into_iter().map(|normalized| {
        let raw_fs = Arc::clone(raw_fs);
        async move {
            let header = raw_fs.read(&normalized, 0, 6).await?;
            let current = shape_from_raw_bytes(&normalized, &header)?;
            Ok::<(String, StorageShape), Error>((normalized, current))
        }
    }))
    .buffer_unordered(16);

    while let Some(item) = probes.next().await {
        let (normalized, current) = item?;

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
