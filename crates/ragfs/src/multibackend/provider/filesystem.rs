//! Filesystem-backed V2 multi-write persistence.

use std::collections::{BTreeSet, HashSet};
use std::sync::Arc;

use async_trait::async_trait;

use crate::core::errors::{Error, Result};
use crate::core::internal_names::is_multiwrite_internal_path;
use crate::lock::{OwnedPathLockLease, PathLockKind, PathLockRequest};
use crate::multibackend::codec::{decode_segment, encode_segment, sha256_hex};
use crate::multibackend::constants::{
    HEAD_SEGMENT_FILE_PREFIX, MAX_SEGMENT_RECORDS, SEALED_SEGMENT_FILE_PREFIX,
    SEGMENT_FILE_EXTENSION,
};
use crate::multibackend::meta::MetadataStore;
use crate::multibackend::model::{
    FlushResult, MarkerPosition, PartitionContext, PartitionsManifest, PendingEvent,
    PendingEventKind, ScopeKey, SegmentDescriptor, SegmentEventType, SegmentManifest,
    SegmentRecord,
};
use crate::multibackend::router::AccountRouter;

use super::{read_sealed_segment, select_record_range, MultiWriteProvider};

/// Persists V2 manifests and segment blobs on the primary filesystem.
pub struct FilesystemProvider {
    store: Arc<MetadataStore>,
    router: AccountRouter,
}

impl FilesystemProvider {
    /// Creates a filesystem-backed metadata provider.
    pub fn new(store: Arc<MetadataStore>) -> Self {
        Self {
            router: AccountRouter::new(store.clone()),
            store,
        }
    }

    /// Validates normal flush input before any route or storage access.
    fn validate_flush_events(events: &[PendingEvent]) -> Result<()> {
        let first = events
            .first()
            .ok_or_else(|| Error::invalid_operation("flush requires at least one event"))?;
        for event in events {
            if event.account_id != first.account_id {
                return Err(Error::invalid_operation(
                    "flush events must belong to one account",
                ));
            }
            if is_multiwrite_internal_path(&event.path) {
                return Err(Error::invalid_path(format!(
                    "cannot flush internal path: {}",
                    event.path
                )));
            }
            match event.kind {
                PendingEventKind::Data(SegmentEventType::Write | SegmentEventType::Remove) => {}
                _ => {
                    return Err(Error::invalid_operation(
                        "normal flush accepts only write and remove events",
                    ))
                }
            }
            if event.destination_path.is_some() {
                return Err(Error::invalid_operation(
                    "normal data event cannot have a destination",
                ));
            }
        }
        Ok(())
    }

    /// Routes every event and requires one current partition scope.
    async fn route_events(&self, events: &[PendingEvent]) -> Result<PartitionContext> {
        let mut selected = None;
        for event in events {
            let current = self.router.route(&event.account_id, &event.path).await?;
            if selected
                .as_ref()
                .is_some_and(|existing: &PartitionContext| existing.scope != current.scope)
            {
                return Err(Error::invalid_operation(
                    "flush events route to different partition scopes",
                ));
            }
            selected.get_or_insert(current);
        }
        selected.ok_or_else(|| Error::invalid_operation("flush requires at least one event"))
    }

    /// Reads and validates the partitions manifest owning one scope.
    async fn validate_scope(&self, scope: &ScopeKey) -> Result<()> {
        let path = self.store.paths().account_manifest(&scope.account_id)?.0;
        let manifest: PartitionsManifest = self.store.read_json(&path).await?;
        manifest.validate()?;
        scope.validate_against_manifest(&manifest)
    }

    /// Reads and validates one segment manifest without acquiring locks.
    async fn load_manifest(&self, scope: &ScopeKey) -> Result<SegmentManifest> {
        self.validate_scope(scope).await?;
        let path = self
            .store
            .paths()
            .segment_manifest(&scope.account_id, scope.partition_id)?
            .0;
        let manifest: SegmentManifest = self.store.read_json(&path).await?;
        manifest.validate()?;
        Ok(manifest)
    }

    /// Returns the current mutable head descriptor.
    fn head(manifest: &SegmentManifest) -> Option<&SegmentDescriptor> {
        manifest
            .segments
            .last()
            .filter(|segment| segment.segment_to_seq.is_none())
    }

