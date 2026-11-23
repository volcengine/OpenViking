import type { QueryClient } from '@tanstack/query-core';
import { OV_PATHS, type OvPath } from './paths.js';
import { queryKeys, type QueryScope } from './query-keys.js';

export type RequestConcurrency = Partial<Record<OvPath, number>>;
export const defaultConcurrency: RequestConcurrency = { [OV_PATHS.fsLs]: 3 };

export function mergeConcurrency(
  parent: RequestConcurrency,
  local?: RequestConcurrency,
) {
  const result = { ...parent };
  for (const [path, limit] of Object.entries(local ?? {})) {
    if (!Number.isSafeInteger(limit) || limit <= 0)
      throw new RangeError(
        'requestConcurrency values must be positive integers',
      );
    result[path as OvPath] = limit;
  }
  return result;
}

type Job = { limit: number; start: () => void };

/** Owned by a QueryClient; shared callers use the same lanes and opaque identities. */
export class RequestScheduler {
  constructor(private queryClient?: QueryClient) {}

  invalidate(scope: QueryScope) {
    const queryClient = this.queryClient;
    if (!queryClient) return;
    const filters = { queryKey: queryKeys.scope(scope) };
    // cancelQueries also replaces an initial in-flight read with no cached data.
    void queryClient
      .cancelQueries(filters)
      .then(() => queryClient.invalidateQueries(filters))
      .catch(() => undefined);
  }

  // Restored caches and separate SDK runtimes must not reuse local identity IDs.
  private namespace?: string;
  private identities = new Map<string, string>();
  private lanes = new Map<string, { active: number; jobs: Job[] }>();

  identity(value: string) {
    let id = this.identities.get(value);
    if (id === undefined) {
      this.namespace ??=
        globalThis.crypto?.randomUUID?.() ??
        Math.random().toString(36).slice(2);
      id = `${this.namespace}:${this.identities.size + 1}`;
      this.identities.set(value, id);
    }
    return id;
  }

  run<T>(
    key: string,
    limit: number | undefined,
    request: () => Promise<T>,
    signal?: AbortSignal,
  ): Promise<T> {
    if (!limit) return request();
    const lane = this.lanes.get(key) ?? { active: 0, jobs: [] };
    this.lanes.set(key, lane);
    const drain = () => {
      while (lane.jobs.length && lane.active < lane.jobs[0]!.limit)
        lane.jobs.shift()!.start();
      if (!lane.active && !lane.jobs.length) this.lanes.delete(key);
    };
    return new Promise<T>((resolve, reject) => {
      if (signal?.aborted) {
        reject(signal.reason as Error);
        return;
      }
      const job: Job = {
        limit,
        start: () => {
          signal?.removeEventListener('abort', abort);
          lane.active++;
          void Promise.resolve()
            .then(() => {
              signal?.throwIfAborted();
              return request();
            })
            .then(resolve, reject)
            .finally(() => {
              lane.active--;
              drain();
            });
        },
      };
      const abort = () => {
        const index = lane.jobs.indexOf(job);
        if (index < 0) return;
        lane.jobs.splice(index, 1);
        signal?.removeEventListener('abort', abort);
        reject(signal?.reason as Error);
        drain();
      };
      signal?.addEventListener('abort', abort, { once: true });
      lane.jobs.push(job);
      drain();
    });
  }
}

const schedulers = new WeakMap<QueryClient, RequestScheduler>();
export function schedulerFor(client: QueryClient) {
  let scheduler = schedulers.get(client);
  if (!scheduler) {
    scheduler = new RequestScheduler(client);
    schedulers.set(client, scheduler);
  }
  return scheduler;
}
