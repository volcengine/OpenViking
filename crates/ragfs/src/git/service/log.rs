use super::*;

impl GitService {
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

        let mut next_oid = Some(
            resolve_ref(
                self.ref_store.as_ref(),
                self.object_store.as_ref(),
                &req.account,
                &req.branch,
            )
            .await?,
        );
        let filtered_paths = req.paths.as_deref();
        // Tree OIDs are immutable; reuse decoded ancestors between comparisons.
        let mut tree_cache = crate::git::tree_builder::TreeLookupCache::new();
        let mut next_meta: Option<CommitMeta> = None;
        let mut scanned = 0usize;
        let mut out = Vec::new();

        while let Some(commit_oid) = next_oid {
            let meta = match next_meta.take() {
                Some(meta) => meta,
                None => {
                    load_commit_meta(self.object_store.as_ref(), &req.account, &commit_oid).await?
                }
            };
            let parent_oid = meta.parents.first().copied();

            let parent_meta = match (filtered_paths, parent_oid) {
                (Some(_), Some(parent_oid)) => Some(
                    load_commit_meta(self.object_store.as_ref(), &req.account, &parent_oid).await?,
                ),
                _ => None,
            };

            let include = match filtered_paths {
                None => true,
                Some(paths) => {
                    scanned += 1;
                    tree_cache.begin_batch();
                    commit_touches_any_path(
                        self.object_store.as_ref(),
                        &req.account,
                        meta.tree,
                        parent_meta.as_ref().map(|parent| parent.tree),
                        paths,
                        &mut tree_cache,
                    )
                    .await?
                }
            };

            next_oid = parent_oid;
            next_meta = parent_meta;

            if include {
                out.push(log_entry_from_meta(commit_oid, meta));
                if out.len() >= req.limit {
                    return Ok(out);
                }
            }

            if filtered_paths.is_some() && scanned >= max_commits_scanned && next_oid.is_some() {
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
        let mut tree_cache = crate::git::tree_builder::TreeLookupCache::new();
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
            tree_cache.begin_batch();
            if commit_touches_any_path(
                self.object_store.as_ref(),
                &req.account,
                meta.tree,
                parent_meta.as_ref().map(|parent| parent.tree),
                paths,
                &mut tree_cache,
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
    tree_cache: &mut crate::git::tree_builder::TreeLookupCache,
) -> Result<bool, GitError> {
    for path in paths {
        let current =
            crate::git::tree_builder::lookup_cached(store, account, current_tree, path, tree_cache)
                .await?;
        let parent = match parent_tree {
            Some(tree) => {
                crate::git::tree_builder::lookup_cached(store, account, tree, path, tree_cache)
                    .await?
            }
            None => None,
        };
        if current != parent {
            return Ok(true);
        }
    }
    Ok(false)
}