    /// Returns the deterministic mutable head filename.
    fn head_name(from_seq: u64) -> String {
        format!("{HEAD_SEGMENT_FILE_PREFIX}{from_seq:020}{SEGMENT_FILE_EXTENSION}")
    }

    /// Returns the deterministic immutable sealed filename.
    fn sealed_name(from_seq: u64, to_seq: u64) -> String {
        format!("{SEALED_SEGMENT_FILE_PREFIX}{from_seq:020}-{to_seq:020}{SEGMENT_FILE_EXTENSION}")
    }

    /// Returns one logical segment blob path.
    fn segment_path(&self, scope: &ScopeKey, name: &str) -> Result<String> {
        let directory = self
            .store
            .paths()
            .segments_dir(&scope.account_id, scope.partition_id)?
            .0;
        Ok(format!("{directory}/{name}"))
    }

    /// Builds the complete Exact-lock set for one append candidate.
    fn append_lock_requests(
        &self,
        scope: &ScopeKey,
        manifest: &SegmentManifest,
        event_count: usize,
        force_final_seal: bool,
    ) -> Result<Vec<PathLockRequest>> {
        let mut paths = BTreeSet::from([self
            .store
            .paths()
            .segment_manifest(&scope.account_id, scope.partition_id)?
            .0]);
        paths.insert(self.store.paths().account_manifest(&scope.account_id)?.0);
        let mut remaining = event_count;
        let mut next_seq = manifest.next_seq;
        if next_seq.checked_add(event_count as u64).is_none() {
            return Err(Error::invalid_operation("multi-write sequence overflow"));
        }
        let mut count = 0;
        let mut segment_from_seq = next_seq;
        if let Some(head) = Self::head(manifest) {
            paths.insert(self.segment_path(scope, &head.path)?);
            count = head.record_count as usize;
            segment_from_seq = head.segment_from_seq;
        }

        if count == MAX_SEGMENT_RECORDS {
            paths.insert(
                self.segment_path(scope, &Self::sealed_name(segment_from_seq, next_seq - 1))?,
            );
            count = 0;
            segment_from_seq = next_seq;
        }
        while remaining > 0 {
            let appended = remaining.min(MAX_SEGMENT_RECORDS - count);
            remaining -= appended;
            next_seq = next_seq
                .checked_add(appended as u64)
                .ok_or_else(|| Error::Serialization("segment sequence overflow".to_string()))?;
            count += appended;
            if count == MAX_SEGMENT_RECORDS || (force_final_seal && remaining == 0) {
                paths.insert(
                    self.segment_path(scope, &Self::sealed_name(segment_from_seq, next_seq - 1))?,
                );
                count = 0;
                segment_from_seq = next_seq;
            }
        }
        if count > 0 {
            let name = if force_final_seal {
                Self::sealed_name(segment_from_seq, next_seq - 1)
            } else {
                Self::head_name(segment_from_seq)
            };
            paths.insert(self.segment_path(scope, &name)?);
        }
        Ok(paths
            .into_iter()
            .map(|path| PathLockRequest {
                path,
                kind: PathLockKind::Exact,
            })
            .collect())
    }

    /// Builds the Exact-lock set for a manifest-only state mutation.
    fn manifest_lock_requests(&self, scope: &ScopeKey) -> Result<Vec<PathLockRequest>> {
        let mut paths = vec![PathLockRequest {
            path: self
                .store
                .paths()
                .segment_manifest(&scope.account_id, scope.partition_id)?
                .0,
            kind: PathLockKind::Exact,
        }];
        paths.push(PathLockRequest {
            path: self.store.paths().account_manifest(&scope.account_id)?.0,
            kind: PathLockKind::Exact,
        });
        Ok(paths)
    }

    /// Acquires one all-or-nothing Exact-lock batch.
    async fn acquire(&self, requests: &[PathLockRequest]) -> Result<OwnedPathLockLease> {
        let manager = self.store.pathlock_manager();
        Ok(manager
            .acquire_batch(requests, std::time::Duration::from_secs(5), None)
            .await?)
    }

