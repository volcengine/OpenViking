use super::*;

impl GitService {
    /// Publish optional acceleration only after the branch CAS succeeds. A
    /// restored snapshot is indexed even when later VFS writeback partially fails.
    pub(super) async fn record_history(
        &self,
        account: &str,
        branch: &str,
        oid: ObjectId,
        tree: ObjectId,
        parent: Option<ObjectId>,
        parent_tree: Option<ObjectId>,
    ) {
        use crate::git::history::{self, Record, Segment};
        let store = self.object_store.as_ref();
        if !store.supports_history_segments() {
            return;
        }
        let work = async {
            let mut budget = history::BuildBudget::new(32);
            let bloom = history::build_bloom(store, account, parent_tree, tree, &mut budget)
                .await
                .unwrap_or_else(history::Bloom::saturated);
            let mut records = vec![Record::new(oid, tree, parent, parent_tree, Some(bloom))];
            if let Some(parent) = parent {
                if let Some(segment) = self.history_cache.load(store, account, parent).await {
                    if segment.records.len() < history::SEGMENT_RECORDS {
                        records.extend(segment.records.iter().cloned());
                    }
                }
            }
            if self
                .history_cache
                .save(store, account, Segment { records })
                .await
            {
                self.history_cache
                    .publish_anchor(store, account, branch, oid)
                    .await;
            }
        };
        if tokio::time::timeout(std::time::Duration::from_millis(500), work)
            .await
            .is_err()
        {
            warn!("snapshot history maintenance timed out; exact traversal remains available");
        }
    }

    /// Walk commit history newest-first, optionally returning only commits
    /// that changed at least one requested path relative to their first parent.
    ///
    /// `limit` counts returned commits, not commits inspected. With `paths`
    /// set, traversal continues through unrelated commits until `limit`
    /// matching commits are collected or history ends. Filtered traversal
    /// returns `GitError::LogScanLimitExceeded` when history remains after
    /// 1,000 candidate commits.
    pub async fn log(&self, mut req: LogRequest) -> Result<Vec<LogEntry>, GitError> {
        validate_account_id(&req.account)?;
        req.paths = normalize_log_paths(req.paths)?;
        if req.limit == 0 {
            return Ok(Vec::new());
        }

        if req.paths.is_some() {
            if let Some(out) = self.log_with_snapshot_log(&req).await? {
                return Ok(out);
            }
        }

        self.log_with_scan_limit(req, MAX_LOG_COMMITS_SCANNED).await
    }

