//! Public CacheRuntime operation types and interfaces.

use bytes::Bytes;
use std::time::Duration;

/// One sorted-set member with its score.
#[derive(Debug, Clone, PartialEq)]
pub struct ScoredMember {
    /// UTF-8 sorted-set member.
    pub member: String,
    /// Sorted-set score.
    pub score: f64,
}

/// Score bounds and pagination for one sorted-set range query.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct ScoreRange {
    /// Lower score bound.
    pub min: f64,
    /// Exclude the lower score bound when true.
    pub min_exclusive: bool,
    /// Upper score bound.
    pub max: f64,
    /// Exclude the upper score bound when true.
    pub max_exclusive: bool,
    /// Number of matching members to skip.
    pub offset: u64,
    /// Maximum number of members to return.
    pub limit: u32,
}

/// Expiration applied by Redis SET.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Expiration {
    /// Expire after the given duration.
    After(Duration),
}

/// Conditional behavior for Redis SET.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub enum SetCondition {
    /// Always store the value.
    #[default]
    None,
    /// Store only when the key does not exist.
    Nx,
    /// Store only when the key already exists.
    Xx,
}

/// Options applied to Redis-style SET operations.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct SetOptions {
    /// Optional value expiration.
    pub expiration: Option<Expiration>,
    /// Optional existence condition.
    pub condition: SetCondition,
    /// Preserve an existing expiration when replacing a value.
    pub keep_ttl: bool,
}

/// Outcome of a Redis-style SET operation.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SetResult {
    /// The value was stored.
    Applied,
    /// NX or XX prevented the write.
    ConditionNotMet,
}

/// Position used by LINSERT.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ListInsertPosition {
    /// Insert before the pivot.
    Before,
    /// Insert after the pivot.
    After,
}

/// List end used by LMOVE.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ListDirection {
    /// The head of a list.
    Left,
    /// The tail of a list.
    Right,
}

/// Arguments for LINSERT.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ListInsertRequest {
    /// List key.
    pub key: String,
    /// Insert before or after the pivot.
    pub position: ListInsertPosition,
    /// Existing pivot value.
    pub pivot: Bytes,
    /// Value to insert.
    pub value: Bytes,
}

/// Arguments for LMOVE.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ListMoveRequest {
    /// Source list key.
    pub source: String,
    /// Destination list key.
    pub destination: String,
    /// Source list end.
    pub source_direction: ListDirection,
    /// Destination list end.
    pub destination_direction: ListDirection,
}
