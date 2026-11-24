//! Lightweight metrics owned by the cache wrapper.

use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;

/// Snapshot of cache wrapper counters.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct CacheMetricsSnapshot {
    /// Successful query cache hits.
    pub query_hits: u64,
    /// Query cache misses.
    pub query_misses: u64,
    /// Backend loads caused by cache misses.
    pub backend_fallbacks: u64,
    /// Cache reads attempted.
    pub gets: u64,
    /// Cache writes attempted.
    pub puts: u64,
    /// Cache deletes attempted.
    pub deletes: u64,
    /// Logical invalidations completed.
    pub invalidations: u64,
    /// Provider or cache-data errors.
    pub errors: u64,
    /// Total provider get latency in nanoseconds.
    pub get_latency_ns: u64,
    /// Total provider put latency in nanoseconds.
    pub put_latency_ns: u64,
    /// Total provider delete latency in nanoseconds.
    pub delete_latency_ns: u64,
}

/// Thread-safe cache metric counters.
#[derive(Default)]
pub struct CacheMetrics {
    query_hits: AtomicU64,
    query_misses: AtomicU64,
    backend_fallbacks: AtomicU64,
    gets: AtomicU64,
    puts: AtomicU64,
    deletes: AtomicU64,
    invalidations: AtomicU64,
    errors: AtomicU64,
    get_latency_ns: AtomicU64,
    put_latency_ns: AtomicU64,
    delete_latency_ns: AtomicU64,
}

impl CacheMetrics {
    /// Return an immutable snapshot.
    pub fn snapshot(&self) -> CacheMetricsSnapshot {
        CacheMetricsSnapshot {
            query_hits: self.query_hits.load(Ordering::Relaxed),
            query_misses: self.query_misses.load(Ordering::Relaxed),
            backend_fallbacks: self.backend_fallbacks.load(Ordering::Relaxed),
            gets: self.gets.load(Ordering::Relaxed),
            puts: self.puts.load(Ordering::Relaxed),
            deletes: self.deletes.load(Ordering::Relaxed),
            invalidations: self.invalidations.load(Ordering::Relaxed),
            errors: self.errors.load(Ordering::Relaxed),
            get_latency_ns: self.get_latency_ns.load(Ordering::Relaxed),
            put_latency_ns: self.put_latency_ns.load(Ordering::Relaxed),
            delete_latency_ns: self.delete_latency_ns.load(Ordering::Relaxed),
        }
    }

    /// Record one successful query cache hit.
    pub(crate) fn query_hit(&self) {
        self.query_hits.fetch_add(1, Ordering::Relaxed);
    }

    /// Record one query cache miss.
    pub(crate) fn query_miss(&self) {
        self.query_misses.fetch_add(1, Ordering::Relaxed);
    }

    /// Record one backend fallback after a query cache miss.
    pub(crate) fn backend_fallback(&self) {
        self.backend_fallbacks.fetch_add(1, Ordering::Relaxed);
    }

    /// Record one cache write attempt.
    pub(crate) fn put(&self, elapsed: Duration) {
        self.puts.fetch_add(1, Ordering::Relaxed);
        self.put_latency_ns.fetch_add(
            elapsed.as_nanos().min(u64::MAX as u128) as u64,
            Ordering::Relaxed,
        );
    }

    /// Record one cache read latency sample.
    pub(crate) fn get(&self, elapsed: Duration) {
        self.gets.fetch_add(1, Ordering::Relaxed);
        self.get_latency_ns.fetch_add(
            elapsed.as_nanos().min(u64::MAX as u128) as u64,
            Ordering::Relaxed,
        );
    }

    /// Record one cache delete attempt.
    pub(crate) fn delete(&self, elapsed: Duration) {
        self.deletes.fetch_add(1, Ordering::Relaxed);
        self.delete_latency_ns.fetch_add(
            elapsed.as_nanos().min(u64::MAX as u128) as u64,
            Ordering::Relaxed,
        );
    }

    /// Record one completed cache invalidation.
    pub(crate) fn invalidation(&self) {
        self.invalidations.fetch_add(1, Ordering::Relaxed);
    }

    /// Record one provider or cache-data error.
    pub(crate) fn error(&self) {
        self.errors.fetch_add(1, Ordering::Relaxed);
    }
}
