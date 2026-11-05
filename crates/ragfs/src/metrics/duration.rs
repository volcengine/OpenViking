//! SDK-owned duration aggregation shared by filesystem and path-lock instrumentation.

use std::sync::{Arc, Weak};
use std::time::Duration;

use opentelemetry::metrics::{Histogram, MeterProvider};
use opentelemetry::KeyValue;
use opentelemetry_sdk::error::OTelSdkResult;
use opentelemetry_sdk::metrics::data::{
    AggregatedMetrics, HistogramDataPoint, MetricData, ResourceMetrics,
};
use opentelemetry_sdk::metrics::reader::MetricReader;
use opentelemetry_sdk::metrics::{
    InstrumentKind, ManualReader, Pipeline, SdkMeterProvider, Temporality,
};

/// Retain the same reader in the provider and the native snapshot owner.
#[derive(Clone, Debug)]
struct SharedReader(Arc<ManualReader>);

impl MetricReader for SharedReader {
    fn register_pipeline(&self, pipeline: Weak<Pipeline>) {
        self.0.register_pipeline(pipeline);
    }

    fn collect(&self, metrics: &mut ResourceMetrics) -> OTelSdkResult {
        self.0.collect(metrics)
    }

    fn force_flush(&self) -> OTelSdkResult {
        self.0.force_flush()
    }

    fn shutdown_with_timeout(&self, timeout: Duration) -> OTelSdkResult {
        self.0.shutdown_with_timeout(timeout)
    }

    fn temporality(&self, kind: InstrumentKind) -> Temporality {
        self.0.temporality(kind)
    }
}

/// Own one SDK provider for the lifetime of the measured subsystem.
#[derive(Clone, Debug)]
pub(crate) struct DurationMetrics {
    // Dropping the final provider handle shuts down its reader.
    _provider: SdkMeterProvider,
    reader: SharedReader,
    histogram: Histogram<u64>,
}

impl Default for DurationMetrics {
    fn default() -> Self {
        let reader = SharedReader(Arc::new(ManualReader::default()));
        let provider = SdkMeterProvider::builder()
            .with_reader(reader.clone())
            .build();
        // A bounded 1/2/5 scale covers short I/O through multi-day background work.
        // This is SDK collection policy, not a timeout or a per-operation setting.
        let boundaries = (5..=15)
            .flat_map(|power| [1, 2, 5].map(|factor| (factor * 10_u64.pow(power)) as f64))
            .collect();
        let histogram = provider
            .meter("ragfs")
            .u64_histogram("duration")
            .with_unit("ns")
            .with_boundaries(boundaries)
            .build();
        Self {
            _provider: provider,
            reader,
            histogram,
        }
    }
}

impl DurationMetrics {
    pub(crate) fn record(&self, duration: Duration, attributes: &[KeyValue]) {
        self.histogram.record(
            u64::try_from(duration.as_nanos()).unwrap_or(u64::MAX),
            attributes,
        );
    }

    pub(crate) fn snapshot(&self) -> Vec<HistogramDataPoint<u64>> {
        let mut metrics = ResourceMetrics::default();
        // The provider and reader have the same owner; neither is shut down independently.
        self.reader
            .collect(&mut metrics)
            .expect("live duration metrics provider");
        metrics
            .scope_metrics()
            .flat_map(|scope| scope.metrics())
            .flat_map(|metric| match metric.data() {
                AggregatedMetrics::U64(MetricData::Histogram(histogram)) => {
                    histogram.data_points().cloned().collect::<Vec<_>>()
                }
                _ => unreachable!("duration provider contains only a u64 histogram"),
            })
            .collect()
    }
}
