import type { ErrorKind } from '../../../client/monitoring/fault.js';
import type { StageName } from '../../../client/monitoring/targets.js';
import type {
  SpanSnapshot,
  TraceContext,
  TraceSnapshot,
  Trigger,
} from '../../../client/monitoring/trace.js';
import type { OpenVikingMonitoringEventName } from '../../../client/monitoring/types.js';
import type { OpenVikingMonitoringEventMap } from '../../../client/monitoring/events.js';
import type { MonitoringBinding } from '../binding.js';
import { now, spanId, traceId, visibility } from '../clock.js';
import { SpanHistory } from './span-history.js';
import { failSpan, type FailureName } from './span-fault.js';
import { snapshot } from './span-snapshot.js';
export type { FaultRecord } from './span-fault.js';

export class MonitoringSpan {
  started = now();
  private endedAt?: number;
  private occurredAt?: number;
  readonly epoch: number;
  readonly controller: MonitoringBinding['controller'];
  readonly node: SpanSnapshot;
  readonly trace: TraceContext;
  readonly root: MonitoringSpan;
  readonly history: SpanHistory;
  readonly parentSpan?: MonitoringSpan;
  private ended = false;
  private release?: () => void;
  constructor(
    readonly binding: MonitoringBinding,
    stage: StageName,
    trigger: Trigger = 'programmatic',
    parent?: MonitoringSpan,
  ) {
    this.controller = binding.controller;
    this.epoch = binding.controller.epoch;
    this.root = parent?.root ?? this;
    this.history = parent?.history ?? new SpanHistory();
    this.parentSpan = parent;
    const id = spanId();
    const source = binding.source();
    const attempt = binding.controller.nextAttempt(
      source.component.instanceId,
      stage,
    );
    this.trace = {
      traceId: parent?.trace.traceId ?? traceId(),
      spanId: id,
      parentSpanId: parent?.trace.spanId ?? null,
      rootSpanId: parent?.trace.rootSpanId ?? id,
      attempt,
      sequence: 0,
      trigger: parent?.trace.trigger ?? trigger,
    };
    this.node = {
      spanId: id,
      parentSpanId: this.trace.parentSpanId,
      stage,
      source,
      status: 'running',
      startOffsetMs: this.started - this.root.started,
      elapsedMs: null,
      attempt,
    };
    this.history.add(this);
    if (!parent) {
      this.release = binding.controller.retain(
        () => this.dispose(),
        this.node.source.component.instanceId,
      );
      binding.controller.observeRoot(this);
    }
  }
  timing(start: number, end?: number, occurredAt?: number) {
    this.started = start;
    this.endedAt = end;
    this.occurredAt = occurredAt;
    Object.assign(this.node, { startOffsetMs: start - this.root.started });
  }
  available(): boolean {
    return (
      this.controller === this.binding.controller &&
      !this.history.disposed &&
      this.binding.active() &&
      !!this.binding.controller.options &&
      this.binding.controller.epoch === this.epoch
    );
  }
  valid(): boolean {
    return (
      this.available() &&
      this.node.status !== 'cancelled' &&
      (!this.parentSpan || this.parentSpan.valid())
    );
  }
  child(stage: StageName, binding = this.binding) {
    return this.valid()
      ? new MonitoringSpan(binding, stage, this.trace.trigger, this)
      : undefined;
  }
  ready() {
    return {
      durationMs:
        this.node.elapsedMs ??
        Math.max(0, (this.endedAt ?? now()) - this.started),
      visibility: visibility(),
    };
  }
  finish(
    status: SpanSnapshot['status'] = 'ready',
    endReason?: SpanSnapshot['endReason'],
  ) {
    if (this.ended || this.node.status !== 'running') return false;
    this.ended = true;
    this.history.pending.delete(this);
    Object.assign(this.node, {
      status,
      elapsedMs: Math.max(0, (this.endedAt ?? now()) - this.started),
      endReason,
    });
    if (status === 'cancelled') this.history.cancelAfter(this, endReason);
    if (this === this.root) {
      if (status === 'cancelled') this.history.clearRaw();
      this.release?.();
    }
    return this.valid();
  }
  emit<N extends OpenVikingMonitoringEventName>(
    name: N,
    params: OpenVikingMonitoringEventMap[N]['params'],
    raw?: Parameters<
      import('../controller.js').MonitoringController['emit']
    >[4],
  ) {
    if (!this.valid()) return;
    return this.binding.controller.emit(
      name,
      this.node.source,
      { ...this.trace, sequence: ++this.history.sequence },
      params,
      raw,
      this.occurredAt,
    );
  }
  fail(
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
    try {
      return failSpan(this, name, error, extra, kind, signal, rawExtra);
    } catch {
      // Diagnostics must preserve the original business error, including custom Error getters.
      return undefined;
    }
  }
  gap(reason: TraceSnapshot['gaps'][number]) {
    this.history.gaps.add(reason);
  }
  link(
    parent: MonitoringSpan,
    relation: TraceSnapshot['links'][number]['relation'],
  ) {
    this.history.link(parent, relation);
  }
  snapshot(includeLinks = true) {
    return snapshot(this, includeLinks);
  }
  cancelSource(instanceId: string) {
    this.history.cancelSource(instanceId);
  }
  dispose() {
    this.history.dispose();
    this.release?.();
  }
}

export { startSpan } from './start.js';
