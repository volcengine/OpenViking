//! PathLockProvider trait and built-in implementations.
//!
//! The provider owns token storage. Backends with transactional storage may also
//! implement the optional atomic capabilities used by `PathLockManager`.

use std::collections::BTreeMap;
use std::sync::Arc;

use async_trait::async_trait;
use tokio::sync::RwLock;

use crate::core::internal_names::is_hidden_runtime_lock_name;
use crate::core::FileSystem;
use crate::crypto;

use super::codec::LockTokenCodec;
use super::types::{
    HandoffRecoveryReason, LockHolder, LockToken, PathLockError, PathLockKind, PathLockRequest,
    PathLockResult,
};

/// Representation used for handles stored in a PathLock lease.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PathLockHandleMode {
    /// Handles are resolver-generated lock-file paths.
    LockPath,
    /// Handles are normalized logical paths.
    LogicalPath,
}

/// Token mutation made during one lock acquisition.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AcquisitionChange {
    /// A new token was created.
    Created {
        /// Token written by the provider.
        replacement: LockToken,
    },
    /// The same owner already held an equal or stronger token.
    Reentrant,
    /// A same-owner Exact token became Tree.
    Upgraded {
        /// Token restored if local lease publication fails.
        previous: LockToken,
        /// Tree token written by the provider.
        replacement: LockToken,
    },
}

/// One mutation made by an atomic provider acquisition.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AtomicAcquisition {
    /// Provider handle stored in the PathLock lease.
    pub handle: String,
    /// Token mutation used by rollback.
    pub change: AcquisitionChange,
}

/// Committed atomic acquisition mutations and storage-side recovery statistics.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AtomicAcquisitionResult {
    /// Mutations used to publish or roll back the local lease.
    pub acquisitions: Vec<AtomicAcquisition>,
    /// Stale tokens actually removed or replaced by this committed transaction.
    pub stale_tokens_removed: usize,
}

/// Storage abstraction for lock tokens. Only `PathLockManager` should call these methods.
#[async_trait]
pub trait PathLockProvider: Send + Sync {
    /// Undo a handoff by removing only its new holder and restoring a still-live
    /// source. Preserve concurrent sibling renewals. Backends that store their
    /// own timestamps must override this and judge expiry using the same clock;
    /// `stale_before` is only authoritative for client-clock storage.
    async fn replace_holder(
        &self,
        path: &str,
        owner: &str,
        holder: &str,
        restored: LockHolder,
        stale_before: u128,
    ) -> PathLockResult<()> {
        let started_at = std::time::Instant::now();
        for _ in 0..16 {
            let Some(current) = self.read_token(path).await? else {
                return Ok(());
            };
            if current.owner_id != owner || !current.holders.iter().any(|h| h.id == holder) {
                return Ok(());
            }
            let stale_before = stale_before.saturating_add(started_at.elapsed().as_nanos());
            let mut next = current.clone();
            next.holders
                .retain(|h| h.id != holder && h.time_ns >= stale_before);
            if restored.time_ns >= stale_before && !next.holders.iter().any(|h| h.id == restored.id)
            {
                next.holders.push(restored.clone());
            }
            next.recompute();
            let changed = if next.holders.is_empty() {
                self.compare_and_remove_token(path, &current).await?
            } else {
                self.compare_and_write_token(path, &current, &next).await?
            };
            if changed {
                return Ok(());
            }
        }
        Err(PathLockError::Busy {
            lock_path: path.into(),
            operation: "holder rollback".into(),
        })
    }

    /// Atomic conflict check plus holder insertion/replacement. A supplied source
    /// must exist, be live and match the requested kind before any mutation.
    /// None selects the filesystem CAS protocol; transactional backends override.
    async fn try_acquire_holders_atomic(
        &self,
        _requests: &[PathLockRequest],
        _owner: &str,
        _holder: &str,
        _now: u128,
        _stale_before: u128,
        _source: Option<&str>,
    ) -> PathLockResult<Option<AtomicAcquisitionResult>> {
        Ok(None)
    }

    /// Delete precisely the observed token, including its holder set.
    async fn compare_and_remove_token(
        &self,
        _path: &str,
        _expected: &LockToken,
    ) -> PathLockResult<bool> {
        Err(PathLockError::Internal(
            "provider lacks conditional token deletion".into(),
        ))
    }

