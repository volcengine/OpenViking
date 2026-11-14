use super::*;

impl GitService {
    /// Build a new commit on `branch` reflecting the current state of the
    /// account's VFS subtree.
    ///
    /// - If `paths` is `Some`, only those account-relative paths are
    ///   considered. Directories are expanded recursively.
    /// - If `paths` is `None`, the full `/local/{account}` subtree is
    ///   enumerated via `enumerate::collect_all`.
    ///
    /// The first commit persists an empty tree if no paths change. On later
    /// no-op commits the branch ref is untouched and `CommitResponse::Noop`
    /// is returned.
    ///
    /// Entries in `paths` may identify files, directories, or paths deleted
    /// since the previous snapshot. Directories are expanded recursively;
    /// missing entries remove matching files/subtrees from the snapshot.
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
        validate_account_id(&req.account)?;
        if let Some(paths) = &req.paths {
            for path in paths {
                validate_relative_path(path)?;
            }
        }

        let ctx = Arc::new(FsContextInner::new(req.account.clone()));
        FS_CTX.scope(ctx, self.commit_impl(req)).await
    }

    async fn commit_impl(&self, req: CommitRequest) -> Result<CommitResponse, GitError> {
        let CommitRequest {
            account,
            branch,
            message,
            paths,
            author_name,
            author_email,
        } = req;
        let ref_name = format!("refs/heads/{branch}");

        // Load ignore matcher and initialize counters.
        //
        // `credited_ignored` / `credited_removed` deduplicate counting across
        // the per-path iterations of a scoped commit: a path listed twice, or
        // listed both as a directory and as a file under it, is processed by
        // multiple iterations but must be credited at most once to `ignored`
        // (on-disk skip) and once to `changed` (snapshot removal). The tree
        // itself is already correct — `editor.remove` is idempotent and the
        // BTreeSet dedups candidates — only the reported counts need guarding.
        let ignore_matcher = load_ignore_matcher(&self.vfs, &account).await?;
        let mut ignored = 0usize;
        let mut changed = 0usize;
        let mut credited_ignored: HashSet<String> = HashSet::new();
        let mut credited_removed: HashSet<String> = HashSet::new();

        let mut include_path = |path: &str| -> bool {
            if crate::git::enumerate::prune_path(path) {
                return false;
            }
            if !should_track_path(path, &ignore_matcher) {
                if credited_ignored.insert(path.to_string()) {
                    ignored += 1;
                }
                return false;
            }
            true
        };

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
        let collect_snapshot_log_paths = self.snapshot_log_store.is_some();
        let mut actual_changed_paths: Vec<String> = Vec::new();

        let candidates: Vec<String> = match &paths {
            Some(ps) => {
                let mut set: std::collections::BTreeSet<String> = std::collections::BTreeSet::new();

                for p in ps {
                    let abs = format!("/local/{}/{}", account, p);
                    match self.vfs.stat(&abs).await {
                        Ok(info) if info.is_dir => {
                            // Directory: recursive listing + prev_tree subtree. Include an
                            // old blob at this exact path too (file -> directory), even if
                            // the new directory is empty and contributes no listed files.
                            cleanup_exact.insert(p.clone());
                            cleanup_prefixes.push(format!("{}/", p));

                            let listed =
                                crate::git::enumerate::collect_under(&self.vfs, &account, p)
                                    .await?;
                            for rel in listed {
                                if include_path(&rel) {
                                    set.insert(rel);
                                }
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
                                    if path != p && !path.starts_with(&pref) {
                                        continue;
                                    }
                                    if should_track_path(path, &ignore_matcher) {
                                        set.insert(path.clone());
                                    } else {
                                        // Previously tracked under this scope but
                                        // now ignored/pruned: drop it from the
                                        // snapshot. Every entry here came from
                                        // prev_tree, so the removal is a real
                                        // change. Use should_track_path (not
                                        // include_path) so `ignored` is not
                                        // double-counted for paths that are also
                                        // on disk — the on-disk pass already
                                        // counted them.
                                        editor
                                            .remove(self.object_store.as_ref(), &account, path)
                                            .await?;
                                        if credited_removed.insert(path.clone()) {
                                            changed += 1;
                                            if collect_snapshot_log_paths {
                                                actual_changed_paths.push(path.clone());
                                            }
                                        }
                                    }
                                }
                            }
                        }
                        Ok(_) => {
                            // File: take it verbatim, subject to pruning and ignore.
                            cleanup_exact.insert(p.clone());
                            cleanup_prefixes.push(format!("{}/", p));
                            if include_path(p) {
                                set.insert(p.clone());
                            } else if let Some(t) = prev_tree {
                                // Explicit file is now ignored/pruned: drop it
                                // from the snapshot if it was previously tracked.
                                // Only counts as a change when it actually existed
                                // in prev_tree — TreeEditor::remove silently
                                // no-ops for missing paths (mirrors the main
                                // loop's NotFound branch below).
                                //
                                // Reuse the already-flattened prev_paths_cache
                                // (loading it once on first need) instead of a
                                // per-file root-to-leaf `lookup` tree walk, so
                                // a scoped commit listing many now-ignored
                                // explicit files pays one flatten + O(1) probes
                                // rather than K independent walks.
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
                                let was_tracked = prev_paths_cache
                                    .as_ref()
                                    .unwrap()
                                    .iter()
                                    .any(|(pp, _)| pp == p);
                                if was_tracked && credited_removed.insert(p.clone()) {
                                    editor
                                        .remove(self.object_store.as_ref(), &account, p)
                                        .await?;
                                    changed += 1;
                                    if collect_snapshot_log_paths {
                                        actual_changed_paths.push(p.clone());
                                    }
                                }
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

                            if include_path(p) {
                                set.insert(p.clone());
                            } else if let Some(t) = prev_tree {
                                // `p` is gone from disk AND now ignored/pruned, so
                                // it never enters the candidate set (the main
                                // loop's NotFound branch won't see it). Mirror the
                                // File branch: drop it from the snapshot directly
                                // if it was previously tracked, so the user's
                                // deletion is not silently lost as a Noop. Reuse
                                // prev_paths_cache (loaded once on first need)
                                // rather than a per-path `lookup` walk.
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
                                let was_tracked = prev_paths_cache
                                    .as_ref()
                                    .unwrap()
                                    .iter()
                                    .any(|(pp, _)| pp == p);
                                if was_tracked && credited_removed.insert(p.clone()) {
                                    editor
                                        .remove(self.object_store.as_ref(), &account, p)
                                        .await?;
                                    changed += 1;
                                    if collect_snapshot_log_paths {
                                        actual_changed_paths.push(p.clone());
                                    }
                                }
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
                                    if !path.starts_with(&pref) {
                                        continue;
                                    }
                                    if should_track_path(path, &ignore_matcher) {
                                        set.insert(path.clone());
                                    } else {
                                        // Previously tracked under this scope but
                                        // now ignored/pruned: drop it from the
                                        // snapshot. Every entry here came from
                                        // prev_tree, so the removal is a real
                                        // change. Use should_track_path (not
                                        // include_path) so `ignored` is not
                                        // double-counted for paths that are also
                                        // on disk — the on-disk pass already
                                        // counted them.
                                        editor
                                            .remove(self.object_store.as_ref(), &account, path)
                                            .await?;
                                        if credited_removed.insert(path.clone()) {
                                            changed += 1;
                                            if collect_snapshot_log_paths {
                                                actual_changed_paths.push(path.clone());
                                            }
                                        }
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
                let listed = crate::git::enumerate::collect_all(&self.vfs, &account).await?;
                let mut set: std::collections::BTreeSet<String> = std::collections::BTreeSet::new();
                for p in listed {
                    if include_path(&p) {
                        set.insert(p);
                    }
                }
                if let Some(t) = prev_tree {
                    let prev_paths = crate::git::tree_builder::flatten(
                        self.object_store.as_ref(),
                        &account,
                        t,
                        &None,
                    )
                    .await?;
                    for (p, _) in prev_paths {
                        if should_track_path(&p, &ignore_matcher) {
                            set.insert(p);
                        } else {
                            // Previously tracked but now ignored/pruned: drop it
                            // from the next tree and from the commit index seed.
                            // Use should_track_path (not include_path) so `ignored`
                            // is not double-counted — paths still on disk were
                            // already counted by the collect_all pass above.
                            editor
                                .remove(self.object_store.as_ref(), &account, &p)
                                .await?;
                            if credited_removed.insert(p.clone()) {
                                changed += 1;
                                if collect_snapshot_log_paths {
                                    actual_changed_paths.push(p.clone());
                                }
                            }
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

        // Filter index seed for ignored paths
        if self.index_store.is_some() {
            new_index_entries.retain(|path, _| should_track_path(path, &ignore_matcher));
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
        for rel_path in candidates {
            let abs = format!("/local/{}/{}", account, rel_path);
            // Candidates include previous snapshot blobs. If one has become a
            // directory, remove the old blob rather than reading the directory as
            // a file. Its current children are separate, later-sorted candidates.
            let file_info = match self.vfs.stat(&abs).await {
                Ok(info) if !info.is_dir => Some(info),
                Ok(_) => None,
                Err(e) if is_not_found(&e) => None,
                Err(e) => return Err(e.into()),
            };
            match file_info {
                Some(info) => {
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
                    let prev_was_tree = prev_entry
                        .as_ref()
                        .map(|(_, mode)| mode.is_tree())
                        .unwrap_or(false);
                    if prev_entry.as_ref().map(|(o, _)| *o) != Some(oid) {
                        editor
                            .upsert(self.object_store.as_ref(), &account, &rel_path, oid)
                            .await?;
                        changed += 1;
                        if collect_snapshot_log_paths {
                            actual_changed_paths.push(rel_path.clone());
                            if prev_was_tree {
                                let pref = format!("{}/", rel_path);
                                if let Some(prev_paths) = &prev_paths_cache {
                                    for (path, _) in prev_paths {
                                        if path.starts_with(&pref) {
                                            actual_changed_paths.push(path.clone());
                                        }
                                    }
                                } else if let Some((old_tree_oid, _)) = prev_entry.as_ref() {
                                    let subtree_paths = crate::git::tree_builder::flatten(
                                        self.object_store.as_ref(),
                                        &account,
                                        *old_tree_oid,
                                        &None,
                                    )
                                    .await?;
                                    for (path, _) in subtree_paths {
                                        actual_changed_paths.push(format!("{pref}{path}"));
                                    }
                                }
                            }
                        }
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
                None => {
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
                        if collect_snapshot_log_paths {
                            actual_changed_paths.push(rel_path.clone());
                        }
                    }
                    // The file is gone (or became a directory): drop its cached blob.
                    if self.index_store.is_some() {
                        new_index_entries.remove(&rel_path);
                    }
                }
            }
        }

        // 5. An existing branch reuses HEAD when unchanged. The on-disk
        //    (size, mtime_ns) for enumerated paths may have shifted
        //    (e.g. `touch` of an unchanged file). Persist the refreshed index
        //    keyed on the *current* HEAD so the next commit can still hit the
        //    fast path. Soft-fail on save errors.
        if let (0, Some(head)) = (changed, prev_head) {
            if let Some(store) = &self.index_store {
                let new_index = CommitIndex {
                    parent_oid: head,
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
                commit_oid: head,
                ignored,
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

        if collect_snapshot_log_paths {
            self.try_append_snapshot_log(
                &account,
                &branch,
                prev_head,
                commit_oid,
                &actual_changed_paths,
            )
            .await;
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
            ignored,
        })
    }
}

/// Return true iff `e` is `Error::NotFound(_)`.
fn is_not_found(e: &crate::core::errors::Error) -> bool {
    matches!(e, crate::core::errors::Error::NotFound(_))
}

async fn load_ignore_matcher(
    vfs: &Arc<dyn FileSystem>,
    account: &str,
) -> Result<IgnoreMatcher, GitError> {
    let abs = format!("/local/{}/{}", account, OVGITIGNORE_PATH);
    match vfs.read(&abs, 0, 0).await {
        Ok(bytes) => IgnoreMatcher::parse(&bytes),
        Err(e) if is_not_found(&e) => Ok(IgnoreMatcher::empty()),
        Err(e) => Err(e.into()),
    }
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