    pub(super) async fn log_with_scan_limit(
        &self,
        req: LogRequest,
        max_commits_scanned: usize,
    ) -> Result<Vec<LogEntry>, GitError> {
        debug_assert!(max_commits_scanned > 0);

        use crate::git::history::{self, Record, Segment};
        let store = self.object_store.as_ref();
        let mut next_oid =
            Some(resolve_ref(self.ref_store.as_ref(), store, &req.account, &req.branch).await?);
        let filtered_paths = req.paths.as_deref();
        let accelerated = filtered_paths.is_some() && store.supports_history_segments();
        let mut next_meta: Option<CommitMeta> = None;
        let mut scanned = 0usize;
        let mut out = Vec::new();
        let mut trees = crate::git::tree_builder::TreeLookupCache::new();
        let mut loaded = std::collections::VecDeque::new();
        let mut probe_after = 0;
        let mut anchor_checked = false;
        let mut anchor = None::<Arc<Segment>>;
        let mut anchor_published = false;
        let mut pending = Vec::new();
        let mut dirty = false;
        let mut build_trees = 64;
        let deadline = tokio::time::Instant::now() + std::time::Duration::from_millis(250);

        while let Some(commit_oid) = next_oid {
            if loaded.is_empty() {
                if let Some(segment) = anchor.as_ref() {
                    if let Some(position) =
                        segment.records.iter().position(|r| r.oid() == commit_oid)
                    {
                        loaded.extend(segment.records[position..].iter().cloned());
                        anchor = None;
                        probe_after = 0;
                    }
                }
            }
            if accelerated && loaded.is_empty() && scanned >= probe_after {
                if let Some(segment) = self
                    .history_cache
                    .load(store, &req.account, commit_oid)
                    .await
                {
                    loaded.extend(segment.records.iter().cloned());
                } else {
                    // A legacy run has no per-commit sidecars: probe only once per
                    // segment, rather than adding a remote miss to every commit.
                    probe_after = scanned + history::SEGMENT_RECORDS;
                    if !anchor_checked {
                        anchor_checked = true;
                        anchor = self
                            .history_cache
                            .anchor(store, &req.account, &req.branch)
                            .await;
                        // The hint never changes next_oid. Only consume records
                        // once the exact parent walk reaches an anchored OID.
                        if let Some(segment) = anchor.as_ref() {
                            if let Some(position) =
                                segment.records.iter().position(|r| r.oid() == commit_oid)
                            {
                                loaded.extend(segment.records[position..].iter().cloned());
                                anchor = None;
                                probe_after = 0;
                            }
                        }
                    }
                }
            }
            let mut meta = None;
            let mut record = if let Some(record) = loaded.pop_front() {
                debug_assert_eq!(record.oid(), commit_oid);
                // Any lookahead from the fallback walk is now redundant.
                next_meta = None;
                record
            } else {
                let current = match next_meta.take() {
                    Some(meta) => meta,
                    None => load_commit_meta(store, &req.account, &commit_oid).await?,
                };
                let parent = current.parents.first().copied();
                let parent_meta = match (filtered_paths, parent) {
                    (Some(_), Some(oid)) => {
                        Some(load_commit_meta(store, &req.account, &oid).await?)
                    }
                    _ => None,
                };
                let record = Record::new(
                    commit_oid,
                    current.tree,
                    parent,
                    parent_meta.as_ref().map(|p| p.tree),
                    None,
                );
                next_meta = parent_meta;
                meta = Some(current);
                dirty = true;
                record
            };
            if accelerated
                && record.bloom.is_none()
                && build_trees > 0
                && tokio::time::Instant::now() < deadline
            {
                let allowance = build_trees.min(32);
                let mut budget = history::BuildBudget::new(allowance);
                let bloom = tokio::time::timeout_at(
                    deadline,
                    history::build_bloom(
                        store,
                        &req.account,
                        record.parent_tree(),
                        record.tree(),
                        &mut budget,
                    ),
                )
                .await;
                build_trees -= allowance - budget.trees;
                if let Ok(bloom) = bloom {
                    // Saturation remembers a per-commit work-limit failure. A
                    // partially built filter must never reject any path.
                    record.bloom =
                        bloom.or_else(|| (allowance == 32).then(history::Bloom::saturated));
                    dirty = true;
                } else {
                    build_trees = 0;
                }
            }
            let include = match filtered_paths {
                None => true,
                Some(paths) => {
                    scanned += 1;
                    if record
                        .bloom
                        .as_ref()
                        .is_some_and(|filter| paths.iter().all(|p| !filter.maybe_contains(p)))
                    {
                        false
                    } else {
                        commit_touches_any_path(
                            store,
                            &req.account,
                            record.tree(),
                            record.parent_tree(),
                            paths,
                            &mut trees,
                        )
                        .await?
                    }
                }
            };
            next_oid = record.parent();
            if accelerated {
                pending.push(record);
            }
            if include {
                let meta = match meta {
                    Some(meta) => meta,
                    None => load_commit_meta(store, &req.account, &commit_oid).await?,
                };
                out.push(log_entry_from_meta(commit_oid, meta));
            }
            let finished = out.len() >= req.limit || next_oid.is_none();
            let exhausted =
                filtered_paths.is_some() && scanned >= max_commits_scanned && next_oid.is_some();
            if accelerated && (pending.len() == history::SEGMENT_RECORDS || finished || exhausted) {
                if dirty {
                    // An early limit must not replace a complete cached segment
                    // with only its visited prefix. Its already validated tail
                    // remains useful without counting as scanned candidates.
                    let available = history::SEGMENT_RECORDS - pending.len();
                    pending.extend(loaded.iter().take(available).cloned());
                    let tip = pending[0].oid();
                    let saved = self
                        .history_cache
                        .save(
                            store,
                            &req.account,
                            Segment {
                                records: std::mem::take(&mut pending),
                            },
                        )
                        .await;
                    if saved && !anchor_published {
                        self.history_cache
                            .publish_anchor(store, &req.account, &req.branch, tip)
                            .await;
                        anchor_published = true;
                    }
                } else {
                    pending.clear();
                }
                dirty = false;
            }
            if finished {
                return Ok(out);
            }
            if exhausted {
                return Err(GitError::LogScanLimitExceeded {
                    scanned,
                    max_scanned: max_commits_scanned,
                    matched: out.len(),
                    requested: req.limit,
                });
            }
        }
        Ok(out)
    }
}

