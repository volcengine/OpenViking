import type { TraceContext } from './trace.js';
import type { OpenVikingMonitoringEventMap } from './events.js';

export type OpenVikingMonitoringEventName = keyof OpenVikingMonitoringEventMap;

export type JsonValue =
  | null
  | boolean
  | number
  | string
  | readonly JsonValue[]
  | { readonly [key: string]: JsonValue };

export type SanitizedDiagnostics = {
  readonly message?: string;
  readonly stack?: string;
  readonly componentStack?: string;
  readonly requestBody?: JsonValue;
  readonly responseBody?: JsonValue;
};

export type OpenVikingMonitoringEvent = {
  [N in OpenVikingMonitoringEventName]: {
    readonly schemaVersion: 1;
    readonly libraryVersion: string;
    readonly eventId: string;
    readonly occurredAtUnixMs: number;
    readonly name: N;
    readonly source: OpenVikingMonitoringEventMap[N]['source'];
    readonly trace: TraceContext;
    readonly params: OpenVikingMonitoringEventMap[N]['params'];
    readonly diagnostics?: SanitizedDiagnostics;
  };
}[OpenVikingMonitoringEventName];

export interface OpenVikingMonitoringOptions {
  readonly events?: readonly OpenVikingMonitoringEventName[];
  readonly onEvent: (event: OpenVikingMonitoringEvent) => void | Promise<void>;
  readonly diagnostics?: {
    readonly transform: (input: {
      readonly error?: unknown;
      readonly message?: string;
      readonly stack?: string;
      readonly componentStack?: string;
      readonly requestBody?: unknown;
      readonly responseBody?: unknown;
    }) => SanitizedDiagnostics | undefined;
  };
}
