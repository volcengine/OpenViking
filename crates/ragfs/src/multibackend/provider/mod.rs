//! Persistence providers for V2 multi-write state.

use async_trait::async_trait;

use crate::core::errors::{Error, Result};
use crate::multibackend::codec::{decode_segment, sha256_hex};
use crate::multibackend::meta::MetadataStore;
use crate::multibackend::model::{
    FlushResult, MarkerPosition, PartitionContext, PartitionState, PartitionsManifest,
    PendingEvent, ScopeKey, SegmentDescriptor, SegmentManifest, SegmentRecord,
};

mod filesystem;
#[cfg(feature = "cache")]
mod cache;

pub use filesystem::FilesystemProvider;
#[cfg(feature = "cache")]
pub use cache::CacheProvider;

/// Persists and reads partitioned multi-write segment state.
#[async_trait]
pub trait MultiWriteProvider: Send + Sync {
    /// Creates missing provider state without replacing an existing manifest.
    async fn bootstrap_manifest(
        &self,
        scope: &ScopeKey,
        manifest: SegmentManifest,
    ) -> Result<()>;

    /// Flushes normal events after validating their current partition route.
    async fn flush(
        &self,
        route_hint: Option<&PartitionContext>,
        events: Vec<PendingEvent>,
    ) -> Result<FlushResult>;

    /// Reads committed head records in the requested half-open sequence range.
    async fn read_committed_head(
        &self,
        scope: &ScopeKey,
        from_seq_inclusive: u64,
        to_seq_exclusive: u64,
    ) -> Result<Vec<SegmentRecord>>;

    /// Reads committed records across sealed segments and the current head.
    async fn read_committed_range(
        &self,
        scope: &ScopeKey,
        from_seq_inclusive: u64,
        to_seq_exclusive: u64,
    ) -> Result<Vec<SegmentRecord>>;

    /// Reads one immutable sealed segment named by a validated descriptor.
    async fn read_sealed_segment(
        &self,
        scope: &ScopeKey,
        descriptor: &SegmentDescriptor,
    ) -> Result<Vec<SegmentRecord>>;

    /// Returns the current segment state for one scope.
    async fn read_manifest(&self, scope: &ScopeKey) -> Result<SegmentManifest>;

    /// Appends and seals one directory marker in the caller-selected scope.
    async fn append_marker(
        &self,
        scope: &ScopeKey,
        event: &PendingEvent,
    ) -> Result<MarkerPosition>;

    /// Advances one backup's monotonic progress while its catch-up lease is held.
    async fn advance_backend_state(
        &self,
        scope: &ScopeKey,
        backend_id: &str,
        synced_seq: u64,
    ) -> Result<()>;

    /// Atomically removes selected sealed descriptors and returns the removed set.
    async fn prune_sealed(
        &self,
        scope: &ScopeKey,
        removable_paths: &[String],
    ) -> Result<Vec<SegmentDescriptor>>;

}

/// Bootstrap every stable provider partition from its raw-primary manifest.
pub(crate) async fn bootstrap_account(
    provider: &dyn MultiWriteProvider,
    store: &MetadataStore,
    account_id: &str,
    partitions: &PartitionsManifest,
) -> Result<()> {
    for (&partition_id, partition) in &partitions.partitions {
        if partition.state != PartitionState::Stable {
            continue;
        }
        let scope = ScopeKey {
            account_id: account_id.to_string(),
            partition_id,
            epoch: partitions.epoch,
        };
        let path = store.paths().segment_manifest(account_id, partition_id)?.0;
        provider.bootstrap_manifest(&scope, store.read_json(&path).await?).await?;
    }
    Ok(())
}

/// Read and validate one immutable segment through the shared metadata store.
async fn read_sealed_segment(
    store: &MetadataStore,
    scope: &ScopeKey,
    descriptor: &SegmentDescriptor,
) -> Result<Vec<SegmentRecord>> {
    let directory = store
        .paths()
        .segments_dir(&scope.account_id, scope.partition_id)?
        .0;
    let bytes = store
        .read_bytes(&format!("{directory}/{}", descriptor.path))
        .await?;
    if bytes.len() as u64 != descriptor.byte_len
        || sha256_hex(&bytes) != descriptor.checksum.as_deref().unwrap_or_default()
    {
        return Err(Error::Serialization(
            "sealed segment bytes do not match descriptor".to_string(),
        ));
    }
    let decoded = decode_segment(&bytes)?;
    if decoded.truncated_tail
        || decoded.valid_bytes != descriptor.byte_len
        || decoded.records.len() != descriptor.record_count as usize
        || decoded.records.iter().enumerate().any(|(index, record)| {
            record.seq
                != descriptor
                    .segment_from_seq
                    .checked_add(index as u64)
                    .unwrap_or(u64::MAX)
        })
    {
        return Err(Error::Serialization(
            "sealed segment records do not match descriptor".to_string(),
        ));
    }
    Ok(decoded.records)
}

/// Select and validate one complete half-open sequence range.
fn select_record_range(
    records: impl IntoIterator<Item = SegmentRecord>,
    from_seq_inclusive: u64,
    to_seq_exclusive: u64,
) -> Result<Vec<SegmentRecord>> {
    let range_len = to_seq_exclusive
        .checked_sub(from_seq_inclusive)
        .ok_or_else(|| Error::invalid_operation("committed range is reversed"))?;
    let expected_len = usize::try_from(range_len)
        .map_err(|_| Error::invalid_operation("committed range is too large"))?;
    let selected = records
        .into_iter()
        .filter(|record| {
            record.seq >= from_seq_inclusive && record.seq < to_seq_exclusive
        })
        .collect::<Vec<_>>();
    if selected.len() != expected_len
        || selected.iter().enumerate().any(|(index, record)| {
            record.seq
                != from_seq_inclusive
                    .checked_add(index as u64)
                    .unwrap_or(u64::MAX)
        })
    {
        return Err(Error::Serialization(
            "committed record range is incomplete".to_string(),
        ));
    }
    Ok(selected)
}
