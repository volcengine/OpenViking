//! SnapshotLog path history index provider.

mod mysql;
mod sqlite;
mod store;

pub use mysql::MySqlSnapshotLogStore;
pub use sqlite::SqliteSnapshotLogStore;
pub use store::{
    fanout_changed_paths, can_index_account_branch, can_index_paths, SnapshotLogError,
    SnapshotLogHit, SnapshotLogLimits, SnapshotLogRow, SnapshotLogStore,
};

/// Maximum commits scanned between HEAD and the latest indexed commit.
pub const SNAPSHOT_LOG_TAIL_SCAN_LIMIT: usize = 1_000;

/// Conservative per-commit row cap for synchronous best-effort indexing.
pub const SNAPSHOT_LOG_MAX_ROWS_PER_COMMIT: usize = 100_000;