    /// Releases a lock lease while preserving any operation failure.
    async fn release<T>(&self, lease: &OwnedPathLockLease, operation: Result<T>) -> Result<T> {
        let release = self
            .store
            .pathlock_manager()
            .release(lease)
            .await
            .map_err(Error::from);
        merge_operation_and_release(operation, release)
    }

    /// Reads and validates only the committed prefix of the current head.
    async fn committed_head(
        &self,
        scope: &ScopeKey,
        manifest: &SegmentManifest,
    ) -> Result<Vec<SegmentRecord>> {
        let Some(head) = Self::head(manifest) else {
            return Ok(Vec::new());
        };
        if head.path != Self::head_name(head.segment_from_seq) {
            return Err(Error::Serialization(
                "head segment path is not deterministic".to_string(),
            ));
        }
        let bytes = self
            .store
            .read_bytes(&self.segment_path(scope, &head.path)?)
            .await?;
        let committed_len = usize::try_from(head.byte_len)
            .map_err(|_| Error::Serialization("head byte length is too large".to_string()))?;
        if bytes.len() < committed_len {
            return Err(Error::Serialization(
                "head segment is shorter than its committed byte length".to_string(),
            ));
        }
        let decoded = decode_segment(&bytes[..committed_len])?;
        if decoded.truncated_tail
            || decoded.valid_bytes != head.byte_len
            || decoded.records.len() != head.record_count as usize
        {
            return Err(Error::Serialization(
                "head segment committed prefix does not match its descriptor".to_string(),
            ));
        }
        for (index, record) in decoded.records.iter().enumerate() {
            let expected = head
                .segment_from_seq
                .checked_add(index as u64)
                .ok_or_else(|| Error::Serialization("segment sequence overflow".to_string()))?;
            if record.seq != expected {
                return Err(Error::Serialization(
                    "head segment contains a sequence gap".to_string(),
                ));
            }
        }
        Ok(decoded.records)
    }

    /// Converts one pending event into its persisted segment record.
    fn record(event: &PendingEvent, seq: u64) -> Result<SegmentRecord> {
        let event_type = match event.kind {
            PendingEventKind::Data(kind) => kind,
            PendingEventKind::DeleteAccount => {
                return Err(Error::invalid_operation(
                    "account deletion is not a segment record",
                ))
            }
        };
        let marker = matches!(
            event_type,
            SegmentEventType::RemoveTree | SegmentEventType::MoveTree
        );
        let record = SegmentRecord {
            seq,
            event_type,
            path: event.path.clone(),
            op_id: marker.then_some(event.operation_id),
            destination_path: event.destination_path.clone(),
        };
        record.validate()?;
        Ok(record)
    }

    /// Persists one immutable sealed segment and returns its descriptor.
    async fn persist_sealed(
        &self,
        scope: &ScopeKey,
        manifest: &SegmentManifest,
        records: &[SegmentRecord],
    ) -> Result<SegmentDescriptor> {
        let first = records
            .first()
            .ok_or_else(|| Error::invalid_operation("cannot seal an empty segment"))?
            .seq;
        let last = records.last().unwrap().seq;
        let bytes = encode_segment(records)?;
        let name = Self::sealed_name(first, last);
        let path = self.segment_path(scope, &name)?;
        if !manifest.segments.iter().any(|segment| segment.path == name) {
            self.store.remove_file(&path).await?;
        }
        self.store.write_immutable(&path, &bytes).await?;
        Ok(SegmentDescriptor {
            path: name,
            segment_from_seq: first,
            segment_to_seq: Some(last),
            record_count: records.len() as u32,
            byte_len: bytes.len() as u64,
            checksum: Some(sha256_hex(&bytes)),
        })
    }

    /// Publishes one mutable head and returns its descriptor.
    async fn persist_head(
        &self,
        scope: &ScopeKey,
        records: &[SegmentRecord],
    ) -> Result<SegmentDescriptor> {
        let first = records
            .first()
            .ok_or_else(|| Error::invalid_operation("cannot publish an empty head"))?
            .seq;
        let bytes = encode_segment(records)?;
        let name = Self::head_name(first);
        self.store
            .write_bytes(&self.segment_path(scope, &name)?, &bytes)
            .await?;
        Ok(SegmentDescriptor {
            path: name,
            segment_from_seq: first,
            segment_to_seq: None,
            record_count: records.len() as u32,
            byte_len: bytes.len() as u64,
            checksum: None,
        })
    }

