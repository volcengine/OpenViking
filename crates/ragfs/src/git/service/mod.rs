//! `GitService` - high-level integration tying together object/ref stores,
//! VFS enumeration, tree building, and commit-object construction.
//!
//! See design §8.1 for the `commit()` algorithm. Fast Path 1 (persistent
//! stat cache `commit_index.bin`) is wired through an optional
//! `IndexStore`: when present and the cached snapshot's `parent_oid` matches
//! the current branch HEAD, files whose `(size, mtime_ns)` match the cached
//! entry skip the read+SHA-1 step and reuse the cached blob OID. Fast Path 3
//! (`exists()` dedup before blob write) is implemented in the slow path: after
//! a blob's oid is computed, an `exists()` precheck skips the zlib compression
//! and `put` when the object is already present. It is a pure performance
//! optimization (`write_object` is idempotent) and can be toggled off via
//! [`GitService::with_blob_exists_precheck`].

use std::collections::{HashMap, HashSet};
use std::sync::Arc;
use std::sync::Mutex;
use std::time::UNIX_EPOCH;

use gix_hash::ObjectId;
use tracing::warn;

use crate::core::filesystem::FileSystem;
use crate::core::types::FileInfo;
use crate::core::{FsContextInner, FsContextView, PathLockContext, FS_CTX};
use crate::git::{
    error::{GitError, ObjectStoreError, RefStoreError},
    ignore::{should_track_path, IgnoreMatcher, OVGITIGNORE_PATH},
    index_store::{CommitIndex, IndexStore},
    object_store::ObjectStore,
    ref_store::RefStore,
    types::{
        CommitRequest, CommitResponse, IndexEntry, LogEntry, LogRequest, RestoreRequest,
        RestoreResponse, ShowRequest, ShowResponse,
    },
};

mod commit;
mod diff;
mod log;
mod restore;
mod snapshot_log;
mod show;
mod validate;

use diff::compute_subtree_diff;
#[cfg(test)]
use log::normalize_log_paths;
use validate::{validate_account_id, validate_project_dir, validate_relative_path};

const MAX_LOG_COMMITS_SCANNED: usize = 1_000;
const MAX_LOG_FILTER_PATHS: usize = 32;
const MAX_LOG_PATH_DEPTH: usize = 64;

/// `GitService` orchestrates the full commit pipeline against a `FileSystem`
/// (the working tree), an `ObjectStore`, and a `RefStore`. An optional
/// `IndexStore` enables Fast Path 1 — the persistent stat cache that lets
/// `commit()` skip read+SHA-1 for files whose `(size, mtime_ns)` are
/// unchanged since the last commit.
pub struct GitService {
    /// Working-tree filesystem rooted at `/local/{account}`.
    pub vfs: Arc<dyn FileSystem>,
    /// Backing object store (loose Git objects, content-addressed).
    pub object_store: Arc<dyn ObjectStore>,
    /// Backing ref store (branch heads).
    pub ref_store: Arc<dyn RefStore>,
    /// Optional Fast Path 1 stat cache. `None` disables the optimization
    /// — every commit then reads and SHA-1s every candidate file.
    pub index_store: Option<Arc<dyn IndexStore>>,
    /// Fast Path 3 toggle: when `true` (default), the slow path runs an
    /// `exists()` precheck before compressing and putting a blob. Disable to
    /// fall back to an unconditional `write_object`.
    pub blob_exists_precheck: bool,
    /// Optional SnapshotLog provider for filtered history acceleration.
    pub snapshot_log_store: Option<Arc<dyn crate::git::snapshot_log::SnapshotLogStore>>,
    /// In-process singleflight guard for SnapshotLog backfill tasks.
    snapshot_log_backfills: Arc<Mutex<HashSet<(String, String)>>>,
}

impl GitService {
    /// Build a service without Fast Path 1. Equivalent to
    /// [`GitService::with_index`] passing `None`.
    pub fn new(
        vfs: Arc<dyn FileSystem>,
        object_store: Arc<dyn ObjectStore>,
        ref_store: Arc<dyn RefStore>,
    ) -> Self {
        Self {
            vfs,
            object_store,
            ref_store,
            index_store: None,
            blob_exists_precheck: true,
            snapshot_log_store: None,
            snapshot_log_backfills: Arc::new(Mutex::new(HashSet::new())),
        }
    }

