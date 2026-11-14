import { traceLink } from '../monitoring/trace/link.js';
import type { TraceSnapshot } from '../../client/monitoring/trace.js';
import { scopeBinding } from '../monitoring/binding.js';
import type { MonitoringBinding } from '../monitoring/binding.js';
import { startSpan, type MonitoringSpan } from '../monitoring/trace/span.js';
import { now, eventId } from '../monitoring/clock.js';
import { scopeService } from '../monitoring/service.js';
import type { TaskService } from './types.js';

type Outage = {
  id: string;
  started: number;
  failures: number;
  categories: Set<'request' | 'protocol'>;
};
export type PollState = {
  controller: MonitoringBinding['controller'];
  epoch: number;
  taskRef: string;
  attempt: number;
  outage?: Outage;
};
export type PollEntry = {
  monitoring?: PollState;
  monitoringRelease?: () => void;
  origin?: TraceSnapshot['links'][number];
  observedAtStart?: boolean;
};
export function retainTaskMonitoring(
  entry: PollEntry,
  binding?: MonitoringBinding,
  origin?: MonitoringSpan,
) {
  if (!binding?.active() || !binding.controller.options) return;
  if (entry.monitoringRelease) return;
  const controller = binding.controller;
  if (origin?.valid() && origin.controller === controller)
    entry.origin = traceLink(
      origin.snapshot(),
      origin.trace.spanId,
      'background-task-of',
    );
  entry.monitoringRelease = controller.retain(() => {
    entry.monitoringRelease?.();
    entry.monitoringRelease = undefined;
    entry.monitoring = undefined;
    entry.origin = undefined;
    entry.observedAtStart = false;
  }, binding.source('task-tracking').component.instanceId);
}
export function observePoll(
  entry: PollEntry,
  binding: MonitoringBinding | undefined,
  service: TaskService,
  signal: AbortSignal,
) {
  if (!binding?.active() || !binding.controller.options || signal.aborted)
    return;
  retainTaskMonitoring(entry, binding);
  let state = entry.monitoring;
  if (
    !state ||
    state.controller !== binding.controller ||
    state.epoch !== binding.controller.epoch
  ) {
    state = entry.monitoring = {
      controller: binding.controller,
      epoch: binding.controller.epoch,
      taskRef: eventId(),
      attempt: 0,
    };
  }
  state.attempt++;
  const span = startSpan(
    scopeBinding(binding, 'task-tracking', null, []),
    'task-query',
    'background-poll',
  );
  if (!span) return;
  if (entry.origin) span.history.addLink(entry.origin);
  else if (!entry.observedAtStart) span.gap('started-before-enabled');
  const queryAttempt = state.attempt;
  const abort = () => span.finish('cancelled', 'scope-change');
  signal.addEventListener('abort', abort, { once: true });
  const cleanup = () => signal.removeEventListener('abort', abort);
  return {
    span,
    service: scopeService(service, span),
    success() {
      cleanup();
      span.child('task-query-protocol')?.finish();
      if (!span.finish()) return;
      const outage = state.outage;
      if (!outage) return;
      span.emit('task_poll_recovered', {
        ...span.ready(),
        taskRef: state.taskRef,
        outageId: outage.id,
        queryAttempt,
        consecutiveFailures: 0,
        outageDurationMs: Math.max(0, now() - outage.started),
        failureCount: outage.failures,
        failedCategories: [...outage.categories],
      });
      state.outage = undefined;
    },
    fail(error: unknown, protocol: boolean) {
      cleanup();
      if (!span.valid() || signal.aborted) return;
      const category = protocol ? 'protocol' : 'request';
      const outage = (state.outage ??= {
        id: eventId(),
        started: now(),
        failures: 0,
        categories: new Set(),
      });
      outage.failures++;
      if (protocol) span.child('task-query-protocol')?.finish('failed');
      if (outage.categories.has(category)) {
        span.finish('failed');
        return;
      }
      outage.categories.add(category);
      span.fail(
        protocol ? 'task_poll_protocol_fail' : 'task_poll_request_fail',
        error,
        {
          taskRef: state.taskRef,
          outageId: outage.id,
          queryAttempt,
          consecutiveFailures: outage.failures,
        },
        protocol ? 'protocol' : 'host-service',
        signal,
      );
    },
    cancel() {
      cleanup();
      span.finish('cancelled', 'scope-change');
    },
  };
}
