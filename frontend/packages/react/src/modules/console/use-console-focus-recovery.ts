import { useCallback, useRef } from 'react';
import type { RefObject } from 'react';

/** Restore focus only when removing a Console region removed its owner. */
export function useConsoleFocusRecovery(
  commandPanel: RefObject<HTMLDivElement | null>,
) {
  const source = useRef<HTMLDivElement | null>(null);
  return useCallback(
    (node: HTMLDivElement | null) => {
      const previous = source.current;
      source.current = node;
      if (node || !previous) return;
      const document = previous.ownerDocument;
      const focused = document.activeElement;
      if (!focused || !previous.contains(focused)) return;
      // Ref detachment precedes DOM deletion. Wait for this commit to expose
      // the surviving command panel, then reject ref replays, unmounts and any
      // focus the user or host has already moved to another element.
      queueMicrotask(() => {
        if (source.current || previous.isConnected || focused.isConnected)
          return;
        const current = document.activeElement;
        if (current && current !== document.body && current !== focused) return;
        const panel = commandPanel.current;
        if (!panel?.isConnected || panel.closest('[hidden]')) return;
        const input = panel.querySelector<HTMLTextAreaElement>(
          'textarea:not(:disabled)',
        );
        (input ?? panel).focus({ preventScroll: true });
      });
    },
    [commandPanel],
  );
}