    /// Build a service with an optional [`IndexStore`] backing Fast Path 1.
    /// Pass `Some(...)` to enable the stat cache, `None` for parity with
    /// [`GitService::new`].
    pub fn with_index(
        vfs: Arc<dyn FileSystem>,
        object_store: Arc<dyn ObjectStore>,
        ref_store: Arc<dyn RefStore>,
        index_store: Option<Arc<dyn IndexStore>>,
    ) -> Self {
        Self {
            vfs,
            object_store,
            ref_store,
            index_store,
            blob_exists_precheck: true,
            snapshot_log_store: None,
            snapshot_log_backfills: Arc::new(Mutex::new(HashSet::new())),
        }
    }

    /// Toggle Fast Path 3 (`exists()` precheck before blob write). Defaults to
    /// enabled; pass `false` to force an unconditional `write_object` on the
    /// slow path.
    pub fn with_blob_exists_precheck(mut self, enabled: bool) -> Self {
        self.blob_exists_precheck = enabled;
        self
    }

    /// Attach an optional SnapshotLog store for filtered log acceleration.
    pub fn with_snapshot_log(
        mut self,
        store: Option<Arc<dyn crate::git::snapshot_log::SnapshotLogStore>>,
    ) -> Self {
        self.snapshot_log_store = store;
        self
    }
}

/// Resolve `target_ref` to a commit OID.
///
/// Accepts:
///   1. 40-hex commit OID (validated by `ObjectId::from_hex`)
///   2. Abbreviated OID (4–39 hex chars) — resolved by listing refs and
///      walking parent chains; returns `OidPrefixNotFound` or `AmbiguousOid`
///      on zero / multiple matches
///   3. Full ref path beginning with `refs/` (passed through `validate_ref_name`,
///      then read from `ref_store`)
///   4. Short branch name (e.g. "main") — auto-prefixed to `refs/heads/{name}`,
///      validated, then read from `ref_store`
///
/// Returns `RefStoreError::NotFound` (wrapped) if the ref doesn't exist;
/// `GitError::Other` if `target_ref` is neither a valid OID nor a valid ref name.
///
/// Note: a 40-char hex string is always interpreted as an OID, even if it
/// happens to also be a valid branch name (e.g. `deadbeefdeadbeef...`).
/// To disambiguate such a branch, pass the full ref path `refs/heads/<name>`.
async fn resolve_ref(
    ref_store: &dyn RefStore,
    object_store: &dyn ObjectStore,
    account: &str,
    target_ref: &str,
) -> Result<ObjectId, GitError> {
    // 1. 40-hex commit OID — ASCII hex (case-insensitive), exactly len 40.
    if target_ref.len() == 40 && target_ref.bytes().all(|b| b.is_ascii_hexdigit()) {
        return ObjectId::from_hex(target_ref.as_bytes())
            .map_err(|e| GitError::Other(format!("invalid oid {target_ref}: {e}")));
    }

    // 2. Abbreviated OID (4–39 hex chars) — list refs and walk parent chains.
    if target_ref.len() >= 4 && target_ref.bytes().all(|b| b.is_ascii_hexdigit()) {
        return resolve_abbreviated_oid(ref_store, object_store, account, target_ref).await;
    }

    // 3 & 4. Normalize to full ref path then read.
    let full = if target_ref.starts_with("refs/") {
        target_ref.to_string()
    } else {
        format!("refs/heads/{target_ref}")
    };
    crate::git::util::validate_ref_name(&full)?;
    Ok(ref_store.read(account, &full).await?)
}

/// Decoded commit metadata used by `commit()` (just the tree) and `show()`
/// (full set). Owned so callers don't have to juggle the raw buffer.
struct CommitMeta {
    tree: ObjectId,
    parents: Vec<ObjectId>,
    author: crate::git::types::Actor,
    committer: crate::git::types::Actor,
    message: String,
}

/// Read a commit object and return its decoded metadata.
async fn load_commit_meta(
    store: &dyn ObjectStore,
    account: &str,
    commit_oid: &ObjectId,
) -> Result<CommitMeta, GitError> {
    let raw = crate::git::util::read_object(store, account, commit_oid).await?;
    let (kind, _, hdr) = crate::git::util::parse_object_header(&raw)?;
    if kind != gix_object::Kind::Commit {
        return Err(GitError::Other(format!(
            "expected commit object, got {kind:?}"
        )));
    }
    let parsed = gix_object::CommitRef::from_bytes(&raw[hdr..])
        .map_err(|e| GitError::Other(format!("commit decode: {e}")))?;
    Ok(CommitMeta {
        tree: parsed.tree(),
        parents: parsed.parents().collect(),
        author: actor_from_signature_ref(&parsed.author),
        committer: actor_from_signature_ref(&parsed.committer),
        message: parsed.message.to_string(),
    })
}

