//! Persistent commit index ("Fast Path 1" stat cache).
//!
//! After each successful commit, `GitService::commit` saves a snapshot of
//! `(path -> size, mtime_ns, oid)` for every file that was part of the
//! resulting tree. The next commit can then skip the read+SHA-1+write path
//! for any file whose `(size, mtime_ns)` match the cached entry — saving
//! the expensive blob materialization that produces the same OID we already
//! have.
//!
//! Correctness guard: every saved index records its `parent_oid` (the commit
//! the index reflects). On load, if `parent_oid != prev_head` (concurrent
//! commit, branch switch, first run) the cache is silently discarded and
//! commit proceeds via the slow path. Cache misses are *always* a soft
//! failure — we never produce an incorrect commit because of a stale or
//! corrupt index.
//!
//! Racy-clean guard: `(size, mtime_ns)` alone cannot distinguish "unchanged"
//! from "changed to the same size within the same filesystem clock tick as
//! the last commit". To close that window (the classic git "racy clean"
//! problem) the store records *when the index was written* in
//! [`CommitIndex::saved_at_ns`] — populated at `load()` time from the index
//! file's own mtime (local) or object `last_modified` (S3), i.e. the same
//! clock and granularity as the working-tree file mtimes it is compared
//! against. A cache entry is only trusted when its `mtime_ns` is strictly
//! older than `saved_at_ns`; an entry whose mtime is in the same tick as (or
//! newer than) the index write is "racy" and forced down the slow path. This
//! costs at most one extra read per file, and only for files that shared a
//! clock tick with a commit — steady-state Fast Path 1 is unaffected.
//!
//! The wire format is JSON for debuggability. OIDs are stored as 40-char hex.
//! Any deserialization or backend error is mapped to `Ok(None)` on `load()`,
//! and `save()` failures are logged by the caller (the commit itself has
//! already succeeded by the time we save).
//!
//! Per-(account, branch). The branch component is `validate_ref_name`-checked
//! before any path is constructed, so attempts at path traversal via crafted
//! branch names are rejected.

use std::collections::HashMap;
use std::time::SystemTime;

use async_trait::async_trait;
use gix_hash::ObjectId;
use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::git::types::IndexEntry;

/// Convert a [`SystemTime`] to nanoseconds since the Unix epoch, matching the
/// `i128` representation used for `IndexEntry::mtime_ns`. Returns `None` for
/// pre-epoch times (which the racy-clean guard treats conservatively). Shared
/// by the `IndexStore` backends to stamp [`CommitIndex::saved_at_ns`] from the
/// index file's own modification time on load.
pub fn systemtime_to_ns(t: SystemTime) -> Option<i128> {
    t.duration_since(SystemTime::UNIX_EPOCH)
        .ok()
        .map(|d| d.as_nanos() as i128)
}

/// Snapshot of the working tree's `(size, mtime_ns, oid)` after the commit
/// identified by `parent_oid`.
///
/// `entries` is keyed by account-relative path (same form as `CommitRequest::paths`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommitIndex {
    /// Commit OID this snapshot reflects. Used to invalidate the cache when
    /// the branch head has moved (concurrent commit, branch switch, etc.).
    pub parent_oid: ObjectId,
    /// Account-relative path → cached `(size, mtime_ns, oid)`.
    pub entries: HashMap<String, IndexEntry>,
    /// Wall-clock time (ns since epoch) the index file was written, as
    /// observed from the backend's own metadata at `load()` time. `None` for
    /// freshly-built (not-yet-persisted) indexes and when the backend cannot
    /// report a modification time. Used for the racy-clean guard: a cache
    /// entry whose `mtime_ns >= saved_at_ns` may have been modified within the
    /// same clock tick as the last commit and is forced down the slow path.
    /// Not part of the on-disk wire format — it is derived from the file's own
    /// timestamp on load.
    pub saved_at_ns: Option<i128>,
}

/// Error type returned by [`IndexStore`] operations. All error variants are
/// soft failures from the caller's perspective — `load()` callers map them to
/// `Ok(None)` and `save()` callers log and continue.
#[derive(Debug, Error)]
pub enum IndexStoreError {
    /// Underlying I/O error from the backend (filesystem, network, etc.).
    #[error("i/o error: {0}")]
    Io(#[from] std::io::Error),
    /// On-disk format could not be parsed (corruption, version skew, bad oid).
    #[error("decode error: {0}")]
    Decode(String),
    /// Branch component failed `validate_ref_name` (path-traversal guard).
    #[error("invalid branch name: {0}")]
    InvalidBranch(String),
    /// Non-I/O backend failure (e.g. S3 SDK error).
    #[error("backend error: {0}")]
    Backend(String),
}

/// Per-(account, branch) commit-index storage.
///
/// `load` returns `Ok(None)` for any "no usable index here" outcome — missing
/// file, decode failure, version mismatch, etc. The caller treats every miss
/// as "skip Fast Path 1, fall back to the full read/hash path".
///
/// `save` is fire-and-forget from the caller's perspective: the commit has
/// already succeeded; the worst-case cost of a save failure is one extra
/// slow-path commit next time.
#[async_trait]
pub trait IndexStore: Send + Sync + 'static {
    /// Load the latest persisted index for `(account, branch)`. Returns
    /// `Ok(None)` if no index has been written yet, or if the persisted bytes
    /// fail to decode (treated as a soft miss so commit falls back to the
    /// slow path).
    async fn load(
        &self,
        account: &str,
        branch: &str,
    ) -> Result<Option<CommitIndex>, IndexStoreError>;

