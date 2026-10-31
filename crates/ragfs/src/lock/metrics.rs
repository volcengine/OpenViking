//! Lock observability metrics.

use crate::lock::types::PathLockConflict;
use crate::metrics::duration::DurationMetrics;
use std::future::Future;
use std::time::Instant;

use opentelemetry::KeyValue;

use super::types::{PathLockError, PathLockResult};

/// One operation's bounded labels and shared native duration collector.
pub(crate) struct LockProfile {
    durations: DurationMetrics,
    batch_size: &'static str,
}

impl LockProfile {
    pub(crate) fn new(durations: DurationMetrics, paths: usize) -> Self {
        Self {
            durations,
            batch_size: match paths {
                0..=1 => "1",
                2..=5 => "2_5",
                6..=13 => "6_13",
                _ => "14_plus",
            },
        }
    }

    pub(crate) fn timer(&self, phase: &'static str) -> PhaseTimer<'_> {
        PhaseTimer {
            profile: self,
            phase,
            start: Instant::now(),
            outcome: "cancelled",
        }
    }

    pub(crate) async fn measure<T>(
        &self,
        phase: &'static str,
        future: impl Future<Output = PathLockResult<T>>,
    ) -> PathLockResult<T> {
        let mut timer = self.timer(phase);
        let result = future.await;
        timer.finish(&result);
        result
    }
}

/// Record even failed or cancelled I/O, without paths, owners, or token values.
pub(crate) struct PhaseTimer<'a> {
    profile: &'a LockProfile,
    phase: &'static str,
    start: Instant,
    outcome: &'static str,
}

impl PhaseTimer<'_> {
    pub(crate) fn finish<T>(&mut self, result: &PathLockResult<T>) {
        self.outcome = match result {
            Ok(_) => "success",
            Err(PathLockError::Conflict { .. } | PathLockError::Busy { .. }) => "conflict",
            Err(PathLockError::Timeout { .. }) => "timeout",
            Err(_) => "error",
        };
    }
}

impl Drop for PhaseTimer<'_> {
    fn drop(&mut self) {
        self.profile.durations.record(
            self.start.elapsed(),
            &[
                KeyValue::new("phase", self.phase),
                KeyValue::new("outcome", self.outcome),
                KeyValue::new("batch_size", self.profile.batch_size),
            ],
        );
    }
}

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
    /// SDK-owned descendant scan duration distribution.
    pub(crate) descendant_scan: DurationMetrics,
    /// Native acquire/release and individual I/O phase durations.
    pub(crate) phases: DurationMetrics,
    /// SDK-owned Redis acquisition round-trip durations, when using Redis.
    pub(crate) redis_acquire: Option<DurationMetrics>,
    /// Redis acquisitions exceeding the slow-log threshold.
    pub redis_slow_acquisitions: u64,
    /// Total nanoseconds spent waiting for successfully acquired locks.
    pub wait_duration_ns: u64,
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
