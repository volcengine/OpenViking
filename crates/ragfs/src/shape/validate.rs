use std::sync::Arc;

use crate::core::FileSystem;
use crate::core::errors::{Error, Result};
use crate::crypto;

use super::manifest::StorageShape;
use super::probe::{probe_shape_guard_storage, write_shape_guard};

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

#[cfg(test)]
mod tests {
    use std::sync::atomic::{AtomicBool, Ordering};

    use async_trait::async_trait;

    use super::*;
    use crate::core::{FileInfo, TreeEntry, WriteFlag};
    use crate::shape::SHAPE_MANIFEST_PATH;

    #[derive(Default)]
    struct MissingGuardNoScanFs {
        wrote_guard: AtomicBool,
    }

    #[async_trait]
    impl FileSystem for MissingGuardNoScanFs {
        async fn create(&self, _path: &str) -> Result<()> {
            Ok(())
        }

        async fn mkdir(&self, _path: &str, _mode: u32) -> Result<()> {
            Ok(())
        }

        async fn remove(&self, _path: &str) -> Result<()> {
            Ok(())
        }

        async fn remove_all(&self, _path: &str) -> Result<()> {
            Ok(())
        }

        async fn read(&self, path: &str, _offset: u64, _size: u64) -> Result<Vec<u8>> {
            if path == SHAPE_MANIFEST_PATH {
                return Err(Error::not_found(path));
            }
            Ok(Vec::new())
        }

        async fn write(
            &self,
            path: &str,
            data: &[u8],
            offset: u64,
            _flags: WriteFlag,
        ) -> Result<u64> {
            assert_eq!(path, SHAPE_MANIFEST_PATH);
            assert_eq!(data, b"");
            assert_eq!(offset, 0);
            self.wrote_guard.store(true, Ordering::SeqCst);
            Ok(data.len() as u64)
        }

        async fn read_dir(&self, _path: &str) -> Result<Vec<FileInfo>> {
            Ok(Vec::new())
        }

        async fn stat(&self, path: &str) -> Result<FileInfo> {
            Ok(FileInfo::new_file(path.to_string(), 0, 0o644))
        }

        async fn rename(&self, _old_path: &str, _new_path: &str) -> Result<()> {
            Ok(())
        }

        async fn chmod(&self, _path: &str, _mode: u32) -> Result<()> {
            Ok(())
        }

        async fn tree_directory(
            &self,
            _path: &str,
            _show_hidden: bool,
            _node_limit: Option<usize>,
            _level_limit: Option<usize>,
        ) -> Result<Vec<TreeEntry>> {
            Err(Error::config("legacy full-backend scan should not run"))
        }
    }

    #[tokio::test]
    async fn missing_shape_guard_writes_guard_without_scanning_backend() {
        let fs = Arc::new(MissingGuardNoScanFs::default());
        let raw_fs: Arc<dyn FileSystem> = fs.clone();

        ensure_backend_shape(&raw_fs, "s3", false, None, None)
            .await
            .unwrap();

        assert!(fs.wrote_guard.load(Ordering::SeqCst));
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

    write_shape_guard(raw_fs, &expected, root_key.as_ref()).await
}
