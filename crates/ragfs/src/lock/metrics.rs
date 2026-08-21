//! Lock observability metrics.

use crate::lock::types::PathLockConflict;

/// Max number of recent conflicts retained for observability.
const MAX_RECENT_CONFLICTS: usize = 32;

/// Runtime metrics collected by `PathLockManager`.
#[derive(Debug, Clone, Default)]
pub struct LockMetrics {
    /// Current number of active (held) locks.
    pub active_lock_count: usize,
    /// Current number of waiting lock requests.
    pub waiting_lock_count: usize,
    /// Cumulative count of stale tokens removed during acquire.
    pub stale_tokens_removed: usize,
    /// Cumulative count of stale leases released by heartbeat.
    pub stale_leases_released: usize,
    /// Cumulative count of stale cleanup failures.
    pub stale_cleanup_failures: usize,
    /// Cumulative count of descendant scan operations.
    pub descendant_scan_count: usize,
    /// Total milliseconds spent in descendant scans.
    pub descendant_scan_duration_ms: u64,
    /// Total milliseconds spent waiting for locks.
    pub wait_duration_ms: u64,
    /// Count of conflicts by type (exact/exact, tree/exact, etc.).
    pub conflict_count: usize,
    /// Recent conflicts for observability (ring buffer).
    pub recent_conflicts: Vec<PathLockConflict>,
}

impl LockMetrics {
    /// Record a conflict, keeping at most `MAX_RECENT_CONFLICTS` entries.
    pub fn record_conflict(&mut self, conflict: PathLockConflict) {
        self.conflict_count += 1;
        if self.recent_conflicts.len() >= MAX_RECENT_CONFLICTS {
            self.recent_conflicts.remove(0);
        }
        self.recent_conflicts.push(conflict);
    }
}
