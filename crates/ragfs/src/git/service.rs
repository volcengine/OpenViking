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
use std::time::UNIX_EPOCH;

use gix_hash::ObjectId;
use tracing::warn;

use crate::core::filesystem::FileSystem;
use crate::core::types::FileInfo;
use crate::git::{
    error::{GitError, ObjectStoreError, RefStoreError},
    index_store::{CommitIndex, IndexStore},
    object_store::ObjectStore,
    ref_store::RefStore,
    types::{
        CommitRequest, CommitResponse, IndexEntry, RestoreRequest, RestoreResponse, ShowRequest,
        ShowResponse,
    },
};

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
        }
    }

    /// Toggle Fast Path 3 (`exists()` precheck before blob write). Defaults to
    /// enabled; pass `false` to force an unconditional `write_object` on the
    /// slow path.
    pub fn with_blob_exists_precheck(mut self, enabled: bool) -> Self {
        self.blob_exists_precheck = enabled;
        self
    }

    /// Build a new commit on `branch` reflecting the current state of the
    /// account's VFS subtree.
    ///
    /// - If `paths` is `Some`, only those account-relative paths are
    ///   considered (each is still pruned via `enumerate::prune_path`).
    /// - If `paths` is `None`, the full `/local/{account}` subtree is
    ///   enumerated via `enumerate::collect_all`.
    ///
    /// On no-op (no editor change) the branch ref is untouched and
    /// `CommitResponse::Noop` is returned.
    ///
    /// When `paths` is `Some(...)`, every listed path must refer to a file in
    /// the VFS. To commit a subtree, list each file explicitly or omit `paths`
    /// for full enumeration.
    ///
    /// On a CAS conflict, returns `GitError::ConcurrentCommit` so the
    /// caller can decide whether to retry. There is intentionally no
    /// retry loop inside `commit()`.
    ///
    /// Fast Path 1: when an [`IndexStore`] is configured and the cached
    /// snapshot's `parent_oid` matches `prev_head`, candidates whose
    /// `(size, mtime_ns)` match the cached entry skip read+SHA-1 and reuse
    /// the cached blob OID. Any cache miss / parent mismatch / decode
    /// error silently falls back to the slow path; a stale or corrupt
    /// index can never produce an incorrect commit.
    pub async fn commit(&self, req: CommitRequest) -> Result<CommitResponse, GitError> {
        let CommitRequest {
            account,
            branch,
            message,
            paths,
            author_name,
            author_email,
        } = req;
        validate_account_id(&account)?;
        if let Some(ps) = &paths {
            for p in ps {
                validate_relative_path(p)?;
            }
        }
        let ref_name = format!("refs/heads/{branch}");

        // 1. Resolve current HEAD (may not exist → root commit).
        let prev_head: Option<ObjectId> = match self.ref_store.read(&account, &ref_name).await {
            Ok(oid) => Some(oid),
            Err(RefStoreError::NotFound(_)) => None,
            Err(e) => return Err(e.into()),
        };
        let prev_tree: Option<ObjectId> = match prev_head {
            Some(commit_oid) => Some(
                load_commit_meta(self.object_store.as_ref(), &account, &commit_oid)
                    .await?
                    .tree,
            ),
            None => None,
        };

        // 1b. Fast Path 1: load the persisted commit index for this branch
        //     and verify its `parent_oid` matches `prev_head`. Any mismatch /
        //     missing file / decode error → no cache for this commit.
        let prev_index: Option<CommitIndex> = match (&self.index_store, prev_head) {
            (Some(store), Some(head)) => match store.load(&account, &branch).await {
                Ok(Some(idx)) if idx.parent_oid == head => Some(idx),
                Ok(_) => None,
                Err(e) => {
                    warn!(
                        "commit index load failed for {account}/{branch}: {e}; \
                           falling back to slow path"
                    );
                    None
                }
            },
            _ => None,
        };
        // Track whether Fast Path 1 is even potentially live: if disabled
        // (`index_store` is None), we skip the new-index build below.
        let fast_path_active = self.index_store.is_some() && prev_index.is_some();

        // 2. Build TreeEditor from prev tree if any; otherwise start empty.
        //    (The well-known empty-tree oid is not guaranteed to exist in the
        //    store, so we cannot blindly hand it to `from_tree`.)
        let mut editor = match prev_tree {
            Some(t) => {
                crate::git::tree_builder::TreeEditor::from_tree(
                    self.object_store.as_ref(),
                    &account,
                    t,
                )
                .await?
            }
            None => crate::git::tree_builder::TreeEditor::empty(),
        };

        // 2.5. Explicit paths: classify each entry as File / Directory /
        //      NotFound via a single VFS stat, and assemble three locals:
        //
        //      * `candidates` - the deduped set of files this commit will
        //        process. Directories contribute their recursive listing
        //        (pruned) plus any prev_tree paths under the same prefix
        //        (so deletions inside the directory surface as a remove).
        //        A NotFound entry behaves the same way for any prev_tree
        //        paths under its prefix, treating it as "delete whatever
        //        used to live here".
        //      * `cleanup_exact` - keys to drop from the new index seed
        //        before the main loop re-fills them.
        //      * `cleanup_prefixes` - directory-style prefixes ("docs/")
        //        whose contents in the new index seed must also be dropped.
        //
        //      Pruning is applied uniformly: explicit files, expanded
        //      directory contents, and prev_tree-derived entries all run
        //      through `prune_path`. A path supplied by the caller that is
        //      pruned is silently dropped (no error, no warn).
        let mut cleanup_exact: HashSet<String> = HashSet::new();
        let mut cleanup_prefixes: Vec<String> = Vec::new();

        // Lazily flatten prev_tree once if any explicit path requires it.
        let mut prev_paths_cache: Option<Vec<(String, ObjectId)>> = None;

        let candidates: Vec<String> = match &paths {
            Some(ps) => {
                let mut set: std::collections::BTreeSet<String> = std::collections::BTreeSet::new();

                for p in ps {
                    let abs = format!("/local/{}/{}", account, p);
                    match self.vfs.stat(&abs).await {
                        Ok(info) if info.is_dir => {
                            // Directory: recursive listing + prev_tree subtree.
                            cleanup_prefixes.push(format!("{}/", p));

                            let listed =
                                crate::git::enumerate::collect_under(&self.vfs, &account, p)
                                    .await?;
                            for rel in listed {
                                set.insert(rel);
                            }

                            if let Some(t) = prev_tree {
                                if prev_paths_cache.is_none() {
                                    prev_paths_cache = Some(
                                        crate::git::tree_builder::flatten(
                                            self.object_store.as_ref(),
                                            &account,
                                            t,
                                            &None,
                                        )
                                        .await?,
                                    );
                                }
                                let pref = format!("{}/", p);
                                for (path, _) in prev_paths_cache.as_ref().unwrap() {
                                    if path.starts_with(&pref)
                                        && !crate::git::enumerate::prune_path(path)
                                    {
                                        set.insert(path.clone());
                                    }
                                }
                            }
                        }
                        Ok(_) => {
                            // File: take it verbatim, subject to pruning.
                            cleanup_exact.insert(p.clone());
                            if !crate::git::enumerate::prune_path(p) {
                                set.insert(p.clone());
                            }
                        }
                        Err(e) if is_not_found(&e) => {
                            // Neither file nor directory in the VFS. Treat
                            // it as a delete-by-name: feed `p` into the main
                            // loop (where the NotFound branch will remove it
                            // from the tree if it was a file) AND union in
                            // every prev_tree path under "p/" so a missing
                            // directory drops its whole subtree.
                            warn!(
                                "commit path {:?} not found in VFS; \
                                 treating as deletion of any matching subtree",
                                p
                            );
                            cleanup_exact.insert(p.clone());
                            cleanup_prefixes.push(format!("{}/", p));

                            if !crate::git::enumerate::prune_path(p) {
                                set.insert(p.clone());
                            }
                            if let Some(t) = prev_tree {
                                if prev_paths_cache.is_none() {
                                    prev_paths_cache = Some(
                                        crate::git::tree_builder::flatten(
                                            self.object_store.as_ref(),
                                            &account,
                                            t,
                                            &None,
                                        )
                                        .await?,
                                    );
                                }
                                let pref = format!("{}/", p);
                                for (path, _) in prev_paths_cache.as_ref().unwrap() {
                                    if path.starts_with(&pref)
                                        && !crate::git::enumerate::prune_path(path)
                                    {
                                        set.insert(path.clone());
                                    }
                                }
                            }
                        }
                        Err(e) => return Err(e.into()),
                    }
                }

                set.into_iter().collect()
            }
            // Full enumeration: union the files currently on disk with the
            // paths recorded in prev_tree. `collect_all` only sees files that
            // still exist, so a file deleted since the last commit would never
            // become a candidate and its deletion would be silently lost. By
            // adding prev_tree's paths, a path that's gone from disk falls into
            // the `NotFound → remove` branch below and is dropped from the new
            // snapshot. Deduped via BTreeSet so a path present in both sources
            // is only processed once.
            None => {
                let mut set: std::collections::BTreeSet<String> =
                    crate::git::enumerate::collect_all(&self.vfs, &account)
                        .await?
                        .into_iter()
                        .collect();
                if let Some(t) = prev_tree {
                    let prev_paths = crate::git::tree_builder::flatten(
                        self.object_store.as_ref(),
                        &account,
                        t,
                        &None,
                    )
                    .await?;
                    for (p, _) in prev_paths {
                        if !crate::git::enumerate::prune_path(&p) {
                            set.insert(p);
                        }
                    }
                }
                set.into_iter().collect()
            }
        };

        // 3b. Seed the new index. For partial commits (paths=Some), unlisted
        //     paths in the previous index must be preserved verbatim — they
        //     were not enumerated this round, so the cache should keep them.
        //     For full enumeration (paths=None), start empty: only paths seen
        //     this commit end up in the new index.
        let mut new_index_entries: HashMap<String, IndexEntry> =
            match (self.index_store.is_some(), &paths, &prev_index) {
                (true, Some(_), Some(idx)) => idx.entries.clone(),
                (true, _, _) => HashMap::new(),
                _ => HashMap::new(),
            };
        // For partial commits we still need to drop entries for any explicitly
        // listed path before we re-fill it — otherwise a deleted path that
        // was in the old index would linger. Directory entries clean by
        // prefix; file/NotFound entries clean by exact key.
        if paths.is_some() {
            for key in &cleanup_exact {
                new_index_entries.remove(key);
            }
            if !cleanup_prefixes.is_empty() {
                new_index_entries
                    .retain(|k, _| !cleanup_prefixes.iter().any(|pref| k.starts_with(pref)));
            }
        }

        // 4. For each candidate: detect delete vs upsert. Blob writes on the
        //    slow path go through Fast Path 3 (exists precheck) when enabled;
        //    write_object is idempotent regardless.
        //
        //    `prev_lookup_cache` memoises decoded prev_tree subtree contents
        //    keyed on tree OID, so K candidate paths sharing the same depth-D
        //    ancestor chain pay D unique loads instead of K×D — every commit
        //    in the same parent subtree only fetches each ancestor once.
        //    Pre-seeded with the editor's root entries so the first
        //    `lookup_cached` doesn't re-fetch what `from_tree` already decoded.
        let mut prev_lookup_cache = crate::git::tree_builder::TreeLookupCache::new();
        if let Some(t) = prev_tree {
            prev_lookup_cache.seed(t, editor.root.clone());
        }
        // Racy-clean threshold: the time the loaded index was written (from the
        // backend's own metadata). A cached entry is only trustworthy when its
        // `mtime_ns` is strictly older than this — otherwise the file may have
        // been changed (to the same size) within the same clock tick as the
        // last commit, and `(size, mtime_ns)` cannot detect it. `None` (backend
        // could not report a write time) forces every entry down the slow path.
        let index_saved_at_ns: Option<i128> = prev_index.as_ref().and_then(|idx| idx.saved_at_ns);
        let mut changed = 0usize;
        for rel_path in candidates {
            let abs = format!("/local/{}/{}", account, rel_path);
            match self.vfs.stat(&abs).await {
                Ok(info) => {
                    let stat = stat_signature(&info);

                    // Fast Path 1: cached `(size, mtime_ns)` match → reuse oid,
                    // skip vfs.read + write_object. The cached oid was once
                    // written by a successful commit, so it's known good in
                    // the object store. Guarded by the racy-clean check: the
                    // entry's mtime must predate the index write, otherwise a
                    // same-size change in the index's clock tick could slip
                    // through undetected.
                    let cached = prev_index
                        .as_ref()
                        .and_then(|idx| idx.entries.get(&rel_path));
                    let oid = match (cached, stat) {
                        (Some(entry), Some((size, mtime_ns)))
                            if entry.size == size
                                && entry.mtime_ns == mtime_ns
                                && index_saved_at_ns.is_some_and(|saved| mtime_ns < saved) =>
                        {
                            entry.oid
                        }
                        _ => {
                            let bytes = self.vfs.read(&abs, 0, 0).await?;
                            if self.blob_exists_precheck {
                                crate::git::util::write_object_if_absent(
                                    self.object_store.as_ref(),
                                    &account,
                                    gix_object::Kind::Blob,
                                    &bytes,
                                )
                                .await?
                            } else {
                                crate::git::util::write_object(
                                    self.object_store.as_ref(),
                                    &account,
                                    gix_object::Kind::Blob,
                                    &bytes,
                                )
                                .await?
                            }
                        }
                    };

                    // Skip the upsert if prev_tree already has this exact
                    // path+oid — re-writing the same blob is not an editor
                    // change and shouldn't count toward the no-op decision.
                    let prev_entry = match prev_tree {
                        Some(t) => {
                            crate::git::tree_builder::lookup_cached(
                                self.object_store.as_ref(),
                                &account,
                                t,
                                &rel_path,
                                &mut prev_lookup_cache,
                            )
                            .await?
                        }
                        None => None,
                    };
                    if prev_entry.map(|(o, _)| o) != Some(oid) {
                        editor
                            .upsert(self.object_store.as_ref(), &account, &rel_path, oid)
                            .await?;
                        changed += 1;
                    }

                    // Record in the new index regardless of whether the editor
                    // was touched — the on-disk file is still present and
                    // its (size, mtime_ns, oid) is the new ground truth.
                    if self.index_store.is_some() {
                        if let Some((size, mtime_ns)) = stat {
                            new_index_entries.insert(
                                rel_path.clone(),
                                IndexEntry {
                                    size,
                                    mtime_ns,
                                    oid,
                                },
                            );
                        } else {
                            // No usable mtime → don't poison the cache.
                            new_index_entries.remove(&rel_path);
                        }
                    }
                }
                Err(e) if is_not_found(&e) => {
                    // Only count as a change if the path actually existed
                    // in prev_tree, since TreeEditor::remove silently no-ops
                    // for missing paths. With no prev_tree (root commit) a
                    // missing path is just irrelevant.
                    let prev_entry = match prev_tree {
                        Some(t) => {
                            crate::git::tree_builder::lookup_cached(
                                self.object_store.as_ref(),
                                &account,
                                t,
                                &rel_path,
                                &mut prev_lookup_cache,
                            )
                            .await?
                        }
                        None => None,
                    };
                    if prev_entry.is_some() {
                        editor
                            .remove(self.object_store.as_ref(), &account, &rel_path)
                            .await?;
                        changed += 1;
                    }
                    // Path is gone → drop any lingering cache entry.
                    if self.index_store.is_some() {
                        new_index_entries.remove(&rel_path);
                    }
                }
                Err(e) => return Err(e.into()),
            }
        }

        // 5. No-op short-circuit. Even though the tree didn't change, the
        //    on-disk (size, mtime_ns) for enumerated paths may have shifted
        //    (e.g. `touch` of an unchanged file). Persist the refreshed index
        //    keyed on the *current* HEAD so the next commit can still hit the
        //    fast path. Soft-fail on save errors.
        if changed == 0 {
            let noop_oid = prev_head.unwrap_or_else(|| ObjectId::null(gix_hash::Kind::Sha1));
            if let (Some(store), Some(parent)) = (&self.index_store, prev_head) {
                let new_index = CommitIndex {
                    parent_oid: parent,
                    entries: new_index_entries,
                    // Stamped from the backing file's mtime on the next load.
                    saved_at_ns: None,
                };
                if let Err(e) = store.save(&account, &branch, &new_index).await {
                    warn!("commit index save failed for {account}/{branch}: {e}");
                }
            }
            let _ = fast_path_active;
            return Ok(CommitResponse::Noop {
                commit_oid: noop_oid,
            });
        }

        // 6. Write the new tree + the commit object.
        let new_tree = editor.write(self.object_store.as_ref(), &account).await?;
        let parents: Vec<ObjectId> = prev_head.iter().copied().collect();
        let commit_oid = crate::git::commit::write_commit(
            self.object_store.as_ref(),
            &account,
            new_tree,
            parents,
            &author_name,
            &author_email,
            &message,
        )
        .await?;

        // 7. CAS update the branch ref. Map Conflict → ConcurrentCommit.
        match self
            .ref_store
            .cas_update(&account, &ref_name, prev_head, commit_oid)
            .await
        {
            Ok(()) => {}
            Err(RefStoreError::Conflict { expected, actual }) => {
                return Err(GitError::ConcurrentCommit {
                    ref_name,
                    expected,
                    actual,
                });
            }
            Err(other) => return Err(other.into()),
        }

        // 8. Persist the new commit index. Soft-fail: a save error logs and
        //    continues — the commit itself has already succeeded; the worst
        //    case is one slow-path commit next time.
        if let Some(store) = &self.index_store {
            let new_index = CommitIndex {
                parent_oid: commit_oid,
                entries: new_index_entries,
                // Stamped from the backing file's mtime on the next load.
                saved_at_ns: None,
            };
            if let Err(e) = store.save(&account, &branch, &new_index).await {
                warn!("commit index save failed for {account}/{branch}: {e}");
            }
        }
        // Suppress the "fast_path_active was set but never read" lint when no
        // future code path inspects it; left in scope for diagnostics.
        let _ = fast_path_active;

        Ok(CommitResponse::Created {
            commit_oid,
            changed,
        })
    }

    /// Read a commit's metadata, or a single blob's bytes from inside a commit's tree.
    ///
    /// `target_ref` resolution: 40-hex OID / "main" / "refs/heads/main".
    ///
    /// - `path = None`  → returns `ShowResponse::Commit { oid, tree, parents, author, committer, message }`.
    /// - `path = Some(p)` → returns `ShowResponse::Blob { oid, size, bytes }` for the path inside
    ///   the commit's tree. Missing path → `GitError::PathNotFound(p)`. Path that resolves to
    ///   a tree (not a blob) → `GitError::PathIsDirectory(p)` — distinct from missing so callers
    ///   can tell apart "no such path" from "path exists but is a directory, not a file".
    ///
    /// Missing ref → `GitError::RefStore(RefStoreError::NotFound)`.
    /// Missing commit object → `GitError::ObjectStore(ObjectStoreError::NotFound)`.
    pub async fn show(&self, req: ShowRequest) -> Result<ShowResponse, GitError> {
        let ShowRequest {
            account,
            target_ref,
            path,
        } = req;

        validate_account_id(&account)?;
        if let Some(p) = &path {
            validate_relative_path(p)?;
        }

        let commit_oid = resolve_ref(
            self.ref_store.as_ref(),
            self.object_store.as_ref(),
            &account,
            &target_ref,
        )
        .await?;
        let meta = load_commit_meta(self.object_store.as_ref(), &account, &commit_oid).await?;

        match path {
            None => Ok(ShowResponse::Commit {
                oid: commit_oid,
                tree: meta.tree,
                parents: meta.parents,
                author: meta.author,
                committer: meta.committer,
                message: meta.message,
            }),
            Some(p) => {
                let entry = crate::git::tree_builder::lookup(
                    self.object_store.as_ref(),
                    &account,
                    meta.tree,
                    &p,
                )
                .await?;
                let (blob_oid, mode) = entry.ok_or_else(|| GitError::PathNotFound(p.clone()))?;
                // Reject trees masquerading as paths: callers asked for blob bytes.
                if mode.is_tree() {
                    return Err(GitError::PathIsDirectory(p));
                }
                let raw =
                    crate::git::util::read_object(self.object_store.as_ref(), &account, &blob_oid)
                        .await?;
                let (kind, payload_size, hdr) = crate::git::util::parse_object_header(&raw)?;
                if kind != gix_object::Kind::Blob {
                    return Err(GitError::CorruptedObject(format!(
                        "expected blob at {p}, got {kind:?}"
                    )));
                }
                // `raw` is already a `Bytes`; `slice` is O(1) and shares the
                // backing buffer instead of allocating a fresh payload copy.
                let bytes = raw.slice(hdr..);
                Ok(ShowResponse::Blob {
                    oid: blob_oid,
                    size: payload_size,
                    bytes,
                })
            }
        }
    }

    /// Restore a subtree at `project_dir` to the state it had in `source_commit`,
    /// producing a new commit whose parent is the **current HEAD** (not
    /// `source_commit`). HEAD always moves forward.
    ///
    /// See design §8.2 for the full algorithm and `RestoreResponse` for the
    /// three possible outcomes (`Applied` / `Noop` / `DryRun`).
    ///
    /// Errors:
    /// - `GitError::InvalidProjectDir` — `project_dir` is empty / malformed.
    /// - `GitError::RefStore(NotFound)` — branch HEAD or source_commit ref missing.
    /// - `GitError::SubtreeNotFoundInCommit` — `project_dir` does not resolve
    ///   to a subtree in `source_commit`'s tree.
    /// - `GitError::ConcurrentCommit` — branch ref changed between our read
    ///   and the CAS swap.
    pub async fn restore(&self, req: RestoreRequest) -> Result<RestoreResponse, GitError> {
        let RestoreRequest {
            account,
            branch,
            project_dir,
            source_commit,
            dry_run,
            message: _,
            author_name: _,
            author_email: _,
        } = &req;

        validate_account_id(account)?;

        if let Some(project_dir) = project_dir {
            validate_project_dir(project_dir)?;
        }
        let ref_name = format!("refs/heads/{branch}");

        // 1. Resolve both commits.
        let source_oid = resolve_ref(
            self.ref_store.as_ref(),
            self.object_store.as_ref(),
            account,
            source_commit,
        )
        .await?;
        let head_oid = self.ref_store.read(account, &ref_name).await?;
        let source_meta =
            load_commit_meta(self.object_store.as_ref(), account, &source_oid).await?;
        let head_meta = load_commit_meta(self.object_store.as_ref(), account, &head_oid).await?;

        // 2. Extract subtree from each (or use full tree if project_dir is None).
        //    Source missing → error (if project_dir is Some).
        //    Head missing → treat as empty (every file is a fresh write).
        let (source_tree_to_flatten, head_tree_to_flatten) = match project_dir {
            Some(project_dir) => {
                let source_subtree = match crate::git::tree_builder::lookup(
                    self.object_store.as_ref(),
                    account,
                    source_meta.tree,
                    project_dir,
                )
                .await?
                {
                    Some((oid, mode)) if mode.is_tree() => oid,
                    _ => {
                        return Err(GitError::SubtreeNotFoundInCommit {
                            project_dir: project_dir.clone(),
                            commit: source_oid,
                        });
                    }
                };
                let head_subtree = match crate::git::tree_builder::lookup(
                    self.object_store.as_ref(),
                    account,
                    head_meta.tree,
                    project_dir,
                )
                .await?
                {
                    Some((oid, mode)) if mode.is_tree() => Some(oid),
                    _ => None,
                };
                (source_subtree, head_subtree)
            }
            None => (source_meta.tree, Some(head_meta.tree)),
        };

        // 3. Flatten and diff (paths in the result are subtree-relative, or account-relative if full tree).
        let source_entries = crate::git::tree_builder::flatten(
            self.object_store.as_ref(),
            account,
            source_tree_to_flatten,
            &None,
        )
        .await?;
        let head_entries = match head_tree_to_flatten {
            Some(oid) => {
                crate::git::tree_builder::flatten(self.object_store.as_ref(), account, oid, &None)
                    .await?
            }
            None => Vec::new(),
        };
        let diff = compute_subtree_diff(&source_entries, &head_entries);

        // 4. dry_run short-circuits BEFORE any writes.
        if *dry_run {
            return Ok(RestoreResponse::DryRun {
                diff,
                head: head_oid,
                source: source_oid,
            });
        }

        // 5. Source == head → noop.
        if diff.to_write.is_empty() && diff.to_delete.is_empty() {
            return Ok(RestoreResponse::Noop {
                head: head_oid,
                source: source_oid,
            });
        }

        // 6. Prepare writeback metadata. Paths in the diff are relative to
        //    project_dir — prefix here. NOTE: the VFS is NOT mutated yet. The
        //    ref-consistency protocol (steps 7–9) must complete first so that a
        //    losing CAS race leaves the working tree untouched; the actual
        //    writeback happens in step 10 only after the ref swap succeeds.
        use futures::stream::{self, StreamExt};

        let abs_prefix = match project_dir {
            Some(project_dir) => format!("/local/{}/{}", account, project_dir),
            None => format!("/local/{}", account),
        };
        let unchanged_count = diff.unchanged.len();

        // 7. Build the new tree: load head.tree into an editor and splice
        //    source_subtree at project_dir, or use source tree directly if full restore.
        let new_tree_oid = match project_dir {
            Some(project_dir) => {
                let mut editor = crate::git::tree_builder::TreeEditor::from_tree(
                    self.object_store.as_ref(),
                    account,
                    head_meta.tree,
                )
                .await?;
                editor
                    .upsert_subtree(
                        self.object_store.as_ref(),
                        account,
                        project_dir,
                        source_tree_to_flatten,
                    )
                    .await?;
                editor.write(self.object_store.as_ref(), account).await?
            }
            None => source_meta.tree,
        };

        // 8. Construct the new commit. parent = head_oid (NOT source_oid).
        let msg = req.message.clone().unwrap_or_else(|| {
            let short = &source_oid.to_hex().to_string()[..12.min(40)];
            match &req.project_dir {
                Some(project_dir) => format!("restore {} from {}", project_dir, short),
                None => format!("restore full tree from {}", short),
            }
        });
        let new_commit_oid = crate::git::commit::write_commit(
            self.object_store.as_ref(),
            account,
            new_tree_oid,
            vec![head_oid],
            &req.author_name,
            &req.author_email,
            &msg,
        )
        .await?;

        // 9. CAS-swap the branch ref. Map Conflict → ConcurrentCommit.
        //    This MUST happen before any VFS writeback: if another commit
        //    advanced the branch between our HEAD read and now, the CAS fails
        //    and we return early with the working tree still matching HEAD,
        //    leaving caller-driven reindex and on-disk state consistent.
        match self
            .ref_store
            .cas_update(account, &ref_name, Some(head_oid), new_commit_oid)
            .await
        {
            Ok(()) => {}
            Err(crate::git::error::RefStoreError::Conflict { expected, actual }) => {
                return Err(GitError::ConcurrentCommit {
                    ref_name,
                    expected,
                    actual,
                });
            }
            Err(other) => return Err(other.into()),
        }

        // 10. The ref swap committed our new state. Now write back through the
        //     VFS so the working tree reflects the restored content. The ref
        //     has already advanced, so a per-path failure here can NOT be
        //     rolled back — instead the streams below collect every failure
        //     and we surface them as `GitError::RestoreWritebackPartial`. The
        //     caller (Python) then schedules reindex for the paths that *did*
        //     reach the VFS so the vector index does not stay stale.
        let object_store_ref = self.object_store.clone();
        let vfs_ref = self.vfs.clone();
        let account_owned = account.clone();
        let abs_prefix_for_writes = abs_prefix.clone();
        let project_dir_for_writes = project_dir.clone();

        let write_results: Vec<(String, Result<(), GitError>)> =
            stream::iter(diff.to_write.clone().into_iter())
                .map(|(rel, blob_oid)| {
                    let object_store = object_store_ref.clone();
                    let vfs = vfs_ref.clone();
                    let account = account_owned.clone();
                    let abs_prefix = abs_prefix_for_writes.clone();
                    let project_dir = project_dir_for_writes.clone();
                    async move {
                        let account_rel = match &project_dir {
                            Some(pd) => format!("{}/{}", pd, rel),
                            None => rel.clone(),
                        };
                        let r = async {
                            let bytes =
                                read_blob_payload(object_store.as_ref(), &account, &blob_oid)
                                    .await?;
                            let abs = format!("{}/{}", abs_prefix, rel);
                            // The target's parent directory may have been removed out of
                            // band (e.g. an `rm -r` that a later commit recorded as a
                            // deletion), so the restore must recreate the directory
                            // chain before writing the blob back.
                            crate::core::filesystem::FileSystem::ensure_parent_dirs(
                                vfs.as_ref(),
                                &abs,
                                0o755,
                            )
                            .await?;
                            crate::core::filesystem::FileSystem::write(
                                vfs.as_ref(),
                                &abs,
                                &bytes,
                                0,
                                crate::core::types::WriteFlag::Create,
                            )
                            .await?;
                            Ok::<(), GitError>(())
                        }
                        .await;
                        (account_rel, r)
                    }
                })
                .buffer_unordered(32)
                .collect()
                .await;

        let abs_prefix_for_deletes = abs_prefix.clone();
        let vfs_for_deletes = self.vfs.clone();
        let project_dir_for_deletes = project_dir.clone();
        let delete_results: Vec<(String, Result<(), GitError>)> =
            stream::iter(diff.to_delete.clone().into_iter())
                .map(|rel| {
                    let vfs = vfs_for_deletes.clone();
                    let abs_prefix = abs_prefix_for_deletes.clone();
                    let project_dir = project_dir_for_deletes.clone();
                    async move {
                        let account_rel = match &project_dir {
                            Some(pd) => format!("{}/{}", pd, rel),
                            None => rel.clone(),
                        };
                        let abs = format!("{}/{}", abs_prefix, rel);
                        // Restore is idempotent: a path the diff wants to delete may
                        // already be absent from the VFS (e.g. derived files like
                        // `.abstract.md` that were removed or regenerated out of band).
                        // Treat NotFound as success rather than counting it as a failure.
                        let r =
                            match crate::core::filesystem::FileSystem::remove(vfs.as_ref(), &abs)
                                .await
                            {
                                Ok(_) => Ok::<(), GitError>(()),
                                Err(crate::core::errors::Error::NotFound(_)) => Ok(()),
                                Err(e) => Err(e.into()),
                            };
                        (account_rel, r)
                    }
                })
                .buffer_unordered(32)
                .collect()
                .await;

        // 10b. Prune directories left empty by the deletes above. Git does not
        //      track directories, so `to_delete` only ever lists files; removing
        //      the last file in a directory would otherwise leave an empty husk
        //      in the VFS. Walk each deleted file's ancestor directories (within
        //      project_dir, deepest first) and drop any that are now empty.
        //      Best-effort: a directory that still holds entries, or has already
        //      vanished, is simply skipped — pruning never aborts the restore.
        use std::collections::BTreeSet;
        // (depth, rel_dir): BTreeSet iterates ascending, so reversing yields the
        // deepest directories first — children are pruned before their parents,
        // letting a parent that held only pruned subdirs be removed in turn.
        let mut prune_candidates: BTreeSet<(usize, String)> = BTreeSet::new();
        for rel in &diff.to_delete {
            let mut dir = rel.as_str();
            while let Some(idx) = dir.rfind('/') {
                dir = &dir[..idx];
                prune_candidates.insert((dir.split('/').count(), dir.to_string()));
            }
        }
        for (_depth, rel_dir) in prune_candidates.into_iter().rev() {
            let abs = format!("{}/{}", abs_prefix, rel_dir);
            let is_empty = match crate::core::filesystem::FileSystem::read_dir(
                self.vfs.as_ref(),
                &abs,
            )
            .await
            {
                Ok(entries) => entries.is_empty(),
                // Missing or not a directory → nothing to prune.
                Err(_) => false,
            };
            if is_empty {
                // Ignore failures: a concurrent writer may have repopulated the
                // directory, or it may already be gone. Either way the restore
                // itself has succeeded.
                let _ = crate::core::filesystem::FileSystem::remove(self.vfs.as_ref(), &abs).await;
            }
        }

        // 10c. Partition the per-path results into success / failure buckets.
        //      `written_paths` / `deleted_paths` here only carry the paths
        //      that actually reached the VFS — callers use these lists to
        //      drive reindex, and a path whose write failed must not be
        //      reindexed (the file's blob never landed).
        let mut written_paths: Vec<String> = Vec::with_capacity(write_results.len());
        let mut failed_writes: Vec<(String, String)> = Vec::new();
        for (path, r) in write_results {
            match r {
                Ok(()) => written_paths.push(path),
                Err(e) => failed_writes.push((path, e.to_string())),
            }
        }
        let mut deleted_paths: Vec<String> = Vec::with_capacity(delete_results.len());
        let mut failed_deletes: Vec<(String, String)> = Vec::new();
        for (path, r) in delete_results {
            match r {
                Ok(()) => deleted_paths.push(path),
                Err(e) => failed_deletes.push((path, e.to_string())),
            }
        }

        let written_actual = written_paths.len();
        let deleted_actual = deleted_paths.len();

        // 11. Partial failure path. The ref has already advanced, so we
        //     cannot rollback — surface a structured error carrying enough
        //     payload for the caller to schedule reindex for the paths that
        //     *did* succeed and to report the failures upward.
        if !failed_writes.is_empty() || !failed_deletes.is_empty() {
            return Err(GitError::RestoreWritebackPartial(Box::new(
                crate::git::types::RestoreWritebackPartial {
                    new_commit_oid,
                    source_commit: source_oid,
                    parent_commit: head_oid,
                    written: written_actual,
                    deleted: deleted_actual,
                    unchanged: unchanged_count,
                    written_paths,
                    deleted_paths,
                    failed_writes,
                    failed_deletes,
                },
            )));
        }

        Ok(RestoreResponse::Applied {
            new_commit_oid,
            source_commit: source_oid,
            parent_commit: head_oid,
            written: written_actual,
            deleted: deleted_actual,
            unchanged: unchanged_count,
            written_paths,
            deleted_paths,
        })
    }
}

