import type {
  OpenVikingAnalyticsOptions,
  OpenVikingAnalyticsEvent,
  OpenVikingAnalyticsEventMap,
  OpenVikingProviderProps,
} from '@openviking/react/client';
const analytics: OpenVikingAnalyticsOptions = {
  events: [
    'openviking_file_preview_copy_path_click',
    'openviking_file_browser_search_success',
  ],
  onEvent(event) {
    if (event.name === 'openviking_file_browser_search_success') {
      const count: number = event.params.result_count;
      const mode: 'find' | 'search' | 'grep' | 'glob' = event.params.mode;
      void count;
      void mode;
      // @ts-expect-error Copy events and search payloads are not interchangeable.
      const content: string = event.params.content;
      void content;
    }
  },
};
const off: OpenVikingProviderProps = { analytics: false };
// @ts-expect-error Unknown names must never become untyped strings.
const bad: OpenVikingAnalyticsOptions = { events: ['copy'], onEvent() {} };
// @ts-expect-error An outcome requires its own duration and result count.
const missing: OpenVikingAnalyticsEventMap['openviking_file_browser_search_success'] =
  { operation_id: 'op', mode: 'find', has_filters: false };
const wrong: OpenVikingAnalyticsEvent = {
  name: 'openviking_file_browser_search_success',
  // @ts-expect-error The discriminant fixes the payload shape.
  params: { step_index: 1 },
  instanceId: 'point',
};
// @ts-expect-error Sending arbitrary metadata through component props is unsupported.
const receiver: OpenVikingProviderProps = { analytics: { track() {} } };
void [analytics, off, bad, missing, wrong, receiver];

const copiedValue: OpenVikingAnalyticsEventMap['openviking_file_preview_copy_path_click'] =
  {
    // @ts-expect-error No-parameter copy events cannot carry a path or body.
    path: 'private',
  };
void copiedValue;
