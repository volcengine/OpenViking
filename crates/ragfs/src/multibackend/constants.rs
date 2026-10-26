//! Shared constants for V2 multi-write metadata.

/// Canonical logical mount prefix used by V2 multi-write metadata.
pub(crate) const MULTIWRITE_MOUNT_PREFIX: &str = "/local";
/// Internal metadata directory name.
pub(crate) const SYSTEM_DIR: &str = "_system";
/// Mount-level protocol manifest file name.
pub(crate) const MULTIWRITE_PROTOCOL_FILE: &str = ".multiwrite.json";
/// Account partition directory name.
pub(crate) const PARTITIONS_DIR: &str = "partitions";
/// Standard manifest file name.
pub(crate) const MANIFEST_FILE: &str = "manifest.json";
/// Segment blob directory name.
pub(crate) const SEGMENTS_DIR: &str = "segments";
/// Checkpoint directory name.
pub(crate) const CHECKPOINTS_DIR: &str = "checkpoints";

/// Mutable head segment file prefix.
pub(crate) const HEAD_SEGMENT_FILE_PREFIX: &str = "head-";
/// Immutable sealed segment file prefix.
pub(crate) const SEALED_SEGMENT_FILE_PREFIX: &str = "segment-";
/// Segment blob file extension.
pub(crate) const SEGMENT_FILE_EXTENSION: &str = ".ovsg";
/// Checkpoint candidate directory prefix.
pub(crate) const CHECKPOINT_DIR_PREFIX: &str = "cp-";
/// Checkpoint candidate timestamp format.
pub(crate) const CHECKPOINT_DIR_TIME_FORMAT: &str = "%Y%m%d%H%M%S";
/// Checkpoint chunk file prefix.
pub(crate) const CHECKPOINT_CHUNK_FILE_PREFIX: &str = "chunk-";
/// Checkpoint chunk file extension.
pub(crate) const CHECKPOINT_CHUNK_FILE_EXTENSION: &str = ".ovcp";

/// Total number of fixed routing buckets.
pub const VBUCKETS: u32 = 65_536;
/// Maximum records allowed in one segment.
pub(crate) const MAX_SEGMENT_RECORDS: usize = 8_192;
/// Maximum file states allowed in one checkpoint chunk.
pub(crate) const MAX_CHECKPOINT_NODES: usize = 500_000;