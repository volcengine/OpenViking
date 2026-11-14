import { traceLink } from './trace/link.js';
import { scopeBinding } from './binding.js';
import { useInsertionEffect, useLayoutEffect, useMemo, useRef } from 'react';
import { useMonitoringBinding, useRenderedMonitoring } from './context.js';
import { useMonitoringFlow } from './flow-context.js';
import type { MonitoringFrame } from './frame.js';
import { startSpan, type MonitoringSpan } from './trace/span.js';
import { now } from './clock.js';

const targets = {
  file_preview_viewer_load: 'viewer-loader',
  file_preview_editor_load: 'editor-loader',
  viking_bot_composer_load: 'composer',
  viking_bot_message_list_load: 'message-list',
} as const;
export type CodeTarget = keyof typeof targets;

/** Real import attempts are observed only by a committed loader or boundary. */
export function useCodeLoad<T>(load: () => Promise<T>, target?: CodeTarget) {
  const owner = useMonitoringBinding() as MonitoringFrame | undefined;
  const enabled = useRenderedMonitoring();
  const flow = useMonitoringFlow();
  const current = useRef({ enabled, flow });
  useInsertionEffect(() => {
    current.current = { enabled, flow };
  }, [enabled, flow]);
  const observer = useMemo(() => {
    let active = false;
    let pending:
      | Set<{
          flush: () => void;
          cancel: () => void;
          caught: (error: unknown) => void;
        }>
      | undefined;
    return {
      start() {
        active = true;
        pending?.forEach((record) => record.flush());
        return () => {
          active = false;
          pending?.forEach((record) => record.cancel());
        };
      },
      caught(error: unknown) {
        pending?.forEach((record) => record.caught(error));
      },
      async load(this: void) {
        if (!current.current.enabled || !target || !owner) return load();
        const controller = owner.controller;
        const epoch = controller.epoch;
        const started = now();
        const parent = current.current.flow;
        let span: MonitoringSpan | undefined;
        let delivered = false;
        let ended: number | undefined;
        let occurredAt: number | undefined;
        let result: 'ready' | 'failed' | undefined;
        let failure: unknown;
        const record = {
          caught(error: unknown) {
            if (result === 'failed' && failure === error) record.flush();
          },
          cancel() {
            span?.finish('cancelled', 'unmount');
            span = undefined;
          },
          flush() {
            if (delivered || !owner.live) return;
            if (controller !== owner.controller || controller.epoch !== epoch) {
              delivered = true;
              pending?.delete(record);
              return;
            }
            span ??= startSpan(
              scopeBinding(
                owner,
                targets[target],
                parent?.spans.length === 1 ? parent.spans[0] : owner.parent,
                parent?.spans ?? owner.related,
              ),
              target,
              'programmatic',
              owner.retryLink(target),
            );
            if (!span) return;
            span.timing(started, ended, occurredAt);
            if (!result) return;
            delivered = true;
            pending?.delete(record);
            if (result === 'failed') {
              const fault = span.fail(
                `${target}_fail`,
                failure,
                {},
                'resource-load',
              );
              if (fault)
                owner.recordRetry(
                  target,
                  traceLink(fault.snapshot, fault.trace.spanId, 'retry-of'),
                );
            } else if (span.finish()) {
              owner.recordRetry(target);
              span.emit(`${target}_ready`, {
                ...span.ready(),
                cacheState: 'unknown',
              });
            }
          },
        };
        (pending ??= new Set()).add(record);
        if (active) record.flush();
        try {
          const module = await load();
          ended = now();
          occurredAt = Date.now();
          result = 'ready';
          if (active) record.flush();
          return module;
        } catch (error) {
          ended = now();
          occurredAt = Date.now();
          result = 'failed';
          failure = error;
          if (active) record.flush();
          throw error;
        }
      },
    };
  }, [load, target, owner]);
  // Imports may settle before Suspense commits. Discarded loaders never deliver.
  useLayoutEffect(() => observer.start(), [observer]);
  return observer;
}
