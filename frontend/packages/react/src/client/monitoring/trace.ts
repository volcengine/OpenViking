import type { Source } from './source.js';
import type { RequestSummary } from './transport.js';
import type { StageName } from './targets.js';

export type TraceId = string & { readonly __traceId: unique symbol };

export type SpanId = string & { readonly __spanId: unique symbol };

export type Trigger =
  | 'initial'
  | 'expand'
  | 'refresh'
  | 'selection'
  | 'submit'
  | 'retry'
  | 'background-poll'
  | 'render'
  | 'source-change'
  | 'view-change'
  | 'dialog-open'
  | 'session-switch'
  | 'programmatic';

export type TraceContext = {
  readonly traceId: TraceId;
  readonly spanId: SpanId;
  readonly parentSpanId: SpanId | null;
  readonly rootSpanId: SpanId;
  readonly attempt: number;
  readonly trigger: Trigger;
  readonly sequence: number;
};

export type SpanSnapshot = {
  readonly spanId: SpanId;
  readonly parentSpanId: SpanId | null;
  readonly stage: StageName;
  readonly source: Source;
  readonly status: 'running' | 'ready' | 'failed' | 'cancelled' | 'skipped';
  readonly startOffsetMs: number | null;
  readonly elapsedMs: number | null;
  readonly attempt: number;
  readonly request?: RequestSummary;
  readonly endReason?:
    | 'user-cancel'
    | 'unmount'
    | 'scope-change'
    | 'superseded'
    | 'consumer-complete'
    | 'interrupted'
    | 'unsupported'
    | 'no-renderable-content'
    | 'upstream-failure';
};

export type TraceSnapshot = {
  readonly traceId: TraceId;
  readonly rootSpanId: SpanId;
  readonly trigger: Trigger;
  readonly spans: readonly SpanSnapshot[];
  readonly causalPath: readonly SpanId[];
  readonly coverage: 'complete' | 'partial';
  readonly gaps: readonly (
    | 'started-before-enabled'
    | 'opaque-host-service'
    | 'disabled-scope'
    | 'shared-query-input'
    | 'capacity'
  )[];
  readonly omittedSpanCount: number;
  readonly omittedByStage: readonly {
    readonly stage: StageName;
    readonly count: number;
  }[];
  readonly links: readonly {
    readonly traceId: TraceId;
    readonly spanId: SpanId;
    readonly relation: 'retry-of' | 'background-task-of' | 'render-input';
    readonly causalSummary: readonly SpanSnapshot[];
  }[];
};