    /// Refresh/release only the named reference. Missing and expired references
    /// never revive, even when another live reference has the same owner.
    async fn mutate_holder(
        &self,
        path: &str,
        owner: &str,
        holder: &str,
        now: u128,
        stale_before: u128,
        release: bool,
    ) -> PathLockResult<bool> {
        for _ in 0..16 {
            let Some(current) = self.read_token(path).await? else {
                return Ok(false);
            };
            let Some(next) =
                mutate_holder_record(&current, owner, holder, now, stale_before, release)
            else {
                return Ok(false);
            };
            let changed = if next.holders.is_empty() {
                self.compare_and_remove_token(path, &current).await?
            } else {
                self.compare_and_write_token(path, &current, &next).await?
            };
            if changed {
                return Ok(true);
            }
        }
        Err(PathLockError::Busy {
            lock_path: path.into(),
            operation: "holder mutation".into(),
        })
    }

    /// Renew one lease across its paths, preserving result order.
    async fn refresh_holders(
        &self,
        paths: &[String],
        owner: &str,
        holder: &str,
        now: u128,
        stale_before: u128,
    ) -> Vec<PathLockResult<bool>> {
        let mut results = Vec::with_capacity(paths.len());
        for path in paths {
            results.push(
                self.mutate_holder(path, owner, holder, now, stale_before, false)
                    .await,
            );
        }
        results
    }
    /// Add provider-owned observations to the manager's metrics snapshot.
    fn update_metrics(&self, _metrics: &mut super::metrics::LockMetrics) {}

    /// Human-readable provider name for config/logging/metrics.
    fn name(&self) -> &'static str;

    /// Return how this provider represents lease handles.
    fn handle_mode(&self) -> PathLockHandleMode {
        PathLockHandleMode::LockPath
    }

    /// Legacy v1 token API, retained for storage compatibility tests. The manager
    /// exclusively uses `try_acquire_holders_atomic`.
    async fn try_acquire_batch_atomic(
        &self,
        _requests: &[PathLockRequest],
        _owner_id: &str,
        _now_ns: u128,
        _stale_before_ns: u128,
    ) -> PathLockResult<Option<Vec<AtomicAcquisition>>> {
        Ok(None)
    }

    /// Acquire with recovery statistics. Existing providers keep their atomic hook
    /// and report zero until they implement storage-side recovery accounting.
    async fn try_acquire_batch_atomic_with_stats(
        &self,
        requests: &[PathLockRequest],
        owner_id: &str,
        now_ns: u128,
        stale_before_ns: u128,
    ) -> PathLockResult<Option<AtomicAcquisitionResult>> {
        Ok(self
            .try_acquire_batch_atomic(requests, owner_id, now_ns, stale_before_ns)
            .await?
            .map(|acquisitions| AtomicAcquisitionResult {
                acquisitions,
                stale_tokens_removed: 0,
            }))
    }

    /// Legacy v1 rollback. Holder acquisitions roll back the individual holder
    /// and handoff source through `mutate_holder` and `replace_holder`.
    async fn rollback_acquisitions_atomic(
        &self,
        _acquisitions: &[AtomicAcquisition],
        _owner_id: &str,
    ) -> PathLockResult<Option<()>> {
        Ok(None)
    }

    /// Return whether one path is covered by a live token when supported.
    async fn is_path_locked_atomic(
        &self,
        _path: &str,
        _now_ns: u128,
        _stale_before_ns: u128,
        _ignore_stale: bool,
    ) -> PathLockResult<Option<bool>> {
        Ok(None)
    }

    /// Read the token at `lock_path`, returning `None` if no token exists.
    async fn read_token(&self, lock_path: &str) -> PathLockResult<Option<LockToken>>;

    /// Atomically create a token at `lock_path`. Must fail if a token already exists
    /// (unless it is stale and can be cleaned up by the manager).
    async fn try_create_token(&self, lock_path: &str, token: &LockToken) -> PathLockResult<()>;

    /// Atomically replace one exact token value.
    async fn compare_and_write_token(
        &self,
        lock_path: &str,
        expected: &LockToken,
        replacement: &LockToken,
    ) -> PathLockResult<bool>;

    /// Legacy v1 refresh; must refuse tokens containing holders.
    /// Returns `true` if the token was refreshed, `false` if not found or wrong owner.
    async fn refresh_token(
        &self,
        lock_path: &str,
        owner_id: &str,
        time_ns: u128,
    ) -> PathLockResult<bool>;

    /// Refresh owned tokens, returning one result per input path in input order.
    /// A failed path must not prevent the remaining paths from being refreshed.
    /// Backends without batch support retain the single-token behavior.
    async fn refresh_tokens(
        &self,
        lock_paths: &[String],
        owner_id: &str,
        time_ns: u128,
    ) -> Vec<PathLockResult<bool>> {
        let mut results = Vec::with_capacity(lock_paths.len());
        for lock_path in lock_paths {
            results.push(self.refresh_token(lock_path, owner_id, time_ns).await);
        }
        results
    }

