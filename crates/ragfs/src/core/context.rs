//! Filesystem request context, propagated out-of-band via `tokio::task_local!`.
//!
//! The context (currently just `account_id`) must NOT enter the `FileSystem` trait method
//! signatures (that would pollute the core trait and force every plugin to change). Instead it
//! rides alongside the future via a task-local, so it survives `.await` points and cross-worker
//! migration on the multi-threaded runtime (a `thread_local` would not).
//!
//! The context is an immutable snapshot (`Arc<FsContextInner>`): fields are private and read-only,
//! there are no setters, so no wrapper can mutate it mid-operation.

use std::sync::Arc;

/// Immutable filesystem context snapshot. Currently carries `account_id` (the tenant)
/// and optional `PathLockContext`.
pub type FsContext = Arc<FsContextInner>;

tokio::task_local! {
    /// The FS context bound to the current task (future). Set via `FS_CTX.scope(ctx, fut)`.
    pub static FS_CTX: FsContext;
}

/// PathLock context carried in the FS context.
#[derive(Clone, Debug, Default)]
pub struct PathLockContext {
    /// Lease reference string for an existing owned lease.
    pub lease_ref: Option<String>,
    /// When true, automatic PathLock consumers skip lock validation and acquisition.
    pub disable_auto_pathlock: bool,
}

/// Context payload (immutable). Fields are filled once at construction, then read-only.
#[derive(Clone, Debug)]
pub struct FsContextInner {
    account_id: String,
    pathlock: Option<PathLockContext>,
    bypass_cache: bool,
}

impl FsContextInner {
    /// Construct a context carrying the given tenant `account_id`.
    pub fn new(account_id: impl Into<String>) -> Self {
        Self {
            account_id: account_id.into(),
            pathlock: None,
            bypass_cache: false,
        }
    }

    /// Construct a context with account_id and pathlock context.
    pub fn with_pathlock(
        account_id: impl Into<String>,
        pathlock: PathLockContext,
    ) -> Self {
        Self {
            account_id: account_id.into(),
            pathlock: Some(pathlock),
            bypass_cache: false,
        }
    }

    /// Return a context copy that forces plugin-local caches to serve fresh reads.
    pub fn with_bypass_cache(mut self, bypass_cache: bool) -> Self {
        self.bypass_cache = bypass_cache;
        self
    }

    /// Tenant identifier (account_id == tenant).
    pub fn account_id(&self) -> &str {
        &self.account_id
    }

    /// PathLock context, if set.
    pub fn pathlock(&self) -> Option<&PathLockContext> {
        self.pathlock.as_ref()
    }

    /// Whether plugin-local caches must be bypassed for this operation.
    pub fn bypass_cache(&self) -> bool {
        self.bypass_cache
    }

    /// Return a context copy that preserves identity and lease while disabling automatic PathLock.
    pub fn with_auto_pathlock_disabled(&self) -> Self {
        let mut pathlock = self.pathlock.clone().unwrap_or_default();
        pathlock.disable_auto_pathlock = true;
        Self {
            account_id: self.account_id.clone(),
            pathlock: Some(pathlock),
            bypass_cache: self.bypass_cache,
        }
    }
}

/// Read-only view over the current task-local context.
///
/// Centralizes "read current FS_CTX" so wrappers never repeat `FS_CTX.try_with(...)`. When the
/// task-local is not set (e.g. a direct unit test), returns an empty view whose getters yield
/// `None` instead of panicking.
pub struct FsContextView {
    inner: Option<FsContext>,
}

impl FsContextView {
    /// Snapshot the current task-local context (empty view if unset).
    pub fn current() -> Self {
        Self {
            inner: FS_CTX.try_with(|c| c.clone()).ok(),
        }
    }

    /// Tenant id from the current context, or `None` if unset.
    pub fn account_id(&self) -> Option<&str> {
        self.inner
            .as_ref()
            .map(|c| c.account_id())
            .filter(|account_id| !account_id.is_empty())
    }

    /// PathLock lease reference from the current context, or `None` if unset.
    pub fn pathlock_lease_ref(&self) -> Option<&str> {
        self.inner
            .as_ref()
            .and_then(|c| c.pathlock())
            .and_then(|p| p.lease_ref.as_deref())
    }

    /// Whether auto pathlock is disabled in the current context.
    pub fn disable_auto_pathlock(&self) -> bool {
        self.inner
            .as_ref()
            .and_then(|c| c.pathlock())
            .map(|p| p.disable_auto_pathlock)
            .unwrap_or(false)
    }

    /// Whether plugin-local caches must be bypassed for the current operation.
    pub fn bypass_cache(&self) -> bool {
        self.inner
            .as_ref()
            .map(|c| c.bypass_cache())
            .unwrap_or(false)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_view_when_unset_does_not_panic() {
        // No FS_CTX.scope around this call.
        let view = FsContextView::current();
        assert_eq!(view.account_id(), None);
    }

    #[tokio::test]
    async fn view_reads_scoped_context() {
        let ctx = Arc::new(FsContextInner::new("tenant-x"));
        FS_CTX
            .scope(ctx, async {
                assert_eq!(FsContextView::current().account_id(), Some("tenant-x"));
            })
            .await;
    }

    #[tokio::test]
    async fn view_treats_empty_account_id_as_missing() {
        let ctx = Arc::new(FsContextInner::new(""));
        FS_CTX
            .scope(ctx, async {
                assert_eq!(FsContextView::current().account_id(), None);
            })
            .await;
    }

    #[tokio::test]
    async fn context_is_isolated_across_tasks() {
        let a = Arc::new(FsContextInner::new("a"));
        FS_CTX
            .scope(a, async {
                assert_eq!(FsContextView::current().account_id(), Some("a"));
            })
            .await;
        // Outside the scope it is unset again.
        assert_eq!(FsContextView::current().account_id(), None);
    }

    #[tokio::test]
    async fn bypass_cache_defaults_false_and_round_trips() {
        assert!(!FsContextInner::new("t").bypass_cache());

        let ctx = Arc::new(FsContextInner::new("t").with_bypass_cache(true));
        FS_CTX
            .scope(ctx, async {
                assert!(FsContextView::current().bypass_cache());
            })
            .await;
        // Unset context reports no bypass.
        assert!(!FsContextView::current().bypass_cache());
    }
}
