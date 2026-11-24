//! SnapshotLog path history index provider.

mod commit_index;
pub use commit_index::{CommitIndexRow, CommitIndexStore};

mod repair;
pub use repair::{PathHistoryRepair, PathHistoryGeneration};
mod mysql;
mod sqlite;
mod store;

pub use mysql::MySqlSnapshotLogStore;
pub use sqlite::SqliteSnapshotLogStore;
pub use store::{
    fanout_changed_paths, can_index_account_branch, can_index_paths, SnapshotLogError,
    SnapshotLogHit, SnapshotLogLimits, SnapshotLogRow, SnapshotLogStore,
};

/// Conservative per-commit row cap for synchronous best-effort indexing.
pub const SNAPSHOT_LOG_MAX_ROWS_PER_COMMIT: usize = 100_000;
