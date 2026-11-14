import type {
  OvClient,
  OvRequestSpec,
  OvResponseEnvelope,
} from '../../client/types.js';
import {
  clientBinding,
  rawClient,
  type BoundClient,
  type MonitoringBinding,
} from './binding.js';
import { startSpan } from './trace/span.js';
import { RequestObserver, requestObserver } from './request-observer.js';
import { monitoredStream } from './stream.js';

export function bindClient(
  client: OvClient,
  binding: MonitoringBinding,
): OvClient {
  const raw = (client as OvClient & BoundClient)[rawClient] ?? client;
  const run = async <T>(
    spec: OvRequestSpec,
    send: (spec: OvRequestSpec) => Promise<T>,
  ) => {
    const span = startSpan(binding, 'client-request');
    if (!span) return send(spec);
    const observer = new RequestObserver(span, spec);
    const responseType = spec.responseType === 'blob' ? 'blob' : 'json';
    try {
      const result = await send({
        ...spec,
        [requestObserver]: observer,
      } as OvRequestSpec);
      if (observer.signal?.aborted || spec.options?.signal?.aborted) {
        span.finish('cancelled', 'user-cancel');
        return result;
      }
      if (span.finish())
        span.emit('client_request_success', {
          ...span.ready(),
          ...observer.summary,
          responseType,
          httpStatus: observer.summary.httpStatus!,
        });
      return result;
    } catch (error) {
      span.fail(
        'client_request_fail',
        error,
        { ...observer.summary, responseType, phase: observer.phase },
        observer.phase === 'resolve-config'
          ? 'configuration'
          : observer.phase === 'read-body'
            ? 'response-read'
            : 'unknown',
        observer.signal ?? spec.options?.signal,
        observer.raw(),
      );
      throw error;
    } finally {
      observer.clear();
    }
  };
  const bound: OvClient & BoundClient = {
    [clientBinding]: binding,
    [rawClient]: raw,
    get baseURL() {
      return raw.baseURL;
    },
    request: <T>(spec: OvRequestSpec) => run<T>(spec, (s) => raw.request<T>(s)),
    requestEnvelope: <T>(spec: OvRequestSpec) =>
      run<OvResponseEnvelope<T>>(spec, (s) => raw.requestEnvelope<T>(s)),
    stream: (spec) => monitoredStream(raw, binding, spec),
  };
  return bound;
}

export const bindingOf = (client: (OvClient & BoundClient) | null) =>
  client?.[clientBinding];