    /// Appends records to a locked manifest and publishes blobs before the manifest.
    async fn commit_events(
        &self,
        scope: &ScopeKey,
        mut manifest: SegmentManifest,
        events: &[PendingEvent],
        force_final_seal: bool,
    ) -> Result<(SegmentManifest, Vec<SegmentDescriptor>)> {
        let mut records = self.committed_head(scope, &manifest).await?;
        let mut sealed = Vec::new();
        if records.len() == MAX_SEGMENT_RECORDS {
            manifest.segments.pop();
            let descriptor = self.persist_sealed(scope, &manifest, &records).await?;
            manifest.segments.push(descriptor.clone());
            sealed.push(descriptor);
            records.clear();
        }

        for (index, event) in events.iter().enumerate() {
            let seq = manifest.next_seq;
            manifest.next_seq = manifest
                .next_seq
                .checked_add(1)
                .ok_or_else(|| Error::Serialization("segment sequence overflow".to_string()))?;
            records.push(Self::record(event, seq)?);
            let seal_now = records.len() == MAX_SEGMENT_RECORDS
                || (force_final_seal && index + 1 == events.len());
            if seal_now {
                if Self::head(&manifest).is_some() {
                    manifest.segments.pop();
                }
                let descriptor = self.persist_sealed(scope, &manifest, &records).await?;
                manifest.segments.push(descriptor.clone());
                sealed.push(descriptor);
                records.clear();
            }
        }

        if !records.is_empty() {
            let descriptor = self.persist_head(scope, &records).await?;
            if Self::head(&manifest).is_some() {
                manifest.segments.pop();
            }
            manifest.segments.push(descriptor);
        }
        manifest.validate()?;
        let path = self
            .store
            .paths()
            .segment_manifest(&scope.account_id, scope.partition_id)?
            .0;
        self.store
            .publish_json(&path, &manifest, SegmentManifest::validate)
            .await?;
        Ok((manifest, sealed))
    }

    /// Returns whether the locked append snapshot still matches its candidate.
    fn append_snapshot_matches(
        candidate_scope: &ScopeKey,
        current_scope: &ScopeKey,
        candidate_manifest: &SegmentManifest,
        current_manifest: &SegmentManifest,
        candidate_requests: &[PathLockRequest],
        current_requests: &[PathLockRequest],
    ) -> bool {
        candidate_scope == current_scope
            && candidate_manifest == current_manifest
            && candidate_requests == current_requests
    }

    /// Validates one marker against its caller-selected scope.
    fn validate_marker(&self, scope: &ScopeKey, event: &PendingEvent) -> Result<()> {
        if event.account_id != scope.account_id {
            return Err(Error::invalid_operation(
                "marker account does not match supplied scope",
            ));
        }
        match event.kind {
            PendingEventKind::Data(SegmentEventType::RemoveTree | SegmentEventType::MoveTree) => {}
            _ => {
                return Err(Error::invalid_operation(
                    "append_marker accepts only directory markers",
                ))
            }
        }
        if is_multiwrite_internal_path(&event.path) {
            return Err(Error::invalid_path(format!(
                "cannot append marker for internal path: {}",
                event.path
            )));
        }
        self.store
            .paths()
            .backend_path(&scope.account_id, &event.path)?;
        if let Some(destination) = &event.destination_path {
            if is_multiwrite_internal_path(destination) {
                return Err(Error::invalid_path(format!(
                    "cannot append marker for internal destination: {destination}"
                )));
            }
            self.store
                .paths()
                .backend_path(&scope.account_id, destination)?;
        }
        Self::record(event, 1).map(|_| ())
    }
}

#[async_trait]
impl MultiWriteProvider for FilesystemProvider {
    /// Confirms that filesystem state exists for the account partition.
    async fn bootstrap_manifest(&self, scope: &ScopeKey, manifest: SegmentManifest) -> Result<()> {
        manifest.validate()?;
        self.load_manifest(scope).await.map(|_| ())
    }

