import { traceLink } from './link.js';
import type {
  SpanSnapshot,
  TraceSnapshot,
} from '../../../client/monitoring/trace.js';
import type { MonitoringSpan } from './span.js';
export function snapshot(
  span: MonitoringSpan,
  includeLinks = true,
): TraceSnapshot {
  const root = span.history;
  const path: SpanSnapshot[] = [];
  let current: MonitoringSpan | undefined = span;
  while (current) {
    path.unshift(current.node);
    current = current.parentSpan;
  }
  const priority = path.length <= 64 ? path : [path[0]!, ...path.slice(-63)];
  const kept = [...new Set([...priority, ...root.nodes])].slice(0, 64);
  const omitted = [...root.counts]
    .map(([stage, count]) => ({
      stage,
      count: count - kept.filter((n) => n.stage === stage).length,
    }))
    .filter((row) => row.count > 0);
  const related = (span.binding.related ?? []).filter(
    (s) =>
      s.root !== span.root && s.controller === span.controller && s.available(),
  );
  const partial =
    root.total > kept.length ||
    related.length + (root.links?.length ?? 0) > 8 ||
    root.gaps.size > 0 ||
    related.some((input) => input.history.gaps.size > 0);
  return {
    traceId: span.trace.traceId,
    rootSpanId: span.trace.rootSpanId,
    trigger: span.trace.trigger,
    spans: kept.map((n) => ({
      ...n,
      source: n.source,
      request: n.request && { ...n.request },
    })),
    causalPath: path.filter((n) => kept.includes(n)).map((n) => n.spanId),
    coverage: partial ? 'partial' : 'complete',
    gaps: [
      ...new Set([
        ...root.gaps,
        ...related.flatMap((input) => [...input.history.gaps]),
        ...(root.total > kept.length ||
        related.length + (root.links?.length ?? 0) > 8
          ? ['capacity' as const]
          : []),
      ]),
    ],
    omittedSpanCount: root.total - kept.length,
    omittedByStage: omitted,
    links: includeLinks
      ? [
          ...(root.links ?? []),
          ...related.map((s) =>
            traceLink(s.snapshot(false), s.trace.spanId, 'render-input'),
          ),
        ].slice(-8)
      : [],
  };
}
