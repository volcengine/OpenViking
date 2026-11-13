//! ObjectStore trait - content-addressable storage for Git objects

use async_trait::async_trait;
use bytes::Bytes;
use gix_hash::ObjectId;

use crate::git::error::ObjectStoreError;

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
    /// Optional rebuildable history accelerator. Custom stores keep exact traversal.
    fn supports_history_segments(&self) -> bool {
        false
    }

    /// Load a bounded, versioned segment keyed by its first commit OID.
    async fn get_history_segment(
        &self,
        _account: &str,
        _tip: &ObjectId,
    ) -> Result<Option<Bytes>, ObjectStoreError> {
        Ok(None)
    }

    /// Replace derived data for the same immutable first-parent chain.
    async fn put_history_segment(
        &self,
        _account: &str,
        _tip: &ObjectId,
        _bytes: Bytes,
    ) -> Result<(), ObjectStoreError> {
        Ok(())
    }

    /// Best-effort branch hint to find persisted segments past an unindexed tip.
    async fn get_history_anchor(
        &self,
        _account: &str,
        _branch: &str,
    ) -> Result<Option<ObjectId>, ObjectStoreError> {
        Ok(None)
    }

    /// Publish a hint only after the referenced segment was persisted.
    async fn put_history_anchor(
        &self,
        _account: &str,
        _branch: &str,
        _tip: ObjectId,
    ) -> Result<(), ObjectStoreError> {
        Ok(())
    }

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
}