    /// Flushes normal events after validating their current partition route.
    async fn flush(
        &self,
        _route_hint: Option<&PartitionContext>,
        events: Vec<PendingEvent>,
    ) -> Result<FlushResult> {
        Self::validate_flush_events(&events)?;
        loop {
            let candidate = self.route_events(&events).await?;
            let candidate_manifest = self.load_manifest(&candidate.scope).await?;
            let requests = self.append_lock_requests(
                &candidate.scope,
                &candidate_manifest,
                events.len(),
                false,
            )?;
            let lease = self.acquire(&requests).await?;
            let operation = async {
                let current = self.route_events(&events).await?;
                let manifest = self.load_manifest(&current.scope).await?;
                let current_requests =
                    self.append_lock_requests(&current.scope, &manifest, events.len(), false)?;
                if !Self::append_snapshot_matches(
                    &candidate.scope,
                    &current.scope,
                    &candidate_manifest,
                    &manifest,
                    &requests,
                    &current_requests,
                ) {
                    return Ok(None);
                }
                let first_seq = manifest.next_seq;
                let (_, sealed_segments) = self
                    .commit_events(&current.scope, manifest, &events, false)
                    .await?;
                let last_seq = first_seq
                    .checked_add(events.len() as u64 - 1)
                    .ok_or_else(|| Error::Serialization("segment sequence overflow".to_string()))?;
                Ok(Some(FlushResult {
                    scope: current.scope,
                    first_seq,
                    last_seq,
                    sealed_segments,
                }))
            }
            .await;
            if let Some(result) = self.release(&lease, operation).await? {
                return Ok(result);
            }
        }
    }

    /// Reads committed head records in the requested half-open sequence range.
    async fn read_committed_head(
        &self,
        scope: &ScopeKey,
        from_seq_inclusive: u64,
        to_seq_exclusive: u64,
    ) -> Result<Vec<SegmentRecord>> {
        let manifest = self.load_manifest(scope).await?;
        if from_seq_inclusive > to_seq_exclusive || to_seq_exclusive > manifest.next_seq {
            return Err(Error::invalid_operation(
                "committed head range is outside the manifest",
            ));
        }
        let records = self.committed_head(scope, &manifest).await?;
        Ok(records
            .into_iter()
            .filter(|record| record.seq >= from_seq_inclusive && record.seq < to_seq_exclusive)
            .collect())
    }

    /// Reads one complete committed range from sealed segments and the current head.
    async fn read_committed_range(
        &self,
        scope: &ScopeKey,
        from_seq_inclusive: u64,
        to_seq_exclusive: u64,
    ) -> Result<Vec<SegmentRecord>> {
        let manifest = self.load_manifest(scope).await?;
        if from_seq_inclusive > to_seq_exclusive || to_seq_exclusive > manifest.next_seq {
            return Err(Error::invalid_operation(
                "committed range is outside the manifest",
            ));
        }
        let mut records = Vec::new();
        for descriptor in &manifest.segments {
            if descriptor.segment_from_seq >= to_seq_exclusive
                || descriptor
                    .segment_to_seq
                    .is_some_and(|to| to < from_seq_inclusive)
            {
                continue;
            }
            if descriptor.segment_to_seq.is_some() {
                records.extend(read_sealed_segment(&self.store, scope, descriptor).await?);
            } else {
                records.extend(self.committed_head(scope, &manifest).await?);
            }
        }
        select_record_range(records, from_seq_inclusive, to_seq_exclusive)
    }

    /// Reads one immutable sealed segment named by a validated descriptor.
    async fn read_sealed_segment(
        &self,
        scope: &ScopeKey,
        descriptor: &SegmentDescriptor,
    ) -> Result<Vec<SegmentRecord>> {
        read_sealed_segment(&self.store, scope, descriptor).await
    }

    /// Returns the current segment state for one scope.
    async fn read_manifest(&self, scope: &ScopeKey) -> Result<SegmentManifest> {
        self.load_manifest(scope).await
    }

