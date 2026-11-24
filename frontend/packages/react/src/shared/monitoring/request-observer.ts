import { markFault } from './fault.js';
import { OV_PATHS } from '../../client/paths.js';
import type { OvRequestSpec } from '../../client/types.js';
import type { NativeRouteKey } from '../../client/monitoring/targets.js';
import type { RequestSummary } from '../../client/monitoring/transport.js';
import type { MonitoringSpan } from './trace/span.js';
import { eventId } from './clock.js';

export const requestObserver = Symbol('OpenViking.request-observer');
export type ObservedSpec = { readonly [requestObserver]?: RequestObserver };
function route(path: string): NativeRouteKey | 'custom' {
  const exact = Object.entries(OV_PATHS).find(
    ([, value]) => value === path,
  )?.[0];
  if (exact) return exact as NativeRouteKey;
  if (/^\/api\/v1\/tasks\/[^/]+\/cancel$/.test(path)) return 'taskCancel';
  if (/^\/api\/v1\/tasks\/[^/]+$/.test(path)) return 'task';
  if (/^\/bot\/v1\/sessions\/[^/]+\/interrupt$/.test(path))
    return 'botInterrupt';
  if (/^\/bot\/v1\/sessions\/[^/]+\/steer$/.test(path)) return 'botSteer';
  if (/^\/api\/v1\/sessions\/[^/]+\/messages$/.test(path))
    return 'sessionMessages';
  if (/^\/api\/v1\/sessions\/[^/]+\/commit$/.test(path)) return 'sessionCommit';
  if (/^\/api\/v1\/sessions\/[^/]+\/context$/.test(path))
    return 'sessionContext';
  if (/^\/api\/v1\/sessions\/[^/]+\/archives\/[^/]+$/.test(path))
    return 'sessionArchive';
  if (/^\/api\/v1\/sessions\/[^/]+$/.test(path)) return 'session';
  if (/^\/api\/v1\/admin\/accounts\/[^/]+\/users$/.test(path))
    return 'accountUsers';
  if (/^\/api\/v1\/admin\/accounts\/[^/]+\/groups$/.test(path))
    return 'accountGroups';
  return 'custom';
}

/** Carries safe transport facts without retaining URL, identity or request body. */
export class RequestObserver {
  signal?: AbortSignal;
  callerSignals?: readonly AbortSignal[];
  bytesRead = 0;
  bytes(size: number) {
    if (this.span.valid()) this.bytesRead += size;
  }
  business = (error: Error) => {
    markFault(error, 'business');
  };
  private release?: () => void;
  requestBody?: unknown;
  responseBody?: unknown;
  private retain() {
    if (this.release) return;
    const a = this.span.controller.retainRaw(() => this.clear());
    const b = this.span.history.retain(() => this.clear());
    this.release = () => {
      a();
      b();
    };
  }
  capture(body: unknown) {
    if (
      this.span.valid() &&
      this.span.binding.controller.options &&
      this.span.binding.controller.options.diagnostics
    ) {
      this.retain();
      this.responseBody = body;
    }
  }
  raw() {
    return { requestBody: this.requestBody, responseBody: this.responseBody };
  }
  clear() {
    this.requestBody = this.responseBody = undefined;
    this.release?.();
    this.release = undefined;
  }
  phase: 'resolve-config' | 'send' | 'read-body' | 'business-envelope' =
    'resolve-config';
  readonly summary: RequestSummary;
  constructor(
    readonly span: MonitoringSpan,
    spec: Pick<OvRequestSpec, 'path' | 'method' | 'body'>,
  ) {
    if (
      span.binding.controller.options &&
      span.binding.controller.options.diagnostics
    ) {
      this.retain();
      this.requestBody = spec.body;
    }
    this.summary = {
      method: spec.method,
      routeKey: route(spec.path),
      requestAttemptId: eventId(),
      requestId: null,
      httpStatus: null,
    };
    Object.assign(span.node, { request: this.summary });
  }
  response(response: Response) {
    Object.assign(this.summary, {
      requestId: response.headers.get('x-request-id'),
      httpStatus: response.status,
    });
  }
}

export const observation = (spec: ObservedSpec) => spec[requestObserver];