    /// Legacy v1 removal; must refuse tokens containing holders.
    /// `force` bypasses storage CAS after the ownership check.
    /// Returns `true` if removed, `false` if not found or wrong owner.
    async fn remove_token(
        &self,
        lock_path: &str,
        owner_id: &str,
        force: bool,
    ) -> PathLockResult<bool>;

    /// Scan all descendant lock paths under `root`.
    async fn scan_descendant_locks(&self, root: &str) -> PathLockResult<Vec<String>>;
}

// ── Memory provider ──

/// In-process lock table indexed by path. Single-process only.
pub struct MemoryPathLockProvider {
    tokens: RwLock<BTreeMap<String, LockToken>>,
}

impl MemoryPathLockProvider {
    /// Create an empty in-memory provider.
    pub fn new() -> Self {
        Self {
            tokens: RwLock::new(BTreeMap::new()),
        }
    }
}

impl Default for MemoryPathLockProvider {
    fn default() -> Self {
        Self::new()
    }
}

/// Refresh or release one holder; `None` when it is absent or expired. Expired
/// sibling holders are dropped, and an empty result means delete the token.
pub(crate) fn mutate_holder_record(
    current: &LockToken,
    owner: &str,
    holder: &str,
    now: u128,
    stale_before: u128,
    release: bool,
) -> Option<LockToken> {
    if current.owner_id != owner {
        return None;
    }
    let index = current.holders.iter().position(|h| h.id == holder)?;
    if !release && current.holders[index].time_ns < stale_before {
        return None;
    }
    let mut next = current.clone();
    if release {
        next.holders.remove(index);
    } else {
        next.holders[index].time_ns = now;
    }
    next.holders.retain(|h| h.time_ns >= stale_before);
    next.recompute();
    Some(next)
}

/// Token storage visible inside one memory or SQLite acquisition transaction.
pub(crate) trait HolderStore {
    fn get(&mut self, path: &str) -> PathLockResult<Option<LockToken>>;
    /// Delete other owners' expired tokens at `path`, and below it for `subtree`.
    fn remove_stale(
        &mut self,
        path: &str,
        subtree: bool,
        owner: &str,
        stale_before: u128,
    ) -> PathLockResult<usize>;
    /// Return one live descendant token held by another owner.
    fn live_descendant(
        &mut self,
        root: &str,
        owner: &str,
        stale_before: u128,
    ) -> PathLockResult<Option<(String, LockToken)>>;
    fn put(&mut self, path: &str, token: &LockToken) -> PathLockResult<()>;
}

pub(crate) fn is_descendant(path: &str, root: &str) -> bool {
    path != root && (root == "/" || path.strip_prefix(root).is_some_and(|s| s.starts_with('/')))
}

pub(crate) fn descendant_bounds(path: &str) -> (String, String) {
    if path == "/" {
        ("/".into(), "0".into())
    } else {
        (format!("{path}/"), format!("{path}0"))
    }
}

/// Validate the source against the token snapshot guarded by a transaction or CAS.
pub(crate) fn validate_handoff_source(
    token: Option<&LockToken>,
    owner: &str,
    source: &str,
    kind: PathLockKind,
    stale_before: u128,
) -> PathLockResult<()> {
    let source = token
        .filter(|token| token.owner_id == owner)
        .and_then(|token| token.holders.iter().find(|holder| holder.id == source))
        .filter(|holder| holder.time_ns >= stale_before)
        .ok_or(PathLockError::HandoffRecoveryRequired {
            reason: HandoffRecoveryReason::TokenUnavailable,
        })?;
    if source.kind != kind {
        return Err(PathLockError::InvalidRequest(
            "handoff kind does not match holder".into(),
        ));
    }
    Ok(())
}

