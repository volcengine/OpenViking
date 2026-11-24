import { isAbortError } from './response.js';
import { inheritClientConfiguration } from './managed-client.js';
import type { RequestFailure } from './error-events.js';
import type { OvClient, OvRequestSpec, OvStreamSpec } from './types.js';

const local = Symbol('local error feedback');
type Spec = (OvRequestSpec | OvStreamSpec) & { [local]?: true };

/** A transport decorator: publish failures, preserving results and rejection identity. */
export function observeRequestErrors(
  client: OvClient,
  publish: (failure: Omit<RequestFailure, 'id'>) => void,
  inheritedSignal?: AbortSignal,
): OvClient {
  const report = (error: unknown, spec: Spec) => {
    const signal = spec.options?.signal ?? inheritedSignal;
    if (spec[local] || signal?.aborted || isAbortError(error)) return;
    try {
      publish({
        error,
        request: { method: spec.method, path: spec.path },
        signal,
      });
    } catch {
      // A feedback subscriber must never replace the request's own failure.
    }
  };
  const run = async <T>(spec: Spec, operation: () => Promise<T>) => {
    try {
      return await operation();
    } catch (error) {
      report(error, spec);
      throw error;
    }
  };
  return inheritClientConfiguration(client, {
    get baseURL() {
      return client.baseURL;
    },
    request: (spec) => run(spec, () => client.request(spec)),
    requestEnvelope: (spec) => run(spec, () => client.requestEnvelope(spec)),
    async stream(spec) {
      const stream = await run(spec, () => client.stream(spec));
      return {
        abort: () => stream.abort(),
        async *[Symbol.asyncIterator]() {
          try {
            yield* stream;
          } catch (error) {
            report(error, spec);
            throw error;
          }
        },
      };
    },
  });
}

/** Queries/messages/tasks with their own persistent error presentation use this adapter. */
export function localRequestErrors(client: OvClient): OvClient {
  return inheritClientConfiguration(client, {
    get baseURL() {
      return client.baseURL;
    },
    request: (spec) => client.request({ ...spec, [local]: true } as Spec),
    requestEnvelope: (spec) =>
      client.requestEnvelope({ ...spec, [local]: true } as Spec),
    stream: (spec) => client.stream({ ...spec, [local]: true } as Spec),
  });
}
