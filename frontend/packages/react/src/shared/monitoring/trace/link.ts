import type {
  SpanId,
  TraceSnapshot,
} from '../../../client/monitoring/trace.js';

/** Only safe stage summaries cross an asynchronous causal boundary. */
export function traceLink(
  snapshot: TraceSnapshot,
  spanId: SpanId,
  relation: TraceSnapshot['links'][number]['relation'],
): TraceSnapshot['links'][number] {
  const path = snapshot.spans.filter((node) =>
    snapshot.causalPath.includes(node.spanId),
  );
  const priority = path.length > 8 ? [path[0]!, ...path.slice(-7)] : path;
  const summary = [
    ...new Set([...priority, ...[...snapshot.spans].reverse()]),
  ].slice(0, 8);
  return {
    traceId: snapshot.traceId,
    spanId,
    relation,
    causalSummary: snapshot.spans.filter((node) => summary.includes(node)),
  };
}