/// Transaction-local holder acquisition used by memory and SQLite. Reads only the
/// requested paths, their ancestors and, for Tree requests, their subtrees.
pub(crate) fn acquire_holder_records(
    store: &mut impl HolderStore,
    requests: &[PathLockRequest],
    owner: &str,
    holder: &str,
    now: u128,
    stale_before: u128,
    source: Option<&str>,
) -> PathLockResult<AtomicAcquisitionResult> {
    if requests.is_empty() || !LockTokenCodec::valid_id(owner) || !LockTokenCodec::valid_id(holder)
    {
        return Err(PathLockError::InvalidRequest(
            "invalid holder acquisition".into(),
        ));
    }
    let conflict = |path: &str, token: &LockToken| PathLockError::Conflict {
        lock_path: path.into(),
        owner: token.owner_id.clone(),
        kind: token.lock_type,
    };
    let mut stale_tokens_removed = 0;
    if let Some(source) = source {
        for request in requests {
            validate_handoff_source(
                store.get(&request.path)?.as_ref(),
                owner,
                source,
                request.kind,
                stale_before,
            )?;
        }
    }
    for request in requests {
        super::path::validate_normalized_logical_path(&request.path)?;
        let tree = request.kind == super::types::PathLockKind::Tree;
        // Expired conflicting records are deleted so a legacy owner cannot
        // revive them after this takeover commits.
        stale_tokens_removed += store.remove_stale(&request.path, tree, owner, stale_before)?;
        if let Some(token) = store.get(&request.path)? {
            if token.owner_id != owner && token.live_kind(stale_before).is_some() {
                return Err(conflict(&request.path, &token));
            }
        }
        for ancestor in super::resolver::LockPathResolver::ancestor_paths(&request.path) {
            stale_tokens_removed += store.remove_stale(&ancestor, false, owner, stale_before)?;
            if let Some(token) = store.get(&ancestor)? {
                if token.owner_id != owner
                    && token.live_kind(stale_before) == Some(super::types::PathLockKind::Tree)
                {
                    return Err(conflict(&ancestor, &token));
                }
            }
        }
        if tree {
            if let Some((path, token)) =
                store.live_descendant(&request.path, owner, stale_before)?
            {
                return Err(conflict(&path, &token));
            }
        }
    }
    let mut acquisitions = Vec::with_capacity(requests.len());
    for request in requests {
        let previous = store.get(&request.path)?;
        let mut replacement = LockToken::held(owner, holder, request.kind, now);
        let change = if let Some(previous) = previous.filter(|t| t.owner_id == owner) {
            replacement = previous.clone();
            replacement.add_holder(holder, request.kind, now, stale_before, source);
            AcquisitionChange::Upgraded {
                previous,
                replacement: replacement.clone(),
            }
        } else {
            AcquisitionChange::Created {
                replacement: replacement.clone(),
            }
        };
        store.put(&request.path, &replacement)?;
        acquisitions.push(AtomicAcquisition {
            handle: request.path.clone(),
            change,
        });
    }
    Ok(AtomicAcquisitionResult {
        acquisitions,
        stale_tokens_removed,
    })
}

impl HolderStore for BTreeMap<String, LockToken> {
    fn get(&mut self, path: &str) -> PathLockResult<Option<LockToken>> {
        Ok(BTreeMap::get(self, path).cloned())
    }

    fn remove_stale(
        &mut self,
        path: &str,
        subtree: bool,
        owner: &str,
        stale_before: u128,
    ) -> PathLockResult<usize> {
        let stale = |t: &LockToken| t.owner_id != owner && t.live_kind(stale_before).is_none();
        let remove = BTreeMap::get(self, path).is_some_and(stale);
        if remove {
            self.remove(path);
        }
        let mut removed = usize::from(remove);
        if subtree {
            let (lower, upper) = descendant_bounds(path);
            let expired: Vec<_> = self
                .range(lower..upper)
                .filter(|(p, t)| p.as_str() != path && stale(t))
                .map(|(p, _)| p.clone())
                .collect();
            removed += expired.len();
            for p in expired {
                self.remove(&p);
            }
        }
        Ok(removed)
    }

    fn live_descendant(
        &mut self,
        root: &str,
        owner: &str,
        stale_before: u128,
    ) -> PathLockResult<Option<(String, LockToken)>> {
        let (lower, upper) = descendant_bounds(root);
        Ok(self
            .range(lower..upper)
            .find(|(p, t)| {
                is_descendant(p, root) && t.owner_id != owner && t.live_kind(stale_before).is_some()
            })
            .map(|(p, t)| (p.clone(), t.clone())))
    }

    fn put(&mut self, path: &str, token: &LockToken) -> PathLockResult<()> {
        self.insert(path.into(), token.clone());
        Ok(())
    }
}

#[async_trait]
impl PathLockProvider for MemoryPathLockProvider {
    fn handle_mode(&self) -> PathLockHandleMode {
        PathLockHandleMode::LogicalPath
    }

    async fn try_acquire_holders_atomic(
        &self,
        requests: &[PathLockRequest],
        owner: &str,
        holder: &str,
        now: u128,
        stale_before: u128,
        source: Option<&str>,
    ) -> PathLockResult<Option<AtomicAcquisitionResult>> {
        let mut tokens = self.tokens.write().await;
        acquire_holder_records(
            &mut *tokens,
            requests,
            owner,
            holder,
            now,
            stale_before,
            source,
        )
        .map(Some)
    }