/// Resolve an abbreviated commit OID (4–39 hex chars) by walking the parent
/// chains from every ref tip in the account. The traversal is bounded by
/// `MAX_OID_RESOLVE_VISITED` to keep degenerate histories from running away.
///
/// Returns:
/// - `Ok(oid)` if exactly one commit's hex starts with `prefix`.
/// - `Err(GitError::OidPrefixNotFound)` if no commit matches.
/// - `Err(GitError::AmbiguousOid)` if 2+ commits match (lists up to 5 candidates).
///
/// Lowercases `prefix` before comparison; the input is already known to be
/// ASCII hex by the caller.
async fn resolve_abbreviated_oid(
    ref_store: &dyn RefStore,
    object_store: &dyn ObjectStore,
    account: &str,
    prefix: &str,
) -> Result<ObjectId, GitError> {
    use std::collections::HashSet;

    const MAX_OID_RESOLVE_VISITED: usize = 50_000;
    const MAX_REPORTED_CANDIDATES: usize = 5;

    let prefix_lc = prefix.to_ascii_lowercase();

    let refs = ref_store.list(account, "refs/").await?;
    let mut visited: HashSet<ObjectId> = HashSet::new();
    let mut queue: Vec<ObjectId> = refs.into_iter().map(|(_, oid)| oid).collect();
    let mut matches: Vec<ObjectId> = Vec::new();

    while let Some(oid) = queue.pop() {
        if !visited.insert(oid) {
            continue;
        }
        if visited.len() > MAX_OID_RESOLVE_VISITED {
            return Err(GitError::Other(format!(
                "OID prefix resolution aborted: scanned over {MAX_OID_RESOLVE_VISITED} commits without converging"
            )));
        }
        if oid.to_hex().to_string().starts_with(&prefix_lc) {
            matches.push(oid);
            if matches.len() > MAX_REPORTED_CANDIDATES {
                // Continue scanning a little longer to give a useful error,
                // but we already know it's ambiguous.
                break;
            }
        }
        let meta = match load_commit_meta(object_store, account, &oid).await {
            Ok(m) => m,
            Err(GitError::ObjectStore(ObjectStoreError::NotFound(_))) => continue,
            Err(GitError::Other(_)) => continue, // not a commit (tag etc.) — skip
            Err(e) => return Err(e),
        };
        for p in meta.parents {
            if !visited.contains(&p) {
                queue.push(p);
            }
        }
    }

    match matches.len() {
        0 => Err(GitError::OidPrefixNotFound {
            prefix: prefix.to_string(),
        }),
        1 => Ok(matches.into_iter().next().unwrap()),
        n => {
            let listed: Vec<String> = matches
                .iter()
                .take(MAX_REPORTED_CANDIDATES)
                .map(|o| o.to_hex().to_string())
                .collect();
            Err(GitError::AmbiguousOid {
                prefix: prefix.to_string(),
                count: n,
                candidates: listed.join(", "),
            })
        }
    }
}

/// Project a borrowed `gix_actor::SignatureRef` into our owned `Actor` DTO.
///
/// gix-actor 0.31.5 fields used: `SignatureRef.name: &BStr`, `.email: &BStr`,
/// `.time: gix_date::Time` (not the raw `&str` of later versions). `Time`
/// provides `.seconds: i64` and `.offset: i32`.
// TODO: gix_date::Time.sign dropped — Actor not roundtrip-safe for "-0000"
fn actor_from_signature_ref(sig: &gix_actor::SignatureRef<'_>) -> crate::git::types::Actor {
    crate::git::types::Actor {
        name: sig.name.to_string(),
        email: sig.email.to_string(),
        time_seconds: sig.time.seconds,
        tz_offset_seconds: sig.time.offset,
    }
}


#[cfg(test)]
mod tests {
    include!("../tests/common.rs");
    include!("../tests/test_commit.rs");
    include!("../tests/test_show.rs");
    include!("../tests/test_log.rs");
    include!("../tests/test_snapshot_log.rs");
    include!("../tests/test_restore.rs");
}

#[cfg(test)]
mod fast_path1_tests {
    include!("../tests/test_fast_path1.rs");
}
