//! V2 multi-write persistence and in-memory data models.
#![allow(missing_docs)]
use crate::core::errors::{Error, Result};
use crate::multibackend::constants::{
    CHECKPOINT_DIR_PREFIX, HEAD_SEGMENT_FILE_PREFIX, MAX_CHECKPOINT_NODES, MAX_SEGMENT_RECORDS,
    SEALED_SEGMENT_FILE_PREFIX, SEGMENT_FILE_EXTENSION,
};
use crate::multibackend::router::validate_routes;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashSet};
use uuid::Uuid;
macro_rules! ensure { ($condition:expr, $($arg:tt)*) => { if !$condition { return Err(Error::Serialization(format!($($arg)*))); } }; }
pub use crate::multibackend::constants::VBUCKETS;
const FORMAT_VERSION: u32 = 1;
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ProtocolState {
    pub protocol_version: ProtocolVersion,
    pub status: ProtocolStatus,
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
pub enum ProtocolVersion {
    V2,
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ProtocolStatus {
    Migrating,
    Stable,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct PartitionsManifest {
    pub version: u32,
    pub epoch: u64,
    pub vbuckets: u32,
    pub partitions: BTreeMap<u32, PartitionEntry>,
    pub routes: Vec<RouteEntry>,
    pub directory_events: Vec<DirectoryEvent>,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct PartitionEntry {
    pub state: PartitionState,
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
pub enum PartitionState {
    Stable,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct RouteEntry {
    pub start: u32,
    pub end: u32,
    pub partition: u32,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct PartitionManifest {
    pub version: u32,
    pub partition_id: u32,
    pub epoch: u64,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct DirectoryEvent {
    pub op_id: Uuid,
    pub operation: DirectoryOperation,
    pub source_path: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub destination_path: Option<String>,
    pub positions: Vec<MarkerPosition>,
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
pub enum DirectoryOperation {
    RemoveTree,
    MoveTree,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct MarkerPosition {
    pub partition_id: u32,
    pub epoch: u64,
    pub seq: u64,
}
#[derive(Debug, Clone, Hash, Serialize, Deserialize, PartialEq, Eq)]
pub struct ScopeKey {
    pub account_id: String,
    pub partition_id: u32,
    pub epoch: u64,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ScopedSeq {
    pub scope: ScopeKey,
    pub seq: u64,
}
#[derive(Debug, Clone)]
pub struct PartitionContext {
    pub scope: ScopeKey,
    pub segment_dir: String,
}
#[derive(Debug, Clone)]
pub struct PendingEvent {
    pub operation_id: Uuid,
    pub account_id: String,
    pub kind: PendingEventKind,
    pub path: String,
    pub destination_path: Option<String>,
    pub route_hint: Option<PartitionContext>,
}
#[derive(Debug, Clone)]
pub enum PendingEventKind {
    Data(SegmentEventType),
    DeleteAccount,
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[repr(u8)]
pub enum SegmentEventType {
    Write = 1,
    Remove = 2,
    RemoveTree = 3,
    MoveTree = 4,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct SegmentRecord {
    pub seq: u64,
    pub event_type: SegmentEventType,
    pub path: String,
    pub op_id: Option<Uuid>,
    pub destination_path: Option<String>,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct SegmentManifest {
    pub version: u32,
    pub next_seq: u64,
    pub segments: Vec<SegmentDescriptor>,
    pub backend_states: BTreeMap<String, BackendState>,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct SegmentDescriptor {
    pub path: String,
    pub segment_from_seq: u64,
    pub segment_to_seq: Option<u64>,
    pub record_count: u32,
    pub byte_len: u64,
    pub checksum: Option<String>,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct BackendState {
    pub synced_seq: u64,
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FlushResult {
    pub scope: ScopeKey,
    pub first_seq: u64,
    pub last_seq: u64,
    pub sealed_segments: Vec<SegmentDescriptor>,
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DecodedSegment {
    pub records: Vec<SegmentRecord>,
    pub valid_bytes: u64,
    pub truncated_tail: bool,
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FileState {
    pub path: String,
    pub latest_seq: ScopedSeq,
    pub deleted: Option<bool>,
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CheckpointNode {
    pub path_fragment: String,
    pub latest_seq: Option<u64>,
    pub deleted: Option<bool>,
    pub children: Vec<CheckpointNode>,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct CheckpointsManifest {
    pub version: u32,
    #[serde(deserialize_with = "Option::deserialize")]
    pub latest_checkpoint: Option<LatestCheckpoint>,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct LatestCheckpoint {
    pub path: String,
    pub checkpoint_to_seq: u64,
    pub updated_at_ns: u64,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct CheckpointManifest {
    pub version: u32,
    pub partition_id: u32,
    pub epoch: u64,
    pub checkpoint_from_seq: u64,
    pub checkpoint_to_seq: u64,
    pub created_at_ns: u64,
    pub root: ChunkDescriptor,
    pub checksum: String,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ChunkDescriptor {
    pub file: String,
    pub root_path: String,
    pub start_path: Option<String>,
    pub end_path: Option<String>,
    pub file_state_count: u32,
    pub checksum: String,
    pub chunks: Vec<ChunkDescriptor>,
}
impl ProtocolState {
    /// Validate the closed protocol version value.
    pub fn validate(&self) -> Result<()> {
        Ok(())
    }
}
impl PartitionsManifest {
    /// Validate routing and directory marker invariants.
    pub fn validate(&self) -> Result<()> {
        validate_version("partitions manifest", self.version)?;
        ensure!(self.epoch > 0, "partitions epoch must be positive");
        ensure!(self.vbuckets == VBUCKETS, "invalid vbucket count");
        ensure!(!self.partitions.is_empty(), "partitions must not be empty");
        for entry in self.partitions.values() {
            entry.validate()?;
        }
        validate_routes(&self.routes, self.partitions.keys().copied())?;
        let mut operation_ids = HashSet::new();
        for event in &self.directory_events {
            ensure!(
                operation_ids.insert(event.op_id),
                "duplicate directory op_id"
            );
            event.validate()?;
            for position in &event.positions {
                ensure!(
                    position.epoch == self.epoch,
                    "marker epoch does not match manifest"
                );
                ensure!(
                    self.partitions
                        .get(&position.partition_id)
                        .is_some_and(|entry| entry.state == PartitionState::Stable),
                    "marker partition is unavailable"
                );
            }
        }
        ensure!(
            self.partitions
                .values()
                .all(|entry| entry.state == PartitionState::Stable),
            "partition manifest contains an unsupported state"
        );
        Ok(())
    }
}
impl PartitionEntry {
    /// Validate the closed partition state value.
    pub fn validate(&self) -> Result<()> {
        Ok(())
    }
}
impl RouteEntry {
    /// Validate one inclusive vbucket route range.
    pub fn validate(&self) -> Result<()> {
        ensure!(
            self.start <= self.end && self.end < VBUCKETS,
            "invalid route range"
        );
        Ok(())
    }
}
impl PartitionManifest {
    /// Validate one partition manifest version and epoch.
    pub fn validate(&self) -> Result<()> {
        validate_version("partition manifest", self.version)?;
        ensure!(self.epoch > 0, "partition epoch must be positive");
        Ok(())
    }
}
impl DirectoryEvent {
    /// Validate one directory operation and its marker positions.
    pub fn validate(&self) -> Result<()> {
        validate_path(&self.source_path)?;
        match self.operation {
            DirectoryOperation::RemoveTree => ensure!(
                self.destination_path.is_none(),
                "remove tree has a destination"
            ),
            DirectoryOperation::MoveTree => {
                validate_path(self.destination_path.as_deref().ok_or_else(|| {
                    Error::Serialization("move tree requires a destination".into())
                })?)?
            }
        }
        let mut partitions = HashSet::new();
        for position in &self.positions {
            position.validate()?;
            ensure!(
                partitions.insert(position.partition_id),
                "duplicate marker partition"
            );
        }
        Ok(())
    }
}
impl MarkerPosition {
    /// Validate one persisted directory marker position.
    pub fn validate(&self) -> Result<()> {
        ensure!(self.epoch > 0 && self.seq > 0, "invalid marker position");
        Ok(())
    }
}
impl ScopeKey {
    /// Validate an account-scoped partition identity.
    pub fn validate(&self) -> Result<()> {
        ensure!(
            !self.account_id.trim().is_empty() && self.epoch > 0,
            "invalid scope"
        );
        Ok(())
    }
    /// Validate this scope against its owning partitions manifest.
    pub fn validate_against_manifest(&self, manifest: &PartitionsManifest) -> Result<()> {
        self.validate()?;
        ensure!(
            manifest.partitions.contains_key(&self.partition_id),
            "scope partition is unknown"
        );
        ensure!(
            self.epoch == manifest.epoch,
            "scope epoch does not match manifest"
        );
        Ok(())
    }
}
impl ScopedSeq {
    /// Validate a scoped sequence value.
    pub fn validate(&self) -> Result<()> {
        self.scope.validate()?;
        ensure!(self.seq > 0, "scoped sequence must be positive");
        Ok(())
    }
}
impl SegmentRecord {
    /// Validate a persisted segment record.
    pub fn validate(&self) -> Result<()> {
        ensure!(self.seq > 0, "record sequence must be positive");
        validate_path(&self.path)?;
        match self.event_type {
            SegmentEventType::MoveTree => {
                ensure!(self.op_id.is_some(), "move marker requires an op_id");
                validate_path(self.destination_path.as_deref().ok_or_else(|| {
                    Error::Serialization("move marker requires a destination".into())
                })?)?;
            }
            SegmentEventType::RemoveTree => {
                ensure!(self.op_id.is_some(), "remove marker requires an op_id");
                ensure!(
                    self.destination_path.is_none(),
                    "remove marker has a destination"
                );
            }
            _ => ensure!(
                self.destination_path.is_none(),
                "data event has a destination"
            ),
        }
        Ok(())
    }
}
impl SegmentManifest {
    /// Validate segment continuity and backend progress.
    pub fn validate(&self) -> Result<()> {
        validate_version("segment manifest", self.version)?;
        ensure!(self.next_seq > 0, "next sequence must be positive");
        let (mut next, mut paths) = (None, HashSet::new());
        for (index, segment) in self.segments.iter().enumerate() {
            segment.validate()?;
            ensure!(paths.insert(&segment.path), "duplicate segment path");
            if let Some(expected) = next {
                ensure!(segment.segment_from_seq == expected, "segment sequence gap");
            }
            ensure!(
                segment.segment_to_seq.is_some() || index + 1 == self.segments.len(),
                "head segment must be last"
            );
            next = segment
                .segment_from_seq
                .checked_add(u64::from(segment.record_count));
            ensure!(next.is_some(), "segment sequence overflow");
        }
        if let Some(expected) = next {
            ensure!(
                self.next_seq == expected,
                "next sequence does not follow segments"
            );
        }
        for (backend, state) in &self.backend_states {
            ensure!(!backend.trim().is_empty(), "backend id must not be empty");
            state.validate(self.next_seq - 1)?;
        }
        Ok(())
    }
}
impl SegmentDescriptor {
    /// Validate one segment summary.
    pub fn validate(&self) -> Result<()> {
        ensure!(
            !self.path.is_empty() && self.segment_from_seq > 0,
            "invalid segment identity"
        );
        ensure!(self.byte_len >= 6, "segment byte length is too small");
        match self.segment_to_seq {
            Some(to) => {
                ensure!(to >= self.segment_from_seq, "segment range is invalid");
                ensure!(
                    self.path
                        == format!(
                            "{SEALED_SEGMENT_FILE_PREFIX}{:020}-{to:020}{SEGMENT_FILE_EXTENSION}",
                            self.segment_from_seq
                        ),
                    "sealed segment path does not match range"
                );
                ensure!(
                    u64::from(self.record_count) == to - self.segment_from_seq + 1,
                    "sealed segment count does not match range"
                );
                ensure!(
                    self.record_count <= MAX_SEGMENT_RECORDS as u32,
                    "sealed segment has too many records"
                );
                validate_checksum(self.checksum.as_deref().ok_or_else(|| {
                    Error::Serialization("sealed segment requires a checksum".into())
                })?)?;
            }
            None => {
                ensure!(
                    self.path
                        == format!(
                            "{HEAD_SEGMENT_FILE_PREFIX}{:020}{SEGMENT_FILE_EXTENSION}",
                            self.segment_from_seq
                        ),
                    "head segment path does not match start sequence"
                );
                ensure!(self.checksum.is_none(), "head segment has a checksum");
            }
        }
        ensure!(
            self.segment_from_seq
                .checked_add(u64::from(self.record_count))
                .is_some(),
            "segment sequence overflow"
        );
        Ok(())
    }
}
impl BackendState {
    /// Validate backend progress against the committed sequence.
    pub fn validate(&self, committed_seq: u64) -> Result<()> {
        ensure!(
            self.synced_seq <= committed_seq,
            "backend progress is ahead"
        );
        Ok(())
    }
}
impl FileState {
    /// Validate a checkpoint file state against its owning scope.
    pub fn validate(&self, expected_scope: &ScopeKey) -> Result<()> {
        validate_path(&self.path)?;
        self.latest_seq.validate()?;
        ensure!(
            &self.latest_seq.scope == expected_scope,
            "file state crosses checkpoint scope"
        );
        validate_tombstone(self.deleted)
    }
}
impl CheckpointNode {
    /// Validate one checkpoint radix tree and its tombstones.
    pub fn validate(&self) -> Result<()> {
        ensure!(
            self.latest_seq.is_none_or(|seq| seq > 0),
            "invalid checkpoint node sequence"
        );
        validate_tombstone(self.deleted)?;
        ensure!(
            self.deleted.is_none() || self.latest_seq.is_some(),
            "tombstone requires a sequence"
        );
        let mut previous = None;
        for child in &self.children {
            child.validate()?;
            ensure!(
                previous.is_none_or(|fragment| fragment < child.path_fragment.as_str()),
                "checkpoint children are not strictly ordered"
            );
            previous = Some(child.path_fragment.as_str());
        }
        Ok(())
    }
}
impl CheckpointsManifest {
    /// Validate the checkpoint publication pointer.
    pub fn validate(&self) -> Result<()> {
        validate_version("checkpoints manifest", self.version)?;
        if let Some(latest) = &self.latest_checkpoint {
            latest.validate()?;
        }
        Ok(())
    }
}
impl LatestCheckpoint {
    /// Validate one published checkpoint reference.
    pub fn validate(&self) -> Result<()> {
        let name = self.path.strip_suffix('/').ok_or_else(|| {
            Error::Serialization("checkpoint pointer must reference a directory".into())
        })?;
        ensure!(
            is_checkpoint_directory_name(name),
            "invalid checkpoint reference"
        );
        Ok(())
    }
}

/// Return whether a single directory name identifies a published checkpoint.
pub(crate) fn is_checkpoint_directory_name(name: &str) -> bool {
    !name.contains('/') && name.starts_with(CHECKPOINT_DIR_PREFIX)
}

impl CheckpointManifest {
    /// Validate a checkpoint manifest and its chunk tree.
    pub fn validate(&self) -> Result<()> {
        validate_version("checkpoint manifest", self.version)?;
        ensure!(self.epoch > 0, "checkpoint epoch must be positive");
        ensure!(
            self.checkpoint_from_seq == 1 && self.checkpoint_to_seq == 0
                || self.checkpoint_from_seq > 0
                    && self.checkpoint_from_seq <= self.checkpoint_to_seq,
            "invalid checkpoint sequence range"
        );
        validate_checksum(&self.checksum)?;
        self.root.validate()
    }
    /// Validate this checkpoint against its owning scope.
    pub fn validate_for_scope(&self, scope: &ScopeKey) -> Result<()> {
        self.validate()?;
        scope.validate()?;
        ensure!(
            self.partition_id == scope.partition_id,
            "checkpoint partition does not match scope"
        );
        ensure!(
            self.epoch == scope.epoch,
            "checkpoint epoch does not match scope"
        );
        Ok(())
    }
}
impl ChunkDescriptor {
    /// Validate a checkpoint chunk tree, including unique files and ranges.
    pub fn validate(&self) -> Result<()> {
        self.validate_inner(true, &mut HashSet::new())
    }
    /// Validate one chunk and recurse into its direct children.
    fn validate_inner(&self, root: bool, files: &mut HashSet<String>) -> Result<()> {
        ensure!(
            !self.file.is_empty() && files.insert(self.file.clone()),
            "invalid chunk file"
        );
        validate_path(&self.root_path)?;
        ensure!(
            self.file_state_count <= MAX_CHECKPOINT_NODES as u32,
            "chunk has too many file states"
        );
        validate_checksum(&self.checksum)?;
        let (range_start, range_end) =
            match (root, self.start_path.as_deref(), self.end_path.as_deref()) {
                (true, None, None) => (self.root_path.as_str(), None),
                (true, _, _) => {
                    return Err(Error::Serialization(
                        "root chunk must not have bounds".into(),
                    ));
                }
                (false, Some(start), end) => {
                    if let Some(end) = end {
                        ensure!(start < end, "chunk range is reversed");
                    }
                    (start, end)
                }
                (false, None, _) => {
                    return Err(Error::Serialization(
                        "non-root chunk requires start bound".into(),
                    ));
                }
            };
        let mut expected_start = Some(range_start);
        for child in &self.chunks {
            child.validate_inner(false, files)?;
            let child_start = child.start_path.as_deref().unwrap();
            ensure!(
                expected_start.is_some_and(|start| start == child_start),
                "chunk ranges are not contiguous"
            );
            expected_start = child.end_path.as_deref();
        }
        if !self.chunks.is_empty() {
            ensure!(
                expected_start == range_end,
                "chunk ranges are not contiguous"
            );
        }
        Ok(())
    }
}
/// Validate a supported persisted format version.
fn validate_version(name: &str, version: u32) -> Result<()> {
    ensure!(
        version == FORMAT_VERSION,
        "unsupported {name} version {version}"
    );
    Ok(())
}
/// Validate a complete logical path.
fn validate_path(path: &str) -> Result<()> {
    ensure!(path.starts_with('/'), "logical path must be absolute");
    Ok(())
}
/// Validate a lowercase SHA-256 hexadecimal checksum.
fn validate_checksum(checksum: &str) -> Result<()> {
    ensure!(
        checksum.len() == 64
            && checksum
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)),
        "invalid SHA-256 checksum"
    );
    Ok(())
}
/// Validate an optional deletion tombstone.
fn validate_tombstone(deleted: Option<bool>) -> Result<()> {
    ensure!(deleted != Some(false), "tombstone may only be true");
    Ok(())
}

#[cfg(test)]
#[path = "tests/test_model.rs"]
mod tests;
