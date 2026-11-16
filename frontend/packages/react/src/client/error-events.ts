import type { OvRequestSpec } from './types.js';

export interface RequestFailure {
  id: number;
  error: unknown;
  request: Pick<OvRequestSpec, 'method' | 'path'>;
  signal?: AbortSignal;
}

const empty: readonly RequestFailure[] = [];

/** Instance-owned publication/subscription; contains no React or browser state. */
export function createRequestErrors(reported = new WeakSet<object>()) {
  const listeners = new Set<() => void>();
  let failures = empty;
  let sequence = 0;
  const update = (next: readonly RequestFailure[]) => {
    failures = next;
    for (const listener of listeners) listener();
  };
  return {
    reported,
    getSnapshot: () => failures,
    subscribe(this: void, listener: () => void) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    publish(failure: Omit<RequestFailure, 'id'>) {
      if (failure.error && typeof failure.error === 'object')
        reported.add(failure.error);
      update([...failures, { ...failure, id: ++sequence }]);
    },
    dismiss(id: number) {
      const next = failures.filter((failure) => failure.id !== id);
      if (next.length !== failures.length) update(next);
    },
    dismissError(error: unknown) {
      const next = failures.filter((failure) => failure.error !== error);
      if (next.length !== failures.length) update(next);
    },
    wasReported(error: unknown) {
      const visited = new Set<object>();
      while (error && typeof error === 'object' && !visited.has(error)) {
        if (reported.has(error)) return true;
        visited.add(error);
        error = error instanceof Error ? error.cause : undefined;
      }
      return false;
    },
  };
}

export type RequestErrors = ReturnType<typeof createRequestErrors>;
