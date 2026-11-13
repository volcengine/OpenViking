import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
} from 'react';
import type { ReactNode } from 'react';
import { createOvClient } from '../../client/client.js';
import { useOvClient, useOvSignal } from '../../client/provider.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { createNativeTaskService } from './native-service.js';
import { createTaskTracker, type TaskTracker } from './tracker.js';
import type { TaskService, TaskReference } from './types.js';

const Context = createContext<
  { tracker: TaskTracker; service?: TaskService; client: OvClient } | undefined
>(undefined);

export function TaskTrackingProvider({
  children,
  service,
}: {
  children: ReactNode;
  service?: TaskService;
}) {
  const inheritedClient = useOvClient();
  const [fallbackClient] = useState(() => createOvClient());
  const client = inheritedClient ?? fallbackClient;
  const [tracker] = useState(() => createTaskTracker());
  useEffect(() => tracker.start(), [tracker]);
  const value = useMemo(
    () => ({ tracker, service, client }),
    [tracker, service, client],
  );
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

/** Shared in Console; standalone headless Bot hooks own an isolated fallback. */
export function useTaskTracking({
  requestOptions = {},
  scopeKey,
  taskService,
}: {
  requestOptions?: OvRequestOptions;
  scopeKey?: string;
  taskService?: TaskService;
}) {
  const inherited = useContext(Context);
  const inheritedClient = useOvClient();
  const inheritedSignal = useOvSignal();
  const [fallbackClient] = useState(() => createOvClient());
  const [fallbackTracker] = useState(() => createTaskTracker());
  const tracker = inherited?.tracker ?? fallbackTracker;
  const service = taskService ?? inherited?.service;
  const client = inheritedClient ?? inherited?.client ?? fallbackClient;
  const {
    baseURL,
    apiKey,
    accountId,
    userId,
    resourceId,
    signal = inheritedSignal,
  } = requestOptions;
  const source = useMemo(() => {
    const options = { baseURL, apiKey, accountId, userId, resourceId, signal };
    return tracker.source(
      [
        client,
        baseURL,
        apiKey,
        accountId,
        userId,
        resourceId,
        signal,
        scopeKey,
        service,
      ],
      service ?? createNativeTaskService(client, options),
    );
  }, [
    tracker,
    client,
    baseURL,
    apiKey,
    accountId,
    userId,
    resourceId,
    signal,
    scopeKey,
    service,
  ]);
  useEffect(
    () => (inherited ? undefined : tracker.start()),
    [tracker, inherited],
  );
  useEffect(() => {
    const release = tracker.retain(source);
    let released = false;
    const stop = () => {
      if (!released) {
        released = true;
        release();
      }
    };
    if (signal?.aborted) stop();
    else signal?.addEventListener('abort', stop, { once: true });
    return () => {
      signal?.removeEventListener('abort', stop);
      stop();
    };
  }, [tracker, source, signal]);
  const tasks = useSyncExternalStore(
    tracker.subscribe,
    tracker.getSnapshot,
    tracker.getSnapshot,
  );
  const track = useMemo(
    () => (reference: TaskReference) =>
      signal?.aborted
        ? `${source.key}:${reference.id}`
        : tracker.track(reference, source),
    [tracker, source, signal],
  );
  return { tracker, source, tasks, track };
}

export const useTaskTrackerContext = () => useContext(Context);
