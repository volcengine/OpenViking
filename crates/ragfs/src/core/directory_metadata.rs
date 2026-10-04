//! Persistent directory attributes. The business metadata and deadline share
//! one .meta.json file on every backend; no second expiration index is stored here.

use serde_json::{Map, Value};

use super::{Error, FileSystem, Result, WriteFlag};

pub fn metadata_path(path: &str) -> String {
    format!("{}/.meta.json", path.trim_end_matches('/'))
}

pub async fn read_directory_metadata(
    fs: &dyn FileSystem,
    path: &str,
) -> Result<Map<String, Value>> {
    let raw = match fs.read(&metadata_path(path), 0, 0).await {
        Ok(raw) => raw,
        Err(Error::NotFound(_)) => return Ok(Map::new()),
        Err(err) => return Err(err),
    };
    let value: Value = serde_json::from_slice(&raw)?;
    value.as_object().cloned().ok_or_else(|| {
        Error::Serialization(format!("directory metadata must be an object: {path}"))
    })
}

/// The caller holds the exact .meta.json lock across this read-modify-write.
pub async fn update_directory_metadata(
    fs: &dyn FileSystem,
    path: &str,
    patch: Map<String, Value>,
) -> Result<Map<String, Value>> {
    if !fs.stat(path).await?.is_dir {
        return Err(Error::NotADirectory(path.to_owned()));
    }
    if let Some(expiry) = patch.get("expires_at") {
        if !expiry.is_null() {
            let text = expiry.as_str().ok_or_else(|| {
                Error::Serialization("expires_at must be an RFC3339 string or null".to_owned())
            })?;
            chrono::DateTime::parse_from_rfc3339(text)
                .map_err(|err| Error::Serialization(format!("invalid expires_at: {err}")))?;
        }
    }
    let mut fields = read_directory_metadata(fs, path).await?;
    fields.extend(patch);
    fs.write(
        &metadata_path(path),
        &serde_json::to_vec(&fields)?,
        0,
        WriteFlag::Create,
    )
    .await?;
    Ok(fields)
}