    async fn is_path_locked_atomic(
        &self,
        path: &str,
        _now: u128,
        stale_before: u128,
        ignore_stale: bool,
    ) -> PathLockResult<Option<bool>> {
        let tokens = self.tokens.read().await;
        Ok(Some(tokens.iter().any(|(p, t)| {
            let kind = if ignore_stale {
                t.live_kind(stale_before)
            } else {
                Some(t.lock_type)
            };
            kind.is_some()
                && (p == path
                    || (kind == Some(super::types::PathLockKind::Tree)
                        && (p == "/" || path.strip_prefix(p).is_some_and(|s| s.starts_with('/')))))
        })))
    }
    async fn compare_and_remove_token(
        &self,
        path: &str,
        expected: &LockToken,
    ) -> PathLockResult<bool> {
        let mut tokens = self.tokens.write().await;
        if tokens.get(path) == Some(expected) {
            tokens.remove(path);
            Ok(true)
        } else {
            Ok(false)
        }
    }
    fn name(&self) -> &'static str {
        "memory"
    }

    async fn read_token(&self, lock_path: &str) -> PathLockResult<Option<LockToken>> {
        Ok(self.tokens.read().await.get(lock_path).cloned())
    }

    async fn try_create_token(&self, lock_path: &str, token: &LockToken) -> PathLockResult<()> {
        let mut tokens = self.tokens.write().await;
        if tokens.contains_key(lock_path) {
            return Err(PathLockError::Conflict {
                lock_path: lock_path.to_string(),
                owner: tokens[lock_path].owner_id.clone(),
                kind: tokens[lock_path].lock_type,
            });
        }
        tokens.insert(lock_path.to_string(), token.clone());
        Ok(())
    }

    async fn compare_and_write_token(
        &self,
        lock_path: &str,
        expected: &LockToken,
        replacement: &LockToken,
    ) -> PathLockResult<bool> {
        let mut tokens = self.tokens.write().await;
        match tokens.get(lock_path) {
            Some(token) if token == expected => {
                tokens.insert(lock_path.to_string(), replacement.clone());
                Ok(true)
            }
            _ => Ok(false),
        }
    }

    async fn refresh_token(
        &self,
        lock_path: &str,
        owner_id: &str,
        time_ns: u128,
    ) -> PathLockResult<bool> {
        let mut tokens = self.tokens.write().await;
        match tokens.get_mut(lock_path) {
            Some(token) if token.owner_id == owner_id && token.holders.is_empty() => {
                token.time_ns = time_ns;
                Ok(true)
            }
            _ => Ok(false),
        }
    }

    async fn remove_token(
        &self,
        lock_path: &str,
        owner_id: &str,
        _force: bool,
    ) -> PathLockResult<bool> {
        let mut tokens = self.tokens.write().await;
        match tokens.get(lock_path) {
            Some(token) if token.owner_id == owner_id && token.holders.is_empty() => {
                tokens.remove(lock_path);
                Ok(true)
            }
            _ => Ok(false),
        }
    }

    async fn scan_descendant_locks(&self, root: &str) -> PathLockResult<Vec<String>> {
        let prefix = if root.ends_with('/') {
            root.to_string()
        } else {
            format!("{}/", root)
        };
        let upper = if root == "/" {
            "0".into()
        } else {
            format!("{root}0")
        };
        Ok(self
            .tokens
            .read()
            .await
            .range(prefix..upper)
            .map(|(p, _)| p.clone())
            .collect())
    }
}

// ── Filesystem provider ──

/// Lock provider that stores tokens as files on the underlying filesystem.
pub struct FilesystemPathLockProvider {
    fs: Arc<dyn FileSystem>,
    empty_token_expire_secs: f64,
}

impl FilesystemPathLockProvider {
    /// Create a filesystem-backed provider with an explicit empty-token expiry.
    ///
    /// `fs` stores lock files, and `lock_expire_secs` controls empty-token recovery.
    /// Returns a configured filesystem provider.
    pub fn new(fs: Arc<dyn FileSystem>, lock_expire_secs: f64) -> Self {
        Self {
            fs,
            empty_token_expire_secs: lock_expire_secs,
        }
    }

    /// Read raw token bytes from `lock_path`, returning `None` if no token exists.
    async fn read_token_raw(&self, lock_path: &str) -> PathLockResult<Option<Vec<u8>>> {
        let mut result = self.fs.read(lock_path, 0, 0).await;
        if result
            .as_ref()
            .is_ok_and(|data| LockTokenCodec::decode(String::from_utf8_lossy(data).trim()).is_err())
        {
            // A concurrent in-place CAS write can expose a transient partial token.
            tokio::task::yield_now().await;
            result = self.fs.read(lock_path, 0, 0).await;
        }
        match result {
            Ok(data) => Ok(Some(data)),
            Err(e) => {
                if matches!(
                    e,
                    crate::core::Error::NotFound(_) | crate::core::Error::MountPointNotFound(_)
                ) {
                    Ok(None)
                } else {
                    Err(PathLockError::Io(format!(
                        "failed to read lock token at '{lock_path}': {e}"
                    )))
                }
            }
        }
    }

