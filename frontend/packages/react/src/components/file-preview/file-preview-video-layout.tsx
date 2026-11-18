import { useRef, useState, type ReactNode, type RefObject } from 'react';
import { Button } from '../../shared/primitives/button/button.js';
import { FilePreviewVideoDetails } from './file-preview-video-details.js';
import type { FilePreviewFileTab, FilePreviewLabels } from './types.js';

export function FilePreviewVideoLayout({
  children,
  details,
  fileName,
  ready,
  text,
  timeMs,
  player,
}: {
  children: ReactNode;
  details?: FilePreviewFileTab['videoDetails'];
  fileName: string;
  ready: boolean;
  text: Required<FilePreviewLabels>;
  timeMs: number;
  player: RefObject<HTMLVideoElement | null>;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const toggle = useRef<HTMLButtonElement>(null);
  const [expanded, setExpanded] = useState(false);
  const label = expanded ? text.closePreview : text.expandVideo;

  // The same video stays mounted when the dialog enters or leaves the top layer.
  return (
    <dialog
      ref={dialog}
      className="ov-file-preview__video-layout"
      role={expanded ? undefined : 'presentation'}
      aria-label={expanded ? fileName : undefined}
      onClose={() => {
        setExpanded(false);
        toggle.current?.focus({ preventScroll: true });
      }}
    >
      {children}
      {details && (
        <>
          <Button
            ref={toggle}
            className="ov-file-preview__video-toggle"
            size="small"
            disabled={!ready}
            aria-haspopup={expanded ? undefined : 'dialog'}
            aria-label={label}
            title={label}
            onClick={() => {
              if (!dialog.current) return;
              if (dialog.current.open) {
                dialog.current.close();
              } else {
                dialog.current.showModal();
                setExpanded(true);
                toggle.current?.focus({ preventScroll: true });
              }
            }}
          >
            {label}
          </Button>
          <FilePreviewVideoDetails
            details={details}
            text={text}
            timeMs={timeMs}
            onSeek={(ms) => {
              if (player.current && ready)
                player.current.currentTime = ms / 1000;
            }}
            expanded={expanded}
          />
        </>
      )}
    </dialog>
  );
}
