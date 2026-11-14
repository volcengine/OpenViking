import type {
  OvClient,
  OvStreamSpec,
  OvStreamEvent,
} from '../../client/types.js';
import type { MonitoringBinding } from './binding.js';
import { startSpan } from './trace/span.js';
import { RequestObserver, requestObserver } from './request-observer.js';

export async function monitoredStream(
  client: OvClient,
  binding: MonitoringBinding,
  spec: OvStreamSpec,
) {
  const connect = startSpan(binding, 'client-stream-connect');
  if (!connect) return client.stream(spec);
  const observer = new RequestObserver(connect, spec);
  let stream;
  try {
    stream = await client.stream({
      ...spec,
      [requestObserver]: observer,
    } as OvStreamSpec);
    if (observer.callerSignals?.some((signal) => signal.aborted)) {
      connect.finish('cancelled', 'user-cancel');
    } else if (connect.finish())
      connect.emit('client_stream_connect_ready', {
        ...connect.ready(),
        ...observer.summary,
        httpStatus: observer.summary.httpStatus!,
      });
  } catch (error) {
    connect.fail(
      'client_stream_connect_fail',
      error,
      {
        ...observer.summary,
        phase:
          observer.phase === 'resolve-config'
            ? 'resolve-config'
            : observer.phase === 'send'
              ? 'send'
              : 'validate-response',
      },
      observer.phase === 'resolve-config'
        ? 'configuration'
        : observer.phase === 'send'
          ? 'network'
          : 'protocol',
      observer.callerSignals?.find((signal) => signal.aborted) ??
        spec.options?.signal,
      observer.raw(),
    );
    observer.clear();
    throw error;
  }
  let stopped = false;
  const original = stream;
  return {
    abort() {
      stopped = true;
      observer.clear();
      original.abort();
    },
    async *[Symbol.asyncIterator](): AsyncGenerator<OvStreamEvent> {
      const read = startSpan(
        { ...binding, parent: connect },
        'client-stream-read',
      );
      if (read) Object.assign(read.node, { request: observer.summary });
      let complete = false;
      let frameCount = 0;
      try {
        for await (const event of original) {
          if (read?.valid()) {
            frameCount++;
            if (event.id !== undefined)
              Object.assign(observer.summary, { nativeEventId: event.id });
          }
          yield event;
        }
        complete =
          !stopped &&
          !observer.callerSignals?.some((signal) => signal.aborted) &&
          !spec.options?.signal?.aborted;
        if (complete && read?.finish())
          read.emit('client_stream_read_complete', {
            ...read.ready(),
            ...observer.summary,
            frameCount,
            bytesRead: observer.bytesRead,
          });
      } catch (error) {
        if (!stopped)
          read?.fail(
            'client_stream_read_fail',
            error,
            observer.summary,
            'response-read',
            observer.callerSignals?.find((signal) => signal.aborted) ??
              spec.options?.signal,
            observer.raw(),
          );
        throw error;
      } finally {
        observer.clear();
        if (!complete)
          read?.finish(
            'cancelled',
            stopped ? 'user-cancel' : 'consumer-complete',
          );
      }
    },
  };
}
