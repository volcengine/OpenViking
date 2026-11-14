import type { Spec } from './event-spec.js';
import type { ComponentName } from './source.js';
import type {
  RequestReady,
  RequestFault,
  StreamConnectReady,
  StreamConnectFault,
  StreamReadReady,
  StreamReadFault,
} from './transport.js';
import type {
  PollRequestFault,
  PollProtocolFault,
  PollRecovered,
} from './poll.js';
export type TransportEvents = {
  readonly client_request_success: Spec<ComponentName, RequestReady>;
  readonly client_request_fail: Spec<ComponentName, RequestFault>;
  readonly client_stream_connect_ready: Spec<ComponentName, StreamConnectReady>;
  readonly client_stream_connect_fail: Spec<ComponentName, StreamConnectFault>;
  readonly client_stream_read_complete: Spec<ComponentName, StreamReadReady>;
  readonly client_stream_read_fail: Spec<ComponentName, StreamReadFault>;
  readonly task_poll_request_fail: Spec<ComponentName, PollRequestFault>;
  readonly task_poll_protocol_fail: Spec<ComponentName, PollProtocolFault>;
  readonly task_poll_recovered: Spec<ComponentName, PollRecovered>;
};
