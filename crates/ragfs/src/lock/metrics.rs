//! Lock observability metrics.

use crate::core::stats::DURATION_BUCKET_BOUNDS_NS;
use crate::lock::types::PathLockConflict;

/// Max number of recent conflicts retained for observability.
const MAX_RECENT_CONFLICTS: usize = 32;

/// Runtime metrics collected by `PathLockManager`.
#[derive(Debug, Clone)]
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
    /// Total nanoseconds spent in descendant scans.
    pub descendant_scan_duration_ns: u64,
    /// Noncumulative scan duration counts, with one final overflow bucket.
    pub descendant_scan_duration_bucket_counts: Vec<u64>,
    /// Total nanoseconds spent waiting for successfully acquired locks.
    pub wait_duration_ns: u64,
    /// Count of conflicts by type (exact/exact, tree/exact, etc.).
    pub conflict_count: usize,
    /// Recent conflicts for observability (ring buffer).
    pub recent_conflicts: Vec<PathLockConflict>,
}

impl Default for LockMetrics {
    fn default() -> Self {
        Self {
            active_lock_count: 0,
            waiting_lock_count: 0,
            stale_tokens_removed: 0,
            stale_leases_released: 0,
            stale_cleanup_failures: 0,
            descendant_scan_count: 0,
            descendant_scan_duration_ns: 0,
            descendant_scan_duration_bucket_counts: vec![0; DURATION_BUCKET_BOUNDS_NS.len() + 1],
            wait_duration_ns: 0,
            conflict_count: 0,
            recent_conflicts: Vec::new(),
        }
    }
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