/// Load a blob object and return only its payload bytes (header stripped).
///
/// Errors out with `CorruptedObject` if the loaded object is not a blob —
/// this should not happen on a well-formed store but is cheap to verify.
async fn read_blob_payload(
    store: &dyn ObjectStore,
    account: &str,
    blob_oid: &gix_hash::ObjectId,
) -> Result<bytes::Bytes, GitError> {
    let raw = crate::git::util::read_object(store, account, blob_oid).await?;
    let (kind, _, hdr) = crate::git::util::parse_object_header(&raw)?;
    if kind != gix_object::Kind::Blob {
        return Err(GitError::CorruptedObject(format!(
            "expected blob, got {kind:?}"
        )));
    }
    Ok(raw.slice(hdr..))
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

/// Return true iff `e` is `Error::NotFound(_)`.
fn is_not_found(e: &crate::core::errors::Error) -> bool {
    matches!(e, crate::core::errors::Error::NotFound(_))
}

/// Project a `FileInfo` into the `(size, mtime_ns)` pair Fast Path 1 keys on.
///
/// Returns `None` when the file's `mod_time` is unrepresentable (pre-epoch
/// times wider than `i128` can hold are degenerate). A `None` here means the
/// path simply will not participate in Fast Path 1 — the slow path
/// (read+SHA-1) is taken and the cache entry is dropped, never poisoned.
fn stat_signature(info: &FileInfo) -> Option<(u64, i128)> {
    let dur = info.mod_time.duration_since(UNIX_EPOCH).ok()?;
    let nanos: i128 = dur.as_nanos() as i128;
    Some((info.size, nanos))
}

/// Validate an `account` id before it is used to build any filesystem path
/// (local backend) or S3 key prefix. This is the Rust-side equivalent of the
/// Python `validate_account_id` and is the single choke point that keeps a
/// crafted account (e.g. `../x`, `a/b`, `a\b`) from escaping its per-account
/// directory / key prefix when a binding is called directly.
///
/// Rules (mirroring `openviking/core/identifiers.py`):
/// - non-empty
/// - not `.` or `..`
/// - only `[A-Za-z0-9_.@-]` (rejects `/`, `\`, whitespace, control chars, …)
/// - at most one `@`
/// - must not start with `_`
fn validate_account_id(account: &str) -> Result<(), GitError> {
    if account.is_empty() {
        return Err(GitError::InvalidAccountId("account_id is empty".into()));
    }
    if account == "." || account == ".." {
        return Err(GitError::InvalidAccountId(
            "account_id must not be '.' or '..'".into(),
        ));
    }
    if !account
        .bytes()
        .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'.' | b'@' | b'-'))
    {
        return Err(GitError::InvalidAccountId(format!(
            "account_id must be an alphanumeric string: {account:?}"
        )));
    }
    if account.bytes().filter(|&b| b == b'@').count() > 1 {
        return Err(GitError::InvalidAccountId(
            "account_id must have at most one @".into(),
        ));
    }
    if account.starts_with('_') {
        return Err(GitError::InvalidAccountId(
            "account_id cannot start with underscore _".into(),
        ));
    }
    Ok(())
}

/// Validate `project_dir` matches the rules of `TreeEditor::upsert`:
/// non-empty, no leading/trailing `/`, no empty components, no `.` / `..`
/// segments, no backslash, no control characters. The traversal-related
/// rules guard the same boundary as `validate_account_id`: a direct PyO3
/// caller could otherwise pass `project_dir="../other"` and have the
/// service splice or restore *outside* the account's tree once the path is
/// concatenated into `/local/{account}/{project_dir}/...`.
fn validate_project_dir(project_dir: &str) -> Result<(), GitError> {
    if project_dir.is_empty() {
        return Err(GitError::InvalidProjectDir(
            "project_dir must be non-empty".into(),
        ));
    }
    if project_dir.starts_with('/') || project_dir.ends_with('/') {
        return Err(GitError::InvalidProjectDir(format!(
            "project_dir must not start or end with '/': {project_dir:?}"
        )));
    }
    for c in project_dir.split('/') {
        if c.is_empty() {
            return Err(GitError::InvalidProjectDir(format!(
                "project_dir contains empty segment: {project_dir:?}"
            )));
        }
        if c == "." || c == ".." {
            return Err(GitError::InvalidProjectDir(format!(
                "project_dir contains '.' or '..' segment: {project_dir:?}"
            )));
        }
    }
    if project_dir.contains('\\') {
        return Err(GitError::InvalidProjectDir(format!(
            "project_dir must not contain backslash: {project_dir:?}"
        )));
    }
    if project_dir.bytes().any(|b| b < 0x20 || b == 0x7f) {
        return Err(GitError::InvalidProjectDir(format!(
            "project_dir contains control character: {project_dir:?}"
        )));
    }
    Ok(())
}

/// Validate a user-supplied relative path that will be concatenated with
/// `/local/{account}/` (commit) or looked up in a Git tree (show). Same
/// reasoning as `validate_account_id` / `validate_project_dir`: the Rust
/// GitService is a native boundary, so it must defend against `..` /
/// backslash / control chars itself rather than trust the caller (PyO3
/// binding, future SDK consumer) to have normalized first.
///
/// Rules: non-empty; no leading/trailing `/`; no empty, `.`, or `..`
/// segment; no backslash; no control character.
fn validate_relative_path(path: &str) -> Result<(), GitError> {
    if path.is_empty() {
        return Err(GitError::InvalidPath("path must be non-empty".into()));
    }
    if path.starts_with('/') || path.ends_with('/') {
        return Err(GitError::InvalidPath(format!(
            "path must not start or end with '/': {path:?}"
        )));
    }
    for c in path.split('/') {
        if c.is_empty() {
            return Err(GitError::InvalidPath(format!(
                "path contains empty segment: {path:?}"
            )));
        }
        if c == "." || c == ".." {
            return Err(GitError::InvalidPath(format!(
                "path contains '.' or '..' segment: {path:?}"
            )));
        }
    }
    if path.contains('\\') {
        return Err(GitError::InvalidPath(format!(
            "path must not contain backslash: {path:?}"
        )));
    }
    if path.bytes().any(|b| b < 0x20 || b == 0x7f) {
        return Err(GitError::InvalidPath(format!(
            "path contains control character: {path:?}"
        )));
    }
    Ok(())
}