impl GitService {
    /// Try SnapshotLog fast path for filtered log requests.
    async fn log_with_snapshot_log(
        &self,
        req: &LogRequest,
    ) -> Result<Option<Vec<LogEntry>>, GitError> {
        let Some(store) = self.snapshot_log_store.clone() else {
            return Ok(None);
        };
        let Some(paths) = req.paths.as_ref() else {
            return Ok(None);
        };
        let limits = store.limits();
        if !crate::git::snapshot_log::can_index_account_branch(limits, &req.account, &req.branch)
            || !crate::git::snapshot_log::can_index_paths(limits, paths)
        {
            return Ok(None);
        }

        let current_head = resolve_ref(
            self.ref_store.as_ref(),
            self.object_store.as_ref(),
            &req.account,
            &req.branch,
        )
        .await?;
        let latest = match store
            .load_latest_indexed_commit(&req.account, &req.branch)
            .await
        {
            Ok(Some(latest)) => latest,
            Ok(None) => {
                self.trigger_snapshot_log_backfill(&req.account, &req.branch);
                return Ok(None);
            }
            Err(err) => {
                warn!(
                    "snapshot log latest query failed for {}/{}: {}",
                    req.account, req.branch, err
                );
                return Ok(None);
            }
        };
        let latest_oid = match ObjectId::from_hex(latest.1.as_bytes()) {
            Ok(oid) => oid,
            Err(err) => {
                warn!(
                    "snapshot log latest oid is invalid for {}/{}: {}",
                    req.account, req.branch, err
                );
                return Ok(None);
            }
        };

        if latest_oid == current_head {
            return self
                .load_snapshot_log_entries(store.as_ref(), req, latest.0, req.limit)
                .await;
        }

        match self
            .scan_snapshot_log_tail(
                req,
                current_head,
                latest_oid,
                crate::git::snapshot_log::SNAPSHOT_LOG_TAIL_SCAN_LIMIT,
            )
            .await?
        {
            Some(tail) => {
                self.trigger_snapshot_log_backfill(&req.account, &req.branch);
                self.merge_snapshot_log_tail(store.as_ref(), req, latest.0, tail)
                    .await
            }
            None => {
                let fresh_head = resolve_ref(
                    self.ref_store.as_ref(),
                    self.object_store.as_ref(),
                    &req.account,
                    &req.branch,
                )
                .await?;
                if fresh_head == latest_oid {
                    return self
                        .load_snapshot_log_entries(store.as_ref(), req, latest.0, req.limit)
                        .await;
                }
                if fresh_head != current_head {
                    if let Some(tail) = self
                        .scan_snapshot_log_tail(
                            req,
                            fresh_head,
                            latest_oid,
                            crate::git::snapshot_log::SNAPSHOT_LOG_TAIL_SCAN_LIMIT,
                        )
                        .await?
                    {
                        self.trigger_snapshot_log_backfill(&req.account, &req.branch);
                        return self
                            .merge_snapshot_log_tail(store.as_ref(), req, latest.0, tail)
                            .await;
                    }
                }
                warn!(
                    "snapshot log latest is not on branch chain for {}/{}",
                    req.account, req.branch
                );
                Ok(None)
            }
        }
    }

    /// Merge tail-scan matches with indexed history behind the latest boundary.
    async fn merge_snapshot_log_tail(
        &self,
        store: &dyn crate::git::snapshot_log::SnapshotLogStore,
        req: &LogRequest,
        latest_id: i64,
        mut tail: Vec<LogEntry>,
    ) -> Result<Option<Vec<LogEntry>>, GitError> {
        if tail.len() >= req.limit {
            tail.truncate(req.limit);
            return Ok(Some(tail));
        }
        let remaining = req.limit - tail.len();
        let Some(indexed) = self
            .load_snapshot_log_entries(store, req, latest_id, remaining)
            .await?
        else {
            return Ok(None);
        };
        let mut seen: HashSet<ObjectId> = tail.iter().map(|entry| entry.oid).collect();
        for entry in indexed {
            if seen.insert(entry.oid) {
                tail.push(entry);
            }
            if tail.len() >= req.limit {
                break;
            }
        }
        Ok(Some(tail))
    }

