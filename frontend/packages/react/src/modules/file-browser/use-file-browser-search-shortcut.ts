import { useEffect, type RefObject } from 'react';

export function useFileBrowserSearchShortcut(
  root: RefObject<HTMLElement | null>,
  open: boolean,
  setOpen: (open: boolean) => void,
) {
  // The document receives shortcuts even before the tree has focus. Register
  // against its owner after mount and remove the listener on unmount or open.
  useEffect(() => {
    const host = root.current;
    if (!host || open) return;
    const doc = host.ownerDocument;
    const onKeyDown = (event: KeyboardEvent) => {
      if (
        event.defaultPrevented ||
        event.isComposing ||
        event.repeat ||
        !(event.metaKey || event.ctrlKey) ||
        event.altKey ||
        event.shiftKey ||
        event.key.toLowerCase() !== 'k'
      )
        return;
      const target = event.target;
      if (!(target instanceof Element)) return;
      if (
        target.closest(
          'input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="dialog"]',
        )
      )
        return;
      const owner = target.closest('.ov-file-browser');
      const soleUnfocused =
        target === doc.body &&
        doc.querySelectorAll('.ov-file-browser').length === 1;
      if (owner !== host && !soleUnfocused) return;
      if (doc.querySelector('[role="dialog"][aria-modal="true"]')) return;
      const trigger = host.querySelector<HTMLButtonElement>(
        '.ov-file-tree__search',
      );
      if (!trigger || trigger.disabled) return;
      event.preventDefault();
      trigger.focus({ preventScroll: true });
      setOpen(true);
    };
    doc.addEventListener('keydown', onKeyDown);
    return () => doc.removeEventListener('keydown', onKeyDown);
  }, [root, open, setOpen]);
}
