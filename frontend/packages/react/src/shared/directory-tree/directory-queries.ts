import { bindingOf } from '../monitoring/client.js';
import { QueryObservation } from '../monitoring/query-observation.js';
import { InfiniteQueryObserver, type InfiniteData } from '@tanstack/query-core';
import type { DirState } from './types.js';
import { toError } from '../request/fs-errors.js';
import { directoryOptions, type DirectoryPage } from './directory-query.js';
import type { QueryRequest } from '../request/use-query-scope.js';
import type { UserRootMode } from './user-root-context.js';

type Pages = InfiniteData<DirectoryPage>;
type Observer = InfiniteQueryObserver<
  DirectoryPage,
  Error,
  Pages,
  ReturnType<typeof directoryOptions>['queryKey'],
  number
>;

/** Only subscriptions live here. All server pages remain in QueryClient. */
export class DirectoryQueries {
  private observers = new Map<
    string,
    {
      observer: Observer;
      unsubscribe?: () => void;
      paused?: boolean;
      adoptedFrom?: string;
      retryCause?: WeakRef<Error>;
      monitoring: QueryObservation<'file_browser_directory_load'>;
    }
  >();
  private listeners = new Set<() => void>();
  readonly dirs = { current: {} as Record<string, DirState> };
  constructor(
    private request: QueryRequest,
    root: string,
    private cacheScopeKey?: string,
    private userRootMode: UserRootMode = 'all',
  ) {
    this.observe(root);
  }
  private options(uri: string, retryError?: () => unknown) {
    const { request, cacheScopeKey, userRootMode: mode } = this;
    return directoryOptions(request, uri, cacheScopeKey, retryError, mode);
  }
  private publish = () => {
    this.dirs.current = Object.fromEntries(
      [...this.observers].map(([uri, { observer, monitoring, paused }]) => {
        const result = observer.getCurrentResult();
        if (paused || !this.listeners.size) monitoring.cancel();
        else monitoring.observe(result, observer.getCurrentQuery());
        const pages = result.data?.pages ?? [];
        const entries = new Map(
          pages
            .flatMap((page) => page.entries)
            .map((entry) => [entry.uri, entry]),
        );
        return [
          uri,
          {
            monitoring: monitoring.operations(observer.getCurrentQuery()),
            ready: result.isFetched,
            entries: [...entries.values()],
            hasMore: result.hasNextPage,
            loading: result.isFetching || result.isPending,
            error: result.error ? toError(result.error) : null,
          },
        ];
      }),
    );
    this.listeners.forEach((listener) => listener());
  };
  private observe(uri: string) {
    let item = this.observers.get(uri);
    if (!item) {
      const options = this.options(uri, () => {
        const error = item?.retryCause?.deref();
        if (item) item.retryCause = undefined;
        return error;
      });
      item = {
        monitoring: new QueryObservation(
          this.request.client,
          'file_browser_directory_load',
        ),
        observer: new InfiniteQueryObserver(this.request.queryClient, options),
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
    return item;
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
  getSnapshot = () => this.dirs.current;
  load = async (
    uri: string,
    offset: number,
    signal?: AbortSignal,
    refresh = false,
  ) => {
    this.request.signal?.throwIfAborted();
    signal?.throwIfAborted();
    const item = this.observe(uri);
    const binding = bindingOf(this.request.client);
    const error = item.observer.getCurrentResult().error;
    item.retryCause =
      error && binding?.controller.options && binding.active()
        ? new WeakRef(error)
        : undefined;
    let rejectAbort!: (reason: unknown) => void;
    const aborted = new Promise<never>((_, reject) => (rejectAbort = reject));
    const abort = () => {
      rejectAbort(signal?.reason);
      if (this.observers.get(uri) !== item) return;
      item.paused = true;
      item.unsubscribe?.();
      item.unsubscribe = undefined;
      item.observer.updateResult();
      this.publish();
    };
    signal?.addEventListener('abort', abort, { once: true });
    try {
      const pending =
        offset > 0
          ? item.observer.fetchNextPage({ cancelRefetch: false })
          : refresh
            ? item.observer.refetch({ cancelRefetch: false })
            : this.request.queryClient
                .fetchInfiniteQuery(this.options(uri))
                .catch(() => undefined);
      await (signal ? Promise.race([pending, aborted]) : pending);
      signal?.throwIfAborted();
      if (this.observers.get(uri) !== item) return;
      this.publish();
      return this.dirs.current[uri];
    } finally {
      signal?.removeEventListener('abort', abort);
    }
  };
  /** Hand off a verified equivalent listing without evicting shared cache data. */
  adoptListing = (uri: string, previousUri: string, retirePrevious = false) => {
    if (this.request.signal?.aborted) return;
    const target = this.observers.get(uri);
    if (target?.adoptedFrom !== previousUri) {
      const { queryClient } = this.request;
      const key = (value: string) => this.options(value).queryKey;
      const previous = queryClient.getQueryState<Pages>(key(previousUri));
      const queryKey = key(uri);
      const current = queryClient.getQueryState(queryKey);
      if (
        previous?.data?.pages.length &&
        current?.fetchStatus !== 'fetching' &&
        (!current?.data || current.dataUpdatedAt < previous.dataUpdatedAt)
      )
        queryClient.setQueryData(queryKey, previous.data, {
          updatedAt: previous.dataUpdatedAt,
        });
      this.observe(uri).adoptedFrom = previousUri;
      if (!target || current?.error)
        void this.load(uri, 0, undefined, true).catch(() => undefined);
    }
    if (retirePrevious) this.retireListing(previousUri);
  };

  /** Release one listing observer while keeping the shared cache entry. */
  retireListing = (uri: string) => {
    const item = this.observers.get(uri);
    if (!item) return;
    this.observers.delete(uri);
    item.unsubscribe?.();
    item.monitoring.cancel();
    this.publish();
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