/// Pure-function diff between two flattened subtrees.
///
/// Both inputs are `(path, oid)` slices as returned by `tree_builder::flatten`
/// on a subtree OID — meaning the paths are already relative to the subtree
/// root (no `project_dir` prefix). Results are sorted by path.
fn compute_subtree_diff(
    source: &[(String, gix_hash::ObjectId)],
    head: &[(String, gix_hash::ObjectId)],
) -> crate::git::types::RestoreDiff {
    use std::collections::HashMap;
    let head_map: HashMap<&str, &gix_hash::ObjectId> =
        head.iter().map(|(p, o)| (p.as_str(), o)).collect();
    let source_map: HashMap<&str, &gix_hash::ObjectId> =
        source.iter().map(|(p, o)| (p.as_str(), o)).collect();

    let mut to_write = Vec::new();
    let mut unchanged = Vec::new();
    for (path, oid) in source {
        match head_map.get(path.as_str()) {
            Some(head_oid) if *head_oid == oid => unchanged.push(path.clone()),
            _ => to_write.push((path.clone(), *oid)),
        }
    }
    let mut to_delete: Vec<String> = head
        .iter()
        .filter(|(p, _)| !source_map.contains_key(p.as_str()))
        .map(|(p, _)| p.clone())
        .collect();

    to_write.sort_by(|a, b| a.0.cmp(&b.0));
    to_delete.sort();
    unchanged.sort();
    crate::git::types::RestoreDiff {
        to_write,
        to_delete,
        unchanged,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use async_trait::async_trait;
    use std::collections::{HashMap, HashSet};
    use std::sync::{Arc, Mutex};

    use crate::core::errors::{Error, Result};
    use crate::core::filesystem::FileSystem;
    use crate::core::types::{FileInfo, TreeEntry, WriteFlag};
    use crate::git::backends::local::{LocalObjectStore, LocalRefStore};
    use crate::git::error::ObjectStoreError;
    use crate::git::error::RefStoreError;
    use crate::git::tree_builder::{flatten, lookup};

    /// In-memory VFS mock that owns a map from absolute path to bytes.
    /// Root for the account is always `/local/{account}` — paths inserted
    /// must be the absolute path including this prefix.
    struct MockVfs {
        account: String,
        files: Arc<Mutex<HashMap<String, Vec<u8>>>>,
        /// When true, `remove` returns NotFound for absent paths (like the real
        /// VFS) instead of silently succeeding. Used to exercise the idempotent
        /// delete path in restore.
        strict_remove: bool,
        /// Absolute paths whose `write` call should fail with an I/O error.
        /// Used by restore-partial tests to force a per-path writeback failure
        /// without otherwise breaking the mock VFS.
        fail_writes: Arc<Mutex<HashSet<String>>>,
        /// Absolute paths whose `remove` call should fail with an I/O error
        /// (NotFound-style errors are still produced by the existing
        /// `strict_remove` flag, not via this set — that mirrors the real
        /// "idempotent delete" semantics in service.rs).
        fail_removes: Arc<Mutex<HashSet<String>>>,
    }

    impl MockVfs {
        fn new(account: &str) -> Arc<Self> {
            Arc::new(Self {
                account: account.to_string(),
                files: Arc::new(Mutex::new(HashMap::new())),
                strict_remove: false,
                fail_writes: Arc::new(Mutex::new(HashSet::new())),
                fail_removes: Arc::new(Mutex::new(HashSet::new())),
            })
        }

        fn new_strict_remove(account: &str) -> Arc<Self> {
            Arc::new(Self {
                account: account.to_string(),
                files: Arc::new(Mutex::new(HashMap::new())),
                strict_remove: true,
                fail_writes: Arc::new(Mutex::new(HashSet::new())),
                fail_removes: Arc::new(Mutex::new(HashSet::new())),
            })
        }

        /// Insert/update file content. `rel` is account-relative.
        fn put(&self, rel: &str, data: &[u8]) {
            let abs = format!("/local/{}/{}", self.account, rel);
            self.files.lock().unwrap().insert(abs, data.to_vec());
        }

        /// Delete a file by account-relative path.
        fn delete(&self, rel: &str) {
            let abs = format!("/local/{}/{}", self.account, rel);
            self.files.lock().unwrap().remove(&abs);
        }

        /// Cause subsequent `write` calls targeting `rel` (account-relative)
        /// to return an I/O error.
        fn fail_write(&self, rel: &str) {
            let abs = format!("/local/{}/{}", self.account, rel);
            self.fail_writes.lock().unwrap().insert(abs);
        }

        /// Cause subsequent `remove` calls targeting `rel` (account-relative)
        /// to return an I/O error.
        fn fail_remove(&self, rel: &str) {
            let abs = format!("/local/{}/{}", self.account, rel);
            self.fail_removes.lock().unwrap().insert(abs);
        }
    }

    #[async_trait]
    impl FileSystem for MockVfs {
        async fn create(&self, _path: &str) -> Result<()> {
            unimplemented!()
        }
        async fn mkdir(&self, _path: &str, _mode: u32) -> Result<()> {
            // Directories are implicit in this flat file map, so creating one is
            // a no-op. Defined (rather than unimplemented!) so the default
            // `ensure_parent_dirs` used by restore's writeback succeeds.
            Ok(())
        }
        async fn remove(&self, path: &str) -> Result<()> {
            if self.fail_removes.lock().unwrap().contains(path) {
                return Err(Error::Internal(format!("forced remove failure: {path}")));
            }
            let existed = self.files.lock().unwrap().remove(path).is_some();
            if self.strict_remove && !existed {
                return Err(Error::not_found(path));
            }
            Ok(())
        }
        async fn remove_all(&self, _path: &str) -> Result<()> {
            unimplemented!()
        }

        async fn read(&self, path: &str, _offset: u64, _size: u64) -> Result<Vec<u8>> {
            let g = self.files.lock().unwrap();
            match g.get(path) {
                Some(bytes) => Ok(bytes.clone()),
                None => Err(Error::not_found(path)),
            }
        }

        async fn write(
            &self,
            path: &str,
            data: &[u8],
            _offset: u64,
            _flags: WriteFlag,
        ) -> Result<u64> {
            if self.fail_writes.lock().unwrap().contains(path) {
                return Err(Error::Internal(format!("forced write failure: {path}")));
            }
            self.files
                .lock()
                .unwrap()
                .insert(path.to_string(), data.to_vec());
            Ok(data.len() as u64)
        }
        async fn read_dir(&self, _path: &str) -> Result<Vec<FileInfo>> {
            unimplemented!()
        }

        async fn stat(&self, path: &str) -> Result<FileInfo> {
            let g = self.files.lock().unwrap();
            if let Some(bytes) = g.get(path) {
                let name = path.rsplit('/').next().unwrap_or(path).to_string();
                return Ok(FileInfo::new_file(name, bytes.len() as u64, 0o644));
            }
            Err(Error::not_found(path))
        }

        async fn rename(&self, _old_path: &str, _new_path: &str) -> Result<()> {
            unimplemented!()
        }
        async fn chmod(&self, _path: &str, _mode: u32) -> Result<()> {
            unimplemented!()
        }

        async fn tree_directory(
            &self,
            path: &str,
            _show_hidden: bool,
            _node_limit: Option<usize>,
            _level_limit: Option<usize>,
        ) -> Result<Vec<TreeEntry>> {
            let prefix = if path == "/" {
                "/".to_string()
            } else {
                format!("{}/", path)
            };
            let g = self.files.lock().unwrap();
            let mut out = Vec::new();
            for (full_path, _bytes) in g.iter() {
                if !full_path.starts_with(&prefix) {
                    continue;
                }
                let rel = full_path
                    .strip_prefix(&prefix)
                    .unwrap_or(full_path)
                    .to_string();
                let name = full_path
                    .rsplit('/')
                    .next()
                    .unwrap_or(full_path)
                    .to_string();
                let info = FileInfo::new_file(name, 0, 0o644);
                out.push(TreeEntry {
                    path: full_path.clone(),
                    rel_path: rel,
                    info,
                    extra: HashMap::new(),
                });
            }
            Ok(out)
        }
    }

    /// Helper: build a fresh GitService backed by a temp dir + a fresh
    /// in-memory VFS for the given account.
    fn make_service(
        account: &str,
    ) -> (
        tempfile::TempDir,
        Arc<MockVfs>,
        Arc<LocalObjectStore>,
        Arc<LocalRefStore>,
        GitService,
    ) {
        let dir = tempfile::tempdir().unwrap();
        let object_store = Arc::new(LocalObjectStore::new(dir.path()));
        let ref_store = Arc::new(LocalRefStore::new(dir.path()));
        let vfs = MockVfs::new(account);
        let svc = GitService::new(
            vfs.clone() as Arc<dyn FileSystem>,
            object_store.clone() as Arc<dyn ObjectStore>,
            ref_store.clone() as Arc<dyn RefStore>,
        );
        (dir, vfs, object_store, ref_store, svc)
    }

    fn req(
        account: &str,
        branch: &str,
        message: &str,
        paths: Option<Vec<String>>,
    ) -> CommitRequest {
        CommitRequest {
            account: account.to_string(),
            branch: branch.to_string(),
            message: message.to_string(),
            paths,
            author_name: "tester".to_string(),
            author_email: "tester@example.com".to_string(),
        }
    }

    /// Load a commit's parent OIDs from the object store.
    async fn commit_parents(
        store: &dyn ObjectStore,
        account: &str,
        commit_oid: ObjectId,
    ) -> Vec<ObjectId> {
        let raw = crate::git::util::read_object(store, account, &commit_oid)
            .await
            .unwrap();
        let (_, _, hdr) = crate::git::util::parse_object_header(&raw).unwrap();
        let parsed = gix_object::CommitRef::from_bytes(&raw[hdr..]).unwrap();
        parsed.parents().collect()
    }

    async fn commit_tree(store: &dyn ObjectStore, account: &str, commit_oid: ObjectId) -> ObjectId {
        load_commit_meta(store, account, &commit_oid)
            .await
            .unwrap()
            .tree
    }

    /// Make a commit and return its OID.
    async fn make_commit(svc: &GitService, account: &str, branch: &str, msg: &str) -> ObjectId {
        match svc.commit(req(account, branch, msg, None)).await.unwrap() {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        }
    }

    // ── 1 ──────────────────────────────────────────────────────────────
    #[tokio::test]
    async fn test_commit_first_creates_root_commit() {
        let (_dir, vfs, object_store, ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"hello");

        let resp = svc
            .commit(req("acct", "main", "first", None))
            .await
            .unwrap();

        match resp {
            CommitResponse::Created {
                commit_oid,
                changed,
            } => {
                assert!(changed >= 1, "should record at least one change");
                let parents = commit_parents(
                    object_store.as_ref() as &dyn ObjectStore,
                    "acct",
                    commit_oid,
                )
                .await;
                assert!(parents.is_empty(), "root commit must have no parents");
                let tree = commit_tree(
                    object_store.as_ref() as &dyn ObjectStore,
                    "acct",
                    commit_oid,
                )
                .await;
                assert_ne!(tree, ObjectId::empty_tree(gix_hash::Kind::Sha1));
                let head = ref_store.read("acct", "refs/heads/main").await.unwrap();
                assert_eq!(head, commit_oid);
            }
            other => panic!("expected Created, got {other:?}"),
        }
    }

    // ── 2 ──────────────────────────────────────────────────────────────
    #[tokio::test]
    async fn test_commit_second_links_to_first() {
        let (_dir, vfs, object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"hello");
        let first = svc
            .commit(req("acct", "main", "first", None))
            .await
            .unwrap();
        let first_oid = match first {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        };

        vfs.put("resources/a.md", b"world");
        let second = svc
            .commit(req("acct", "main", "second", None))
            .await
            .unwrap();
        let second_oid = match second {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        };

        let parents = commit_parents(
            object_store.as_ref() as &dyn ObjectStore,
            "acct",
            second_oid,
        )
        .await;
        assert_eq!(parents, vec![first_oid]);
    }

    // ── 3 ──────────────────────────────────────────────────────────────
    #[tokio::test]
    async fn test_commit_noop_when_nothing_changed() {
        let (_dir, vfs, _object_store, ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"hello");
        let first = svc
            .commit(req("acct", "main", "first", None))
            .await
            .unwrap();
        let first_oid = match first {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        };

        let second = svc.commit(req("acct", "main", "noop", None)).await.unwrap();
        match second {
            CommitResponse::Noop { commit_oid } => assert_eq!(commit_oid, first_oid),
            other => panic!("expected Noop, got {other:?}"),
        }

        let head = ref_store.read("acct", "refs/heads/main").await.unwrap();
        assert_eq!(head, first_oid);
    }

    // ── 4 ──────────────────────────────────────────────────────────────
    #[tokio::test]
    async fn test_commit_handles_deletes() {
        let (_dir, vfs, object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"hello");
        vfs.put("resources/b.md", b"world");
        let _ = svc
            .commit(req("acct", "main", "first", None))
            .await
            .unwrap();

        vfs.delete("resources/a.md");
        let resp = svc
            .commit(req(
                "acct",
                "main",
                "delete-a",
                Some(vec!["resources/a.md".to_string()]),
            ))
            .await
            .unwrap();
        let second_oid = match resp {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        };

        let tree = commit_tree(
            object_store.as_ref() as &dyn ObjectStore,
            "acct",
            second_oid,
        )
        .await;
        let all = flatten(
            object_store.as_ref() as &dyn ObjectStore,
            "acct",
            tree,
            &None,
        )
        .await
        .unwrap();
        let paths: Vec<String> = all.into_iter().map(|(p, _)| p).collect();
        assert_eq!(paths, vec!["resources/b.md".to_string()]);
    }

    /// Helper: list every blob path in a commit's tree, sorted.
    async fn commit_paths(
        store: &dyn ObjectStore,
        account: &str,
        commit_oid: ObjectId,
    ) -> Vec<String> {
        let tree = commit_tree(store, account, commit_oid).await;
        let all = flatten(store, account, tree, &None).await.unwrap();
        all.into_iter().map(|(p, _)| p).collect()
    }

    // ── 4b ─────────────────────────────────────────────────────────────
    /// Full enumeration (`paths=None`) must capture a deletion: a file gone
    /// from disk but present in prev_tree is dropped from the new snapshot.
    #[tokio::test]
    async fn test_full_commit_captures_delete() {
        let (_dir, vfs, object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"hello");
        vfs.put("resources/b.md", b"world");
        let _ = make_commit(&svc, "acct", "main", "first").await;

        vfs.delete("resources/a.md");
        let resp = svc
            .commit(req("acct", "main", "full-delete", None))
            .await
            .unwrap();
        let oid = match resp {
            CommitResponse::Created {
                commit_oid,
                changed,
            } => {
                assert_eq!(changed, 1, "exactly one path (a.md) was removed");
                commit_oid
            }
            other => panic!("expected Created, got {other:?}"),
        };

        let paths = commit_paths(object_store.as_ref() as &dyn ObjectStore, "acct", oid).await;
        assert_eq!(paths, vec!["resources/b.md".to_string()]);
    }

    // ── 4c ─────────────────────────────────────────────────────────────
    /// Full enumeration must capture deletion of an entire subdirectory.
    #[tokio::test]
    async fn test_full_commit_captures_subdir_delete() {
        let (_dir, vfs, object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/keep.md", b"keep");
        vfs.put("resources/sub/a.md", b"a");
        vfs.put("resources/sub/b.md", b"b");
        let _ = make_commit(&svc, "acct", "main", "first").await;

        vfs.delete("resources/sub/a.md");
        vfs.delete("resources/sub/b.md");
        let resp = svc
            .commit(req("acct", "main", "drop-sub", None))
            .await
            .unwrap();
        let oid = match resp {
            CommitResponse::Created {
                commit_oid,
                changed,
            } => {
                assert_eq!(changed, 2, "both files under sub/ were removed");
                commit_oid
            }
            other => panic!("expected Created, got {other:?}"),
        };

        let paths = commit_paths(object_store.as_ref() as &dyn ObjectStore, "acct", oid).await;
        assert_eq!(paths, vec!["resources/keep.md".to_string()]);
    }

    // ── 4d ─────────────────────────────────────────────────────────────
    /// Full enumeration must handle a file→dir transition: `foo` was a file,
    /// now `foo/bar.md` is a directory entry. The stale blob is replaced.
    #[tokio::test]
    async fn test_full_commit_file_to_dir_transition() {
        let (_dir, vfs, object_store, _ref_store, svc) = make_service("acct");
        vfs.put("foo", b"i am a file");
        let _ = make_commit(&svc, "acct", "main", "first").await;

        vfs.delete("foo");
        vfs.put("foo/bar.md", b"now a dir");
        let resp = svc
            .commit(req("acct", "main", "file-to-dir", None))
            .await
            .unwrap();
        let oid = match resp {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        };

        let paths = commit_paths(object_store.as_ref() as &dyn ObjectStore, "acct", oid).await;
        assert_eq!(paths, vec!["foo/bar.md".to_string()]);
    }

    // ── 4e ─────────────────────────────────────────────────────────────
    /// Full enumeration must handle a dir→file transition: `foo/bar.md` was a
    /// directory, now `foo` is a file. The stale subtree is dropped.
    #[tokio::test]
    async fn test_full_commit_dir_to_file_transition() {
        let (_dir, vfs, object_store, _ref_store, svc) = make_service("acct");
        vfs.put("foo/bar.md", b"i am in a dir");
        let _ = make_commit(&svc, "acct", "main", "first").await;

        vfs.delete("foo/bar.md");
        vfs.put("foo", b"now a file");
        let resp = svc
            .commit(req("acct", "main", "dir-to-file", None))
            .await
            .unwrap();
        let oid = match resp {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        };

        let paths = commit_paths(object_store.as_ref() as &dyn ObjectStore, "acct", oid).await;
        assert_eq!(paths, vec!["foo".to_string()]);
    }

    // ── 4f ─────────────────────────────────────────────────────────────
    /// Multi-level dir→file: `foo/bar/baz.md` collapses to a file `foo`.
    #[tokio::test]
    async fn test_full_commit_dir_to_file_transition_multilevel() {
        let (_dir, vfs, object_store, _ref_store, svc) = make_service("acct");
        vfs.put("foo/bar/baz.md", b"deep");
        let _ = make_commit(&svc, "acct", "main", "first").await;

        vfs.delete("foo/bar/baz.md");
        vfs.put("foo", b"now a file");
        let resp = svc
            .commit(req("acct", "main", "deep-collapse", None))
            .await
            .unwrap();
        let oid = match resp {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        };

        let paths = commit_paths(object_store.as_ref() as &dyn ObjectStore, "acct", oid).await;
        assert_eq!(paths, vec!["foo".to_string()]);
    }

    // ── 5 ──────────────────────────────────────────────────────────────
    #[tokio::test]
    async fn test_commit_with_explicit_paths_skips_others() {
        let (_dir, vfs, object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"A");
        vfs.put("resources/b.md", b"B");
        vfs.put("resources/c.md", b"C");

        let resp = svc
            .commit(req(
                "acct",
                "main",
                "only-a",
                Some(vec!["resources/a.md".to_string()]),
            ))
            .await
            .unwrap();
        let oid = match resp {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        };

        let tree = commit_tree(object_store.as_ref() as &dyn ObjectStore, "acct", oid).await;
        let all = flatten(
            object_store.as_ref() as &dyn ObjectStore,
            "acct",
            tree,
            &None,
        )
        .await
        .unwrap();
        let paths: Vec<String> = all.into_iter().map(|(p, _)| p).collect();
        assert_eq!(paths, vec!["resources/a.md".to_string()]);
        // Sanity-check the blob is reachable via lookup too.
        let found = lookup(
            object_store.as_ref() as &dyn ObjectStore,
            "acct",
            tree,
            "resources/a.md",
        )
        .await
        .unwrap();
        assert!(found.is_some());
    }

    // ── 6 ──────────────────────────────────────────────────────────────

    /// Wrapping RefStore that forces the next `cas_update` call to fail
    /// with `Conflict`, then delegates to the inner store afterwards.
    struct ConflictOnceRef {
        inner: Arc<LocalRefStore>,
        fired: Mutex<bool>,
        actual: Option<ObjectId>,
    }

    #[async_trait]
    impl RefStore for ConflictOnceRef {
        async fn read(
            &self,
            account: &str,
            ref_name: &str,
        ) -> std::result::Result<ObjectId, RefStoreError> {
            self.inner.read(account, ref_name).await
        }

        async fn cas_update(
            &self,
            account: &str,
            ref_name: &str,
            expected: Option<ObjectId>,
            new: ObjectId,
        ) -> std::result::Result<(), RefStoreError> {
            let should_conflict = {
                let mut fired = self.fired.lock().unwrap();
                if !*fired {
                    *fired = true;
                    true
                } else {
                    false
                }
            };
            if should_conflict {
                return Err(RefStoreError::Conflict {
                    expected,
                    actual: self.actual,
                });
            }
            self.inner
                .cas_update(account, ref_name, expected, new)
                .await
        }

        async fn list(
            &self,
            account: &str,
            prefix: &str,
        ) -> std::result::Result<Vec<(String, ObjectId)>, RefStoreError> {
            self.inner.list(account, prefix).await
        }
    }

    #[tokio::test]
    async fn test_commit_cas_conflict_surfaces_as_error() {
        let dir = tempfile::tempdir().unwrap();
        let object_store = Arc::new(LocalObjectStore::new(dir.path()));
        let inner_ref = Arc::new(LocalRefStore::new(dir.path()));
        let bogus = ObjectId::from_hex(b"deadbeefdeadbeefdeadbeefdeadbeefdeadbeef").unwrap();
        let ref_store = Arc::new(ConflictOnceRef {
            inner: inner_ref.clone(),
            fired: Mutex::new(false),
            actual: Some(bogus),
        });
        let vfs = MockVfs::new("acct");
        vfs.put("resources/a.md", b"hello");
        let svc = GitService::new(
            vfs.clone() as Arc<dyn FileSystem>,
            object_store.clone() as Arc<dyn ObjectStore>,
            ref_store.clone() as Arc<dyn RefStore>,
        );

        let result = svc.commit(req("acct", "main", "boom", None)).await;
        match result {
            Err(GitError::ConcurrentCommit {
                ref_name,
                expected,
                actual,
            }) => {
                assert_eq!(ref_name, "refs/heads/main");
                assert_eq!(expected, None);
                assert_eq!(actual, Some(bogus));
            }
            other => panic!("expected ConcurrentCommit, got {other:?}"),
        }
    }

    // ── 7 ──────────────────────────────────────────────────────────────
    // Verifies the incremental commit path reuses unchanged subtree OIDs:
    // modifying a file under `resources/` must NOT rewrite the `agent/`
    // subtree object — its OID must be byte-identical across commits.
    #[tokio::test]
    async fn test_commit_incremental_reuses_unchanged_subtree_oids() {
        let (_dir, vfs, object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"hello");
        vfs.put("agent/b.py", b"print('hi')");

        let first = svc
            .commit(req("acct", "main", "first", None))
            .await
            .unwrap();
        let first_oid = match first {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        };
        let first_tree =
            commit_tree(object_store.as_ref() as &dyn ObjectStore, "acct", first_oid).await;
        let agent_first = lookup(
            object_store.as_ref() as &dyn ObjectStore,
            "acct",
            first_tree,
            "agent",
        )
        .await
        .unwrap()
        .expect("agent subtree must exist after first commit");
        assert!(agent_first.1.is_tree(), "agent entry must be a tree");

        // Touch only resources/a.md.
        vfs.put("resources/a.md", b"world");
        let second = svc
            .commit(req("acct", "main", "second", None))
            .await
            .unwrap();
        let second_oid = match second {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        };
        let second_tree = commit_tree(
            object_store.as_ref() as &dyn ObjectStore,
            "acct",
            second_oid,
        )
        .await;
        assert_ne!(
            first_tree, second_tree,
            "root tree must change because resources/a.md changed",
        );
        let agent_second = lookup(
            object_store.as_ref() as &dyn ObjectStore,
            "acct",
            second_tree,
            "agent",
        )
        .await
        .unwrap()
        .expect("agent subtree must still exist after second commit");

        assert_eq!(
            agent_first.0, agent_second.0,
            "unchanged agent/ subtree OID must be reused across commits",
        );
    }

    // ── 8 ──────────────────────────────────────────────────────────────
    #[tokio::test]
    async fn test_commit_skips_pruned_paths() {
        let (_dir, vfs, object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"hello");
        vfs.put("resources/x.faiss", b"FAISS");
        vfs.put("_system/lock", b"L");

        let resp = svc
            .commit(req("acct", "main", "filtered", None))
            .await
            .unwrap();
        let oid = match resp {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        };

        let tree = commit_tree(object_store.as_ref() as &dyn ObjectStore, "acct", oid).await;
        let all = flatten(
            object_store.as_ref() as &dyn ObjectStore,
            "acct",
            tree,
            &None,
        )
        .await
        .unwrap();
        let paths: Vec<String> = all.into_iter().map(|(p, _)| p).collect();
        assert_eq!(paths, vec!["resources/a.md".to_string()]);
    }

    // ── commit: paths supports directories ──────────────────────────────
    /// A directory in `paths` is expanded to every file under it that
    /// survives pruning. Files under the directory that were in the
    /// previous tree but have since been deleted from the VFS must drop
    /// out of the new snapshot.
    ///
    /// Backed by `LocalFileSystem`: `MockVfs::stat` returns NotFound for
    /// any directory entry, which would route this test through Step 2.5's
    /// NotFound branch instead of the Directory branch. A real filesystem
    /// is the only fixture where `stat("/local/acct/docs")` returns
    /// `is_dir = true`.
    #[tokio::test]
    async fn test_commit_paths_expands_directory_and_drops_deleted_files() {
        use crate::plugins::localfs::LocalFileSystem;

        let store_dir = tempfile::tempdir().unwrap();
        let object_store = Arc::new(LocalObjectStore::new(store_dir.path()));
        let ref_store = Arc::new(LocalRefStore::new(store_dir.path()));
        let work_dir = tempfile::tempdir().unwrap();
        let acct_root = work_dir.path().join("local").join("acct");
        std::fs::create_dir_all(acct_root.join("docs")).unwrap();
        std::fs::create_dir_all(acct_root.join("other")).unwrap();
        std::fs::write(acct_root.join("docs/a.md"), b"AA").unwrap();
        std::fs::write(acct_root.join("docs/b.md"), b"BB").unwrap();
        std::fs::write(acct_root.join("other/c.md"), b"CC").unwrap();
        let vfs: Arc<dyn FileSystem> =
            Arc::new(LocalFileSystem::new(work_dir.path().to_str().unwrap()).unwrap());
        let svc = GitService::new(vfs, object_store.clone(), ref_store);

        let _ = make_commit(&svc, "acct", "main", "first").await;

        // Delete b.md from VFS, add d.md, leave a.md unchanged.
        std::fs::remove_file(acct_root.join("docs/b.md")).unwrap();
        std::fs::write(acct_root.join("docs/d.md"), b"DD").unwrap();

        let resp = svc
            .commit(req("acct", "main", "scoped", Some(vec!["docs".into()])))
            .await
            .unwrap();
        let commit_oid = match resp {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        };

        // Verify the new tree through show():
        //   docs/a.md still present, docs/b.md gone, docs/d.md present,
        //   other/c.md untouched.
        let oid_hex = commit_oid.to_hex().to_string();
        assert!(matches!(
            svc.show(ShowRequest {
                account: "acct".into(),
                target_ref: oid_hex.clone(),
                path: Some("docs/a.md".into()),
            })
            .await,
            Ok(ShowResponse::Blob { .. })
        ));
        assert!(matches!(
            svc.show(ShowRequest {
                account: "acct".into(),
                target_ref: oid_hex.clone(),
                path: Some("docs/b.md".into()),
            })
            .await,
            Err(GitError::PathNotFound(_))
        ));
        assert!(matches!(
            svc.show(ShowRequest {
                account: "acct".into(),
                target_ref: oid_hex.clone(),
                path: Some("docs/d.md".into()),
            })
            .await,
            Ok(ShowResponse::Blob { .. })
        ));
        assert!(matches!(
            svc.show(ShowRequest {
                account: "acct".into(),
                target_ref: oid_hex,
                path: Some("other/c.md".into()),
            })
            .await,
            Ok(ShowResponse::Blob { .. })
        ));
    }

    /// If the directory passed in `paths` does not exist in the VFS at all,
    /// every file under that prefix in prev_tree is dropped from the new
    /// snapshot. A `warn!` is emitted but no error is returned.
    /// Uses MockVfs: the directory is "missing" so Step 2.5 sees NotFound.
    #[tokio::test]
    async fn test_commit_paths_notfound_directory_drops_subtree() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        vfs.put("docs/a.md", b"AA");
        vfs.put("docs/b.md", b"BB");
        vfs.put("other/c.md", b"CC");
        let _ = make_commit(&svc, "acct", "main", "first").await;

        // Whole directory disappears.
        vfs.delete("docs/a.md");
        vfs.delete("docs/b.md");

        let resp = svc
            .commit(req("acct", "main", "drop dir", Some(vec!["docs".into()])))
            .await
            .unwrap();
        let commit_oid = match resp {
            CommitResponse::Created {
                commit_oid,
                changed,
            } => {
                assert_eq!(changed, 3, "three files removed from snapshot");
                commit_oid
            }
            other => panic!("expected Created, got {other:?}"),
        };

        let oid_hex = commit_oid.to_hex().to_string();
        assert!(matches!(
            svc.show(ShowRequest {
                account: "acct".into(),
                target_ref: oid_hex.clone(),
                path: Some("docs/a.md".into()),
            })
            .await,
            Err(GitError::PathNotFound(_))
        ));
        assert!(matches!(
            svc.show(ShowRequest {
                account: "acct".into(),
                target_ref: oid_hex,
                path: Some("other/c.md".into()),
            })
            .await,
            Ok(ShowResponse::Blob { .. })
        ));
    }

    /// Pruning applies to explicit directories: passing `_system` results
    /// in a Noop commit (the directory does not exist in the VFS, but even
    /// if it did, every entry under it would be pruned).
    #[tokio::test]
    async fn test_commit_paths_pruned_directory_is_noop() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"AA");
        let first = make_commit(&svc, "acct", "main", "first").await;

        let resp = svc
            .commit(req(
                "acct",
                "main",
                "pruned dir",
                Some(vec!["_system".into()]),
            ))
            .await
            .unwrap();
        match resp {
            CommitResponse::Noop { commit_oid } => assert_eq!(commit_oid, first),
            other => panic!("expected Noop, got {other:?}"),
        }
    }

    /// Pruning applies to explicit files: passing a pruned file path is
    /// equivalent to passing nothing. Noop on top of an existing commit.
    #[tokio::test]
    async fn test_commit_paths_pruned_file_is_noop() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"AA");
        vfs.put("_system/lock", b"LL"); // pruned, never committed
        let first = make_commit(&svc, "acct", "main", "first").await;

        let resp = svc
            .commit(req(
                "acct",
                "main",
                "pruned file",
                Some(vec!["_system/lock".into()]),
            ))
            .await
            .unwrap();
        match resp {
            CommitResponse::Noop { commit_oid } => assert_eq!(commit_oid, first),
            other => panic!("expected Noop, got {other:?}"),
        }
    }

    /// Mixing a file and a directory containing that file processes each
    /// candidate exactly once. The resulting commit must record exactly
    /// the directory's content, not double-process the listed file. Uses
    /// LocalFileSystem so the Directory branch actually runs.
    #[tokio::test]
    async fn test_commit_paths_mixed_file_and_dir_dedup() {
        use crate::plugins::localfs::LocalFileSystem;

        let store_dir = tempfile::tempdir().unwrap();
        let object_store = Arc::new(LocalObjectStore::new(store_dir.path()));
        let ref_store = Arc::new(LocalRefStore::new(store_dir.path()));
        let work_dir = tempfile::tempdir().unwrap();
        let acct_root = work_dir.path().join("local").join("acct");
        std::fs::create_dir_all(acct_root.join("docs")).unwrap();
        std::fs::write(acct_root.join("docs/a.md"), b"AA").unwrap();
        std::fs::write(acct_root.join("docs/b.md"), b"BB").unwrap();
        let vfs: Arc<dyn FileSystem> =
            Arc::new(LocalFileSystem::new(work_dir.path().to_str().unwrap()).unwrap());
        let svc = GitService::new(vfs, object_store, ref_store);

        let _ = make_commit(&svc, "acct", "main", "first").await;

        // Mutate one file, then commit with both an exact file path and
        // its parent directory.
        std::fs::write(acct_root.join("docs/a.md"), b"AA2").unwrap();
        let resp = svc
            .commit(req(
                "acct",
                "main",
                "mixed",
                Some(vec!["docs/a.md".into(), "docs".into()]),
            ))
            .await
            .unwrap();
        match resp {
            CommitResponse::Created { changed, .. } => {
                assert_eq!(changed, 1, "only docs/a.md content changed");
            }
            other => panic!("expected Created, got {other:?}"),
        }
    }

    // ── 9: show ────────────────────────────────────────────────────────
    #[tokio::test]
    async fn test_show_commit_meta_by_oid() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"hello");
        let oid = make_commit(&svc, "acct", "main", "first").await;

        let resp = svc
            .show(ShowRequest {
                account: "acct".into(),
                target_ref: oid.to_hex().to_string(),
                path: None,
            })
            .await
            .unwrap();

        match resp {
            ShowResponse::Commit {
                oid: returned,
                parents,
                message,
                author,
                committer,
                tree,
            } => {
                assert_eq!(returned, oid);
                assert!(parents.is_empty(), "root commit");
                assert_eq!(message, "first");
                assert_eq!(author.name, "tester");
                assert_eq!(author.email, "tester@example.com");
                assert_eq!(committer.name, "tester");
                assert_ne!(tree, ObjectId::empty_tree(gix_hash::Kind::Sha1));
            }
            other => panic!("expected Commit, got {other:?}"),
        }
    }

    // ── 10 ─────────────────────────────────────────────────────────────
    #[tokio::test]
    async fn test_show_resolves_branch_name_and_full_ref() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"hello");
        let oid = make_commit(&svc, "acct", "main", "first").await;

        for tref in ["main", "refs/heads/main"] {
            let resp = svc
                .show(ShowRequest {
                    account: "acct".into(),
                    target_ref: tref.into(),
                    path: None,
                })
                .await
                .unwrap();
            match resp {
                ShowResponse::Commit { oid: returned, .. } => assert_eq!(returned, oid),
                other => panic!("{tref}: expected Commit, got {other:?}"),
            }
        }
    }

    // ── 10b: abbreviated OID resolution ────────────────────────────────
    #[tokio::test]
    async fn test_show_resolves_short_oid_unique() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"hello");
        let oid = make_commit(&svc, "acct", "main", "first").await;
        let full = oid.to_hex().to_string();

        for len in [4usize, 7, 12, 39] {
            let short = &full[..len];
            let resp = svc
                .show(ShowRequest {
                    account: "acct".into(),
                    target_ref: short.into(),
                    path: None,
                })
                .await
                .unwrap_or_else(|e| {
                    panic!("short oid {short} (len {len}) should resolve, got {e}")
                });
            match resp {
                ShowResponse::Commit { oid: returned, .. } => assert_eq!(returned, oid),
                other => panic!("len {len}: expected Commit, got {other:?}"),
            }
        }
    }

    #[tokio::test]
    async fn test_show_short_oid_not_found_distinguished_from_branch() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"hello");
        let _ = make_commit(&svc, "acct", "main", "first").await;

        // A 4-hex string that almost-certainly does not match any commit.
        // (SHA-1 collision against a single commit is astronomically unlikely
        // for "ffff" — the first commit's hex is deterministic given the
        // test's actor/time-zero, so this is a stable miss.)
        let bogus = "ffff";
        let err = svc
            .show(ShowRequest {
                account: "acct".into(),
                target_ref: bogus.into(),
                path: None,
            })
            .await
            .unwrap_err();
        assert!(
            matches!(err, GitError::OidPrefixNotFound { ref prefix } if prefix == bogus),
            "expected OidPrefixNotFound({bogus}), got {err:?}",
        );
    }

    #[tokio::test]
    async fn test_short_oid_three_chars_falls_through_to_ref_lookup() {
        // 3 hex chars is below the 4-char floor for abbreviated OID; it
        // should be treated as a branch name (which doesn't exist), giving
        // a RefStore::NotFound error — NOT OidPrefixNotFound.
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"hello");
        let _ = make_commit(&svc, "acct", "main", "first").await;

        let err = svc
            .show(ShowRequest {
                account: "acct".into(),
                target_ref: "abc".into(),
                path: None,
            })
            .await
            .unwrap_err();
        assert!(
            matches!(err, GitError::RefStore(RefStoreError::NotFound(_))),
            "expected RefStore::NotFound for 3-char input, got {err:?}",
        );
    }

    // ── 11 ─────────────────────────────────────────────────────────────
    #[tokio::test]
    async fn test_show_blob_round_trip() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        let body = b"hello world\n";
        vfs.put("resources/a.md", body);
        let _ = make_commit(&svc, "acct", "main", "first").await;

        let resp = svc
            .show(ShowRequest {
                account: "acct".into(),
                target_ref: "main".into(),
                path: Some("resources/a.md".into()),
            })
            .await
            .unwrap();

        match resp {
            ShowResponse::Blob {
                bytes,
                size,
                oid: _,
            } => {
                assert_eq!(bytes.as_ref(), body);
                assert_eq!(size, body.len() as u64);
            }
            other => panic!("expected Blob, got {other:?}"),
        }
    }

    // ── 12 ─────────────────────────────────────────────────────────────
    #[tokio::test]
    async fn test_show_blob_path_not_found() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"x");
        let _ = make_commit(&svc, "acct", "main", "first").await;

        let err = svc
            .show(ShowRequest {
                account: "acct".into(),
                target_ref: "main".into(),
                path: Some("resources/missing.md".into()),
            })
            .await
            .unwrap_err();

        match err {
            GitError::PathNotFound(p) => assert_eq!(p, "resources/missing.md"),
            other => panic!("expected PathNotFound, got {other:?}"),
        }
    }

    // ── 13 ─────────────────────────────────────────────────────────────
    #[tokio::test]
    async fn test_show_blob_rejects_directory_path() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"x");
        let _ = make_commit(&svc, "acct", "main", "first").await;

        let err = svc
            .show(ShowRequest {
                account: "acct".into(),
                target_ref: "main".into(),
                path: Some("resources".into()),
            })
            .await
            .unwrap_err();

        match err {
            GitError::PathIsDirectory(p) => assert_eq!(p, "resources"),
            other => panic!("expected PathIsDirectory, got {other:?}"),
        }
    }

    // ── 14 ─────────────────────────────────────────────────────────────
    #[tokio::test]
    async fn test_show_unknown_ref() {
        let (_dir, _vfs, _object_store, _ref_store, svc) = make_service("acct");
        let err = svc
            .show(ShowRequest {
                account: "acct".into(),
                target_ref: "nonexistent".into(),
                path: None,
            })
            .await
            .unwrap_err();

        match err {
            GitError::RefStore(RefStoreError::NotFound(name)) => {
                assert_eq!(name, "refs/heads/nonexistent");
            }
            other => panic!("expected RefStore NotFound, got {other:?}"),
        }
    }

    // ── 15 ─────────────────────────────────────────────────────────────
    #[tokio::test]
    async fn test_show_malformed_oid_input() {
        let (_dir, _vfs, _object_store, _ref_store, svc) = make_service("acct");
        let err = svc
            .show(ShowRequest {
                account: "acct".into(),
                target_ref: "z".repeat(40),
                path: None,
            })
            .await
            .unwrap_err();
        assert!(matches!(err, GitError::Other(_) | GitError::RefStore(_)));
    }

    // ── 16 ─────────────────────────────────────────────────────────────
    /// Blob bytes survive a round-trip even when they contain NUL bytes,
    /// non-UTF-8 sequences, and multiple newlines. Guards against any
    /// future "treat blobs as strings" regression.
    #[tokio::test]
    async fn test_show_blob_binary_and_multiline() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        // NUL, invalid UTF-8 (0xC3 0x28 is an invalid 2-byte sequence), CRLF, LF.
        let body: Vec<u8> = vec![
            b'h', b'i', 0x00, 0xC3, 0x28, b'\r', b'\n', b'l', b'i', b'n', b'e', b'2', b'\n', 0xFF,
            0xFE, 0xFD,
        ];
        vfs.put("resources/bin.dat", &body);
        let _ = make_commit(&svc, "acct", "main", "first").await;

        let resp = svc
            .show(ShowRequest {
                account: "acct".into(),
                target_ref: "main".into(),
                path: Some("resources/bin.dat".into()),
            })
            .await
            .unwrap();

        match resp {
            ShowResponse::Blob { bytes, size, .. } => {
                assert_eq!(bytes, body);
                assert_eq!(size as usize, body.len());
            }
            other => panic!("expected Blob, got {other:?}"),
        }
    }

    // ── 17 ─────────────────────────────────────────────────────────────
    /// Construct a commit whose author and committer differ, write it
    /// directly via `util::write_object`, point a ref at it, and verify
    /// `show()` decodes the two signatures into the two Actor fields
    /// without crossing them. Bypasses `commit()` because the public
    /// `CommitRequest` API only accepts one author (used for both).
    #[tokio::test]
    async fn test_show_distinguishes_committer_from_author() {
        use gix_object::{bstr::BString, Commit, WriteTo};

        let (_dir, vfs, object_store, ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"x");
        // First, create a normal commit just to get a real tree OID.
        let seed_oid = make_commit(&svc, "acct", "main", "seed").await;
        let seed_tree =
            load_commit_meta(object_store.as_ref() as &dyn ObjectStore, "acct", &seed_oid)
                .await
                .unwrap()
                .tree;

        // Build a commit with deliberately mismatched author/committer.
        let author = gix_actor::Signature {
            name: "Alice Author".into(),
            email: "alice@example.com".into(),
            time: gix_date::Time {
                seconds: 1_700_000_000,
                offset: 3600,
                sign: gix_date::time::Sign::Plus,
            },
        };
        let committer = gix_actor::Signature {
            name: "Carol Committer".into(),
            email: "carol@example.com".into(),
            time: gix_date::Time {
                seconds: 1_700_000_100,
                offset: -7200,
                sign: gix_date::time::Sign::Minus,
            },
        };
        let commit = Commit {
            tree: seed_tree,
            parents: Vec::new().into(),
            author,
            committer,
            encoding: None,
            message: BString::from("split-actors"),
            extra_headers: Vec::new(),
        };
        let mut buf = Vec::new();
        commit.write_to(&mut buf).unwrap();
        let oid = crate::git::util::write_object(
            object_store.as_ref() as &dyn ObjectStore,
            "acct",
            gix_object::Kind::Commit,
            &buf,
        )
        .await
        .unwrap();

        // Point a fresh branch at it so show() can find it by name.
        ref_store
            .cas_update("acct", "refs/heads/split", None, oid)
            .await
            .unwrap();

        let resp = svc
            .show(ShowRequest {
                account: "acct".into(),
                target_ref: "split".into(),
                path: None,
            })
            .await
            .unwrap();

        match resp {
            ShowResponse::Commit {
                author, committer, ..
            } => {
                assert_eq!(author.name, "Alice Author");
                assert_eq!(author.email, "alice@example.com");
                assert_eq!(author.time_seconds, 1_700_000_000);
                assert_eq!(author.tz_offset_seconds, 3600);
                assert_eq!(committer.name, "Carol Committer");
                assert_eq!(committer.email, "carol@example.com");
                assert_eq!(committer.time_seconds, 1_700_000_100);
                assert_eq!(committer.tz_offset_seconds, -7200);
            }
            other => panic!("expected Commit, got {other:?}"),
        }
    }

    // ── 18 ─────────────────────────────────────────────────────────────
    /// When an intermediate path component is a blob (not a tree),
    /// `tree_builder::lookup` returns `Ok(None)`, which `show()` maps
    /// to `PathNotFound`. Pin this so a future change can't silently
    /// reinterpret it as `PathIsDirectory` or `CorruptedObject`.
    #[tokio::test]
    async fn test_show_intermediate_path_component_is_blob() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"x");
        let _ = make_commit(&svc, "acct", "main", "first").await;

        let err = svc
            .show(ShowRequest {
                account: "acct".into(),
                target_ref: "main".into(),
                path: Some("resources/a.md/oops".into()),
            })
            .await
            .unwrap_err();

        match err {
            GitError::PathNotFound(p) => assert_eq!(p, "resources/a.md/oops"),
            other => panic!("expected PathNotFound, got {other:?}"),
        }
    }

    // ── 19 ─────────────────────────────────────────────────────────────
    /// `show()` validates the `path` argument up front. Empty string, a
    /// leading or trailing `/`, and embedded `//` all fail
    /// `validate_relative_path` before any tree lookup runs — callers see
    /// `InvalidPath` rather than mixed `Other` / `PathNotFound` results.
    /// This pins the contract guarding the native binding boundary against
    /// traversal-style input (`..`, `/abs`, …) being silently accepted.
    #[tokio::test]
    async fn test_show_path_with_invalid_form() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"x");
        let _ = make_commit(&svc, "acct", "main", "first").await;

        for bad in ["", "/x", "x/", "a//b"] {
            let err = svc
                .show(ShowRequest {
                    account: "acct".into(),
                    target_ref: "main".into(),
                    path: Some(bad.into()),
                })
                .await
                .unwrap_err();
            assert!(
                matches!(err, GitError::InvalidPath(_)),
                "path {bad:?}: expected InvalidPath, got {err:?}",
            );
        }
    }

    // ── 20 ─────────────────────────────────────────────────────────────
    /// If the commit's loose object file is removed from the store
    /// after the ref still points at it, `show()` must surface
    /// `ObjectStoreError::NotFound` (wrapped in `GitError::ObjectStore`).
    /// Guards against any future "swallow missing objects" regression
    /// inside `load_commit_meta`.
    #[tokio::test]
    async fn test_show_commit_object_missing_from_store() {
        let (dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"x");
        let oid = make_commit(&svc, "acct", "main", "first").await;

        // LocalObjectStore layout: {base_dir}/{account}/objects/{aa}/{bb...}
        let hex = oid.to_hex().to_string();
        let path = dir
            .path()
            .join("acct")
            .join("objects")
            .join(&hex[..2])
            .join(&hex[2..]);
        std::fs::remove_file(&path).expect("loose commit object must exist before removal");

        let err = svc
            .show(ShowRequest {
                account: "acct".into(),
                target_ref: "main".into(),
                path: None,
            })
            .await
            .unwrap_err();

        match err {
            GitError::ObjectStore(ObjectStoreError::NotFound(missing)) => {
                assert_eq!(missing, oid);
            }
            other => panic!("expected ObjectStore(NotFound), got {other:?}"),
        }
    }

    #[tokio::test]
    async fn test_mock_vfs_write_then_read_round_trip() {
        let vfs = MockVfs::new("acct");
        let path = "/local/acct/x.md";
        vfs.files
            .lock()
            .unwrap()
            .insert(path.to_string(), Vec::new());
        FileSystem::write(vfs.as_ref(), path, b"hello", 0, WriteFlag::Create)
            .await
            .unwrap();
        let got = FileSystem::read(vfs.as_ref(), path, 0, 0).await.unwrap();
        assert_eq!(got, b"hello");
        FileSystem::remove(vfs.as_ref(), path).await.unwrap();
        let err = FileSystem::read(vfs.as_ref(), path, 0, 0)
            .await
            .unwrap_err();
        assert!(matches!(err, Error::NotFound(_)));
    }

    // ── restore: dry_run ───────────────────────────────────────────────
    #[tokio::test]
    async fn test_restore_dry_run_reports_diff_and_writes_nothing() {
        let (_dir, vfs, object_store, ref_store, svc) = make_service("acct");
        // Source state: resources/proj_a has files a.md, b.md
        vfs.put("resources/proj_a/a.md", b"A v1");
        vfs.put("resources/proj_a/b.md", b"B v1");
        let source_oid = make_commit(&svc, "acct", "main", "source").await;

        // HEAD state: a.md is rewritten, b.md is deleted, c.md is created.
        // We pass explicit paths (including the deleted b.md) so commit()
        // sees the tombstone — collect_all() only enumerates surviving files.
        vfs.put("resources/proj_a/a.md", b"A v2");
        vfs.delete("resources/proj_a/b.md");
        vfs.put("resources/proj_a/c.md", b"C new");
        let head_oid = match svc
            .commit(req(
                "acct",
                "main",
                "head",
                Some(vec![
                    "resources/proj_a/a.md".to_string(),
                    "resources/proj_a/b.md".to_string(),
                    "resources/proj_a/c.md".to_string(),
                ]),
            ))
            .await
            .unwrap()
        {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        };

        let resp = svc
            .restore(RestoreRequest {
                account: "acct".into(),
                branch: "main".into(),
                project_dir: Some("resources/proj_a".into()),
                source_commit: source_oid.to_hex().to_string(),
                dry_run: true,
                message: None,
                author_name: "tester".into(),
                author_email: "tester@example.com".into(),
            })
            .await
            .unwrap();

        match resp {
            RestoreResponse::DryRun { diff, head, source } => {
                assert_eq!(source, source_oid);
                assert_eq!(head, head_oid);
                // a.md needs to roll back to v1, b.md needs to come back,
                // c.md needs to go away. Sorted alphabetically by path.
                assert_eq!(diff.to_write.len(), 2);
                assert_eq!(diff.to_write[0].0, "a.md");
                assert_eq!(diff.to_write[1].0, "b.md");
                assert_eq!(diff.to_delete, vec!["c.md".to_string()]);
                assert!(diff.unchanged.is_empty());
            }
            other => panic!("expected DryRun, got {other:?}"),
        }

        // CRITICAL: dry_run wrote nothing through the VFS — c.md and the v2
        // version of a.md must still be visible on disk.
        let files = vfs.files.lock().unwrap();
        assert_eq!(
            files.get("/local/acct/resources/proj_a/a.md").unwrap(),
            b"A v2",
            "dry_run must not overwrite a.md",
        );
        assert!(
            files.contains_key("/local/acct/resources/proj_a/c.md"),
            "dry_run must not delete c.md",
        );
        // Branch ref must still point at head_oid.
        let head_after = ref_store.read("acct", "refs/heads/main").await.unwrap();
        assert_eq!(head_after, head_oid);
        let _ = object_store; // silence unused warning
    }

    // ── restore: apply ─────────────────────────────────────────────────
    #[tokio::test]
    async fn test_restore_apply_writes_new_commit_with_head_as_parent() {
        let (_dir, vfs, object_store, ref_store, svc) = make_service("acct");
        vfs.put("resources/proj_a/a.md", b"A v1");
        vfs.put("resources/proj_a/b.md", b"B v1");
        let source_oid = make_commit(&svc, "acct", "main", "source").await;

        vfs.put("resources/proj_a/a.md", b"A v2");
        vfs.delete("resources/proj_a/b.md");
        vfs.put("resources/proj_a/c.md", b"C new");
        // IMPORTANT: use explicit paths so the deletion of b.md is captured
        let head_oid = match svc
            .commit(req(
                "acct",
                "main",
                "head",
                Some(vec![
                    "resources/proj_a/a.md".to_string(),
                    "resources/proj_a/b.md".to_string(),
                    "resources/proj_a/c.md".to_string(),
                ]),
            ))
            .await
            .unwrap()
        {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        };

        let resp = svc
            .restore(RestoreRequest {
                account: "acct".into(),
                branch: "main".into(),
                project_dir: Some("resources/proj_a".into()),
                source_commit: source_oid.to_hex().to_string(),
                dry_run: false,
                message: Some("rewind proj_a".into()),
                author_name: "tester".into(),
                author_email: "tester@example.com".into(),
            })
            .await
            .unwrap();

        let new_oid = match resp {
            RestoreResponse::Applied {
                new_commit_oid,
                source_commit,
                parent_commit,
                written,
                deleted,
                unchanged,
                written_paths,
                deleted_paths,
            } => {
                assert_eq!(source_commit, source_oid);
                assert_eq!(parent_commit, head_oid, "parent MUST be HEAD, NOT source");
                assert_eq!(written, 2, "a.md (rewrite) + b.md (recreate) = 2");
                assert_eq!(deleted, 1, "c.md");
                assert_eq!(unchanged, 0);
                assert_eq!(written_paths.len(), 2);
                assert_eq!(deleted_paths.len(), 1);
                // Paths should be account-relative (project_dir-prefixed).
                for p in &written_paths {
                    assert!(
                        p.starts_with("resources/proj_a/"),
                        "written path missing project_dir prefix: {p}"
                    );
                }
                for p in &deleted_paths {
                    assert!(
                        p.starts_with("resources/proj_a/"),
                        "deleted path missing project_dir prefix: {p}"
                    );
                }
                new_commit_oid
            }
            other => panic!("expected Applied, got {other:?}"),
        };

        // Ref now points at new_oid.
        assert_eq!(
            ref_store.read("acct", "refs/heads/main").await.unwrap(),
            new_oid
        );
        // New commit's parents = [head_oid] (NOT source_oid — this is the key
        // invariant of restore vs. plain checkout).
        let parents =
            commit_parents(object_store.as_ref() as &dyn ObjectStore, "acct", new_oid).await;
        assert_eq!(parents, vec![head_oid]);

        // VFS rolled back as expected.
        let files = vfs.files.lock().unwrap();
        assert_eq!(
            files.get("/local/acct/resources/proj_a/a.md").unwrap(),
            b"A v1",
            "a.md rolled back",
        );
        assert_eq!(
            files.get("/local/acct/resources/proj_a/b.md").unwrap(),
            b"B v1",
            "b.md restored",
        );
        assert!(
            !files.contains_key("/local/acct/resources/proj_a/c.md"),
            "c.md deleted",
        );
    }

    // Partial-writeback regression suite. Before this fix, a single failed
    // `FileSystem::write` (or non-NotFound `remove`) during step 10 would let
    // the ref keep pointing at the new commit while `try_collect` short-
    // circuited the rest of the writeback. The caller then saw a generic
    // RuntimeError and never scheduled reindex, leaving HEAD and the working
    // tree (and any vector index) inconsistent. These tests pin the new
    // behavior: every per-path op runs to completion and partial failures
    // surface as a structured `GitError::RestoreWritebackPartial`.

    /// A forced write failure for one path must produce
    /// `GitError::RestoreWritebackPartial` whose payload still reports the
    /// other writes/deletes as succeeded so the caller can reindex them.
    #[tokio::test]
    async fn test_restore_writeback_partial_returns_partial_error_on_write_failure() {
        let (_dir, vfs, _object_store, ref_store, svc) = make_service("acct");
        // Source: two files at v1.
        vfs.put("resources/proj_a/a.md", b"A v1");
        vfs.put("resources/proj_a/b.md", b"B v1");
        let source_oid = make_commit(&svc, "acct", "main", "source").await;

        // HEAD diverges: a.md updated, b.md updated, c.md added.
        vfs.put("resources/proj_a/a.md", b"A v2");
        vfs.put("resources/proj_a/b.md", b"B v2");
        vfs.put("resources/proj_a/c.md", b"C new");
        let _head_oid = match svc
            .commit(req(
                "acct",
                "main",
                "head",
                Some(vec![
                    "resources/proj_a/a.md".to_string(),
                    "resources/proj_a/b.md".to_string(),
                    "resources/proj_a/c.md".to_string(),
                ]),
            ))
            .await
            .unwrap()
        {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        };

        // Force restore's writeback of a.md to fail. The diff also rewrites
        // b.md (success) and deletes c.md (success), so we expect a partial
        // error with exactly one failed write and the other operations
        // reported under the success buckets.
        vfs.fail_write("resources/proj_a/a.md");

        let err = svc
            .restore(RestoreRequest {
                account: "acct".into(),
                branch: "main".into(),
                project_dir: Some("resources/proj_a".into()),
                source_commit: source_oid.to_hex().to_string(),
                dry_run: false,
                message: Some("rewind partial".into()),
                author_name: "tester".into(),
                author_email: "tester@example.com".into(),
            })
            .await
            .expect_err("restore must surface partial failure");

        let partial = match err {
            GitError::RestoreWritebackPartial(p) => p,
            other => panic!("expected RestoreWritebackPartial, got {other:?}"),
        };

        // Ref already advanced — partial must report the new HEAD so the
        // caller knows the commit is durable even though writeback failed.
        let head_after = ref_store.read("acct", "refs/heads/main").await.unwrap();
        assert_eq!(partial.new_commit_oid, head_after);

        assert_eq!(partial.failed_writes.len(), 1, "exactly one write failed");
        assert_eq!(partial.failed_writes[0].0, "resources/proj_a/a.md");
        assert!(
            !partial.failed_writes[0].1.is_empty(),
            "failure entry must carry a message"
        );
        assert!(partial.failed_deletes.is_empty(), "no deletes should fail");

        // The other write (b.md) succeeded and so must show up under
        // written_paths; c.md was deleted and lands under deleted_paths.
        assert_eq!(partial.written_paths, vec!["resources/proj_a/b.md"]);
        assert_eq!(partial.deleted_paths, vec!["resources/proj_a/c.md"]);
        assert_eq!(partial.written, 1);
        assert_eq!(partial.deleted, 1);
    }

    /// With two forced write failures we must still collect *both* — the
    /// stream must not short-circuit after the first one. This is the
    /// behavior change relative to the old `try_collect`.
    #[tokio::test]
    async fn test_restore_writeback_partial_continues_after_failure() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/proj_a/a.md", b"A v1");
        vfs.put("resources/proj_a/b.md", b"B v1");
        vfs.put("resources/proj_a/c.md", b"C v1");
        let source_oid = make_commit(&svc, "acct", "main", "source").await;

        vfs.put("resources/proj_a/a.md", b"A v2");
        vfs.put("resources/proj_a/b.md", b"B v2");
        vfs.put("resources/proj_a/c.md", b"C v2");
        let _head_oid = match svc
            .commit(req(
                "acct",
                "main",
                "head",
                Some(vec![
                    "resources/proj_a/a.md".to_string(),
                    "resources/proj_a/b.md".to_string(),
                    "resources/proj_a/c.md".to_string(),
                ]),
            ))
            .await
            .unwrap()
        {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        };

        // Two of the three writes fail.
        vfs.fail_write("resources/proj_a/a.md");
        vfs.fail_write("resources/proj_a/c.md");

        let err = svc
            .restore(RestoreRequest {
                account: "acct".into(),
                branch: "main".into(),
                project_dir: Some("resources/proj_a".into()),
                source_commit: source_oid.to_hex().to_string(),
                dry_run: false,
                message: None,
                author_name: "tester".into(),
                author_email: "tester@example.com".into(),
            })
            .await
            .expect_err("partial expected");

        let partial = match err {
            GitError::RestoreWritebackPartial(p) => p,
            other => panic!("expected RestoreWritebackPartial, got {other:?}"),
        };
        assert_eq!(
            partial.failed_writes.len(),
            2,
            "stream must not short-circuit on the first failure"
        );
        let mut failed: Vec<String> = partial
            .failed_writes
            .iter()
            .map(|(p, _)| p.clone())
            .collect();
        failed.sort();
        assert_eq!(
            failed,
            vec![
                "resources/proj_a/a.md".to_string(),
                "resources/proj_a/c.md".to_string(),
            ]
        );
        // b.md still rolled back.
        assert_eq!(partial.written_paths, vec!["resources/proj_a/b.md"]);
    }

    /// A forced delete failure (non-NotFound) must surface in
    /// `failed_deletes` without aborting the rest of the stream.
    #[tokio::test]
    async fn test_restore_delete_failure_does_not_short_circuit() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        // Source has only a.md.
        vfs.put("resources/proj_a/a.md", b"A v1");
        let source_oid = make_commit(&svc, "acct", "main", "source").await;

        // HEAD adds b.md and c.md — restore must delete both.
        vfs.put("resources/proj_a/b.md", b"B new");
        vfs.put("resources/proj_a/c.md", b"C new");
        let _head_oid = match svc
            .commit(req(
                "acct",
                "main",
                "head",
                Some(vec![
                    "resources/proj_a/a.md".to_string(),
                    "resources/proj_a/b.md".to_string(),
                    "resources/proj_a/c.md".to_string(),
                ]),
            ))
            .await
            .unwrap()
        {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        };

        // Force b.md's delete to fail; c.md must still be deleted.
        vfs.fail_remove("resources/proj_a/b.md");

        let err = svc
            .restore(RestoreRequest {
                account: "acct".into(),
                branch: "main".into(),
                project_dir: Some("resources/proj_a".into()),
                source_commit: source_oid.to_hex().to_string(),
                dry_run: false,
                message: None,
                author_name: "tester".into(),
                author_email: "tester@example.com".into(),
            })
            .await
            .expect_err("partial expected");

        let partial = match err {
            GitError::RestoreWritebackPartial(p) => p,
            other => panic!("expected RestoreWritebackPartial, got {other:?}"),
        };
        assert_eq!(partial.failed_deletes.len(), 1);
        assert_eq!(partial.failed_deletes[0].0, "resources/proj_a/b.md");
        assert_eq!(partial.deleted_paths, vec!["resources/proj_a/c.md"]);
        assert!(partial.failed_writes.is_empty());
    }

    /// `Error::NotFound` from `remove` is idempotent (the path was already
    /// gone) and must NOT count as a failure. With strict_remove enabled
    /// and a delete target that is already missing, restore must still
    /// return `Applied`.
    #[tokio::test]
    async fn test_restore_delete_notfound_not_counted_as_failure() {
        // Use new_strict_remove so absent paths produce NotFound rather than
        // silently succeeding — that's what the real LocalFileSystem does.
        let dir = tempfile::tempdir().unwrap();
        let object_store = Arc::new(LocalObjectStore::new(dir.path()));
        let ref_store = Arc::new(LocalRefStore::new(dir.path()));
        let vfs = MockVfs::new_strict_remove("acct");
        let svc = GitService::new(
            vfs.clone() as Arc<dyn FileSystem>,
            object_store.clone() as Arc<dyn ObjectStore>,
            ref_store.clone() as Arc<dyn RefStore>,
        );

        vfs.put("resources/proj_a/a.md", b"A v1");
        let source_oid = make_commit(&svc, "acct", "main", "source").await;

        vfs.put("resources/proj_a/b.md", b"B new");
        let _head_oid = match svc
            .commit(req(
                "acct",
                "main",
                "head",
                Some(vec![
                    "resources/proj_a/a.md".to_string(),
                    "resources/proj_a/b.md".to_string(),
                ]),
            ))
            .await
            .unwrap()
        {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        };

        // Out-of-band: remove b.md from the VFS so the diff's delete plan
        // hits the NotFound path. Restore must still return Applied.
        vfs.delete("resources/proj_a/b.md");

        let resp = svc
            .restore(RestoreRequest {
                account: "acct".into(),
                branch: "main".into(),
                project_dir: Some("resources/proj_a".into()),
                source_commit: source_oid.to_hex().to_string(),
                dry_run: false,
                message: None,
                author_name: "tester".into(),
                author_email: "tester@example.com".into(),
            })
            .await
            .expect("idempotent delete must stay on the Applied path");

        match resp {
            RestoreResponse::Applied {
                deleted_paths,
                deleted,
                ..
            } => {
                assert_eq!(
                    deleted, 1,
                    "b.md counts as deleted even though already gone"
                );
                assert_eq!(deleted_paths, vec!["resources/proj_a/b.md"]);
            }
            other => panic!("expected Applied, got {other:?}"),
        }
    }

    #[tokio::test]
    async fn test_restore_full_tree_apply_replaces_account_tree() {
        let (_dir, vfs, object_store, ref_store, svc) = make_service("acct");
        vfs.put("a.md", b"A v1");
        vfs.put("b.md", b"B v1");
        let source_oid = make_commit(&svc, "acct", "main", "source").await;

        vfs.put("a.md", b"A v2");
        vfs.put("c.md", b"C new at head");
        vfs.delete("b.md");
        let head_oid = match svc
            .commit(req(
                "acct",
                "main",
                "head",
                Some(vec![
                    "a.md".to_string(),
                    "c.md".to_string(),
                    "b.md".to_string(),
                ]),
            ))
            .await
            .unwrap()
        {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        };

        let resp = svc
            .restore(RestoreRequest {
                account: "acct".into(),
                branch: "main".into(),
                project_dir: None,
                source_commit: source_oid.to_hex().to_string(),
                dry_run: false,
                message: None,
                author_name: "tester".into(),
                author_email: "tester@example.com".into(),
            })
            .await
            .unwrap();

        let new_oid = match resp {
            RestoreResponse::Applied {
                new_commit_oid,
                source_commit,
                parent_commit,
                written,
                deleted,
                unchanged,
                written_paths,
                deleted_paths,
            } => {
                assert_eq!(source_commit, source_oid);
                assert_eq!(parent_commit, head_oid);
                assert_eq!(written, 2);
                assert_eq!(deleted, 1);
                assert_eq!(unchanged, 0);
                assert_eq!(written_paths, vec!["a.md".to_string(), "b.md".to_string()]);
                assert_eq!(deleted_paths, vec!["c.md".to_string()]);
                new_commit_oid
            }
            other => panic!("expected Applied, got {other:?}"),
        };

        assert_eq!(
            ref_store.read("acct", "refs/heads/main").await.unwrap(),
            new_oid
        );
        let parents =
            commit_parents(object_store.as_ref() as &dyn ObjectStore, "acct", new_oid).await;
        assert_eq!(parents, vec![head_oid]);

        let files = vfs.files.lock().unwrap();
        assert_eq!(files.get("/local/acct/a.md").unwrap(), b"A v1");
        assert_eq!(files.get("/local/acct/b.md").unwrap(), b"B v1");
        assert!(!files.contains_key("/local/acct/c.md"));
    }

    // Regression: restoring to a revision where a whole subdirectory's files
    // are gone must not leave an empty directory husk behind. Git does not
    // track directories, so the delete diff only lists files — restore is
    // responsible for pruning directories emptied by those deletes.
    //
    // Backed by a real `LocalFileSystem`: the in-memory `MockVfs` models
    // directories implicitly (deleting the last file makes the dir vanish for
    // free) and so cannot reproduce the husk. LocalFS keeps the directory on
    // disk, exactly like production, which is what makes this test meaningful.
    #[tokio::test]
    async fn test_restore_prunes_directories_emptied_by_delete() {
        use crate::plugins::localfs::LocalFileSystem;

        let store_dir = tempfile::tempdir().unwrap();
        let object_store = Arc::new(LocalObjectStore::new(store_dir.path()));
        let ref_store = Arc::new(LocalRefStore::new(store_dir.path()));

        // Working tree root: /local/acct lives under this temp dir.
        let work_dir = tempfile::tempdir().unwrap();
        let acct_root = work_dir.path().join("local").join("acct");
        std::fs::create_dir_all(&acct_root).unwrap();
        let vfs: Arc<dyn FileSystem> =
            Arc::new(LocalFileSystem::new(work_dir.path().to_str().unwrap()).unwrap());

        let svc = GitService::new(vfs.clone(), object_store.clone(), ref_store.clone());

        // Source commit: keeper.md at the project root only.
        std::fs::create_dir_all(acct_root.join("resources/proj_a")).unwrap();
        std::fs::write(acct_root.join("resources/proj_a/keeper.md"), b"keep").unwrap();
        let source_oid = make_commit(&svc, "acct", "main", "source").await;

        // HEAD adds a nested subdir whose only files restore will delete.
        std::fs::create_dir_all(acct_root.join("resources/proj_a/nested/deep")).unwrap();
        std::fs::write(acct_root.join("resources/proj_a/nested/x.md"), b"x").unwrap();
        std::fs::write(acct_root.join("resources/proj_a/nested/deep/y.md"), b"y").unwrap();
        let _head_oid = make_commit(&svc, "acct", "main", "head").await;

        svc.restore(RestoreRequest {
            account: "acct".into(),
            branch: "main".into(),
            project_dir: Some("resources/proj_a".into()),
            source_commit: source_oid.to_hex().to_string(),
            dry_run: false,
            message: Some("rewind".into()),
            author_name: "tester".into(),
            author_email: "tester@example.com".into(),
        })
        .await
        .unwrap();

        // Files are gone, and so are the now-empty directories that held them
        // (deepest first: deep/, then nested/).
        assert!(
            !acct_root.join("resources/proj_a/nested/deep/y.md").exists(),
            "nested/deep/y.md must be deleted",
        );
        assert!(
            !acct_root.join("resources/proj_a/nested/x.md").exists(),
            "nested/x.md must be deleted",
        );
        assert!(
            !acct_root.join("resources/proj_a/nested/deep").exists(),
            "emptied directory nested/deep must be pruned",
        );
        assert!(
            !acct_root.join("resources/proj_a/nested").exists(),
            "emptied directory nested must be pruned",
        );
        // The surviving file and its (non-empty) parent are untouched.
        assert!(
            acct_root.join("resources/proj_a/keeper.md").exists(),
            "keeper.md must survive",
        );
        assert!(
            acct_root.join("resources/proj_a").is_dir(),
            "project_dir itself must remain (still holds keeper.md)",
        );
    }

    // Regression: restoring to a source whose subtree contains files under a
    // directory that HEAD removed entirely (e.g. `rm -r` recorded as a commit
    // deletion) must recreate the missing directory chain before writing the
    // blobs back. Before `ensure_parent_dirs` was added to the writeback, this
    // aborted with `vfs: not found: .../resources/proj_a/nested`.
    #[tokio::test]
    async fn test_restore_recreates_directory_removed_by_head() {
        use crate::plugins::localfs::LocalFileSystem;

        let store_dir = tempfile::tempdir().unwrap();
        let object_store = Arc::new(LocalObjectStore::new(store_dir.path()));
        let ref_store = Arc::new(LocalRefStore::new(store_dir.path()));

        let work_dir = tempfile::tempdir().unwrap();
        let acct_root = work_dir.path().join("local").join("acct");
        std::fs::create_dir_all(&acct_root).unwrap();
        let vfs: Arc<dyn FileSystem> =
            Arc::new(LocalFileSystem::new(work_dir.path().to_str().unwrap()).unwrap());

        let svc = GitService::new(vfs.clone(), object_store.clone(), ref_store.clone());

        // Source commit: a nested directory with a file plus a top-level keeper.
        std::fs::create_dir_all(acct_root.join("resources/proj_a/nested/deep")).unwrap();
        std::fs::write(acct_root.join("resources/proj_a/keeper.md"), b"keep").unwrap();
        std::fs::write(acct_root.join("resources/proj_a/nested/x.md"), b"x v1").unwrap();
        std::fs::write(acct_root.join("resources/proj_a/nested/deep/y.md"), b"y v1").unwrap();
        let source_oid = make_commit(&svc, "acct", "main", "source").await;

        // HEAD removes the whole `nested/` directory from disk (rm -r) and the
        // full-enumeration commit records the deletion, so HEAD's tree has no
        // `nested/` subtree at all.
        std::fs::remove_dir_all(acct_root.join("resources/proj_a/nested")).unwrap();
        assert!(!acct_root.join("resources/proj_a/nested").exists());
        let _head_oid = make_commit(&svc, "acct", "main", "head").await;

        // Restore back to source: the writeback must recreate `nested/` and
        // `nested/deep/` on disk before writing x.md / y.md.
        svc.restore(RestoreRequest {
            account: "acct".into(),
            branch: "main".into(),
            project_dir: Some("resources/proj_a".into()),
            source_commit: source_oid.to_hex().to_string(),
            dry_run: false,
            message: Some("rewind".into()),
            author_name: "tester".into(),
            author_email: "tester@example.com".into(),
        })
        .await
        .unwrap();

        assert_eq!(
            std::fs::read(acct_root.join("resources/proj_a/nested/x.md")).unwrap(),
            b"x v1",
            "nested/x.md must be recreated with v1 content",
        );
        assert_eq!(
            std::fs::read(acct_root.join("resources/proj_a/nested/deep/y.md")).unwrap(),
            b"y v1",
            "nested/deep/y.md must be recreated with v1 content",
        );
        assert!(
            acct_root.join("resources/proj_a/keeper.md").exists(),
            "keeper.md must survive",
        );
    }

    // Regression: a path the restore diff wants to delete may already be absent
    // from the VFS (e.g. a derived file like `.abstract.md` removed out of
    // band). The delete must be idempotent — restore should succeed and advance
    // the branch ref rather than aborting with a `vfs: not found` error.
    #[tokio::test]
    async fn test_restore_tolerates_already_deleted_path() {
        let dir = tempfile::tempdir().unwrap();
        let object_store = Arc::new(LocalObjectStore::new(dir.path()));
        let ref_store = Arc::new(LocalRefStore::new(dir.path()));
        let vfs = MockVfs::new_strict_remove("acct");
        let svc = GitService::new(
            vfs.clone() as Arc<dyn FileSystem>,
            object_store.clone() as Arc<dyn ObjectStore>,
            ref_store.clone() as Arc<dyn RefStore>,
        );

        // Source commit: a.md plus a derived file the diff will later delete.
        vfs.put("resources/proj_a/a.md", b"A v1");
        let source_oid = make_commit(&svc, "acct", "main", "source").await;

        // HEAD adds the derived file, so restoring source wants to delete it.
        vfs.put("resources/proj_a/.abstract.md", b"derived");
        vfs.put("resources/proj_a/a.md", b"A v2");
        let head_oid = match svc
            .commit(req(
                "acct",
                "main",
                "head",
                Some(vec![
                    "resources/proj_a/a.md".to_string(),
                    "resources/proj_a/.abstract.md".to_string(),
                ]),
            ))
            .await
            .unwrap()
        {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        };

        // Simulate the derived file vanishing from the VFS out of band, so the
        // restore's delete step hits a missing path.
        vfs.delete("resources/proj_a/.abstract.md");

        let resp = svc
            .restore(RestoreRequest {
                account: "acct".into(),
                branch: "main".into(),
                project_dir: Some("resources/proj_a".into()),
                source_commit: source_oid.to_hex().to_string(),
                dry_run: false,
                message: Some("rewind".into()),
                author_name: "tester".into(),
                author_email: "tester@example.com".into(),
            })
            .await
            .expect("restore must tolerate an already-deleted path");

        let new_oid = match resp {
            RestoreResponse::Applied {
                deleted,
                deleted_paths,
                new_commit_oid,
                ..
            } => {
                // The diff still *plans* the delete (count is unchanged); the
                // VFS apply just no-ops on the missing path.
                assert_eq!(deleted, 1, ".abstract.md");
                assert_eq!(deleted_paths.len(), 1);
                new_commit_oid
            }
            other => panic!("expected Applied, got {other:?}"),
        };

        // Branch ref advanced to the new commit on top of HEAD.
        assert_eq!(
            ref_store.read("acct", "refs/heads/main").await.unwrap(),
            new_oid
        );
        let parents =
            commit_parents(object_store.as_ref() as &dyn ObjectStore, "acct", new_oid).await;
        assert_eq!(parents, vec![head_oid]);
    }

    #[tokio::test]
    async fn test_restore_noop_when_source_equals_head() {
        let (_dir, vfs, _object_store, ref_store, svc) = make_service("acct");
        vfs.put("resources/proj_a/a.md", b"only file");
        let only_oid = make_commit(&svc, "acct", "main", "only").await;

        // No further changes to proj_a — restoring from `only_oid` is a noop.
        let resp = svc
            .restore(RestoreRequest {
                account: "acct".into(),
                branch: "main".into(),
                project_dir: Some("resources/proj_a".into()),
                source_commit: only_oid.to_hex().to_string(),
                dry_run: false,
                message: None,
                author_name: "tester".into(),
                author_email: "tester@example.com".into(),
            })
            .await
            .unwrap();

        match resp {
            RestoreResponse::Noop { head, source } => {
                assert_eq!(head, only_oid);
                assert_eq!(source, only_oid);
            }
            other => panic!("expected Noop, got {other:?}"),
        }
        // Ref unchanged.
        assert_eq!(
            ref_store.read("acct", "refs/heads/main").await.unwrap(),
            only_oid
        );
    }

    #[tokio::test]
    async fn test_restore_full_tree_dry_run_reports_account_relative_diff() {
        let (_dir, vfs, _object_store, ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"A v1");
        let source_oid = make_commit(&svc, "acct", "main", "source").await;

        vfs.put("resources/a.md", b"A v2");
        vfs.put("memory/new.md", b"new");
        let head_oid = match svc
            .commit(req(
                "acct",
                "main",
                "head",
                Some(vec![
                    "resources/a.md".to_string(),
                    "memory/new.md".to_string(),
                ]),
            ))
            .await
            .unwrap()
        {
            CommitResponse::Created { commit_oid, .. } => commit_oid,
            other => panic!("expected Created, got {other:?}"),
        };

        let resp = svc
            .restore(RestoreRequest {
                account: "acct".into(),
                branch: "main".into(),
                project_dir: None,
                source_commit: source_oid.to_hex().to_string(),
                dry_run: true,
                message: None,
                author_name: "tester".into(),
                author_email: "tester@example.com".into(),
            })
            .await
            .unwrap();

        match resp {
            RestoreResponse::DryRun { diff, head, source } => {
                assert_eq!(head, head_oid);
                assert_eq!(source, source_oid);
                assert_eq!(diff.to_write.len(), 1);
                assert_eq!(diff.to_write[0].0, "resources/a.md");
                assert_eq!(diff.to_delete, vec!["memory/new.md".to_string()]);
            }
            other => panic!("expected DryRun, got {other:?}"),
        }

        let files = vfs.files.lock().unwrap();
        assert_eq!(files.get("/local/acct/resources/a.md").unwrap(), b"A v2");
        assert!(files.contains_key("/local/acct/memory/new.md"));
        drop(files);
        assert_eq!(
            ref_store.read("acct", "refs/heads/main").await.unwrap(),
            head_oid
        );
    }

    #[tokio::test]
    async fn test_restore_full_tree_noop_when_source_equals_head() {
        let (_dir, vfs, _object_store, ref_store, svc) = make_service("acct");
        vfs.put("resources/a.md", b"A v1");
        let head_oid = make_commit(&svc, "acct", "main", "source").await;

        let resp = svc
            .restore(RestoreRequest {
                account: "acct".into(),
                branch: "main".into(),
                project_dir: None,
                source_commit: head_oid.to_hex().to_string(),
                dry_run: false,
                message: None,
                author_name: "tester".into(),
                author_email: "tester@example.com".into(),
            })
            .await
            .unwrap();

        match resp {
            RestoreResponse::Noop { head, source } => {
                assert_eq!(head, head_oid);
                assert_eq!(source, head_oid);
            }
            other => panic!("expected Noop, got {other:?}"),
        }
        assert_eq!(
            ref_store.read("acct", "refs/heads/main").await.unwrap(),
            head_oid
        );
    }

    #[tokio::test]
    async fn test_restore_invalid_project_dir() {
        let (_dir, _vfs, _object_store, _ref_store, svc) = make_service("acct");
        let err = svc
            .restore(RestoreRequest {
                account: "acct".into(),
                branch: "main".into(),
                project_dir: Some("".into()), // empty
                source_commit: "main".into(),
                dry_run: true,
                message: None,
                author_name: "x".into(),
                author_email: "x@x".into(),
            })
            .await
            .unwrap_err();
        assert!(matches!(err, GitError::InvalidProjectDir(_)));
    }

    #[tokio::test]
    async fn test_restore_unknown_source_ref() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/proj_a/a.md", b"x");
        let _ = make_commit(&svc, "acct", "main", "init").await;
        let err = svc
            .restore(RestoreRequest {
                account: "acct".into(),
                branch: "main".into(),
                project_dir: Some("resources/proj_a".into()),
                source_commit: "does-not-exist".into(),
                dry_run: true,
                message: None,
                author_name: "x".into(),
                author_email: "x@x".into(),
            })
            .await
            .unwrap_err();
        assert!(matches!(
            err,
            GitError::RefStore(RefStoreError::NotFound(_))
        ));
    }

    #[tokio::test]
    async fn test_restore_unknown_branch_head() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        vfs.put("resources/proj_a/a.md", b"x");
        let only = make_commit(&svc, "acct", "main", "only").await;
        let err = svc
            .restore(RestoreRequest {
                account: "acct".into(),
                branch: "ghost".into(), // doesn't exist
                project_dir: Some("resources/proj_a".into()),
                source_commit: only.to_hex().to_string(),
                dry_run: true,
                message: None,
                author_name: "x".into(),
                author_email: "x@x".into(),
            })
            .await
            .unwrap_err();
        assert!(matches!(
            err,
            GitError::RefStore(RefStoreError::NotFound(_))
        ));
    }

    #[tokio::test]
    async fn test_restore_project_dir_missing_in_source_commit() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");
        // Source commit only has resources/other_proj.
        vfs.put("resources/other_proj/x.md", b"x");
        let source_oid = make_commit(&svc, "acct", "main", "source").await;
        // HEAD has the project we will try to restore.
        vfs.put("resources/proj_a/a.md", b"a");
        let _ = make_commit(&svc, "acct", "main", "head").await;

        let err = svc
            .restore(RestoreRequest {
                account: "acct".into(),
                branch: "main".into(),
                project_dir: Some("resources/proj_a".into()),
                source_commit: source_oid.to_hex().to_string(),
                dry_run: true,
                message: None,
                author_name: "x".into(),
                author_email: "x@x".into(),
            })
            .await
            .unwrap_err();
        match err {
            GitError::SubtreeNotFoundInCommit {
                project_dir,
                commit,
            } => {
                assert_eq!(project_dir, "resources/proj_a");
                assert_eq!(commit, source_oid);
            }
            other => panic!("expected SubtreeNotFoundInCommit, got {other:?}"),
        }
    }

    #[tokio::test]
    async fn test_restore_cas_conflict_surfaces_as_error() {
        let dir = tempfile::tempdir().unwrap();
        let object_store = Arc::new(LocalObjectStore::new(dir.path()));
        let inner_ref = Arc::new(LocalRefStore::new(dir.path()));
        let vfs = MockVfs::new("acct");

        // Build a real first commit through a plain service so we have a HEAD.
        let bootstrap_svc = GitService::new(
            vfs.clone() as Arc<dyn FileSystem>,
            object_store.clone() as Arc<dyn ObjectStore>,
            inner_ref.clone() as Arc<dyn RefStore>,
        );
        vfs.put("resources/proj_a/a.md", b"v1");
        let source_oid = make_commit(&bootstrap_svc, "acct", "main", "source").await;
        vfs.put("resources/proj_a/a.md", b"v2");
        let head_oid = make_commit(&bootstrap_svc, "acct", "main", "head").await;

        // Now wrap the ref store to force the first cas_update to fail.
        let bogus = ObjectId::from_hex(b"deadbeefdeadbeefdeadbeefdeadbeefdeadbeef").unwrap();
        let conflict_ref = Arc::new(ConflictOnceRef {
            inner: inner_ref.clone(),
            fired: Mutex::new(false),
            actual: Some(bogus),
        });
        let svc = GitService::new(
            vfs.clone() as Arc<dyn FileSystem>,
            object_store.clone() as Arc<dyn ObjectStore>,
            conflict_ref as Arc<dyn RefStore>,
        );

        let err = svc
            .restore(RestoreRequest {
                account: "acct".into(),
                branch: "main".into(),
                project_dir: Some("resources/proj_a".into()),
                source_commit: source_oid.to_hex().to_string(),
                dry_run: false,
                message: None,
                author_name: "x".into(),
                author_email: "x@x".into(),
            })
            .await
            .unwrap_err();
        match err {
            GitError::ConcurrentCommit {
                ref_name,
                expected,
                actual,
            } => {
                assert_eq!(ref_name, "refs/heads/main");
                assert_eq!(expected, Some(head_oid));
                assert_eq!(actual, Some(bogus));
            }
            other => panic!("expected ConcurrentCommit, got {other:?}"),
        }
    }

    /// Regression: a losing CAS race during restore must leave the VFS
    /// byte-identical to its pre-restore (HEAD) state. The ref-consistency
    /// protocol now runs before any writeback, so a `ConcurrentCommit` error
    /// implies zero working-tree mutations — neither `to_write` content nor
    /// `to_delete` removals are applied. This keeps the failed request, the
    /// on-disk working tree, and the caller's reindex decision consistent.
    #[tokio::test]
    async fn test_restore_cas_conflict_leaves_vfs_unchanged() {
        let dir = tempfile::tempdir().unwrap();
        let object_store = Arc::new(LocalObjectStore::new(dir.path()));
        let inner_ref = Arc::new(LocalRefStore::new(dir.path()));
        let vfs = MockVfs::new("acct");

        let bootstrap_svc = GitService::new(
            vfs.clone() as Arc<dyn FileSystem>,
            object_store.clone() as Arc<dyn ObjectStore>,
            inner_ref.clone() as Arc<dyn RefStore>,
        );

        // Source commit: a single file under the project dir.
        vfs.put("resources/proj_a/a.md", b"v1");
        let source_oid = make_commit(&bootstrap_svc, "acct", "main", "source").await;

        // HEAD commit: a.md is modified (would be a `to_write`) and a brand new
        // file is added (absent in source → would be a `to_delete` on restore).
        vfs.put("resources/proj_a/a.md", b"v2");
        vfs.put("resources/proj_a/b.md", b"new");
        let head_oid = make_commit(&bootstrap_svc, "acct", "main", "head").await;

        // Snapshot the working tree exactly as it stands at HEAD.
        let before = vfs.files.lock().unwrap().clone();

        // Force the first cas_update to conflict.
        let bogus = ObjectId::from_hex(b"deadbeefdeadbeefdeadbeefdeadbeefdeadbeef").unwrap();
        let conflict_ref = Arc::new(ConflictOnceRef {
            inner: inner_ref.clone(),
            fired: Mutex::new(false),
            actual: Some(bogus),
        });
        let svc = GitService::new(
            vfs.clone() as Arc<dyn FileSystem>,
            object_store.clone() as Arc<dyn ObjectStore>,
            conflict_ref as Arc<dyn RefStore>,
        );

        let err = svc
            .restore(RestoreRequest {
                account: "acct".into(),
                branch: "main".into(),
                project_dir: Some("resources/proj_a".into()),
                source_commit: source_oid.to_hex().to_string(),
                dry_run: false,
                message: None,
                author_name: "x".into(),
                author_email: "x@x".into(),
            })
            .await
            .unwrap_err();
        assert!(
            matches!(err, GitError::ConcurrentCommit { .. }),
            "expected ConcurrentCommit, got {err:?}"
        );

        // The working tree must be untouched: a.md still v2 (not rewritten to
        // v1) and b.md still present (not deleted).
        let after = vfs.files.lock().unwrap().clone();
        assert_eq!(after, before, "VFS must not change on a CAS conflict");
        assert_eq!(
            after
                .get("/local/acct/resources/proj_a/a.md")
                .map(|v| v.as_slice()),
            Some(b"v2".as_slice()),
            "a.md must keep its HEAD content"
        );
        assert!(
            after.contains_key("/local/acct/resources/proj_a/b.md"),
            "b.md must not be deleted"
        );

        // And HEAD must still point at the original commit.
        let head_now = inner_ref.read("acct", "refs/heads/main").await.unwrap();
        assert_eq!(head_now, head_oid, "branch ref must be unchanged");
    }

    #[tokio::test]
    async fn test_restore_does_not_touch_paths_outside_project_dir() {
        let (_dir, vfs, object_store, _ref_store, svc) = make_service("acct");

        // Source: resources/proj_a + an UNRELATED file in another scope.
        vfs.put("resources/proj_a/a.md", b"A v1");
        vfs.put("agent/skills/unrelated.py", b"unrelated v1");
        let source_oid = make_commit(&svc, "acct", "main", "source").await;

        // HEAD: modify proj_a AND the unrelated file. Note we don't delete
        // anything in this test, so make_commit (which uses collect_all) is
        // fine — all files still exist in the VFS.
        vfs.put("resources/proj_a/a.md", b"A v2");
        vfs.put("agent/skills/unrelated.py", b"unrelated v2");
        vfs.put("agent/skills/new_skill.py", b"brand new");
        let _ = make_commit(&svc, "acct", "main", "head").await;

        let resp = svc
            .restore(RestoreRequest {
                account: "acct".into(),
                branch: "main".into(),
                project_dir: Some("resources/proj_a".into()),
                source_commit: source_oid.to_hex().to_string(),
                dry_run: false,
                message: None,
                author_name: "x".into(),
                author_email: "x@x".into(),
            })
            .await
            .unwrap();

        let new_oid = match resp {
            RestoreResponse::Applied { new_commit_oid, .. } => new_commit_oid,
            other => panic!("expected Applied, got {other:?}"),
        };

        // Verify the VFS: unrelated files keep their v2 / new state.
        let files = vfs.files.lock().unwrap();
        assert_eq!(
            files.get("/local/acct/agent/skills/unrelated.py").unwrap(),
            b"unrelated v2",
            "restore must NOT roll back unrelated.py",
        );
        assert!(
            files.contains_key("/local/acct/agent/skills/new_skill.py"),
            "restore must NOT delete new_skill.py",
        );
        // And proj_a/a.md DID roll back.
        assert_eq!(
            files.get("/local/acct/resources/proj_a/a.md").unwrap(),
            b"A v1",
        );
        drop(files);

        // Verify the tree: the new commit's tree should contain the v2 content
        // of unrelated.py and new_skill.py at their original oids. The easiest
        // way: lookup the oid of agent/skills/unrelated.py in both source and
        // new — they must DIFFER (source had v1, new still has v2).
        let new_tree =
            load_commit_meta(object_store.as_ref() as &dyn ObjectStore, "acct", &new_oid)
                .await
                .unwrap()
                .tree;
        let source_tree = load_commit_meta(
            object_store.as_ref() as &dyn ObjectStore,
            "acct",
            &source_oid,
        )
        .await
        .unwrap()
        .tree;
        let unrelated_in_new = crate::git::tree_builder::lookup(
            object_store.as_ref() as &dyn ObjectStore,
            "acct",
            new_tree,
            "agent/skills/unrelated.py",
        )
        .await
        .unwrap()
        .unwrap();
        let unrelated_in_source = crate::git::tree_builder::lookup(
            object_store.as_ref() as &dyn ObjectStore,
            "acct",
            source_tree,
            "agent/skills/unrelated.py",
        )
        .await
        .unwrap()
        .unwrap();
        assert_ne!(
            unrelated_in_new.0, unrelated_in_source.0,
            "agent/skills/unrelated.py in the new tree must be HEAD's v2 oid, not source's v1 oid",
        );
        assert!(
            crate::git::tree_builder::lookup(
                object_store.as_ref() as &dyn ObjectStore,
                "acct",
                new_tree,
                "agent/skills/new_skill.py",
            )
            .await
            .unwrap()
            .is_some(),
            "new_skill.py must still be present in the new tree",
        );
    }

    #[tokio::test]
    async fn test_restore_then_show_reflects_old_content() {
        let (_dir, vfs, _object_store, _ref_store, svc) = make_service("acct");

        vfs.put("resources/proj_a/note.md", b"original");
        let src = make_commit(&svc, "acct", "main", "src").await;

        vfs.put("resources/proj_a/note.md", b"edited");
        let _ = make_commit(&svc, "acct", "main", "edit").await;

        // Sanity: show on HEAD shows "edited".
        let head_show = svc
            .show(ShowRequest {
                account: "acct".into(),
                target_ref: "main".into(),
                path: Some("resources/proj_a/note.md".into()),
            })
            .await
            .unwrap();
        match head_show {
            ShowResponse::Blob { bytes, .. } => assert_eq!(bytes.as_ref(), b"edited"),
            other => panic!("expected Blob, got {other:?}"),
        }

        // Restore.
        let new_oid = match svc
            .restore(RestoreRequest {
                account: "acct".into(),
                branch: "main".into(),
                project_dir: Some("resources/proj_a".into()),
                source_commit: src.to_hex().to_string(),
                dry_run: false,
                message: Some("rewind".into()),
                author_name: "x".into(),
                author_email: "x@x".into(),
            })
            .await
            .unwrap()
        {
            RestoreResponse::Applied { new_commit_oid, .. } => new_commit_oid,
            other => panic!("expected Applied, got {other:?}"),
        };

        // After restore: show on main should reflect the original content.
        let after_show = svc
            .show(ShowRequest {
                account: "acct".into(),
                target_ref: "main".into(),
                path: Some("resources/proj_a/note.md".into()),
            })
            .await
            .unwrap();
        match after_show {
            ShowResponse::Blob { bytes, .. } => assert_eq!(bytes.as_ref(), b"original"),
            other => panic!("expected Blob, got {other:?}"),
        }

        // And show on the new oid by hex resolves to the same content.
        let by_oid = svc
            .show(ShowRequest {
                account: "acct".into(),
                target_ref: new_oid.to_hex().to_string(),
                path: Some("resources/proj_a/note.md".into()),
            })
            .await
            .unwrap();
        match by_oid {
            ShowResponse::Blob { bytes, .. } => assert_eq!(bytes.as_ref(), b"original"),
            other => panic!("expected Blob, got {other:?}"),
        }
    }

    // ── Fast Path 3: blob exists precheck ───────────────────────────────
    /// ObjectStore wrapper that counts `put` / `exists` calls, delegating to
    /// an inner `LocalObjectStore`.
    struct CountingObjectStore {
        inner: LocalObjectStore,
        puts: std::sync::atomic::AtomicUsize,
        exists_calls: std::sync::atomic::AtomicUsize,
    }

    #[async_trait]
    impl ObjectStore for CountingObjectStore {
        async fn put(
            &self,
            account: &str,
            oid: &ObjectId,
            zlib_body: bytes::Bytes,
        ) -> std::result::Result<(), ObjectStoreError> {
            self.puts.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            self.inner.put(account, oid, zlib_body).await
        }
        async fn get(
            &self,
            account: &str,
            oid: &ObjectId,
        ) -> std::result::Result<bytes::Bytes, ObjectStoreError> {
            self.inner.get(account, oid).await
        }
        async fn exists(
            &self,
            account: &str,
            oid: &ObjectId,
        ) -> std::result::Result<bool, ObjectStoreError> {
            self.exists_calls
                .fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            self.inner.exists(account, oid).await
        }
    }

    #[tokio::test]
    async fn test_commit_fast_path_3_skips_put_for_duplicate_blob() {
        use std::sync::atomic::Ordering;
        let dir = tempfile::tempdir().unwrap();
        let object_store = Arc::new(CountingObjectStore {
            inner: LocalObjectStore::new(dir.path()),
            puts: std::sync::atomic::AtomicUsize::new(0),
            exists_calls: std::sync::atomic::AtomicUsize::new(0),
        });
        let ref_store = Arc::new(LocalRefStore::new(dir.path()));
        let vfs = MockVfs::new("acct");
        // No index store → slow path runs → Fast Path 3 active (default on).
        let svc = GitService::new(
            vfs.clone() as Arc<dyn FileSystem>,
            object_store.clone() as Arc<dyn ObjectStore>,
            ref_store as Arc<dyn RefStore>,
        );

        vfs.put("a.md", b"dup");
        svc.commit(req("acct", "main", "first", None))
            .await
            .unwrap();

        // Commit a second file with identical content → same blob oid. The
        // blob `put` must be skipped (exists hit); only the new root tree and
        // commit object are written (2 puts, no blob put).
        vfs.put("b.md", b"dup");
        let puts_before = object_store.puts.load(Ordering::SeqCst);
        let exists_before = object_store.exists_calls.load(Ordering::SeqCst);
        svc.commit(req("acct", "main", "second", Some(vec!["b.md".into()])))
            .await
            .unwrap();
        let put_delta = object_store.puts.load(Ordering::SeqCst) - puts_before;

        // exists() was consulted on the second commit's slow path.
        assert!(object_store.exists_calls.load(Ordering::SeqCst) > exists_before);
        // Only tree + commit objects were put — the duplicate blob was skipped.
        assert_eq!(put_delta, 2, "duplicate blob must not be re-put");
    }

    #[tokio::test]
    async fn test_commit_fast_path_3_disabled_reputs_duplicate_blob() {
        use std::sync::atomic::Ordering;
        let dir = tempfile::tempdir().unwrap();
        let object_store = Arc::new(CountingObjectStore {
            inner: LocalObjectStore::new(dir.path()),
            puts: std::sync::atomic::AtomicUsize::new(0),
            exists_calls: std::sync::atomic::AtomicUsize::new(0),
        });
        let ref_store = Arc::new(LocalRefStore::new(dir.path()));
        let vfs = MockVfs::new("acct");
        let svc = GitService::new(
            vfs.clone() as Arc<dyn FileSystem>,
            object_store.clone() as Arc<dyn ObjectStore>,
            ref_store as Arc<dyn RefStore>,
        )
        .with_blob_exists_precheck(false);

        vfs.put("a.md", b"dup");
        svc.commit(req("acct", "main", "first", None))
            .await
            .unwrap();
        let exists_after_first = object_store.exists_calls.load(Ordering::SeqCst);

        vfs.put("b.md", b"dup");
        let puts_before = object_store.puts.load(Ordering::SeqCst);
        svc.commit(req("acct", "main", "second", Some(vec!["b.md".into()])))
            .await
            .unwrap();

        // With precheck off, the slow path calls write_object unconditionally
        // → at least one put for the dup blob (idempotent at the backend).
        assert!(object_store.puts.load(Ordering::SeqCst) > puts_before);
        // And no extra exists() calls were issued from the blob write path on
        // the second commit (precheck disabled). The backend's own put
        // idempotency uses try_exists internally, not ObjectStore::exists.
        assert_eq!(
            object_store.exists_calls.load(Ordering::SeqCst),
            exists_after_first
        );

        // Result correctness: both files resolve to the same content.
        let show_b = svc
            .show(ShowRequest {
                account: "acct".into(),
                target_ref: "main".into(),
                path: Some("b.md".into()),
            })
            .await
            .unwrap();
        match show_b {
            ShowResponse::Blob { bytes, .. } => assert_eq!(bytes.as_ref(), b"dup"),
            other => panic!("expected Blob, got {other:?}"),
        }
    }

    // ── prev_tree lookup cache: each ancestor tree loaded once per commit ──
    /// ObjectStore wrapper that records every `get` oid so we can prove the
    /// commit loop re-fetches each prev_tree subtree at most once.
    struct GetSpyObjectStore {
        inner: LocalObjectStore,
        gets: std::sync::Mutex<Vec<ObjectId>>,
    }

    impl GetSpyObjectStore {
        fn count_gets(&self, oid: &ObjectId) -> usize {
            self.gets
                .lock()
                .unwrap()
                .iter()
                .filter(|o| *o == oid)
                .count()
        }
        fn reset(&self) {
            self.gets.lock().unwrap().clear();
        }
    }

    #[async_trait]
    impl ObjectStore for GetSpyObjectStore {
        async fn put(
            &self,
            account: &str,
            oid: &ObjectId,
            zlib_body: bytes::Bytes,
        ) -> std::result::Result<(), ObjectStoreError> {
            self.inner.put(account, oid, zlib_body).await
        }
        async fn get(
            &self,
            account: &str,
            oid: &ObjectId,
        ) -> std::result::Result<bytes::Bytes, ObjectStoreError> {
            self.gets.lock().unwrap().push(*oid);
            self.inner.get(account, oid).await
        }
        async fn exists(
            &self,
            account: &str,
            oid: &ObjectId,
        ) -> std::result::Result<bool, ObjectStoreError> {
            self.inner.exists(account, oid).await
        }
    }

    #[tokio::test]
    async fn test_commit_prev_tree_lookup_cache_amortises_ancestors() {
        // Build a prev tree where many candidates share the same depth-3
        // ancestor chain (root → resources → docs). Without the lookup cache,
        // each candidate would re-fetch all three trees from object_store on
        // its `lookup(prev_tree, ...)` call. With the cache, every ancestor
        // along that chain is fetched at most once for the whole commit.
        let dir = tempfile::tempdir().unwrap();
        let object_store = Arc::new(GetSpyObjectStore {
            inner: LocalObjectStore::new(dir.path()),
            gets: std::sync::Mutex::new(Vec::new()),
        });
        let ref_store = Arc::new(LocalRefStore::new(dir.path()));
        let vfs = MockVfs::new("acct");
        let svc = GitService::new(
            vfs.clone() as Arc<dyn FileSystem>,
            object_store.clone() as Arc<dyn ObjectStore>,
            ref_store as Arc<dyn RefStore>,
        );

        // First commit: seed prev_tree with 5 files all under resources/docs/.
        for name in ["a", "b", "c", "d", "e"] {
            vfs.put(&format!("resources/docs/{}.md", name), name.as_bytes());
        }
        svc.commit(req("acct", "main", "seed", None)).await.unwrap();

        // Capture root tree oid and its ancestor chain to resources/docs.
        let head_resp = svc
            .show(ShowRequest {
                account: "acct".into(),
                target_ref: "main".into(),
                path: None,
            })
            .await
            .unwrap();
        let root_tree_oid = match head_resp {
            ShowResponse::Commit { tree, .. } => tree,
            _ => panic!("expected commit"),
        };
        // Resolve resources/docs tree oid by walking root once.
        let mut cache = crate::git::tree_builder::TreeLookupCache::new();
        let (resources_oid, _) = crate::git::tree_builder::lookup_cached(
            object_store.as_ref(),
            "acct",
            root_tree_oid,
            "resources",
            &mut cache,
        )
        .await
        .unwrap()
        .unwrap();
        let (docs_oid, _) = crate::git::tree_builder::lookup_cached(
            object_store.as_ref(),
            "acct",
            root_tree_oid,
            "resources/docs",
            &mut cache,
        )
        .await
        .unwrap()
        .unwrap();

        // Reset spy, then run a commit that touches all 5 candidates with the
        // same content (so every Fast Path 1 miss path runs the prev lookup
        // for the no-op skip check). The assertion proves each ancestor tree
        // is fetched at most once across all 5 lookups.
        object_store.reset();
        let candidates: Vec<String> = ["a", "b", "c", "d", "e"]
            .iter()
            .map(|n| format!("resources/docs/{}.md", n))
            .collect();
        svc.commit(req("acct", "main", "rewrite", Some(candidates)))
            .await
            .unwrap();

        // Each ancestor on the prev_tree chain was fetched at most once.
        assert!(
            object_store.count_gets(&root_tree_oid) <= 1,
            "root tree was fetched {} times, expected ≤1 (cache miss)",
            object_store.count_gets(&root_tree_oid)
        );
        assert!(
            object_store.count_gets(&resources_oid) <= 1,
            "resources tree was fetched {} times, expected ≤1 (cache miss)",
            object_store.count_gets(&resources_oid)
        );
        assert!(
            object_store.count_gets(&docs_oid) <= 1,
            "resources/docs tree was fetched {} times, expected ≤1 (cache miss)",
            object_store.count_gets(&docs_oid)
        );
    }

    // ── account id validation ───────────────────────────────────────────
    #[test]
    fn validate_account_id_accepts_valid() {
        for ok in ["acct", "a", "user-1", "u_2", "name.tag", "a@b", "ABC123"] {
            assert!(validate_account_id(ok).is_ok(), "{ok:?} should be valid");
        }
    }

    #[test]
    fn validate_account_id_rejects_malicious() {
        for bad in [
            "",        // empty
            ".",       // dot
            "..",      // parent
            "../x",    // traversal
            "a/b",     // slash
            "a\\b",    // backslash
            "a\0b",    // NUL
            "a\nb",    // newline / control
            "a b",     // space
            "a@b@c",   // multiple @
            "_system", // leading underscore
        ] {
            assert!(
                matches!(validate_account_id(bad), Err(GitError::InvalidAccountId(_))),
                "{bad:?} should be rejected",
            );
        }
    }

    #[tokio::test]
    async fn commit_rejects_traversal_account() {
        // A crafted account must be rejected before any path is built, so the
        // ref store is never even touched.
        let (_dir, _vfs, _object_store, _ref_store, svc) = make_service("acct");
        let err = svc.commit(req("../escape", "main", "msg", None)).await;
        assert!(matches!(err, Err(GitError::InvalidAccountId(_))));
    }

    #[tokio::test]
    async fn commit_rejects_slash_account() {
        let (_dir, _vfs, _object_store, _ref_store, svc) = make_service("acct");
        let err = svc.commit(req("a/b", "main", "msg", None)).await;
        assert!(matches!(err, Err(GitError::InvalidAccountId(_))));
    }

    #[tokio::test]
    async fn show_rejects_traversal_account() {
        let (_dir, _vfs, _object_store, _ref_store, svc) = make_service("acct");
        let err = svc
            .show(ShowRequest {
                account: "../escape".into(),
                target_ref: "main".into(),
                path: None,
            })
            .await;
        assert!(matches!(err, Err(GitError::InvalidAccountId(_))));
    }

    #[tokio::test]
    async fn restore_rejects_traversal_account() {
        let (_dir, _vfs, _object_store, _ref_store, svc) = make_service("acct");
        let err = svc
            .restore(RestoreRequest {
                account: "../escape".into(),
                branch: "main".into(),
                project_dir: Some("resources/x".into()),
                source_commit: "deadbeef".into(),
                dry_run: false,
                message: None,
                author_name: "n".into(),
                author_email: "e".into(),
            })
            .await;
        assert!(matches!(err, Err(GitError::InvalidAccountId(_))));
    }

    // ── direct-binding traversal defence: account is OK, but the caller
    //    tries to escape via paths / project_dir / show path. These must be
    //    rejected before any VFS / object-store I/O. ──────────────────────
    #[tokio::test]
    async fn commit_rejects_traversal_in_paths() {
        let (_dir, _vfs, _object_store, _ref_store, svc) = make_service("acct");
        for bad in [
            "../other/file.md",
            "a/../../other.md",
            "/abs.md",
            "a/./b.md",
            "a\\b.md",
        ] {
            let err = svc
                .commit(req("acct", "main", "msg", Some(vec![bad.to_string()])))
                .await;
            assert!(
                matches!(err, Err(GitError::InvalidPath(_))),
                "{bad:?} should yield InvalidPath, got {err:?}",
            );
        }
    }

    #[tokio::test]
    async fn show_rejects_traversal_path() {
        let (_dir, _vfs, _object_store, _ref_store, svc) = make_service("acct");
        for bad in ["../other.md", ".", "..", "a/../b", ""] {
            let err = svc
                .show(ShowRequest {
                    account: "acct".into(),
                    target_ref: "main".into(),
                    path: Some(bad.to_string()),
                })
                .await;
            assert!(
                matches!(err, Err(GitError::InvalidPath(_))),
                "{bad:?} should yield InvalidPath, got {err:?}",
            );
        }
    }

    #[tokio::test]
    async fn restore_rejects_traversal_project_dir() {
        let (_dir, _vfs, _object_store, _ref_store, svc) = make_service("acct");
        for bad in ["../other", ".", "..", "a/../b", "a/./b", "a\\b"] {
            let err = svc
                .restore(RestoreRequest {
                    account: "acct".into(),
                    branch: "main".into(),
                    project_dir: Some(bad.to_string()),
                    source_commit: "deadbeef".into(),
                    dry_run: false,
                    message: None,
                    author_name: "n".into(),
                    author_email: "e".into(),
                })
                .await;
            assert!(
                matches!(err, Err(GitError::InvalidProjectDir(_))),
                "{bad:?} should yield InvalidProjectDir, got {err:?}",
            );
        }
    }
}

