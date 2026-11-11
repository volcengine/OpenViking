import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { createScopeController } from './controller.js';
import type {
  ScopeCascadeApi,
  ScopeCascadeLevel,
  ScopeCascadeProps,
} from './types.js';

/** Creates a stable coordinator without subscribing the caller to its data. */
export function useScopeCascade(
  { loadLibraries, loadSpaces, loadUsers }: ScopeCascadeApi,
  onChange?: ScopeCascadeProps['onChange'],
) {
  const notify = useRef(onChange);
  notify.current = onChange;
  const [instance] = useState(() =>
    createScopeController((selection) => notify.current?.(selection)),
  );
  // Synchronize this instance with its host API adapters. Requests must run after
  // commit and restart when adapters change, including authentication changes.
  // Cleanup aborts all requests; controller guards also ignore stale completions.
  useEffect(
    () => instance.start({ loadLibraries, loadSpaces, loadUsers }),
    [instance, loadLibraries, loadSpaces, loadUsers],
  );
  return instance.controller;
}

/** Call in a leaf, never aggregate all three subscriptions in the root. */
export function useScopeCascadeLevel(level: ScopeCascadeLevel) {
  return useSyncExternalStore(
    level.subscribe,
    level.getSnapshot,
    level.getSnapshot,
  );
}
