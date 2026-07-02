//! DTOs for the Git service API.

use gix_hash::ObjectId;

#[derive(Debug, Clone)]
pub struct CommitRequest {
    pub account: String,
    pub branch: String,                 // e.g. "main" — NOT the full "refs/heads/main"
    pub message: String,
    /// Explicit candidate paths (account-relative, e.g. "resources/a.md").
    /// `None` means "enumerate the whole account tree".
    pub paths: Option<Vec<String>>,
    pub author_name: String,
    pub author_email: String,
}

#[derive(Debug, Clone)]
pub enum CommitResponse {
    Created {
        commit_oid: ObjectId,
        changed: usize,
        /// Number of candidate paths skipped by user `.ovgitignore` rules.
        /// Existing hardcoded system pruning is not included.
        ignored: usize,
    },
    /// No path produced an editor change; ref untouched. `commit_oid` is the
    /// existing HEAD (or `ObjectId::null` if the branch did not exist).
    Noop {
        commit_oid: ObjectId,
        /// Number of candidate paths skipped by user `.ovgitignore` rules.
        ignored: usize,
    },
}

/// Per-path stat cache entry. Not persisted yet (Fast Path 1 is deferred),
/// but the type lives here so later work can fill in the index.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IndexEntry {
    pub size: u64,
    pub mtime_ns: i128,
    pub oid: ObjectId,
}

#[derive(Debug, Clone)]
pub struct ShowRequest {
    pub account: String,
    /// One of: 40-hex commit OID, short branch name ("main"),
    /// or full ref path ("refs/heads/main", "refs/tags/v1").
    pub target_ref: String,
    /// If `Some(path)`, return that path's blob bytes from the commit's tree.
    /// If `None`, return the commit's metadata.
    /// `path` is account-relative tree path, e.g. "resources/a.md".
    pub path: Option<String>,
}

#[derive(Debug, Clone)]
pub enum ShowResponse {
    Commit {
        oid: ObjectId,
        tree: ObjectId,
        parents: Vec<ObjectId>,
        author: Actor,
        committer: Actor,
        message: String,
    },
    Blob {
        oid: ObjectId,
        size: u64,
        /// Zero-copy slice over the decompressed object buffer (header + payload).
        /// Cloning is `Arc::clone` — cheap; cloning a `Vec` of the same size is not.
        /// The few bytes of the loose-object header upstream of the payload remain
        /// alive in the backing buffer until the last `Bytes` handle is dropped;
        /// negligible compared to the payload itself.
        bytes: bytes::Bytes,
    },
}

/// Owned, Python-friendly projection of `gix_actor::SignatureRef`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Actor {
    pub name: String,
    pub email: String,
    /// Seconds since UNIX epoch.
    pub time_seconds: i64,
    /// Timezone offset in seconds (e.g. +08:00 → 28800).
    pub tz_offset_seconds: i32,
}

/// Input for `GitService::restore`.
#[derive(Debug, Clone)]
pub struct RestoreRequest {
    /// Account this restore applies to.
    pub account: String,
    /// Branch whose HEAD is the parent of the new commit. Defaults to "main"
    /// in callers; this DTO requires the caller to pass it explicitly to
    /// avoid invisible defaults at this layer.
    pub branch: String,
    /// Optional account-relative subtree path to restore, e.g. "resources/proj_a".
    /// `None` restores the whole account tree.
    pub project_dir: Option<String>,
    /// What to restore from. Same resolution rules as `ShowRequest::target_ref`:
    /// 40-hex commit OID / short branch name / full `refs/heads/xxx`.
    pub source_commit: String,
    /// If `true`, compute and return the diff but write nothing — no VFS
    /// writes, no new objects in the object store, no ref update.
    pub dry_run: bool,
    /// Commit message for the new commit. If `None`, a default is generated:
    /// `"restore {project_dir} from {source_oid_short}"`.
    pub message: Option<String>,
    pub author_name: String,
    pub author_email: String,
}

