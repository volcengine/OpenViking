//! Writer view of the object store. Deduplicated objects are pinned for the GC grace period.
use super::{ObjectMeta, ObjectStore, ObjectStoreError};
use async_trait::async_trait;
use bytes::Bytes;
use gix_hash::ObjectId;
use std::{
    sync::Arc,
    time::{Duration, SystemTime},
};

/// Object-store decorator for writes that may race with garbage collection.
pub struct FreshenObjectStore {
    pub(crate) inner: Arc<dyn ObjectStore>,
    pub(crate) max_age: Duration,
}

impl FreshenObjectStore {
    fn cutoff(&self) -> SystemTime {
        SystemTime::now()
            .checked_sub(self.max_age)
            .unwrap_or(SystemTime::UNIX_EPOCH)
    }
}

#[async_trait]
impl ObjectStore for FreshenObjectStore {
    async fn put(
        &self,
        account: &str,
        oid: &ObjectId,
        data: Bytes,
    ) -> Result<(), ObjectStoreError> {
        self.inner
            .put_or_touch(account, oid, data, self.cutoff())
            .await
    }
    async fn get(&self, account: &str, oid: &ObjectId) -> Result<Bytes, ObjectStoreError> {
        self.inner.get(account, oid).await
    }
    async fn get_limited(
        &self,
        account: &str,
        oid: &ObjectId,
        limit: u64,
    ) -> Result<Bytes, ObjectStoreError> {
        self.inner.get_limited(account, oid, limit).await
    }
    async fn exists(&self, account: &str, oid: &ObjectId) -> Result<bool, ObjectStoreError> {
        if !self.inner.exists(account, oid).await? {
            return Ok(false);
        }
        match self
            .inner
            .touch_if_older_than(account, oid, self.cutoff())
            .await
        {
            Ok(_) => Ok(true),
            Err(ObjectStoreError::NotFound(_)) => Ok(false),
            Err(err) => Err(err),
        }
    }
    async fn list_objects(&self, account: &str) -> Result<Vec<ObjectMeta>, ObjectStoreError> {
        self.inner.list_objects(account).await
    }
    async fn sweep_objects(
        &self,
        account: &str,
        objects: &[ObjectMeta],
        cutoff: SystemTime,
    ) -> Result<Vec<ObjectId>, ObjectStoreError> {
        self.inner.sweep_objects(account, objects, cutoff).await
    }
    async fn touch_if_older_than(
        &self,
        account: &str,
        oid: &ObjectId,
        threshold: SystemTime,
    ) -> Result<bool, ObjectStoreError> {
        self.inner
            .touch_if_older_than(account, oid, threshold)
            .await
    }
    async fn put_or_touch(
        &self,
        account: &str,
        oid: &ObjectId,
        body: Bytes,
        threshold: SystemTime,
    ) -> Result<(), ObjectStoreError> {
        self.inner.put_or_touch(account, oid, body, threshold).await
    }
    async fn cleanup_temp_files(
        &self,
        account: &str,
        cutoff: SystemTime,
    ) -> Result<usize, ObjectStoreError> {
        self.inner.cleanup_temp_files(account, cutoff).await
    }
}