#[cfg(test)]
mod diff_tests {
    use super::*;
    use crate::git::types::RestoreDiff;
    use gix_hash::ObjectId;

    fn oid(byte: u8) -> ObjectId {
        let mut bytes = [0u8; 20];
        bytes.fill(byte);
        ObjectId::from_bytes_or_panic(&bytes)
    }

    #[test]
    fn diff_empty_both() {
        let got = compute_subtree_diff(&[], &[]);
        assert_eq!(
            got,
            RestoreDiff {
                to_write: vec![],
                to_delete: vec![],
                unchanged: vec![]
            }
        );
    }

    #[test]
    fn diff_all_writes_when_head_empty() {
        let source = vec![("a.md".to_string(), oid(0xAA))];
        let got = compute_subtree_diff(&source, &[]);
        assert_eq!(got.to_write, vec![("a.md".to_string(), oid(0xAA))]);
        assert!(got.to_delete.is_empty());
        assert!(got.unchanged.is_empty());
    }

    #[test]
    fn diff_all_deletes_when_source_empty() {
        let head = vec![("b.md".to_string(), oid(0xBB))];
        let got = compute_subtree_diff(&[], &head);
        assert!(got.to_write.is_empty());
        assert_eq!(got.to_delete, vec!["b.md".to_string()]);
        assert!(got.unchanged.is_empty());
    }

