import {
  isTaskTerminal,
  taskStatuses,
  type TaskReference,
  type TaskService,
  type TaskSource,
  type TrackedTask,
} from './types.js';

class InvalidTaskResponse extends Error {}

interface Entry {
  source: TaskSource;
  snapshot: TrackedTask;
  due: number;
  failures: number;
  request?: AbortController;
}

/** One owner, one wake-up timer; sources partition endpoints and identities. */
export function createTaskTracker({ interval = 3000, concurrency = 4 } = {}) {
  const entries = new Map<string, Entry>();
  const sources: TaskSource[] = [];
  const listeners = new Set<() => void>();
  let snapshot: readonly TrackedTask[] = [];
  let active = false;
  const running = new Set<AbortController>();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const publish = () => {
    snapshot = [...entries.values()].map((entry) => entry.snapshot);
    listeners.forEach((listener) => listener());
  };
  const schedule = () => {
    clearTimeout(timer);
    timer = undefined;
    if (!active || running.size >= concurrency) return;
    const next = [...entries.values()].filter(
      (entry) => !entry.request && !isTaskTerminal(entry.snapshot.status),
    );
    if (next.length)
      timer = setTimeout(
        tick,
        Math.max(0, Math.min(...next.map((e) => e.due)) - Date.now()),
      );
  };
  const query = async (entry: Entry) => {
    const request = new AbortController();
    entry.request = request;
    running.add(request);
    request.signal.addEventListener(
      'abort',
      () => {
        running.delete(request);
        schedule();
      },
      { once: true },
    );
    const current = () =>
      active &&
      !request.signal.aborted &&
      entries.get(entry.snapshot.key) === entry;
    try {
      const result = await entry.source.service.getTask(
        entry.snapshot.id,
        request.signal,
      );
      if (!current()) return;
      if (
        result.id !== entry.snapshot.id ||
        !taskStatuses.includes(result.status ?? '')
      )
        throw new InvalidTaskResponse();
      entry.snapshot = { ...entry.snapshot, ...result, queryError: undefined };
      entry.failures = 0;
    } catch (error) {
      if (!current()) return;
      entry.failures++;
      entry.snapshot = {
        ...entry.snapshot,
        queryError: {
          code:
            error instanceof InvalidTaskResponse
              ? 'invalid-response'
              : 'request',
          message:
            error instanceof InvalidTaskResponse
              ? undefined
              : error instanceof Error
                ? error.message
                : String(error),
        },
      };
    } finally {
      running.delete(request);
      entry.request = undefined;
      if (current()) {
        entry.due =
          Date.now() + Math.min(30000, interval * 2 ** entry.failures);
        publish();
      }
      schedule();
    }
  };
  function tick() {
    timer = undefined;
    const due = [...entries.values()]
      .filter(
        (e) =>
          !e.request &&
          !isTaskTerminal(e.snapshot.status) &&
          e.due <= Date.now(),
      )
      .sort((a, b) => a.due - b.due);
    for (const entry of due) {
      if (running.size >= concurrency) break;
      void query(entry);
    }
    schedule();
  }
  return {
    subscribe(this: void, listener: () => void) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    getSnapshot: () => snapshot,
    source(identity: readonly unknown[], service: TaskService): TaskSource {
      let source = sources.find(
        (item) =>
          item.identity.length === identity.length &&
          item.identity.every((value, i) => Object.is(value, identity[i])),
      );
      if (!source) {
        source = {
          key: `source-${sources.length}`,
          identity,
          service,
          owners: 0,
        };
        sources.push(source);
      }
      return source;
    },
    retain(source: TaskSource) {
      source.owners++;
      return () => {
        if (--source.owners) return;
        for (const [key, entry] of entries) {
          if (entry.source !== source) continue;
          entry.request?.abort();
          entries.delete(key);
        }
        publish();
        schedule();
      };
    },
    track(reference: TaskReference, source: TaskSource) {
      const key = `${source.key}:${reference.id}`;
      if (!reference.id.trim()) return key;
      const existing = entries.get(key);
      if (!existing) {
        entries.set(key, {
          source,
          snapshot: { ...reference, key },
          due: Date.now(),
          failures: 0,
        });
        publish();
        schedule();
      }
      return key;
    },
    start() {
      active = true;
      schedule();
      return () => {
        active = false;
        clearTimeout(timer);
        for (const entry of entries.values()) entry.request?.abort();
      };
    },
  };
}

export type TaskTracker = ReturnType<typeof createTaskTracker>;