    /// Map filesystem CAS failures into pathlock busy vs. fatal I/O outcomes.
    fn map_cas_error(operation: &str, lock_path: &str, error: crate::core::Error) -> PathLockError {
        match error {
            crate::core::Error::WouldBlock(message) => PathLockError::Busy {
                lock_path: lock_path.to_string(),
                operation: format!("{operation} CAS ({message})"),
            },
            other => PathLockError::Io(format!("{operation} CAS failed: {other}")),
        }
    }
}

#[cfg(test)]
mod tests {
    use std::sync::Arc;

    use crate::core::{Error, FileSystem, WriteFlag};
    use crate::plugins::memfs::MemFileSystem;

    use super::*;

    #[test]
    fn invalid_source_batch_never_inserts_or_changes_holders() {
        let requests = [
            PathLockRequest {
                path: "/data/a".into(),
                kind: PathLockKind::Exact,
            },
            PathLockRequest {
                path: "/data/b".into(),
                kind: PathLockKind::Exact,
            },
        ];
        for second in [
            None,
            Some(LockToken::held(
                "owner",
                "sibling",
                PathLockKind::Exact,
                200,
            )),
            Some(LockToken::held("owner", "source", PathLockKind::Exact, 1)),
            Some(LockToken::held("owner", "source", PathLockKind::Tree, 200)),
            Some(LockToken::held(
                "foreign",
                "source",
                PathLockKind::Exact,
                200,
            )),
        ] {
            let wrong_kind = second
                .as_ref()
                .is_some_and(|token| token.lock_type == PathLockKind::Tree);
            let mut store = BTreeMap::from([(
                "/data/a".into(),
                LockToken::held("owner", "source", PathLockKind::Exact, 200),
            )]);
            if let Some(second) = second {
                store.insert("/data/b".into(), second);
            }
            let before = store.clone();
            let result = acquire_holder_records(
                &mut store,
                &requests,
                "owner",
                "new",
                300,
                100,
                Some("source"),
            );
            if wrong_kind {
                assert!(matches!(result, Err(PathLockError::InvalidRequest(_))));
            } else {
                assert!(matches!(
                    result,
                    Err(PathLockError::HandoffRecoveryRequired {
                        reason: HandoffRecoveryReason::TokenUnavailable,
                    })
                ));
            }
            assert_eq!(store, before);
        }
    }

    /// Verify the fallback reports each path and continues after a token error.
    #[tokio::test]
    async fn batch_refresh_fallback_preserves_per_path_results() {
        let fs = Arc::new(MemFileSystem::new());
        fs.mkdir("/data", 0o755).await.unwrap();
        for (path, raw) in [
            ("/data/bad", "not-a-token"),
            ("/data/good", "owner:100:T"),
            ("/data/foreign", "other:100:E"),
        ] {
            fs.write(path, raw.as_bytes(), 0, WriteFlag::Create)
                .await
                .unwrap();
        }
        let provider = FilesystemPathLockProvider::new(fs, 30.0);
        let paths = ["bad", "good", "foreign", "missing"].map(|name| format!("/data/{name}"));
        let results = provider.refresh_tokens(&paths, "owner", 200).await;
        assert_eq!(results.len(), paths.len());
        assert!(matches!(results[0], Err(PathLockError::InvalidToken(_))));
        assert!(matches!(results[1], Ok(true)));
        assert!(matches!(results[2], Ok(false)));
        assert!(matches!(results[3], Ok(false)));
        let token = provider.read_token(&paths[1]).await.unwrap().unwrap();
        assert_eq!(token.time_ns, 200);
        assert_eq!(token.lock_type, crate::lock::PathLockKind::Tree);
        assert!(provider.refresh_tokens(&[], "owner", 200).await.is_empty());
    }

    /// Verify legacy encrypted lock files are treated as removable upgrade leftovers.
    #[tokio::test]
    async fn read_token_cleans_up_legacy_encrypted_lock() {
        let fs = Arc::new(MemFileSystem::new());
        fs.mkdir("/data", 0o755).await.unwrap();
        fs.write(
            "/data/.path.ovlock",
            b"OVE1legacy-ciphertext",
            0,
            WriteFlag::Create,
        )
        .await
        .unwrap();
        let provider = FilesystemPathLockProvider::new(fs.clone(), 30.0);

        let token = provider.read_token("/data/.path.ovlock").await.unwrap();

        assert!(token.is_none());
        assert!(matches!(
            fs.read("/data/.path.ovlock", 0, 0).await,
            Err(Error::NotFound(_))
        ));
    }