    #[test]
    fn diff_unchanged_same_oid_same_path() {
        let entries = vec![("a.md".to_string(), oid(0xCC))];
        let got = compute_subtree_diff(&entries, &entries);
        assert!(got.to_write.is_empty());
        assert!(got.to_delete.is_empty());
        assert_eq!(got.unchanged, vec!["a.md".to_string()]);
    }

    #[test]
    fn diff_overwrite_when_same_path_different_oid() {
        let source = vec![("a.md".to_string(), oid(0xAA))];
        let head = vec![("a.md".to_string(), oid(0xBB))];
        let got = compute_subtree_diff(&source, &head);
        assert_eq!(got.to_write, vec![("a.md".to_string(), oid(0xAA))]);
        assert!(got.to_delete.is_empty());
        assert!(got.unchanged.is_empty());
    }

    #[test]
    fn diff_mixed_buckets_sorted_deterministically() {
        let source = vec![
            ("keep.md".to_string(), oid(0x11)),
            ("change.md".to_string(), oid(0x22)),
            ("new.md".to_string(), oid(0x33)),
        ];
        let head = vec![
            ("keep.md".to_string(), oid(0x11)),
            ("change.md".to_string(), oid(0x99)),
            ("gone.md".to_string(), oid(0x44)),
        ];
        let got = compute_subtree_diff(&source, &head);
        assert_eq!(
            got.to_write,
            vec![
                ("change.md".to_string(), oid(0x22)),
                ("new.md".to_string(), oid(0x33)),
            ]
        );
        assert_eq!(got.to_delete, vec!["gone.md".to_string()]);
        assert_eq!(got.unchanged, vec!["keep.md".to_string()]);
    }

