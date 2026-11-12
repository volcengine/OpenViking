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
                    commit_touches_any_path(
                        self.object_store.as_ref(),
                        &req.account,
                        meta.tree,
                        parent_meta.as_ref().map(|parent| parent.tree),
                        paths,
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

pub(super) fn normalize_log_paths(paths: Option<Vec<String>>) -> Result<Option<Vec<String>>, GitError> {
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
) -> Result<bool, GitError> {
    let mut current_cache = crate::git::tree_builder::TreeLookupCache::new();
    let mut parent_cache = crate::git::tree_builder::TreeLookupCache::new();

    for path in paths {
        let current = crate::git::tree_builder::lookup_cached(
            store,
            account,
            current_tree,
            path,
            &mut current_cache,
        )
        .await?;
        let parent = match parent_tree {
            Some(tree) => {
                crate::git::tree_builder::lookup_cached(
                    store,
                    account,
                    tree,
                    path,
                    &mut parent_cache,
                )
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
