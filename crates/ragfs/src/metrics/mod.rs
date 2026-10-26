//! Native metric records converted from existing filesystem collectors.

use std::collections::BTreeMap;

use crate::core::stats::DURATION_BUCKET_BOUNDS_NS;
use crate::core::{Error, FilesystemStats, FsOperation, Result};
use crate::lock::metrics::LockMetrics;

/// One metric series, identified by its name and bounded labels.
#[derive(Debug, Clone, PartialEq)]
pub struct RagfsMetric {
    /// Metric family name without an application prefix.
    pub name: String,
    /// Stable labels; never include paths, mounts, accounts, or owners.
    pub labels: BTreeMap<String, String>,
    /// Raw measurement and its conversion scale.
    pub value: RagfsMetricValue,
}

/// Raw metric values; durations remain integer nanoseconds until export.
#[derive(Debug, Clone, PartialEq)]
pub enum RagfsMetricValue {
    /// Monotonic total with an export-unit multiplier.
    Counter {
        /// Raw count, bytes, or nanoseconds.
        value: u64,
        /// Multiply the raw value by this factor for export.
        scale: f64,
    },
    /// Current value, already in export units.
    Gauge(f64),
    /// Noncumulative duration buckets, including a final overflow bucket.
    Histogram {
        /// Inclusive finite upper bounds in raw units.
        bucket_bounds: Vec<u64>,
        /// Per-bucket counts; one more element than finite bounds.
        bucket_counts: Vec<u64>,
        /// Total number of samples across all outcomes.
        count: u64,
        /// Sum of samples in raw units.
        sum: u64,
        /// Multiply bounds and sum by this factor for export.
        scale: f64,
    },
}

impl RagfsMetric {
    /// Return a counter from its family name, label pairs, raw value, and scale.
    pub(crate) fn counter(name: &str, labels: &[(&str, &str)], value: u64, scale: f64) -> Self {
        Self {
            name: name.to_string(),
            labels: labels.iter().map(|(key, value)| (key.to_string(), value.to_string())).collect(),
            value: RagfsMetricValue::Counter { value, scale },
        }
    }
}

/// Convert one plugin's operation statistics into result counters and histograms.
pub(crate) fn operation_metrics(plugin: &str, stats: &FilesystemStats) -> Vec<RagfsMetric> {
    let mut metrics = Vec::new();
    for operation in FsOperation::all() {
        let stats = stats.get(*operation);
        for (status, count) in [("success", stats.success_count), ("error", stats.error_count)] {
            metrics.push(RagfsMetric::counter(
                "ragfs_operation_results_total",
                &[("plugin", plugin), ("operation", operation.as_str()), ("status", status)],
                count,
                1.0,
            ));
        }
        metrics.push(RagfsMetric {
            name: "ragfs_operation_duration_seconds".into(),
            labels: [
                ("plugin".into(), plugin.into()),
                ("operation".into(), operation.as_str().into()),
            ].into(),
            value: RagfsMetricValue::Histogram {
                bucket_bounds: DURATION_BUCKET_BOUNDS_NS.to_vec(),
                bucket_counts: stats.duration_bucket_counts.to_vec(),
                count: stats.count,
                sum: stats.total_time_ns,
                scale: 1e-9,
            },
        });
    }
    metrics
}

