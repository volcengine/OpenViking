import { InfiniteQueryObserver, type InfiniteData } from '@tanstack/query-core';
import type { DirState } from './fs-model.js';
import { toError } from './fs-model.js';
import { directoryOptions, type DirectoryPage } from './directory-query.js';
import type { QueryRequest } from './use-query-scope.js';

type Observer = InfiniteQueryObserver<
  DirectoryPage,
  Error,
  InfiniteData<DirectoryPage>,
  ReturnType<typeof directoryOptions>['queryKey'],
  number
>;

/** Only subscriptions live here. All server pages remain in QueryClient. */
export class DirectoryQueries {
  private observers = new Map<
    string,
    { observer: Observer; unsubscribe?: () => void; paused?: boolean }
  >();
  private listeners = new Set<() => void>();
  private snapshot: Record<string, DirState> = {};
  readonly dirs = { current: this.snapshot };
  constructor(
    private request: QueryRequest,
    root: string,
  ) {
    this.observe(root);
  }
  private publish = () => {
    this.snapshot = Object.fromEntries(
      [...this.observers].map(([uri, { observer }]) => {
        const result = observer.getCurrentResult();
        const pages = result.data?.pages ?? [];
        const entries = new Map(
          pages
            .flatMap((page) => page.entries)
            .map((entry) => [entry.uri, entry]),
        );
        return [
          uri,
          {
            ready: result.isFetched,
            entries: [...entries.values()],
            hasMore: result.hasNextPage,
            loading: result.isFetching || result.isPending,
            error: result.error ? toError(result.error) : null,
          },
        ];
      }),
    );
    this.dirs.current = this.snapshot;
    this.listeners.forEach((listener) => listener());
  };
  private observe(uri: string) {
    let item = this.observers.get(uri);
    if (!item) {
      item = {
        observer: new InfiniteQueryObserver(
          this.request.queryClient,
          directoryOptions(this.request, uri),
        ),
      };
      this.observers.set(uri, item);
      if (this.listeners.size)
        item.unsubscribe = item.observer.subscribe(this.publish);
      this.publish();
    }
    if (item.paused) {
      item.paused = false;
      if (this.listeners.size)
        item.unsubscribe = item.observer.subscribe(this.publish);
    }
    return item.observer;
  }
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    for (const item of this.observers.values())
      if (!item.paused)
        item.unsubscribe ??= item.observer.subscribe(this.publish);
    this.publish();
    return () => {
      this.listeners.delete(listener);
      if (!this.listeners.size)
        for (const item of this.observers.values()) {
          item.unsubscribe?.();
          item.unsubscribe = undefined;
        }
    };
  };
  getSnapshot = () => this.snapshot;
  load = async (
    uri: string,
    offset: number,
    signal?: AbortSignal,
    refresh = false,
  ) => {
    this.request.signal?.throwIfAborted();
    signal?.throwIfAborted();
    const observer = this.observe(uri);
    const item = this.observers.get(uri)!;
    let rejectAbort: ((reason: unknown) => void) | undefined;
    const aborted = new Promise<never>((_, reject) => {
      rejectAbort = reject;
    });
    const abort = () => {
      item.paused = true;
      item.unsubscribe?.();
      item.unsubscribe = undefined;
      item.observer.updateResult();
      this.publish();
      rejectAbort?.(signal?.reason);
    };
    signal?.addEventListener('abort', abort, { once: true });
    try {
      const pending =
        offset > 0
          ? observer.fetchNextPage({ cancelRefetch: false })
          : refresh
            ? observer.refetch({ cancelRefetch: false })
            : this.request.queryClient
                .fetchInfiniteQuery(directoryOptions(this.request, uri))
                .catch(() => undefined);
      await (signal ? Promise.race([pending, aborted]) : pending);
      signal?.throwIfAborted();
      this.publish();
      return this.snapshot[uri];
    } finally {
      signal?.removeEventListener('abort', abort);
    }
  };
  refresh = async () => {
    if (this.request.signal?.aborted) return;
    await Promise.all(
      [...this.observers.values()].map(({ observer }) =>
        observer.refetch({ cancelRefetch: false }),
      ),
    );
    this.publish();
  };
}