    /// Verify malformed non-legacy lock files still surface as invalid tokens.
    #[tokio::test]
    async fn read_token_rejects_non_legacy_invalid_lock() {
        let fs = Arc::new(MemFileSystem::new());
        fs.mkdir("/data", 0o755).await.unwrap();
        fs.write("/data/.path.ovlock", b"not-a-token", 0, WriteFlag::Create)
            .await
            .unwrap();
        let provider = FilesystemPathLockProvider::new(fs.clone(), 30.0);

        let err = provider.read_token("/data/.path.ovlock").await.unwrap_err();

        assert!(matches!(err, PathLockError::InvalidToken(_)));
        assert_eq!(
            fs.read("/data/.path.ovlock", 0, 0).await.unwrap(),
            b"not-a-token"
        );
    }

    /// Verify valid plaintext lock files continue to decode unchanged.
    #[tokio::test]
    async fn read_token_accepts_plaintext_lock() {
        let fs = Arc::new(MemFileSystem::new());
        fs.mkdir("/data", 0o755).await.unwrap();
        fs.write("/data/.path.ovlock", b"owner:123:E", 0, WriteFlag::Create)
            .await
            .unwrap();
        let provider = FilesystemPathLockProvider::new(fs, 30.0);

        let token = provider
            .read_token("/data/.path.ovlock")
            .await
            .unwrap()
            .unwrap();

        assert_eq!(token.owner_id, "owner");
        assert_eq!(token.time_ns, 123);
        assert_eq!(token.lock_type, crate::lock::PathLockKind::Exact);
    }

    #[test]
    fn compare_and_remove_would_block_maps_to_busy() {
        let err = FilesystemPathLockProvider::map_cas_error(
            "remove",
            "/data/.path.ovlock",
            Error::WouldBlock("lock would block".to_string()),
        );
        assert!(matches!(err, PathLockError::Busy { .. }));
    }
}

