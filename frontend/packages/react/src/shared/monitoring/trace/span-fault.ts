import { faultClassification } from './classification.js';
import { traceLink } from './link.js';
import type { Source } from '../../../client/monitoring/source.js';
import type { ErrorKind } from '../../../client/monitoring/fault.js';
import type {
  TraceContext,
  TraceSnapshot,
} from '../../../client/monitoring/trace.js';
import type { OpenVikingMonitoringEventName } from '../../../client/monitoring/types.js';
import type { OpenVikingMonitoringEventMap } from '../../../client/monitoring/events.js';
import type { MonitoringSpan } from './span.js';
import { classify, cancelled } from '../fault.js';
import { eventId } from '../clock.js';
export type FailureName = {
  [
    N in OpenVikingMonitoringEventName
  ]: OpenVikingMonitoringEventMap[N]['params'] extends { incidentId: string }
    ? N
    : never;
}[OpenVikingMonitoringEventName];
export type FaultRecord = {
  incidentId: string;
  eventId: string;
  snapshot: TraceSnapshot;
  trace: TraceContext;
  source: Source;
  rendered: readonly string[];
  fault: ReturnType<typeof classify>;
};
export function failSpan(
  span: MonitoringSpan,
  name: FailureName,
  error: unknown,
  extra: object = {},
  kind: ErrorKind = 'unknown',
  signal?: AbortSignal,
  rawExtra?: {
    componentStack?: string;
    requestBody?: unknown;
    responseBody?: unknown;
  },
) {
  if (cancelled(error, signal)) {
    span.finish('cancelled', 'user-cancel');
    return;
  }
  if (!span.finish('failed')) return;
  const known = span.binding.controller.cause(error);
  const path = span.node.source.componentPath;
  const sameBranch =
    known &&
    (path.every(
      (part, i) =>
        part.instanceId === known.source.componentPath[i]?.instanceId,
    ) ||
      known.source.componentPath.every(
        (part, i) => part.instanceId === path[i]?.instanceId,
      ));
  const relatedRender =
    kind === 'render' &&
    known &&
    path.every(
      (part, i) =>
        part.instanceId === known.source.componentPath[i]?.instanceId,
    ) &&
    !known.rendered.includes(span.node.source.component.instanceId);
  const repeatedRender =
    kind === 'render' &&
    known?.rendered.includes(span.node.source.component.instanceId);
  const cause =
    known &&
    !repeatedRender &&
    ((known.trace.traceId === span.trace.traceId && sameBranch) ||
      relatedRender ||
      span.history.links?.some(
        (link) =>
          link.relation === 'render-input' &&
          link.traceId === known.trace.traceId &&
          link.spanId === known.trace.spanId,
      ))
      ? known
      : undefined;
  if (cause)
    Object.assign(span.node, {
      source: {
        ...span.node.source,
        affectedComponents: cause.source.componentPath.slice(path.length),
      },
    });
  const fault = faultClassification(name, error, kind, cause);
  if (cause && cause.trace.traceId !== span.trace.traceId)
    span.history.addLink(
      traceLink(cause.snapshot, cause.trace.spanId, 'render-input'),
    );
  if (kind === 'render')
    Object.assign(span.node, { elapsedMs: null, startOffsetMs: null });
  const incidentId = cause?.incidentId ?? eventId();
  const params = {
    ...fault,
    observedAt:
      fault.errorKind === 'host-service'
        ? 'host-service'
        : fault.errorKind === 'http' || fault.errorKind === 'business'
          ? 'upstream-response'
          : name.startsWith('file_preview_image_') ||
              name.startsWith('file_preview_video_')
            ? 'browser'
            : 'library-boundary',
    incidentId,
    faultRole: cause ? 'propagation' : 'origin',
    causeEventId: cause?.eventId ?? null,
    elapsedMs: kind === 'render' ? null : span.ready().durationMs,
    traceSnapshot: span.snapshot(),
    ...extra,
  };
  const options = span.binding.controller.options;
  let raw: Parameters<MonitoringSpan['emit']>[2];
  if (options && options.diagnostics) {
    raw = { error, ...rawExtra };
    try {
      if (error instanceof Error)
        Object.assign(raw, { message: error.message, stack: error.stack });
    } catch {
      /* A custom Error getter cannot suppress the safe failure event. */
    }
  }
  const id = span.emit(
    name,
    params as OpenVikingMonitoringEventMap[FailureName]['params'],
    raw,
  );
  const record = {
    incidentId,
    eventId: id ?? eventId(),
    snapshot: params.traceSnapshot,
    trace: { ...span.trace },
    source: cause?.source ?? span.node.source,
    rendered:
      kind === 'render'
        ? [
            ...new Set([
              ...(cause?.rendered ?? []),
              span.node.source.component.instanceId,
            ]),
          ].slice(-32)
        : (cause?.rendered ?? []),
    fault,
  };
  span.binding.controller.remember(error, record);
  return record;
}
