import type { HTMLAttributes } from 'react';

/** Preserve IME confirmation and prevent file drops from navigating away. */
export function composerEvents(
  interceptDrop: boolean,
  acceptDrop: boolean,
  reportFiles: (files: readonly File[]) => void,
): Pick<
  HTMLAttributes<HTMLDivElement>,
  'onKeyDownCapture' | 'onDragOver' | 'onDrop'
> {
  return {
    onKeyDownCapture: (event) => {
      if (
        event.key === 'Enter' &&
        (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229)
      )
        event.stopPropagation();
    },
    onDragOver: (event) => {
      if (!interceptDrop) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = acceptDrop ? 'copy' : 'none';
    },
    onDrop: (event) => {
      if (!interceptDrop) return;
      event.preventDefault();
      if (acceptDrop) reportFiles([...(event.dataTransfer.files ?? [])]);
    },
  };
}