    /// Load SnapshotLog hits and project them to `LogEntry` values.
    async fn load_snapshot_log_entries(
        &self,
        store: &dyn crate::git::snapshot_log::SnapshotLogStore,
        req: &LogRequest,
        max_id: i64,
        limit: usize,
    ) -> Result<Option<Vec<LogEntry>>, GitError> {
        let Some(paths) = req.paths.as_ref() else {
            return Ok(None);
        };
        let hits = match store
            .query_path_commits(&req.account, &req.branch, paths, max_id, limit)
            .await
        {
            Ok(hits) => hits,
            Err(err) => {
                warn!(
                    "snapshot log path query failed for {}/{}: {}",
                    req.account, req.branch, err
                );
                return Ok(None);
            }
        };
        let mut out = Vec::with_capacity(hits.len());
        for hit in hits {
            let oid = match ObjectId::from_hex(hit.commit_oid.as_bytes()) {
                Ok(oid) => oid,
                Err(err) => {
                    warn!(
                        "snapshot log hit oid is invalid for {}/{}: {}",
                        req.account, req.branch, err
                    );
                    return Ok(None);
                }
            };
            let meta = match load_commit_meta(self.object_store.as_ref(), &req.account, &oid).await
            {
                Ok(meta) => meta,
                Err(err) => {
                    warn!(
                        "snapshot log hit commit is unreadable for {}/{}: {}",
                        req.account, req.branch, err
                    );
                    return Ok(None);
                }
            };
            out.push(log_entry_from_meta(oid, meta));
        }
        Ok(Some(out))
    }

    /// Scan HEAD toward latest indexed commit, returning tail matches only.
    async fn scan_snapshot_log_tail(
        &self,
        req: &LogRequest,
        head: ObjectId,
        latest_oid: ObjectId,
        max_commits_scanned: usize,
    ) -> Result<Option<Vec<LogEntry>>, GitError> {
        let paths = req.paths.as_ref().expect("filtered request");
        let mut next_oid = Some(head);
        let mut next_meta: Option<CommitMeta> = None;
        let mut scanned = 0usize;
        let mut out = Vec::new();

        while let Some(commit_oid) = next_oid {
            if commit_oid == latest_oid {
                return Ok(Some(out));
            }
            let meta = match next_meta.take() {
                Some(meta) => meta,
                None => {
                    load_commit_meta(self.object_store.as_ref(), &req.account, &commit_oid).await?
                }
            };
            let parent_oid = meta.parents.first().copied();
            let parent_meta = match parent_oid {
                Some(parent_oid) => Some(
                    load_commit_meta(self.object_store.as_ref(), &req.account, &parent_oid).await?,
                ),
                None => None,
            };

            scanned += 1;
            if commit_touches_any_path(
                self.object_store.as_ref(),
                &req.account,
                meta.tree,
                parent_meta.as_ref().map(|parent| parent.tree),
                paths,
                &mut crate::git::tree_builder::TreeLookupCache::new(),
            )
            .await?
            {
                out.push(log_entry_from_meta(commit_oid, meta));
                if out.len() >= req.limit {
                    return Ok(Some(out));
                }
            }

            next_oid = parent_oid;
            next_meta = parent_meta;
            if scanned >= max_commits_scanned && next_oid.is_some() && next_oid != Some(latest_oid)
            {
                return Err(GitError::LogScanLimitExceeded {
                    scanned,
                    max_scanned: max_commits_scanned,
                    matched: out.len(),
                    requested: req.limit,
                });
            }
        }

        Ok(None)
    }
}

fn log_entry_from_meta(oid: ObjectId, meta: CommitMeta) -> LogEntry {
    LogEntry {
        oid,
        tree: meta.tree,
        parents: meta.parents,
        author: meta.author,
        committer: meta.committer,
        message: meta.message,
    }
}

pub(super) fn normalize_log_paths(
    paths: Option<Vec<String>>,
) -> Result<Option<Vec<String>>, GitError> {
    let Some(mut paths) = paths else {
        return Ok(None);
    };

    for path in &paths {
        validate_relative_path(path)?;
    }

    let mut seen = HashSet::new();
    paths.retain(|path| seen.insert(path.clone()));

    if paths.len() > MAX_LOG_FILTER_PATHS {
        return Err(GitError::TooManyLogPaths {
            count: paths.len(),
            limit: MAX_LOG_FILTER_PATHS,
        });
    }

    for path in &paths {
        let depth = path.split('/').count();
        if depth > MAX_LOG_PATH_DEPTH {
            return Err(GitError::LogPathTooDeep {
                path: path.clone(),
                depth,
                limit: MAX_LOG_PATH_DEPTH,
            });
        }
    }

    Ok((!paths.is_empty()).then_some(paths))
}

async fn commit_touches_any_path(
    store: &dyn ObjectStore,
    account: &str,
    current_tree: ObjectId,
    parent_tree: Option<ObjectId>,
    paths: &[String],
    cache: &mut crate::git::tree_builder::TreeLookupCache,
) -> Result<bool, GitError> {
    for path in paths {
        if crate::git::tree_builder::path_changed(
            store,
            account,
            parent_tree,
            Some(current_tree),
            path,
            cache,
        )
        .await?
        {
            return Ok(true);
        }
    }
    Ok(false)
}