    /// Persist `index` for `(account, branch)`, replacing any prior snapshot.
    /// Last-write-wins semantics — there is no CAS because the index is a
    /// soft-state cache; correctness is enforced at load time via the
    /// `parent_oid` check.
    async fn save(
        &self,
        account: &str,
        branch: &str,
        index: &CommitIndex,
    ) -> Result<(), IndexStoreError>;
}

// ---- Wire format ---------------------------------------------------------

/// Bumped only when a backwards-incompatible change to the layout ships.
/// Older readers see an unknown version and treat the file as absent.
const INDEX_FORMAT_VERSION: u32 = 1;

#[derive(Debug, Serialize, Deserialize)]
struct WireIndex {
    version: u32,
    parent_oid: String,
    entries: Vec<WireEntry>,
}

#[derive(Debug, Serialize, Deserialize)]
struct WireEntry {
    path: String,
    size: u64,
    mtime_ns: i128,
    oid: String,
}

/// Serialize a `CommitIndex` into the on-disk JSON wire format.
pub fn encode_index(index: &CommitIndex) -> Result<Vec<u8>, IndexStoreError> {
    let mut entries: Vec<WireEntry> = index
        .entries
        .iter()
        .map(|(path, e)| WireEntry {
            path: path.clone(),
            size: e.size,
            mtime_ns: e.mtime_ns,
            oid: e.oid.to_hex().to_string(),
        })
        .collect();
    // Sorted output → deterministic byte content for tests / hashing.
    entries.sort_by(|a, b| a.path.cmp(&b.path));
    let wire = WireIndex {
        version: INDEX_FORMAT_VERSION,
        parent_oid: index.parent_oid.to_hex().to_string(),
        entries,
    };
    serde_json::to_vec(&wire).map_err(|e| IndexStoreError::Decode(e.to_string()))
}

/// Decode the on-disk JSON wire format. An unknown `version` field returns
/// `Ok(None)`; malformed JSON or invalid OIDs return `Err(Decode)` (which
/// the trait `load()` callers also map to `None`).
pub fn decode_index(bytes: &[u8]) -> Result<Option<CommitIndex>, IndexStoreError> {
    let wire: WireIndex = match serde_json::from_slice(bytes) {
        Ok(w) => w,
        Err(e) => return Err(IndexStoreError::Decode(e.to_string())),
    };
    if wire.version != INDEX_FORMAT_VERSION {
        // Forward-compat: unknown version → silently treat as missing.
        return Ok(None);
    }
    let parent_oid = ObjectId::from_hex(wire.parent_oid.as_bytes())
        .map_err(|e| IndexStoreError::Decode(format!("parent_oid: {e}")))?;
    let mut entries = HashMap::with_capacity(wire.entries.len());
    for w in wire.entries {
        let oid = ObjectId::from_hex(w.oid.as_bytes())
            .map_err(|e| IndexStoreError::Decode(format!("entry oid {}: {e}", w.path)))?;
        entries.insert(
            w.path,
            IndexEntry {
                size: w.size,
                mtime_ns: w.mtime_ns,
                oid,
            },
        );
    }
    Ok(Some(CommitIndex {
        parent_oid,
        entries,
        // The wire format carries no timestamp; callers that need the
        // racy-clean guard populate `saved_at_ns` from the backing file's own
        // mtime after a successful decode.
        saved_at_ns: None,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn oid_from_byte(b: u8) -> ObjectId {
        let mut bytes = [0u8; 20];
        bytes.fill(b);
        ObjectId::from_bytes_or_panic(&bytes)
    }

    #[test]
    fn round_trip_preserves_entries() {
        let mut entries = HashMap::new();
        entries.insert(
            "resources/a.md".into(),
            IndexEntry {
                size: 42,
                mtime_ns: 1_700_000_000_000_000_000,
                oid: oid_from_byte(0xAA),
            },
        );
        entries.insert(
            "agent/b.py".into(),
            IndexEntry {
                size: 7,
                mtime_ns: -1,
                oid: oid_from_byte(0xBB),
            },
        );
        let idx = CommitIndex {
            parent_oid: oid_from_byte(0xCC),
            entries,
            saved_at_ns: None,
        };
        let bytes = encode_index(&idx).unwrap();
        let decoded = decode_index(&bytes).unwrap().unwrap();
        assert_eq!(decoded, idx);
    }

    #[test]
    fn unknown_version_is_silent_miss() {
        let bogus = serde_json::json!({
            "version": 9999,
            "parent_oid": format!("{:040}", 0),
            "entries": []
        });
        let bytes = serde_json::to_vec(&bogus).unwrap();
        assert!(decode_index(&bytes).unwrap().is_none());
    }

    #[test]
    fn malformed_json_errors() {
        assert!(decode_index(b"not-json").is_err());
    }

    #[test]
    fn invalid_oid_errors() {
        let bogus = serde_json::json!({
            "version": INDEX_FORMAT_VERSION,
            "parent_oid": "zzzz",
            "entries": []
        });
        let bytes = serde_json::to_vec(&bogus).unwrap();
        assert!(decode_index(&bytes).is_err());
    }
}
