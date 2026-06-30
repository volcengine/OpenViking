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

    /// Read and decompress an object.
    ///
    /// Returns the full uncompressed bytes (including Git header: "type size\0content").
    async fn get(&self, account: &str, oid: &ObjectId) -> Result<Bytes, ObjectStoreError>;

    /// Check if an object exists without reading its content.
    ///
    /// This is an optimization path - implementations should use the cheapest
    /// available method (e.g., `stat` for local, `HEAD` for S3).
    async fn exists(&self, account: &str, oid: &ObjectId) -> Result<bool, ObjectStoreError>;
}
