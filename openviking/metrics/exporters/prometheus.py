# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Expose SDK metric data using the official Prometheus client serializer."""

from itertools import accumulate, chain

from opentelemetry.sdk.metrics.export import Gauge, Histogram, Sum
from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, HistogramMetricFamily

from openviking.metrics.core.base import MetricExporter


class PrometheusExporter(MetricExporter):
    """Adapt SDK data to Prometheus metric families, preserving OpenViking names and tags."""

    def __init__(self, *, registry, collector_manager=None, refresh_deadline_seconds=1.0):
        self._registry = registry
        self._collector_manager = collector_manager
        self._refresh_deadline_seconds = float(refresh_deadline_seconds)
        self._prometheus = CollectorRegistry()
        self._prometheus.register(self)

    async def export(self) -> str:
        if self._collector_manager is not None:
            try:
                await self._collector_manager.refresh_all(
                    self._registry, deadline_seconds=self._refresh_deadline_seconds
                )
            except Exception:
                pass
        return self.render()

    def render(self) -> str:
        return generate_latest(self._prometheus).decode("utf-8")

    def collect(self):
        """Implement the Prometheus custom-collector boundary for SDK and native snapshots."""
        data = self._registry.collect()
        for resource in data.resource_metrics:
            for scope in resource.scope_metrics:
                for metric in scope.metrics:
                    points = metric.data.data_points
                    if not points:
                        continue
                    keys = sorted(points[0].attributes)
                    if isinstance(metric.data, Sum):
                        family = CounterMetricFamily(metric.name, "OpenViking metric.", labels=keys)
                    elif isinstance(metric.data, Gauge):
                        family = GaugeMetricFamily(metric.name, "OpenViking metric.", labels=keys)
                    elif isinstance(metric.data, Histogram):
                        family = HistogramMetricFamily(
                            metric.name, "OpenViking metric.", labels=keys
                        )
                    else:
                        raise TypeError(
                            f"unsupported SDK metric data: {type(metric.data).__name__}"
                        )
                    for point in points:
                        labels = [str(point.attributes[key]) for key in keys]
                        if isinstance(metric.data, Histogram):
                            family.add_metric(
                                labels,
                                buckets=list(
                                    zip(
                                        chain(
                                            (str(bound) for bound in point.explicit_bounds),
                                            ("+Inf",),
                                        ),
                                        accumulate(point.bucket_counts),
                                        strict=True,
                                    )
                                ),
                                sum_value=point.sum,
                            )
                        else:
                            family.add_metric(labels, point.value)
                    yield family
