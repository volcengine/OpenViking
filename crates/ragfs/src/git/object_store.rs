//! ObjectStore trait - content-addressable storage for Git objects

use std::time::SystemTime;

use async_trait::async_trait;
use bytes::Bytes;
use gix_hash::ObjectId;

use crate::git::error::ObjectStoreError;

/// Metadata for one loose Git object observed by a GC scan.
#[derive(Debug, Clone)]
pub struct ObjectMeta {
    /// Content hash of the object.
    pub oid: ObjectId,
    /// Time the data was written or last freshened.
    pub modified: SystemTime,
    /// Stored (compressed) byte count.
    pub size: u64,
}

/// Content-addressable storage for Git objects.
///
/// This trait abstracts over storage backends (local filesystem, S3, etc.)
/// for storing and retrieving Git objects (blobs, trees, commits).
///
/// All operations are content-addressable by ObjectId (SHA-1).
/// `put` operations are idempotent - writing the same object multiple times
/// has the same effect as writing it once.
#[async_trait]
pub trait ObjectStore: Send + Sync + 'static {
    /// Write a zlib-compressed loose object.
    ///
    /// The `zlib_body` must be a valid zlib-compressed Git loose object,
    /// and `oid` must be the SHA-1 hash of the uncompressed object
    /// (including the Git header: "type size\0content").
    ///
    /// Implementations should ensure this is idempotent: calling `put`
    /// multiple times with the same `oid` is safe and has no additional effect.
    async fn put(
        &self,
        account: &str,
        oid: &ObjectId,
        zlib_body: Bytes,
    ) -> Result<(), ObjectStoreError>;

    /// Read a compressed loose object.
    ///
    /// Returns the zlib-compressed bytes written by [`ObjectStore::put`].
    async fn get(&self, account: &str, oid: &ObjectId) -> Result<Bytes, ObjectStoreError>;

    /// Read a compressed loose object without exceeding `max_bytes`.
    ///
    /// Production backends should override this method so the limit is checked
    /// before the complete object is materialized. The default preserves
    /// compatibility for custom stores while still enforcing the contract
    /// before returning to the caller.
    async fn get_limited(
        &self,
        account: &str,
        oid: &ObjectId,
        max_bytes: u64,
    ) -> Result<Bytes, ObjectStoreError> {
        let bytes = self.get(account, oid).await?;
        let size = bytes.len() as u64;
        if size > max_bytes {
            return Err(ObjectStoreError::ReadLimitExceeded {
                size,
                limit: max_bytes,
            });
        }
        Ok(bytes)
    }

    /// Check if an object exists without reading its content.
    ///
    /// This is an optimization path - implementations should use the cheapest
    /// available method (e.g., `stat` for local, `HEAD` for S3).
    async fn exists(&self, account: &str, oid: &ObjectId) -> Result<bool, ObjectStoreError>;

    /// List loose objects for this account, excluding refs and indexes.
    async fn list_objects(&self, _account: &str) -> Result<Vec<ObjectMeta>, ObjectStoreError> {
        Err(ObjectStoreError::Backend("list_objects unsupported".into()))
    }

    /// Remove the supplied objects idempotently.
    async fn delete_objects(
        &self,
        _account: &str,
        _oids: &[ObjectId],
    ) -> Result<(), ObjectStoreError> {
        Err(ObjectStoreError::Backend(
            "delete_objects unsupported".into(),
        ))
    }

    /// Delete only the generations observed during listing. Production GC never
    /// issues an unconditional delete based on a possibly stale observation.
    async fn sweep_objects(
        &self,
        _account: &str,
        _objects: &[ObjectMeta],
        _cutoff: SystemTime,
    ) -> Result<Vec<ObjectId>, ObjectStoreError> {
        Err(ObjectStoreError::Backend(
            "conditional sweep unsupported".into(),
        ))
    }

    /// Freshen a stale object before reusing it in an unpublished commit.
    async fn touch_if_older_than(
        &self,
        _account: &str,
        _oid: &ObjectId,
        _threshold: SystemTime,
    ) -> Result<bool, ObjectStoreError> {
        Ok(false)
    }

    /// Write a new object or freshen the existing copy.
    async fn put_or_touch(
        &self,
        account: &str,
        oid: &ObjectId,
        body: Bytes,
        threshold: SystemTime,
    ) -> Result<(), ObjectStoreError> {
        // put is idempotent. Preserve the caller's disabled-exists-precheck
        // path for older stores, whose touch implementation is a no-op.
        self.put(account, oid, body).await?;
        self.touch_if_older_than(account, oid, threshold).await?;
        Ok(())
    }

    /// Remove temporary object files left behind by interrupted writes.
    async fn cleanup_temp_files(
        &self,
        _account: &str,
        _older_than: SystemTime,
    ) -> Result<usize, ObjectStoreError> {
        Ok(0)
    }
}
