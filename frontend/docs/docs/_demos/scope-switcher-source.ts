import { useSyncExternalStore } from 'react';
import type { ScopeSwitcherOption } from '@openviking/react/components/scope-switcher';

export type ScopeIndex = 0 | 1 | 2;
export interface ScopeSnapshot {
  readonly ancestors: readonly ScopeSwitcherOption[];
  readonly selected?: ScopeSwitcherOption;
}

/** Host example: share identities only, never options, requests or UI state. */
export function createScopeSource(
  initial: readonly ScopeSwitcherOption[] = [],
) {
  const listeners = [
    new Set<() => void>(),
    new Set<() => void>(),
    new Set<() => void>(),
  ];
  let selected = [...initial];
  const snapshot = (index: number): ScopeSnapshot => ({
    ancestors: selected.slice(0, index),
    selected: selected[index],
  });
  const snapshots = [snapshot(0), snapshot(1), snapshot(2)];
  const channels = ([0, 1, 2] as const).map((index) => ({
    getSnapshot: () => snapshots[index]!,
    subscribe: (listener: () => void) => {
      listeners[index]!.add(listener);
      return () => {
        listeners[index]!.delete(listener);
      };
    },
  }));
  return {
    channels,
    select(index: ScopeIndex, option: ScopeSwitcherOption) {
      if (index > selected.length || selected[index]?.id === option.id) return;
      selected = [...selected.slice(0, index), option];
      // Publish all affected snapshots before notifying anyone: no mixed scope.
      for (let i = index; i < 3; i++) snapshots[i] = snapshot(i);
      for (let i = index; i < 3; i++)
        listeners[i]!.forEach((notify) => notify());
    },
  };
}
export type ScopeSource = ReturnType<typeof createScopeSource>;
export function useScope(source: ScopeSource, index: ScopeIndex) {
  const channel = source.channels[index]!;
  return useSyncExternalStore(
    channel.subscribe,
    channel.getSnapshot,
    channel.getSnapshot,
  );
}