/// Structured diff between two subtrees, computed by `restore`.
///
/// All paths in this struct are **relative to `project_dir`** — they are NOT
/// prefixed. Callers (e.g. a future Python wrapper) prefix them when needed
/// for display.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RestoreDiff {
    /// Paths whose content in `source_subtree` should be written into the VFS
    /// (creates or overwrites). Carries the blob oid to fetch from the
    /// object store.
    pub to_write: Vec<(String, ObjectId)>,
    /// Paths present in `head_subtree` but absent in `source_subtree`. Must
    /// be removed from the VFS.
    pub to_delete: Vec<String>,
    /// Paths whose oid is identical in both subtrees. Listed only for
    /// reporting; restore does not touch them.
    pub unchanged: Vec<String>,
}

/// Output of `GitService::restore`.
#[derive(Debug, Clone)]
pub enum RestoreResponse {
    /// A new commit was created and the branch ref now points at it.
    Applied {
        /// The new commit's OID — branch HEAD now points here.
        new_commit_oid: ObjectId,
        /// The source commit (after `resolve_ref`) we restored from.
        source_commit: ObjectId,
        /// Previous HEAD oid (parent of `new_commit_oid`).
        parent_commit: ObjectId,
        /// Number of files written through the VFS.
        written: usize,
        /// Number of files deleted through the VFS.
        deleted: usize,
        /// Number of files left untouched because source/head agreed.
        unchanged: usize,
        /// Account-relative paths (prefixed with `project_dir`) that were
        /// written to the VFS. Lets callers trigger downstream side effects
        /// (vector index rebuild, watcher notifications) without re-walking
        /// the tree.
        written_paths: Vec<String>,
        /// Account-relative paths that were removed from the VFS.
        deleted_paths: Vec<String>,
    },
    /// Source subtree byte-equal to head subtree — nothing to do. No new
    /// commit was created; the branch ref is unchanged.
    Noop {
        /// Current HEAD oid (unchanged).
        head: ObjectId,
        /// Source commit oid (after `resolve_ref`).
        source: ObjectId,
    },
    /// `dry_run = true` request — returns the computed diff without
    /// performing any writes.
    DryRun {
        /// The computed diff (paths are relative to `project_dir`).
        diff: RestoreDiff,
        /// Current HEAD oid (would-be parent if applied).
        head: ObjectId,
        /// Source commit oid (after `resolve_ref`).
        source: ObjectId,
    },
}

/// Restore reached the ref-swap step (`new_commit_oid` is now branch HEAD)
/// but at least one per-path write or delete on the VFS failed. The caller
/// must treat this as "HEAD advanced, working tree partial":
/// `written_paths` / `deleted_paths` list paths that *did* reach the VFS
/// and therefore still need reindex; `failed_writes` / `failed_deletes`
/// list the per-path failures that need follow-up.
///
/// Boxed inside `GitError::RestoreWritebackPartial` to keep the enum size
/// bounded — the two path lists can be large on big restores.
#[derive(Debug, Clone)]
pub struct RestoreWritebackPartial {
    pub new_commit_oid: ObjectId,
    pub source_commit: ObjectId,
    pub parent_commit: ObjectId,
    /// Files that *did* reach the VFS (subset of the original plan).
    pub written: usize,
    /// Files that *were* deleted from the VFS (or were idempotently already gone).
    pub deleted: usize,
    /// Files left untouched because source/head agreed.
    pub unchanged: usize,
    /// Account-relative paths whose blob bytes reached the VFS.
    pub written_paths: Vec<String>,
    /// Account-relative paths that were removed from (or already absent from) the VFS.
    pub deleted_paths: Vec<String>,
    /// `(account-relative path, error message)` for writes that failed
    /// after the ref already advanced.
    pub failed_writes: Vec<(String, String)>,
    /// `(account-relative path, error message)` for deletes that failed
    /// with a non-`NotFound` error after the ref already advanced.
    pub failed_deletes: Vec<(String, String)>,
}