    #[test]
    fn diff_handles_nested_paths() {
        let source = vec![
            ("docs/a.md".to_string(), oid(0xAA)),
            ("docs/sub/b.md".to_string(), oid(0xBB)),
        ];
        let head = vec![("docs/a.md".to_string(), oid(0xAA))];
        let got = compute_subtree_diff(&source, &head);
        assert_eq!(got.to_write, vec![("docs/sub/b.md".to_string(), oid(0xBB))]);
        assert!(got.to_delete.is_empty());
        assert_eq!(got.unchanged, vec!["docs/a.md".to_string()]);
    }

    #[test]
    fn validate_rejects_empty_string() {
        let err = validate_project_dir("").unwrap_err();
        assert!(matches!(err, GitError::InvalidProjectDir(_)));
    }

    #[test]
    fn validate_rejects_leading_slash() {
        assert!(matches!(
            validate_project_dir("/resources/proj_a").unwrap_err(),
            GitError::InvalidProjectDir(_)
        ));
    }

    #[test]
    fn validate_rejects_trailing_slash() {
        assert!(matches!(
            validate_project_dir("resources/proj_a/").unwrap_err(),
            GitError::InvalidProjectDir(_)
        ));
    }

    #[test]
    fn validate_rejects_double_slash() {
        assert!(matches!(
            validate_project_dir("resources//proj_a").unwrap_err(),
            GitError::InvalidProjectDir(_)
        ));
    }