/// Convert a cache snapshot into counters without changing its collector state.
#[cfg(feature = "cache")]
pub(crate) fn cache_metrics(stats: crate::cache::CacheMetricsSnapshot) -> Vec<RagfsMetric> {
    let mut metrics = Vec::new();
    for (kind, result, count) in [
        ("file", "hit", stats.file_hits),
        ("file", "miss", stats.file_misses),
        ("directory", "hit", stats.read_dir_hits),
        ("directory", "miss", stats.read_dir_misses),
    ] {
        metrics.push(RagfsMetric::counter(
            "ragfs_cache_requests_total", &[("kind", kind), ("result", result)], count, 1.0,
        ));
    }
    for (name, count) in [
        ("ragfs_cache_backend_fallbacks_total", stats.backend_fallbacks),
        ("ragfs_cache_invalidations_total", stats.invalidations),
        ("ragfs_cache_errors_total", stats.errors),
        ("ragfs_cache_policy_bypasses_total", stats.policy_bypasses),
    ] {
        metrics.push(RagfsMetric::counter(name, &[], count, 1.0));
    }
    for (name, label, value, count, scale) in [
        ("ragfs_cache_operations_total", "operation", "put", stats.puts, 1.0),
        ("ragfs_cache_operations_total", "operation", "delete", stats.deletes, 1.0),
        ("ragfs_cache_bytes_total", "source", "backend", stats.backend_bytes, 1.0),
        ("ragfs_cache_bytes_total", "source", "cache", stats.cache_bytes, 1.0),
        ("ragfs_cache_operation_duration_seconds_total", "operation", "get", stats.get_latency_ns, 1e-9),
        ("ragfs_cache_operation_duration_seconds_total", "operation", "put", stats.put_latency_ns, 1e-9),
        ("ragfs_cache_operation_duration_seconds_total", "operation", "delete", stats.delete_latency_ns, 1e-9),
        ("ragfs_cache_inflight_events_total", "event", "leader", stats.inflight_leaders, 1.0),
        ("ragfs_cache_inflight_events_total", "event", "follower", stats.inflight_followers, 1.0),
        ("ragfs_cache_inflight_events_total", "event", "backend_saved", stats.inflight_backend_saved, 1.0),
    ] {
        metrics.push(RagfsMetric::counter(name, &[(label, value)], count, scale));
    }
    metrics
}

/// Convert the single lock manager's snapshot into unlabeled metric records.
pub(crate) fn lock_metrics(stats: LockMetrics) -> Vec<RagfsMetric> {
    let mut metrics = Vec::new();
    for (name, value) in [
        ("lock_active", stats.active_lock_count),
        ("lock_waiting", stats.waiting_lock_count),
        ("lock_stale", stats.stale_tokens_removed),
    ] {
        metrics.push(RagfsMetric {
            name: name.into(),
            labels: BTreeMap::new(),
            value: RagfsMetricValue::Gauge(value as f64),
        });
    }
    for (name, value) in [
        ("lock_conflicts_total", stats.conflict_count),
        ("lock_stale_leases_released_total", stats.stale_leases_released),
        ("lock_descendant_scans_total", stats.descendant_scan_count),
    ] {
        metrics.push(RagfsMetric::counter(name, &[], value as u64, 1.0));
    }
    metrics.push(RagfsMetric::counter(
        "lock_descendant_scan_duration_seconds_total", &[], stats.descendant_scan_duration_ns, 1e-9,
    ));
    metrics
}

/// Merge supplied series by name and labels; return sorted records or an incompatibility error.
pub(crate) fn merge_metrics(mut metrics: Vec<RagfsMetric>) -> Result<Vec<RagfsMetric>> {
    metrics.sort_by(|a, b| (&a.name, &a.labels).cmp(&(&b.name, &b.labels)));
    let mut merged: Vec<RagfsMetric> = Vec::new();
    for metric in metrics {
        let Some(previous) = merged.last_mut()
            .filter(|last| last.name == metric.name && last.labels == metric.labels)
        else {
            merged.push(metric);
            continue;
        };
        match (&mut previous.value, metric.value) {
            (
                RagfsMetricValue::Counter { value, scale },
                RagfsMetricValue::Counter { value: other, scale: other_scale },
            ) if *scale == other_scale => *value = value.saturating_add(other),
            (RagfsMetricValue::Gauge(value), RagfsMetricValue::Gauge(other)) => *value += other,
            (
                RagfsMetricValue::Histogram { bucket_bounds, bucket_counts, count, sum, scale },
                RagfsMetricValue::Histogram {
                    bucket_bounds: other_bounds, bucket_counts: other_counts,
                    count: other_count, sum: other_sum, scale: other_scale,
                },
            ) if *bucket_bounds == other_bounds
                && bucket_counts.len() == other_counts.len()
                && bucket_counts.len() == bucket_bounds.len() + 1
                && *scale == other_scale =>
            {
                for (value, other) in bucket_counts.iter_mut().zip(other_counts) {
                    *value = value.saturating_add(other);
                }
                *count = count.saturating_add(other_count);
                *sum = sum.saturating_add(other_sum);
            }
            _ => return Err(Error::internal(format!("incompatible metric series '{}'", metric.name))),
        }
    }
    Ok(merged)
}
