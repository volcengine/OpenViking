//! SnapshotLog path history index provider.

mod mysql;
mod sqlite;
mod store;

pub use mysql::MySqlSnapshotLogStore;
pub use sqlite::SqliteSnapshotLogStore;
pub use store::{
    can_index_account_branch, can_index_paths, fanout_changed_paths, validate_service_name,
    SnapshotLogError, SnapshotLogHit, SnapshotLogLimits, SnapshotLogRow, SnapshotLogStore,
};

/// Conservative per-commit row cap for synchronous best-effort indexing.
pub const SNAPSHOT_LOG_MAX_ROWS_PER_COMMIT: usize = 100_000;