    #[test]
    fn validate_accepts_simple_path() {
        validate_project_dir("resources/proj_a").unwrap();
    }

    #[test]
    fn validate_accepts_single_segment() {
        validate_project_dir("resources").unwrap();
    }

    // ── project_dir hardening (traversal / backslash / control) ─────────
    #[test]
    fn validate_project_dir_rejects_dotdot_segment() {
        for bad in ["..", "../other", "resources/../other", "a/.."] {
            assert!(
                matches!(
                    validate_project_dir(bad),
                    Err(GitError::InvalidProjectDir(_))
                ),
                "{bad:?} should be rejected",
            );
        }
    }

    #[test]
    fn validate_project_dir_rejects_dot_segment() {
        for bad in [".", "./x", "a/./b"] {
            assert!(matches!(
                validate_project_dir(bad),
                Err(GitError::InvalidProjectDir(_))
            ));
        }
    }

    #[test]
    fn validate_project_dir_rejects_backslash_and_control() {
        for bad in ["a\\b", "a\0b", "a\nb"] {
            assert!(
                matches!(
                    validate_project_dir(bad),
                    Err(GitError::InvalidProjectDir(_))
                ),
                "{bad:?} should be rejected",
            );
        }
    }

    // ── relative path validation (commit / show) ────────────────────────
    #[test]
    fn validate_relative_path_accepts_normal_paths() {
        for ok in ["a.md", "dir/a.md", "a/b/c/d.txt", "..hidden", "a..b"] {
            validate_relative_path(ok)
                .unwrap_or_else(|e| panic!("{ok:?} should be valid, got {e:?}"));
        }
    }

