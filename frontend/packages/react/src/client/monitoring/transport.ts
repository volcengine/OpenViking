import type { Fault } from './fault.js';
import type { Ready } from './views.js';
import type { NativeRouteKey } from './targets.js';

export type RequestSummary = {
  readonly method:
    'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE' | 'HEAD' | 'OPTIONS';
  readonly routeKey: NativeRouteKey | 'custom';
  readonly requestAttemptId: string;
  readonly requestId: string | null;
  readonly httpStatus: number | null;
  readonly nativeEventId?: string;
};

export type RequestReady = Ready &
  RequestSummary & {
    readonly httpStatus: number;
    readonly responseType: 'json' | 'blob';
  };

export type RequestFault = Fault<
  | 'configuration'
  | 'network'
  | 'http'
  | 'business'
  | 'response-read'
  | 'unknown'
> &
  RequestSummary & {
    readonly phase:
      'resolve-config' | 'send' | 'read-body' | 'business-envelope';
    readonly responseType: 'json' | 'blob';
  };

export type StreamConnectReady = Ready &
  RequestSummary & { readonly httpStatus: number };

export type StreamConnectFault = Fault<
  'configuration' | 'network' | 'http' | 'business' | 'protocol' | 'unknown'
> &
  RequestSummary & {
    readonly phase: 'resolve-config' | 'send' | 'validate-response';
  };

export type StreamReadReady = Ready &
  RequestSummary & { readonly frameCount: number; readonly bytesRead: number };

export type StreamReadFault = Fault<'network' | 'response-read' | 'unknown'> &
  RequestSummary;
