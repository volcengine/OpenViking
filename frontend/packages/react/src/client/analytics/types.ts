import type { analyticsDefinitions } from './events.js';
import type { AnalyticsParams } from './fields.js';

export type OpenVikingAnalyticsEventMap = {
  [
    K in keyof typeof analyticsDefinitions as `openviking_${K}`
  ]: AnalyticsParams<(typeof analyticsDefinitions)[K]['fields']>;
};
export type OpenVikingAnalyticsEventName = keyof OpenVikingAnalyticsEventMap;
export type OpenVikingAnalyticsEvent = {
  [K in OpenVikingAnalyticsEventName]: {
    readonly name: K;
    readonly params: OpenVikingAnalyticsEventMap[K];
    readonly instanceId: string;
  };
}[OpenVikingAnalyticsEventName];

/** Omit analytics to disable collection; the host owns delivery and SDK configuration. */
export interface OpenVikingAnalyticsOptions {
  events?: readonly OpenVikingAnalyticsEventName[];
  onEvent: (event: OpenVikingAnalyticsEvent) => void | Promise<void>;
}
