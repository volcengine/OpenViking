//! Generic write-time hook registry.
//!
//! A hook observes the bytes of a write before they reach the filesystem.
//! Registration binds one [`PathMatcher`] to one [`WriteHook`]. Hooks run in
//! registration order and the first rejection stops the write.
//!
//! # Registration rules
//!
//! - Use [`PathMatcher::Exact`] for one complete path.
//! - Use [`PathMatcher::Prefix`] for a fixed directory or path family.
//! - Use [`PathMatcher::Suffix`] for a fixed filename or extension.
//! - When a family contains variable path segments, register the broadest
//!   matcher and narrow it inside the hook, for example by calling a canonical
//!   path-shape predicate before validating content.
//!
//! Hooks must be deterministic, cheap and side-effect free apart from logging.
//! They run on the caller's thread, so a hook that rejects a write returns a
//! structured [`WriteHookError`] instead of panicking.
//!
//! # Example
//!
//! ```
//! use std::sync::Arc;
//! use ragfs::write_hook::{
//!     PathMatcher, WriteContext, WriteHook, WriteHookError, WriteHookRegistry,
//! };
//!
//! struct AuditMarkdown;
//!
//! impl WriteHook for AuditMarkdown {
//!     fn name(&self) -> &str {
//!         "audit-markdown"
//!     }
//!
//!     fn on_write(&self, context: &WriteContext<'_>) -> Result<(), WriteHookError> {
//!         tracing::info!(path = context.path, "markdown write");
//!         Ok(())
//!     }
//! }
//!
//! let mut registry = WriteHookRegistry::new();
//! registry.register(PathMatcher::suffix(".md"), Arc::new(AuditMarkdown));
//! ```

use std::fmt;
use std::sync::Arc;

/// Selects the paths observed by one registered hook.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PathMatcher {
    /// Match one complete path.
    Exact(String),
    /// Match every path that starts with this prefix.
    Prefix(String),
    /// Match every path that ends with this suffix.
    Suffix(String),
}

impl PathMatcher {
    /// Match one complete path.
    pub fn exact(path: impl Into<String>) -> Self {
        Self::Exact(path.into())
    }

    /// Match every path that starts with `prefix`.
    pub fn prefix(prefix: impl Into<String>) -> Self {
        Self::Prefix(prefix.into())
    }

    /// Match every path that ends with `suffix`.
    pub fn suffix(suffix: impl Into<String>) -> Self {
        Self::Suffix(suffix.into())
    }

    /// Return whether `path` satisfies this matcher.
    pub fn matches(&self, path: &str) -> bool {
        match self {
            Self::Exact(expected) => path == expected,
            Self::Prefix(prefix) => path.starts_with(prefix),
            Self::Suffix(suffix) => path.ends_with(suffix),
        }
    }
}

/// Borrowed write request passed to every matching hook.
#[derive(Debug, Clone, Copy)]
pub struct WriteContext<'a> {
    /// Absolute path being written.
    pub path: &'a str,
    /// Bytes that will be written.
    pub data: &'a [u8],
}

/// A synchronous observer invoked before a write reaches the filesystem.
///
/// Hooks may emit logs and return [`Ok`] to continue, or return a
/// [`WriteHookError`] to reject the write. A rejected write must not touch the
/// filesystem.
pub trait WriteHook: Send + Sync {
    /// Stable hook name used in diagnostics.
    fn name(&self) -> &str;

    /// Inspect one write and decide whether it may proceed.
    fn on_write(&self, context: &WriteContext<'_>) -> Result<(), WriteHookError>;
}

/// Structured rejection returned by a [`WriteHook`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WriteHookError {
    hook: String,
    code: String,
    message: String,
}

impl WriteHookError {
    /// Build a rejection attributed to `hook` with a stable machine-readable `code`.
    pub fn new(
        hook: impl Into<String>,
        code: impl Into<String>,
        message: impl Into<String>,
    ) -> Self {
        Self {
            hook: hook.into(),
            code: code.into(),
            message: message.into(),
        }
    }

    /// Hook that rejected the write.
    pub fn hook(&self) -> &str {
        &self.hook
    }

    /// Stable machine-readable error code.
    pub fn code(&self) -> &str {
        &self.code
    }

    /// Human-readable rejection reason.
    pub fn message(&self) -> &str {
        &self.message
    }
}

impl fmt::Display for WriteHookError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "{}: {}", self.code, self.message)
    }
}

impl std::error::Error for WriteHookError {}

struct RegisteredWriteHook {
    matcher: PathMatcher,
    hook: Arc<dyn WriteHook>,
}

/// Ordered registry of write-time hooks.
///
/// Every write entry point should call [`WriteHookRegistry::check`] before
/// touching the filesystem so a hook can never be bypassed by a second write
/// path.
#[derive(Default)]
pub struct WriteHookRegistry {
    hooks: Vec<RegisteredWriteHook>,
}

impl WriteHookRegistry {
    /// Create an empty registry.
    pub fn new() -> Self {
        Self::default()
    }

    /// Register `hook` for paths selected by `matcher`.
    ///
    /// Hooks run in registration order.
    pub fn register(&mut self, matcher: PathMatcher, hook: Arc<dyn WriteHook>) {
        self.hooks.push(RegisteredWriteHook { matcher, hook });
    }

    /// Return whether no hooks are registered.
    pub fn is_empty(&self) -> bool {
        self.hooks.is_empty()
    }

    /// Run every hook whose matcher accepts `path`.
    ///
    /// The first rejection is returned and later hooks are skipped.
    pub fn check(&self, path: &str, data: &[u8]) -> Result<(), WriteHookError> {
        let context = WriteContext { path, data };
        for registered in &self.hooks {
            if registered.matcher.matches(path) {
                registered.hook.on_write(&context)?;
            }
        }
        Ok(())
    }
}
