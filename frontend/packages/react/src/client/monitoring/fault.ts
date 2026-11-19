import type { TraceSnapshot } from './trace.js';

export type ErrorKind =
  | 'render'
  | 'network'
  | 'http'
  | 'business'
  | 'configuration'
  | 'response-read'
  | 'protocol'
  | 'resource-load'
  | 'renderer'
  | 'host-service'
  | 'storage'
  | 'unknown';

export type FaultLink =
  | { readonly faultRole: 'origin'; readonly causeEventId: null }
  | { readonly faultRole: 'propagation'; readonly causeEventId: string };

export type ErrorCodeByKind = {
  render: 'RENDER_ERROR';
  network: 'NETWORK_ERROR';
  http: 'HTTP_ERROR';
  business: 'BUSINESS_ERROR';
  configuration: 'REQUEST_CONFIG_ERROR' | 'RESOURCE_EXPORT_INVALID';
  'response-read': 'RESPONSE_READ_ERROR';
  protocol:
    'STREAM_PROTOCOL_ERROR' | 'TASK_RESPONSE_INVALID' | 'DATA_RESPONSE_INVALID';
  'resource-load': 'RESOURCE_LOAD_ERROR';
  renderer: 'RENDERER_ERROR';
  'host-service': 'HOST_SERVICE_ERROR';
  storage: 'STORAGE_ERROR';
  unknown: 'UNKNOWN_ERROR';
};

export type Fault<K extends ErrorKind> = {
  [E in K]: FaultLink & {
    readonly errorKind: E;
    readonly code: ErrorCodeByKind[E];
    readonly observedAt:
      | 'library-boundary'
      | 'host-slot'
      | 'host-callback'
      | 'host-service'
      | 'dependency'
      | 'browser'
      | 'upstream-response'
      | 'unknown';
    readonly incidentId: string;
    readonly elapsedMs: number | null;
    readonly traceSnapshot: TraceSnapshot;
  };
}[K];