    #[test]
    fn validate_relative_path_rejects_malicious() {
        for bad in [
            "",          // empty
            "/abs/path", // leading slash
            "trailing/", // trailing slash
            "a//b",      // empty segment
            ".",         // dot
            "..",        // dotdot
            "../escape", // traversal at root
            "a/../b",    // traversal mid-path
            "a/./b",     // dot mid-path
            "a\\b",      // backslash
            "a\0b",      // NUL
            "a\nb",      // control char
        ] {
            assert!(
                matches!(validate_relative_path(bad), Err(GitError::InvalidPath(_))),
                "{bad:?} should be rejected",
            );
        }
    }
}

#[cfg(test)]
mod fast_path1_tests {
    //! Tests for Fast Path 1 (the persistent stat cache). The strategy:
    //!   - Wrap `MockFsCounting` around a tiny in-memory map with controllable
    //!     `mod_time` per path AND a `reads` counter.
    //!   - Wrap `LocalIndexStore` in a temp dir as the index backend.
    //!   - Run two commits and assert `reads` only goes up by the expected
    //!     amount on the second one.
    //!
    //! Each assertion pins a distinct invariant of Fast Path 1: cache hit
    //! skips read; (size, mtime_ns) mismatch invalidates; parent_oid mismatch
    //! disables the cache; corruption is silent; partial-paths preserves
    //! uncovered entries; deletion removes entries.
    use super::*;
    use async_trait::async_trait;
    use std::collections::HashMap;
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::sync::{Arc, Mutex};
    use std::time::{Duration, SystemTime, UNIX_EPOCH};

    use crate::core::errors::{Error, Result};
    use crate::core::filesystem::FileSystem;
    use crate::core::types::{FileInfo, TreeEntry, WriteFlag};
    use crate::git::backends::local::{LocalIndexStore, LocalObjectStore, LocalRefStore};
    use crate::git::index_store::IndexStore;

    struct CountingVfs {
        account: String,
        // path -> (bytes, mtime_ns)
        files: Arc<Mutex<HashMap<String, (Vec<u8>, i128)>>>,
        reads: AtomicU64,
    }

    impl CountingVfs {
        fn new(account: &str) -> Arc<Self> {
            Arc::new(Self {
                account: account.to_string(),
                files: Arc::new(Mutex::new(HashMap::new())),
                reads: AtomicU64::new(0),
            })
        }

        fn put(&self, rel: &str, data: &[u8], mtime_ns: i128) {
            let abs = format!("/local/{}/{}", self.account, rel);
            self.files
                .lock()
                .unwrap()
                .insert(abs, (data.to_vec(), mtime_ns));
        }

        fn delete(&self, rel: &str) {
            let abs = format!("/local/{}/{}", self.account, rel);
            self.files.lock().unwrap().remove(&abs);
        }

        fn reads(&self) -> u64 {
            self.reads.load(Ordering::SeqCst)
        }
    }

    fn nanos_to_systemtime(ns: i128) -> SystemTime {
        // Tests use small positive nanos, so the cast is lossless.
        let secs = (ns / 1_000_000_000) as u64;
        let sub = (ns % 1_000_000_000) as u32;
        UNIX_EPOCH + Duration::new(secs, sub)
    }

    #[async_trait]
    impl FileSystem for CountingVfs {
        async fn create(&self, _path: &str) -> Result<()> {
            unimplemented!()
        }
        async fn mkdir(&self, _path: &str, _mode: u32) -> Result<()> {
            unimplemented!()
        }
        async fn remove(&self, path: &str) -> Result<()> {
            self.files.lock().unwrap().remove(path);
            Ok(())
        }
        async fn remove_all(&self, _path: &str) -> Result<()> {
            unimplemented!()
        }

        async fn read(&self, path: &str, _offset: u64, _size: u64) -> Result<Vec<u8>> {
            let g = self.files.lock().unwrap();
            match g.get(path) {
                Some((bytes, _)) => {
                    self.reads.fetch_add(1, Ordering::SeqCst);
                    Ok(bytes.clone())
                }
                None => Err(Error::not_found(path)),
            }
        }

        async fn write(
            &self,
            path: &str,
            data: &[u8],
            _offset: u64,
            _flags: WriteFlag,
        ) -> Result<u64> {
            self.files
                .lock()
                .unwrap()
                .insert(path.to_string(), (data.to_vec(), 0));
            Ok(data.len() as u64)
        }
        async fn read_dir(&self, _path: &str) -> Result<Vec<FileInfo>> {
            unimplemented!()
        }

        async fn stat(&self, path: &str) -> Result<FileInfo> {
            let g = self.files.lock().unwrap();
            if let Some((bytes, mtime_ns)) = g.get(path) {
                let name = path.rsplit('/').next().unwrap_or(path).to_string();
                return Ok(FileInfo::new(
                    name,
                    bytes.len() as u64,
                    0o644,
                    nanos_to_systemtime(*mtime_ns),
                    false,
                ));
            }
            Err(Error::not_found(path))
        }

        async fn rename(&self, _o: &str, _n: &str) -> Result<()> {
            unimplemented!()
        }
        async fn chmod(&self, _path: &str, _mode: u32) -> Result<()> {
            unimplemented!()
        }

        async fn tree_directory(
            &self,
            path: &str,
            _show_hidden: bool,
            _node_limit: Option<usize>,
            _level_limit: Option<usize>,
        ) -> Result<Vec<TreeEntry>> {
            let prefix = if path == "/" {
                "/".to_string()
            } else {
                format!("{}/", path)
            };
            let g = self.files.lock().unwrap();
            let mut out = Vec::new();
            for (full_path, (_bytes, _mtime)) in g.iter() {
                if !full_path.starts_with(&prefix) {
                    continue;
                }
                let rel = full_path
                    .strip_prefix(&prefix)
                    .unwrap_or(full_path)
                    .to_string();
                let name = full_path
                    .rsplit('/')
                    .next()
                    .unwrap_or(full_path)
                    .to_string();
                out.push(TreeEntry {
                    path: full_path.clone(),
                    rel_path: rel,
                    info: FileInfo::new_file(name, 0, 0o644),
                    extra: HashMap::new(),
                });
            }
            Ok(out)
        }
    }

    fn make_service_with_index(
        account: &str,
    ) -> (
        tempfile::TempDir,
        Arc<CountingVfs>,
        Arc<LocalIndexStore>,
        GitService,
    ) {
        let dir = tempfile::tempdir().unwrap();
        let object_store = Arc::new(LocalObjectStore::new(dir.path()));
        let ref_store = Arc::new(LocalRefStore::new(dir.path()));
        let index_store = Arc::new(LocalIndexStore::new(dir.path()));
        let vfs = CountingVfs::new(account);
        let svc = GitService::with_index(
            vfs.clone() as Arc<dyn FileSystem>,
            object_store as Arc<dyn ObjectStore>,
            ref_store as Arc<dyn RefStore>,
            Some(index_store.clone() as Arc<dyn IndexStore>),
        );
        (dir, vfs, index_store, svc)
    }

    fn make_service_no_index(account: &str) -> (tempfile::TempDir, Arc<CountingVfs>, GitService) {
        let dir = tempfile::tempdir().unwrap();
        let object_store = Arc::new(LocalObjectStore::new(dir.path()));
        let ref_store = Arc::new(LocalRefStore::new(dir.path()));
        let vfs = CountingVfs::new(account);
        let svc = GitService::new(
            vfs.clone() as Arc<dyn FileSystem>,
            object_store as Arc<dyn ObjectStore>,
            ref_store as Arc<dyn RefStore>,
        );
        (dir, vfs, svc)
    }

    fn req(account: &str, branch: &str, paths: Option<Vec<String>>) -> CommitRequest {
        CommitRequest {
            account: account.to_string(),
            branch: branch.to_string(),
            message: "m".to_string(),
            paths,
            author_name: "tester".to_string(),
            author_email: "t@x".to_string(),
        }
    }

    #[tokio::test]
    async fn cached_stat_match_skips_read() {
        // Two files. First commit reads both. Second commit, with
        // identical (size, mtime_ns), reads NEITHER — Fast Path 1 hits.
        // mtimes are well in the past (year 2001 / 2004) so they predate the
        // real index file's save time → not racy → Fast Path 1 is trusted.
        let (_dir, vfs, _idx, svc) = make_service_with_index("acct");
        vfs.put("a.md", b"hello", 1_000_000_000_000_000_000);
        vfs.put("b.md", b"world", 1_100_000_000_000_000_000);

        let _ = svc.commit(req("acct", "main", None)).await.unwrap();
        let reads_after_first = vfs.reads();
        assert_eq!(reads_after_first, 2, "first commit must read both files");

        // Second commit, no changes → Noop, but commit() still walks
        // candidates and decides whether to read each. Fast Path 1 means
        // (size, mtime_ns) match → no read needed.
        let resp = svc.commit(req("acct", "main", None)).await.unwrap();
        match resp {
            CommitResponse::Noop { .. } => {}
            other => panic!("expected Noop, got {other:?}"),
        }
        assert_eq!(
            vfs.reads(),
            reads_after_first,
            "Fast Path 1 hit: no extra reads on second commit",
        );
    }

    #[tokio::test]
    async fn racy_clean_same_size_same_mtime_is_not_lost() {
        // Regression for the "racy clean" data-loss bug: a file changed to the
        // same byte length within the same filesystem clock tick as the index
        // write must NOT be skipped by Fast Path 1.
        //
        // We simulate "the file's mtime is in (or after) the index's clock
        // tick" by giving the working-tree file a mtime in the far future
        // (year 2033), which is guaranteed to be >= the real index file's
        // save time. Both versions are 2 bytes with the SAME (size, mtime),
        // exactly the signature Fast Path 1 keys on.
        let racy_mtime = 2_000_000_000_000_000_000; // ~year 2033, >= index save time
        let (_dir, vfs, _idx, svc) = make_service_with_index("acct");

        vfs.put("a.md", b"v1", racy_mtime);
        let _ = svc.commit(req("acct", "main", None)).await.unwrap();
        let reads_after_v1 = vfs.reads();

        // Change content to a different 2-byte value, keeping (size, mtime)
        // identical — the pathological case the stat signature cannot detect.
        vfs.put("a.md", b"v2", racy_mtime);
        let resp = svc.commit(req("acct", "main", None)).await.unwrap();

        // The racy-clean guard must force a slow-path read and capture the
        // change as a real commit. Without the guard this would be a Noop
        // (Fast Path 1 reusing v1's blob oid) — silently dropping v2.
        match resp {
            CommitResponse::Created { .. } => {}
            other => panic!("expected Created (v2 must be committed), got {other:?}"),
        }
        assert_eq!(
            vfs.reads(),
            reads_after_v1 + 1,
            "racy entry (mtime >= index save time) must be re-read, not trusted",
        );

        // And the committed blob must actually be v2, not the stale v1.
        let shown = svc
            .show(ShowRequest {
                account: "acct".to_string(),
                target_ref: "main".to_string(),
                path: Some("a.md".to_string()),
            })
            .await
            .unwrap();
        match shown {
            ShowResponse::Blob { bytes, .. } => {
                assert_eq!(&bytes[..], b"v2", "committed blob must reflect v2, not v1");
            }
            other => panic!("expected Blob, got {other:?}"),
        }
    }

    #[tokio::test]
    async fn size_mismatch_invalidates_cache_entry() {
        let (_dir, vfs, _idx, svc) = make_service_with_index("acct");
        vfs.put("a.md", b"hello", 1_000_000_000_000_000_000);
        let _ = svc.commit(req("acct", "main", None)).await.unwrap();
        let reads_after_first = vfs.reads();

        // Same mtime, different size → Fast Path 1 must MISS for this file.
        vfs.put("a.md", b"helloX", 1_000_000_000_000_000_000);
        let _ = svc.commit(req("acct", "main", None)).await.unwrap();
        assert_eq!(
            vfs.reads(),
            reads_after_first + 1,
            "size mismatch must trigger one extra read",
        );
    }

    #[tokio::test]
    async fn mtime_mismatch_invalidates_cache_entry() {
        let (_dir, vfs, _idx, svc) = make_service_with_index("acct");
        vfs.put("a.md", b"hello", 1_000_000_000_000_000_000);
        let _ = svc.commit(req("acct", "main", None)).await.unwrap();
        let reads_after_first = vfs.reads();

        // Same size, different mtime → cache miss.
        vfs.put("a.md", b"hello", 2_000_000_000_000_000_000);
        let resp = svc.commit(req("acct", "main", None)).await.unwrap();
        // Same content → identical oid → no editor change → Noop.
        match resp {
            CommitResponse::Noop { .. } => {}
            other => panic!("expected Noop, got {other:?}"),
        }
        assert_eq!(
            vfs.reads(),
            reads_after_first + 1,
            "mtime mismatch must trigger one extra read",
        );
    }

    #[tokio::test]
    async fn parent_oid_mismatch_disables_cache() {
        // Build a service whose index file's parent_oid is stale relative
        // to the branch HEAD: drop in a hand-crafted CommitIndex pointing at
        // a bogus parent, then commit and assert ALL files were re-read.
        let (dir, vfs, idx, svc) = make_service_with_index("acct");
        vfs.put("a.md", b"hello", 1_000_000_000_000_000_000);
        vfs.put("b.md", b"world", 2_000_000_000_000_000_000);
        let _ = svc.commit(req("acct", "main", None)).await.unwrap();
        let reads_after_first = vfs.reads();

        // Overwrite the index on disk with one whose parent_oid is bogus.
        let bogus = ObjectId::from_hex(b"deadbeefdeadbeefdeadbeefdeadbeefdeadbeef").unwrap();
        let stale = CommitIndex {
            parent_oid: bogus,
            entries: HashMap::new(), // doesn't matter; whole file is rejected
            saved_at_ns: None,
        };
        idx.save("acct", "main", &stale).await.unwrap();
        let _ = dir; // keep tempdir alive

        // Re-commit with same contents. Cache parent_oid != HEAD → cache
        // discarded entirely → both files re-read.
        let _ = svc.commit(req("acct", "main", None)).await.unwrap();
        assert_eq!(
            vfs.reads(),
            reads_after_first + 2,
            "parent_oid mismatch must force read of every candidate",
        );
    }

    #[tokio::test]
    async fn deleted_path_is_removed_from_index() {
        let (_dir, vfs, idx, svc) = make_service_with_index("acct");
        vfs.put("a.md", b"a", 1_000_000_000_000_000_000);
        vfs.put("b.md", b"b", 2_000_000_000_000_000_000);
        let _ = svc.commit(req("acct", "main", None)).await.unwrap();

        // Delete a.md, commit with explicit paths so the deletion is
        // observed. Commit succeeds; the persisted index must drop a.md
        // and keep b.md.
        vfs.delete("a.md");
        let _ = svc
            .commit(req(
                "acct",
                "main",
                Some(vec!["a.md".into(), "b.md".into()]),
            ))
            .await
            .unwrap();

        let saved = idx.load("acct", "main").await.unwrap().unwrap();
        assert!(
            !saved.entries.contains_key("a.md"),
            "deleted path must be removed from the index",
        );
        assert!(
            saved.entries.contains_key("b.md"),
            "surviving path must remain in the index",
        );
    }

    #[tokio::test]
    async fn corrupted_index_falls_back_silently() {
        // Drop a malformed file at the index path BEFORE the first commit,
        // then commit normally. The corrupt file makes load() return None,
        // so commit takes the slow path — but it MUST still succeed and
        // overwrite the corrupt file with a valid one.
        let (dir, vfs, idx, svc) = make_service_with_index("acct");
        let path = dir.path().join("acct").join("index").join("main.json");
        tokio::fs::create_dir_all(path.parent().unwrap())
            .await
            .unwrap();
        tokio::fs::write(&path, b"NOT-JSON-AT-ALL").await.unwrap();

        vfs.put("a.md", b"hi", 1_000_000_000_000_000_000);
        let resp = svc.commit(req("acct", "main", None)).await.unwrap();
        assert!(matches!(resp, CommitResponse::Created { .. }));
        // The save after commit succeeded → load now returns a real index.
        let loaded = idx.load("acct", "main").await.unwrap().unwrap();
        assert!(loaded.entries.contains_key("a.md"));
    }

    #[tokio::test]
    async fn partial_paths_preserves_uncovered_entries() {
        // First commit covers a + b.
        // Second commit lists ONLY [a]; b is never enumerated. The new
        // index must still contain b's entry so a future full-enum commit
        // can still hit the cache for b.
        let (_dir, vfs, idx, svc) = make_service_with_index("acct");
        vfs.put("a.md", b"a", 1_000_000_000_000_000_000);
        vfs.put("b.md", b"b", 2_000_000_000_000_000_000);
        let _ = svc.commit(req("acct", "main", None)).await.unwrap();

        // Touch a (mtime change) — partial commit on a.md alone.
        vfs.put("a.md", b"a", 3_000_000_000_000_000_000);
        let _ = svc
            .commit(req("acct", "main", Some(vec!["a.md".into()])))
            .await
            .unwrap();

        let saved = idx.load("acct", "main").await.unwrap().unwrap();
        let a = saved
            .entries
            .get("a.md")
            .expect("a.md must be in the index");
        let b = saved.entries.get("b.md").expect(
            "b.md was uncovered by paths=[a.md] but must be preserved \
             from the previous index",
        );
        assert_eq!(a.mtime_ns, 3_000_000_000_000_000_000);
        assert_eq!(b.mtime_ns, 2_000_000_000_000_000_000);
    }

    #[tokio::test]
    async fn no_index_store_disables_fast_path() {
        // Sanity: with index_store=None the slow path runs every commit;
        // a noop second commit still reads every file.
        let (_dir, vfs, svc) = make_service_no_index("acct");
        vfs.put("a.md", b"hi", 1_000_000_000_000_000_000);
        let _ = svc.commit(req("acct", "main", None)).await.unwrap();
        let reads_after_first = vfs.reads();

        let _ = svc.commit(req("acct", "main", None)).await.unwrap();
        assert_eq!(
            vfs.reads(),
            reads_after_first + 1,
            "without an IndexStore the slow path runs every commit",
        );
    }

    /// Partial commit with a directory entry must purge `new_index_entries`
    /// by prefix — otherwise a file deleted under that directory leaves a
    /// stale row in the persisted commit index, which the *next* commit
    /// might serve to fast-path 1 as a valid cached oid.
    ///
    /// Uses `LocalFileSystem` because the Directory branch of Step 2.5
    /// only runs when `stat` returns `is_dir = true`. `CountingVfs::stat`
    /// returns NotFound for any directory and so would route this test
    /// through the NotFound branch — which has different prefix-cleanup
    /// behavior (NotFound also clears the exact key).
    #[tokio::test]
    async fn partial_commit_with_directory_path_purges_index_by_prefix() {
        use crate::git::object_store::ObjectStore;
        use crate::git::ref_store::RefStore;
        use crate::plugins::localfs::LocalFileSystem;

        let store_dir = tempfile::tempdir().unwrap();
        let object_store = Arc::new(LocalObjectStore::new(store_dir.path()));
        let ref_store = Arc::new(LocalRefStore::new(store_dir.path()));
        let index_store = Arc::new(LocalIndexStore::new(store_dir.path()));

        let work_dir = tempfile::tempdir().unwrap();
        let acct_root = work_dir.path().join("local").join("acct");
        std::fs::create_dir_all(acct_root.join("docs")).unwrap();
        std::fs::create_dir_all(acct_root.join("other")).unwrap();
        std::fs::write(acct_root.join("docs/a.md"), b"AA").unwrap();
        std::fs::write(acct_root.join("docs/b.md"), b"BB").unwrap();
        std::fs::write(acct_root.join("other/c.md"), b"CC").unwrap();

        let vfs: Arc<dyn FileSystem> =
            Arc::new(LocalFileSystem::new(work_dir.path().to_str().unwrap()).unwrap());
        let svc = GitService::with_index(
            vfs,
            object_store as Arc<dyn ObjectStore>,
            ref_store as Arc<dyn RefStore>,
            Some(index_store.clone() as Arc<dyn IndexStore>),
        );

        let full = CommitRequest {
            account: "acct".into(),
            branch: "main".into(),
            message: "m".into(),
            paths: None,
            author_name: "tester".into(),
            author_email: "t@x".into(),
        };
        let _ = svc.commit(full).await.unwrap();
        let loaded = index_store.load("acct", "main").await.unwrap().unwrap();
        assert!(loaded.entries.contains_key("docs/a.md"));
        assert!(loaded.entries.contains_key("docs/b.md"));
        assert!(loaded.entries.contains_key("other/c.md"));

        // Delete docs/b.md, partial-commit with paths=["docs"].
        std::fs::remove_file(acct_root.join("docs/b.md")).unwrap();
        let partial = CommitRequest {
            account: "acct".into(),
            branch: "main".into(),
            message: "m".into(),
            paths: Some(vec!["docs".into()]),
            author_name: "tester".into(),
            author_email: "t@x".into(),
        };
        let _ = svc.commit(partial).await.unwrap();

        let loaded = index_store.load("acct", "main").await.unwrap().unwrap();
        assert!(
            !loaded.entries.contains_key("docs/b.md"),
            "stale entry for deleted docs/b.md must not survive prefix cleanup"
        );
        assert!(
            loaded.entries.contains_key("docs/a.md"),
            "surviving file under docs/ must have a fresh entry"
        );
        assert!(
            loaded.entries.contains_key("other/c.md"),
            "files outside the partial scope must be preserved verbatim"
        );
    }
}
