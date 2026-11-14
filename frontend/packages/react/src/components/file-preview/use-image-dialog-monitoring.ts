import { useRef, useState } from 'react';
import type { RefObject } from 'react';
import { useRenderedMonitoring } from '../../shared/monitoring/context.js';
import { useMediaMonitoring } from './use-media-monitoring.js';

export function useImageDialogMonitoring(
  dialog: RefObject<HTMLDialogElement | null>,
  identity: object,
  reports: boolean,
) {
  const enabled = useRenderedMonitoring();
  const image = useRef<HTMLImageElement>(null);
  const [opened, setOpened] = useState(false);
  const [state, setState] = useState<{
    identity: object;
    ready?: boolean;
    error?: unknown;
  }>();
  useMediaMonitoring(
    'dialog',
    identity,
    opened && reports && !!dialog.current?.open,
    state?.identity === identity && !!state.ready,
    state?.identity === identity ? state.error : undefined,
  );
  return {
    image,
    open() {
      if (!enabled) return;
      setOpened(true);
      setState({
        identity,
        ready: !!image.current?.complete && image.current.naturalWidth > 0,
      });
    },
    close() {
      setOpened(false);
      setState(undefined);
    },
    load() {
      if (dialog.current?.open && enabled) setState({ identity, ready: true });
    },
    fail() {
      if (dialog.current?.open && enabled)
        setState({
          identity,
          error: new Error('Image dialog resource could not be loaded'),
        });
    },
  };
}
