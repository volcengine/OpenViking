//! SnapshotLog path history index provider.

mod mysql;
#[cfg(feature = "postgres")]
mod postgres;
mod sqlite;
mod store;

pub use mysql::MySqlSnapshotLogStore;
#[cfg(feature = "postgres")]
pub use postgres::PostgresSnapshotLogStore;
pub use sqlite::SqliteSnapshotLogStore;
pub use store::{
    can_index_account_branch, can_index_paths, fanout_changed_paths, SnapshotLogError,
    SnapshotLogHit, SnapshotLogLimits, SnapshotLogRow, SnapshotLogStore,
};

/// Conservative per-commit row cap for synchronous best-effort indexing.
pub const SNAPSHOT_LOG_MAX_ROWS_PER_COMMIT: usize = 100_000;