#[async_trait]
impl PathLockProvider for FilesystemPathLockProvider {
    async fn compare_and_remove_token(
        &self,
        path: &str,
        expected: &LockToken,
    ) -> PathLockResult<bool> {
        self.fs
            .compare_and_remove(path, LockTokenCodec::encode(expected).as_bytes())
            .await
            .map_err(|e| Self::map_cas_error("remove", path, e))
    }
    fn name(&self) -> &'static str {
        "filesystem"
    }

    async fn read_token(&self, lock_path: &str) -> PathLockResult<Option<LockToken>> {
        let mut current = self.read_token_raw(lock_path).await?;
        loop {
            match current {
                Some(data) => {
                    if data.is_empty() {
                        let info = match self.fs.stat(lock_path).await {
                            Ok(info) => info,
                            Err(
                                crate::core::Error::NotFound(_)
                                | crate::core::Error::MountPointNotFound(_),
                            ) => return Ok(None),
                            Err(error) => {
                                return Err(PathLockError::Io(format!(
                                    "failed to stat empty lock token at '{lock_path}': {error}"
                                )));
                            }
                        };
                        let age = std::time::SystemTime::now()
                            .duration_since(info.mod_time)
                            .unwrap_or_default()
                            .as_secs_f64();
                        if age <= self.empty_token_expire_secs {
                            return Err(PathLockError::EmptyToken {
                                lock_path: lock_path.to_string(),
                            });
                        }
                        return Ok(None);
                    }
                    let raw = String::from_utf8_lossy(&data).trim().to_string();
                    match LockTokenCodec::decode(&raw) {
                        Ok(token) => return Ok(Some(token)),
                        Err(error) if crypto::is_encrypted(&data) => {
                            if self
                                .fs
                                .compare_and_remove(lock_path, &data)
                                .await
                                .map_err(|e| {
                                    Self::map_cas_error(
                                        "legacy encrypted lock cleanup",
                                        lock_path,
                                        e,
                                    )
                                })?
                            {
                                return Ok(None);
                            }
                            current = self.read_token_raw(lock_path).await?;
                        }
                        Err(error) => return Err(error),
                    }
                }
                None => return Ok(None),
            }
        }
    }

    async fn try_create_token(&self, lock_path: &str, token: &LockToken) -> PathLockResult<()> {
        use crate::core::WriteFlag;

        let encoded = LockTokenCodec::encode(token);
        let mut last_error = None;
        for _ in 0..2 {
            let error = match self
                .fs
                .write(lock_path, encoded.as_bytes(), 0, WriteFlag::CreateNew)
                .await
            {
                Ok(_) => return Ok(()),
                Err(error) => error,
            };
            match self.read_token(lock_path).await? {
                // Already exists — read and check if stale.
                Some(t) => {
                    return Err(PathLockError::Conflict {
                        lock_path: lock_path.to_string(),
                        owner: t.owner_id,
                        kind: t.lock_type,
                    });
                }
                None => {
                    last_error = Some(error);
                    if self
                        .fs
                        .compare_and_write(lock_path, b"", encoded.as_bytes())
                        .await
                        .map_err(|e| Self::map_cas_error("empty lock recovery", lock_path, e))?
                    {
                        return Ok(());
                    }
                }
            }
        }
        Err(PathLockError::Io(format!(
            "failed to create lock token at {lock_path}: {}",
            last_error.unwrap()
        )))
    }

    async fn compare_and_write_token(
        &self,
        lock_path: &str,
        expected: &LockToken,
        replacement: &LockToken,
    ) -> PathLockResult<bool> {
        let expected = LockTokenCodec::encode(expected);
        let replacement = LockTokenCodec::encode(replacement);
        self.fs
            .compare_and_write(lock_path, expected.as_bytes(), replacement.as_bytes())
            .await
            .map_err(|e| Self::map_cas_error("compare_and_write", lock_path, e))
    }

    async fn refresh_token(
        &self,
        lock_path: &str,
        owner_id: &str,
        time_ns: u128,
    ) -> PathLockResult<bool> {
        let Some(raw) = self.read_token_raw(lock_path).await? else {
            return Ok(false);
        };
        let token = LockTokenCodec::decode(&String::from_utf8_lossy(&raw).trim())?;
        if token.owner_id != owner_id || !token.holders.is_empty() {
            return Ok(false);
        }
        let new_token = LockToken {
            holders: Vec::new(),
            owner_id: owner_id.to_string(),
            time_ns,
            lock_type: token.lock_type,
        };
        let encoded = LockTokenCodec::encode(&new_token);
        self.fs
            .compare_and_write(lock_path, &raw, encoded.as_bytes())
            .await
            .map_err(|e| Self::map_cas_error("refresh", lock_path, e))
    }

    async fn remove_token(
        &self,
        lock_path: &str,
        owner_id: &str,
        force: bool,
    ) -> PathLockResult<bool> {
        let Some(raw) = self.read_token_raw(lock_path).await? else {
            return Ok(false);
        };
        let token = LockTokenCodec::decode(&String::from_utf8_lossy(&raw).trim())?;
        if token.owner_id != owner_id || !token.holders.is_empty() {
            return Ok(false);
        }
        if force {
            return match self.fs.remove(lock_path).await {
                Ok(()) => Ok(true),
                Err(
                    crate::core::Error::NotFound(_) | crate::core::Error::MountPointNotFound(_),
                ) => Ok(false),
                Err(error) => Err(PathLockError::Io(format!(
                    "failed to force remove lock token at '{lock_path}': {error}"
                ))),
            };
        }
        self.fs
            .compare_and_remove(lock_path, &raw)
            .await
            .map_err(|e| Self::map_cas_error("remove", lock_path, e))
    }

    async fn scan_descendant_locks(&self, root: &str) -> PathLockResult<Vec<String>> {
        let mut result = Vec::new();
        self.scan_recursive(root, &mut result).await?;
        Ok(result)
    }
}

impl FilesystemPathLockProvider {
    /// Recursively scan a directory for `.path.ovlock` and `.exact.ovlock.*` files.
    fn is_lock_file(name: &str) -> bool {
        is_hidden_runtime_lock_name(name)
    }

    async fn scan_recursive(&self, dir: &str, result: &mut Vec<String>) -> PathLockResult<()> {
        let entries = match self.fs.read_internal_dir(dir).await {
            Ok(entries) => entries,
            Err(crate::core::Error::NotFound(_) | crate::core::Error::NotADirectory(_)) => {
                return Ok(());
            }
            Err(error) => {
                return Err(PathLockError::Io(format!(
                    "failed to scan lock directory '{dir}': {error}"
                )));
            }
        };

        for entry in entries {
            let child_path = if dir.ends_with('/') {
                format!("{}{}", dir, entry.name)
            } else {
                format!("{}/{}", dir, entry.name)
            };

            if Self::is_lock_file(&entry.name) {
                result.push(child_path);
            } else if entry.is_dir {
                Box::pin(self.scan_recursive(&child_path, result)).await?;
            }
        }
        Ok(())
    }
}
