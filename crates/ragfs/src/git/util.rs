//! Utility functions for Git module

use bytes::Bytes;
use std::io::{Read, Write};

use crate::git::error::RefStoreError;

/// Validate a ref name follows Git naming conventions.
///
/// Checks for:
/// - Empty name
/// - Contains ".."
/// - Starts or ends with "/"
/// - Contains invalid characters
pub fn validate_ref_name(ref_name: &str) -> Result<(), RefStoreError> {
    if ref_name.is_empty() {
        return Err(RefStoreError::InvalidName(
            "ref name cannot be empty".to_string(),
        ));
    }
    if ref_name.contains("..") {
        return Err(RefStoreError::InvalidName(
            "ref name cannot contain '..'".to_string(),
        ));
    }
    if ref_name.starts_with('/') || ref_name.ends_with('/') {
        return Err(RefStoreError::InvalidName(
            "ref name cannot start or end with '/'".to_string(),
        ));
    }
    if ref_name.contains(' ')
        || ref_name.contains('\x00')
        || ref_name.contains('~')
        || ref_name.contains('^')
        || ref_name.contains(':')
        || ref_name.contains('?')
        || ref_name.contains('[')
        || ref_name.contains('*')
    {
        return Err(RefStoreError::InvalidName(
            "ref name contains invalid characters".to_string(),
        ));
    }
    Ok(())
}

/// Compress data using zlib (for Git loose object storage).
pub fn zlib_compress(data: &[u8]) -> Result<Vec<u8>, std::io::Error> {
    let mut encoder = flate2::write::ZlibEncoder::new(Vec::new(), flate2::Compression::default());
    encoder.write_all(data)?;
    encoder.finish()
}

/// Decompress zlib-compressed data (for reading Git loose objects).
///
/// Pre-allocates a generous output buffer to amortize the `Vec` doubling cost
/// that hurts large blobs (a 100 MiB payload otherwise triggers ~27 reallocs).
/// The hint assumes a worst-case compression ratio of ~4×; capped at 64 MiB
/// so a pathologically small-but-compressible header doesn't reserve absurd
/// amounts of memory. Exceeding the hint still works — `Vec` will grow.
pub fn zlib_decompress(data: &[u8]) -> Result<Vec<u8>, std::io::Error> {
    const HINT_CAP: usize = 64 * 1024 * 1024;
    let hint = data.len().saturating_mul(4).min(HINT_CAP);
    let mut decoded = Vec::with_capacity(hint);
    let mut decoder = flate2::read::ZlibDecoder::new(data);
    decoder.read_to_end(&mut decoded)?;
    Ok(decoded)
}

/// Parse a Git loose object header, returning (kind, size, header_end_offset).
pub fn parse_object_header(data: &[u8]) -> Result<(gix_object::Kind, u64, usize), crate::git::error::ObjectStoreError> {
    gix_object::decode::loose_header(data).map_err(|e| {
        crate::git::error::ObjectStoreError::Backend(format!("invalid object header: {e}"))
    })
}

/// Read and decompress a Git object from ObjectStore, returning the full
/// uncompressed bytes (including header).
pub async fn read_object(
    store: &dyn crate::git::object_store::ObjectStore,
    account: &str,
    oid: &gix_hash::ObjectId,
) -> Result<Bytes, crate::git::error::ObjectStoreError> {
    let compressed = store.get(account, oid).await?;
    let decompressed = zlib_decompress(&compressed)
        .map_err(|e| crate::git::error::ObjectStoreError::Zlib(e.to_string()))?;
    Ok(Bytes::from(decompressed))
}