    /// Appends and seals one directory marker in the caller-selected scope.
    async fn append_marker(
        &self,
        scope: &ScopeKey,
        event: &PendingEvent,
    ) -> Result<MarkerPosition> {
        self.validate_marker(scope, event)?;
        loop {
            let candidate_manifest = self.load_manifest(scope).await?;
            let requests = self.append_lock_requests(scope, &candidate_manifest, 1, true)?;
            let lease = self.acquire(&requests).await?;
            let operation = async {
                let manifest = self.load_manifest(scope).await?;
                let current_requests = self.append_lock_requests(scope, &manifest, 1, true)?;
                if !Self::append_snapshot_matches(
                    scope,
                    scope,
                    &candidate_manifest,
                    &manifest,
                    &requests,
                    &current_requests,
                ) {
                    return Ok(None);
                }
                let seq = manifest.next_seq;
                self.commit_events(scope, manifest, std::slice::from_ref(event), true)
                    .await?;
                Ok(Some(MarkerPosition {
                    partition_id: scope.partition_id,
                    epoch: scope.epoch,
                    seq,
                }))
            }
            .await;
            if let Some(position) = self.release(&lease, operation).await? {
                return Ok(position);
            }
        }
    }

    /// Advances one backup's monotonic progress while its catch-up lease is held.
    async fn advance_backend_state(
        &self,
        scope: &ScopeKey,
        backend_id: &str,
        synced_seq: u64,
    ) -> Result<()> {
        let requests = self.manifest_lock_requests(scope)?;
        let lease = self.acquire(&requests).await?;
        let operation = async {
            let mut manifest = self.load_manifest(scope).await?;
            let state = manifest.backend_states.get_mut(backend_id).ok_or_else(|| {
                Error::invalid_operation(format!("unknown backend id: {backend_id}"))
            })?;
            if synced_seq < state.synced_seq {
                return Err(Error::invalid_operation("backend progress cannot regress"));
            }
            if synced_seq >= manifest.next_seq {
                return Err(Error::invalid_operation(
                    "backend progress must remain below next_seq",
                ));
            }
            state.synced_seq = synced_seq;
            let path = self
                .store
                .paths()
                .segment_manifest(&scope.account_id, scope.partition_id)?
                .0;
            self.store
                .publish_json(&path, &manifest, SegmentManifest::validate)
                .await
        }
        .await;
        self.release(&lease, operation).await
    }

    /// Atomically removes selected sealed descriptors and returns the removed set.
    async fn prune_sealed(
        &self,
        scope: &ScopeKey,
        removable_paths: &[String],
    ) -> Result<Vec<SegmentDescriptor>> {
        let requested = removable_paths.iter().collect::<HashSet<_>>();
        if requested.len() != removable_paths.len() {
            return Err(Error::invalid_operation(
                "prune paths must not contain duplicates",
            ));
        }
        let requests = self.manifest_lock_requests(scope)?;
        let lease = self.acquire(&requests).await?;
        let operation = async {
            if !self.store.gc_scope_is_safe(scope).await? {
                return Ok(Vec::new());
            }
            let mut manifest = self.load_manifest(scope).await?;
            for path in removable_paths {
                let descriptor = manifest
                    .segments
                    .iter()
                    .find(|segment| segment.path == *path)
                    .ok_or_else(|| {
                        Error::invalid_operation(format!(
                            "sealed segment is not referenced: {path}"
                        ))
                    })?;
                if descriptor.segment_to_seq.is_none() {
                    return Err(Error::invalid_operation(format!(
                        "cannot prune mutable head: {path}"
                    )));
                }
            }
            let mut removed = Vec::new();
            manifest.segments.retain(|segment| {
                if requested.contains(&segment.path) {
                    removed.push(segment.clone());
                    false
                } else {
                    true
                }
            });
            manifest.validate()?;
            let path = self
                .store
                .paths()
                .segment_manifest(&scope.account_id, scope.partition_id)?
                .0;
            self.store
                .publish_json(&path, &manifest, SegmentManifest::validate)
                .await?;
            Ok(removed)
        }
        .await;
        self.release(&lease, operation).await
    }
}

/// Preserves an operation error when lock release also fails.
fn merge_operation_and_release<T>(operation: Result<T>, release: Result<()>) -> Result<T> {
    match (operation, release) {
        (Ok(value), Ok(())) => Ok(value),
        (Ok(_), Err(error)) => Err(error),
        (Err(error), Ok(())) => Err(error),
        (Err(error), Err(release_error)) => Err(Error::internal(format!(
            "{error}; lock release failed: {release_error}"
        ))),
    }
}

#[cfg(test)]
#[path = "tests/test_filesystem.rs"]
mod tests;
