import { useState } from 'react';
import type { RefObject } from 'react';
import { useSearchShortcut } from './use-search-shortcut.js';

export function useFileBrowserSearchDialog(
  root: RefObject<HTMLDivElement | null>,
) {
  const [open, setOpen] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [session, setSession] = useState(0);
  const show = () => {
    if (!open) setSession((value) => value + 1);
    setOpen(true);
  };
  const close = () => {
    setOpen(false);
    setAdvanced(false);
  };
  const shortcut = useSearchShortcut(root, () => (open ? close() : show()));
  return {
    open,
    advanced,
    setAdvanced,
    session,
    close,
    show,
    shortcut,
    showAdvanced: () => {
      setAdvanced(true);
      show();
    },
  };
}