/// Read and decompress a loose object while bounding both compressed and
/// decompressed materialization. `max_payload_bytes` excludes the Git header.
pub async fn read_object_limited(
    store: &dyn crate::git::object_store::ObjectStore,
    account: &str,
    oid: &gix_hash::ObjectId,
    max_payload_bytes: u64,
) -> Result<Bytes, crate::git::error::ObjectStoreError> {
    const MAX_LOOSE_HEADER_BYTES: u64 = 64;
    let max_loose_bytes = max_payload_bytes.saturating_add(MAX_LOOSE_HEADER_BYTES);
    let compressed_limit = zlib_compress_bound(max_loose_bytes);
    let compressed = store.get_limited(account, oid, compressed_limit).await?;

    let mut decoder = flate2::read::ZlibDecoder::new(compressed.as_ref());
    let mut header = Vec::with_capacity(MAX_LOOSE_HEADER_BYTES as usize);
    loop {
        if header.len() >= MAX_LOOSE_HEADER_BYTES as usize {
            return Err(crate::git::error::ObjectStoreError::Backend(
                "invalid object header: exceeds 64 bytes".to_string(),
            ));
        }
        let mut byte = [0u8; 1];
        let read = decoder.read(&mut byte)?;
        if read == 0 {
            return Err(crate::git::error::ObjectStoreError::Backend(
                "invalid object header: missing terminator".to_string(),
            ));
        }
        header.push(byte[0]);
        if byte[0] == 0 {
            break;
        }
    }

    let (_, payload_size, _) = parse_object_header(&header)?;
    if payload_size > max_payload_bytes {
        return Err(crate::git::error::ObjectStoreError::ReadLimitExceeded {
            size: payload_size,
            limit: max_payload_bytes,
        });
    }

    let payload_limit = payload_size.saturating_add(1);
    let mut payload = Vec::with_capacity(usize::try_from(payload_size).unwrap_or(usize::MAX));
    decoder
        .take(payload_limit)
        .read_to_end(&mut payload)
        .map_err(|e| crate::git::error::ObjectStoreError::Zlib(e.to_string()))?;
    if payload.len() as u64 != payload_size {
        return Err(crate::git::error::ObjectStoreError::Backend(format!(
            "invalid object payload: header declares {payload_size} bytes, decoded {}",
            payload.len()
        )));
    }

    header.extend_from_slice(&payload);
    Ok(Bytes::from(header))
}

fn zlib_compress_bound(source_len: u64) -> u64 {
    source_len
        .saturating_add(source_len >> 12)
        .saturating_add(source_len >> 14)
        .saturating_add(source_len >> 25)
        .saturating_add(13)
}

/// Serialize, compress, and write a Git object to ObjectStore.
/// Returns the object's ObjectId.
pub async fn write_object(
    store: &dyn crate::git::object_store::ObjectStore,
    account: &str,
    kind: gix_object::Kind,
    data: &[u8],
) -> Result<gix_hash::ObjectId, crate::git::error::ObjectStoreError> {
    let header = gix_object::encode::loose_header(kind, data.len() as u64);
    let oid = gix_object::compute_hash(gix_hash::Kind::Sha1, kind, data);
    let mut full = Vec::with_capacity(header.len() + data.len());
    full.extend_from_slice(&header);
    full.extend_from_slice(data);
    let compressed = zlib_compress(&full)?;
    store.put(account, &oid, Bytes::from(compressed)).await?;
    Ok(oid)
}

