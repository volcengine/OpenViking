//! RefStore trait - named references to Git objects

use async_trait::async_trait;
use gix_hash::ObjectId;

use crate::git::error::RefStoreError;

/// Storage for named references (branches, tags) pointing to Git objects.
///
/// The core operation is `cas_update` (Compare-And-Swap), which ensures
/// atomicity and consistency in the presence of concurrent writers.
#[async_trait]
pub trait RefStore: Send + Sync + 'static {
    /// Read the current value of a ref.
    ///
    /// Returns `NotFound` if the ref doesn't exist.
    async fn read(&self, account: &str, ref_name: &str) -> Result<ObjectId, RefStoreError>;

    /// Compare-And-Swap update: write `new` only if current value == `expected`.
    ///
    /// - `expected = None` means "write only if ref doesn't exist yet"
    /// - `expected = Some(oid)` means "write only if ref currently has this oid"
    ///
    /// Returns `Conflict` if the expectation fails.
    async fn cas_update(
        &self,
        account: &str,
        ref_name: &str,
        expected: Option<ObjectId>,
        new: ObjectId,
    ) -> Result<(), RefStoreError>;

    /// List all refs under a prefix (e.g., "refs/heads/").
    ///
    /// Returns a list of (full_ref_name, target_oid) pairs.
    async fn list(
        &self,
        account: &str,
        prefix: &str,
    ) -> Result<Vec<(String, ObjectId)>, RefStoreError>;
}
