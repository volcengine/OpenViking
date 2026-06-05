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

/// Immutable filesystem context snapshot. Currently carries only `account_id` (the tenant).
pub type FsContext = Arc<FsContextInner>;

tokio::task_local! {
    /// The FS context bound to the current task (future). Set via `FS_CTX.scope(ctx, fut)`.
    pub static FS_CTX: FsContext;
}

/// Context payload (immutable). Fields are filled once at construction, then read-only.
#[derive(Clone, Debug)]
pub struct FsContextInner {
    account_id: String,
}

impl FsContextInner {
    /// Construct a context carrying the given tenant `account_id`.
    pub fn new(account_id: impl Into<String>) -> Self {
        Self {
            account_id: account_id.into(),
        }
    }

    /// Tenant identifier (account_id == tenant).
    pub fn account_id(&self) -> &str {
        &self.account_id
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
}