/// Same as [`write_object`], but runs an `exists` precheck before compressing
/// and putting (Fast Path 3). If the object is already present, the zlib
/// compression and `put` are skipped and the oid is returned directly. `put`
/// is itself idempotent, so this precheck is purely a performance optimization
/// (saves S3 body upload / local zlib compression for duplicate blobs).
pub async fn write_object_if_absent(
    store: &dyn crate::git::object_store::ObjectStore,
    account: &str,
    kind: gix_object::Kind,
    data: &[u8],
) -> Result<gix_hash::ObjectId, crate::git::error::ObjectStoreError> {
    let oid = gix_object::compute_hash(gix_hash::Kind::Sha1, kind, data);
    if store.exists(account, &oid).await? {
        return Ok(oid);
    }
    let header = gix_object::encode::loose_header(kind, data.len() as u64);
    let mut full = Vec::with_capacity(header.len() + data.len());
    full.extend_from_slice(&header);
    full.extend_from_slice(data);
    let compressed = zlib_compress(&full)?;
    store.put(account, &oid, Bytes::from(compressed)).await?;
    Ok(oid)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_validate_ref_name() {
        assert!(validate_ref_name("refs/heads/main").is_ok());
        assert!(validate_ref_name("refs/tags/v1.0").is_ok());
        assert!(validate_ref_name("HEAD").is_ok());

        assert!(validate_ref_name("").is_err());
        assert!(validate_ref_name("..").is_err());
        assert!(validate_ref_name("refs/../heads").is_err());
        assert!(validate_ref_name("/refs/heads").is_err());
        assert!(validate_ref_name("refs/heads/ ").is_err());
        assert!(validate_ref_name("refs~head").is_err());
        assert!(validate_ref_name("refs^head").is_err());
        assert!(validate_ref_name("refs:head").is_err());
        assert!(validate_ref_name("refs?head").is_err());
        assert!(validate_ref_name("refs[head]").is_err());
        assert!(validate_ref_name("refs*head").is_err());
    }

    #[test]
    fn test_zlib_round_trip() {
        let original = b"tree 15\0hello world!!!";
        let compressed = zlib_compress(original).unwrap();
        let decompressed = zlib_decompress(&compressed).unwrap();
        assert_eq!(decompressed, original);
    }

    #[test]
    fn test_parse_object_header_tree() {
        let data = b"tree 15\0entries data";
        let (kind, size, offset) = parse_object_header(data).unwrap();
        assert_eq!(kind, gix_object::Kind::Tree);
        assert_eq!(size, 15);
        assert_eq!(offset, 8);
    }

    #[test]
    fn test_parse_object_header_blob() {
        let data = b"blob 5\0hello";
        let (kind, size, offset) = parse_object_header(data).unwrap();
        assert_eq!(kind, gix_object::Kind::Blob);
        assert_eq!(size, 5);
        assert_eq!(offset, 7);
    }

    #[tokio::test]
    async fn test_write_read_object_round_trip() {
        use tempfile::tempdir;
        use crate::git::backends::local::LocalObjectStore;

        let temp_dir = tempdir().unwrap();
        let store = LocalObjectStore::new(temp_dir.path());

        let data = b"hello tree bytes";
        let kind = gix_object::Kind::Blob;

        // Write the object
        let oid = write_object(&store, "test-account", kind, data).await.unwrap();

        // Read the object back
        let raw = read_object(&store, "test-account", &oid).await.unwrap();

        // Parse and validate header
        let (parsed_kind, size, offset) = parse_object_header(&raw).unwrap();
        assert_eq!(parsed_kind, kind);
        assert_eq!(size, data.len() as u64);

        // Validate body
        assert_eq!(&raw[offset..], data);

        // Validate OID matches expected
        let expected_oid = gix_object::compute_hash(gix_hash::Kind::Sha1, kind, data);
        assert_eq!(oid, expected_oid);
    }

    #[tokio::test]
    async fn test_write_object_if_absent_skips_put_on_second_call() {
        use std::sync::atomic::{AtomicUsize, Ordering};
        use std::sync::Arc;
        use tempfile::tempdir;
        use crate::git::backends::local::LocalObjectStore;
        use crate::git::object_store::ObjectStore;
        use crate::git::error::ObjectStoreError;
        use gix_hash::ObjectId;

        struct CountingStore {
            inner: LocalObjectStore,
            puts: AtomicUsize,
            exists_calls: AtomicUsize,
        }

        #[async_trait::async_trait]
        impl ObjectStore for CountingStore {
            async fn put(
                &self,
                account: &str,
                oid: &ObjectId,
                zlib_body: Bytes,
            ) -> Result<(), ObjectStoreError> {
                self.puts.fetch_add(1, Ordering::SeqCst);
                self.inner.put(account, oid, zlib_body).await
            }
            async fn get(&self, account: &str, oid: &ObjectId) -> Result<Bytes, ObjectStoreError> {
                self.inner.get(account, oid).await
            }
            async fn exists(&self, account: &str, oid: &ObjectId) -> Result<bool, ObjectStoreError> {
                self.exists_calls.fetch_add(1, Ordering::SeqCst);
                self.inner.exists(account, oid).await
            }
        }

        let temp_dir = tempdir().unwrap();
        let store = Arc::new(CountingStore {
            inner: LocalObjectStore::new(temp_dir.path()),
            puts: AtomicUsize::new(0),
            exists_calls: AtomicUsize::new(0),
        });

        let data = b"duplicate blob content";
        let kind = gix_object::Kind::Blob;

        let oid1 = write_object_if_absent(store.as_ref(), "acct", kind, data)
            .await
            .unwrap();
        assert_eq!(store.puts.load(Ordering::SeqCst), 1);

        // Second call with identical data: should hit exists and skip put.
        let oid2 = write_object_if_absent(store.as_ref(), "acct", kind, data)
            .await
            .unwrap();
        assert_eq!(oid1, oid2);
        assert_eq!(store.puts.load(Ordering::SeqCst), 1, "put must not be called again");
        assert_eq!(store.exists_calls.load(Ordering::SeqCst), 2);

        // Object is readable and oid matches compute_hash.
        let raw = read_object(store.as_ref(), "acct", &oid1).await.unwrap();
        let (parsed_kind, _size, offset) = parse_object_header(&raw).unwrap();
        assert_eq!(parsed_kind, kind);
        assert_eq!(&raw[offset..], data);
        let expected = gix_object::compute_hash(gix_hash::Kind::Sha1, kind, data);
        assert_eq!(oid1, expected);
    }
}
