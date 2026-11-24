import { readData } from './data.js';
import { traceLink } from './trace/link.js';
import type { Trigger, TraceSnapshot } from '../../client/monitoring/trace.js';
import type { OvClient } from '../../client/types.js';
import type { OpenVikingMonitoringEventMap } from '../../client/monitoring/events.js';
import type { ErrorKind } from '../../client/monitoring/fault.js';
import { bindingOf, bindClient } from './client.js';
import { startSpan, type MonitoringSpan } from './trace/span.js';
import { workflows, type WorkflowTarget } from './workflows.js';
import { scopeBinding, type MonitoringBinding } from './binding.js';

type Name<T extends WorkflowTarget> = (typeof workflows)[T]['ready'];
export type WorkflowPayload<T extends WorkflowTarget> = Omit<
  OpenVikingMonitoringEventMap[Name<T>]['params'],
  'durationMs' | 'visibility'
>;

export class Workflow<T extends WorkflowTarget = WorkflowTarget> {
  identity?: unknown;
  private cleanup?: () => void;
  constructor(
    readonly target: T,
    readonly span: MonitoringSpan,
    signal?: AbortSignal,
  ) {
    const abort = () => {
      span.finish('cancelled', 'scope-change');
    };
    if (signal?.aborted) abort();
    else signal?.addEventListener('abort', abort, { once: true });
    this.cleanup = () => signal?.removeEventListener('abort', abort);
  }
  client(client: OvClient) {
    return bindClient(client, {
      ...this.span.binding,
      source: () => this.span.node.source,
      parent: this.span,
    });
  }
  ready(payload: WorkflowPayload<T>) {
    this.cleanup?.();
    if (this.span.finish()) {
      const name = workflows[this.target].ready;
      this.span.emit(name, {
        ...payload,
        ...this.span.ready(),
      });
    }
  }
  fail(error: unknown, kind: ErrorKind = 'unknown', signal?: AbortSignal) {
    this.cleanup?.();
    const cause = this.span.controller.cause(error);
    if (
      cause &&
      this.target === 'file_preview_content_prepare' &&
      this.span.trace.trigger !== 'retry'
    )
      this.span.history.addLink(
        traceLink(cause.snapshot, cause.trace.spanId, 'render-input'),
      );
    return this.span.fail(workflows[this.target].fail, error, {}, kind, signal);
  }
  cancel() {
    this.cleanup?.();
    this.span.finish('cancelled', 'superseded');
  }
}

export function beginWorkflow<T extends WorkflowTarget>(
  client: OvClient | null,
  target: T,
  signal?: AbortSignal,
  options?: {
    trigger?: Trigger;
    retryOf?: TraceSnapshot['links'][number];
    retryError?: unknown;
  },
) {
  return workflowForBinding(bindingOf(client), target, signal, options);
}

export function workflowForBinding<T extends WorkflowTarget>(
  binding: MonitoringBinding | undefined,
  target: T,
  signal?: AbortSignal,
  options?: {
    trigger?: Trigger;
    retryOf?: TraceSnapshot['links'][number];
    retryError?: unknown;
  },
) {
  if (!binding?.active() || !binding.controller.options || signal?.aborted)
    return;
  const definition = workflows[target];
  const scoped = scopeBinding(binding, definition.region);
  const cause = binding.controller.cause(options?.retryError);
  const matching =
    cause?.snapshot.spans.find((node) => node.spanId === cause.trace.spanId)
      ?.stage === target;
  const retryOf =
    options?.retryOf ??
    (cause && matching
      ? traceLink(cause.snapshot, cause.trace.spanId, 'retry-of')
      : undefined);
  const span = startSpan(scoped, target, options?.trigger, retryOf);
  return span && new Workflow(target, span, signal);
}

export function workflowsForData<T extends WorkflowTarget>(
  client: OvClient | null,
  values: readonly unknown[],
  target: T,
) {
  return readData(bindingOf(client), values)
    .filter((span) => span.node.stage === target)
    .map((span) => new Workflow(target, span));
}
