import { traceLink } from '../../shared/monitoring/trace/link.js';
import { scopeBinding } from '../../shared/monitoring/binding.js';
import { useLayoutEffect, useRef } from 'react';
import {
  useMonitoringBinding,
  useRenderedMonitoring,
} from '../../shared/monitoring/context.js';
import { useMonitoringFlow } from '../../shared/monitoring/flow-context.js';
import {
  startSpan,
  type MonitoringSpan,
} from '../../shared/monitoring/trace/span.js';
import { now } from '../../shared/monitoring/clock.js';

const targets = {
  image: {
    stage: 'file_preview_image_load',
    ready: 'file_preview_image_load_ready',
    fail: 'file_preview_image_load_fail',
    region: 'image',
  },
  video: {
    stage: 'file_preview_video_metadata_load',
    ready: 'file_preview_video_metadata_load_ready',
    fail: 'file_preview_video_metadata_load_fail',
    region: 'video',
  },
  dialog: {
    stage: 'file_preview_image_dialog',
    ready: 'file_preview_image_dialog_ready',
    fail: 'file_preview_image_dialog_fail',
    region: 'image-dialog',
  },
} as const;
export function useMediaMonitoring(
  target: keyof typeof targets,
  identity: unknown,
  active: boolean,
  ready: boolean,
  error?: unknown,
  retry?: { sourceIdentity: object; generation: number; bound?: boolean },
) {
  const binding = useMonitoringBinding();
  const enabled = useRenderedMonitoring();
  const flow = useMonitoringFlow();
  const attempt = useRef<
    | {
        identity: unknown;
        active: boolean;
        bound: boolean;
        ended?: number;
        occurredAt?: number;
        span?: MonitoringSpan;
        started?: number;
        epoch?: number;
        sourceIdentity?: object;
        generation?: number;
        isRetry?: boolean;
        retryOf?: import('../../client/monitoring/trace.js').TraceSnapshot['links'][number];
      }
    | undefined
  >(undefined);
  useLayoutEffect(() => {
    const previous = attempt.current;
    const bound = target === 'dialog' ? active : (retry?.bound ?? active);
    if (
      !previous ||
      previous.identity !== identity ||
      (!previous.bound && bound)
    ) {
      previous?.span?.finish('cancelled', 'superseded');
      attempt.current = {
        identity,
        active,
        bound,
        started: enabled && bound ? now() : undefined,
        epoch: binding?.controller.epoch,
        sourceIdentity: retry?.sourceIdentity,
        generation: retry?.generation,
        isRetry:
          !!retry &&
          previous?.sourceIdentity === retry.sourceIdentity &&
          previous.generation !== retry.generation,
        retryOf:
          retry &&
          previous?.sourceIdentity === retry.sourceIdentity &&
          previous.generation !== retry.generation &&
          previous.span?.available()
            ? traceLink(
                previous.span.snapshot(),
                previous.span.trace.spanId,
                'retry-of',
              )
            : undefined,
      };
    } else if (!active && target === 'dialog') {
      previous.span?.finish('cancelled', 'unmount');
      previous.active = false;
      previous.bound = false;
    }
    const current = attempt.current!;
    if (current.epoch !== binding?.controller.epoch)
      current.started = undefined;
    if (
      enabled &&
      current.started !== undefined &&
      current.ended === undefined &&
      (ready || error)
    ) {
      current.ended = now();
      current.occurredAt = Date.now();
    }
    if (
      !enabled ||
      !active ||
      !binding ||
      current.started === undefined ||
      (!ready && !error)
    )
      return;
    const definition = targets[target];
    current.span ??= startSpan(
      scopeBinding(
        binding,
        definition.region,
        flow?.spans.length === 1 ? flow.spans[0] : binding.parent,
        flow?.spans ?? binding.related,
      ),
      definition.stage,
      current.isRetry
        ? 'retry'
        : target === 'dialog'
          ? 'dialog-open'
          : 'source-change',
      current.retryOf,
    );
    const span = current.span;
    if (!span) return;
    if (current.isRetry && !current.retryOf) span.gap('started-before-enabled');
    span.timing(current.started, current.ended, current.occurredAt);
    if (error) span.fail(definition.fail, error, {}, 'resource-load');
    else if (span.finish())
      span.emit(definition.ready, { ...span.ready(), cacheState: 'unknown' });
  }, [binding, flow, enabled, target, identity, active, ready, error, retry]);
  useLayoutEffect(
    () => () => {
      attempt.current?.span?.finish('cancelled', 'unmount');
      attempt.current = undefined;
    },
    [],
  );
}
