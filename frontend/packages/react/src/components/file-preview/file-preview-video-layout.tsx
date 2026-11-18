import { useRef, useState, type ReactNode, type RefObject } from 'react';
import { Button } from '../../shared/primitives/button/button.js';
import FullscreenIcon from '../../../../icons/svg/file-preview/fullscreen.svg?react';
import CloseIcon from '../../../../icons/svg/viking-bot/remove.svg?react';
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
  children: (toggle?: ReactNode) => ReactNode;
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
      onClose={(event) => {
        if (event.target !== event.currentTarget) return;
        setExpanded(false);
        toggle.current?.focus({ preventScroll: true });
      }}
    >
      {children(
        details && (
          <Button
            ref={toggle}
            className="ov-file-preview__video-toggle"
            size="small"
            shape="circle"
            icon={expanded ? <CloseIcon /> : <FullscreenIcon />}
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
          />
        ),
      )}
      {details && (
        <FilePreviewVideoDetails
          details={details}
          text={text}
          timeMs={timeMs}
          onSeek={(ms) => {
            if (player.current && ready) player.current.currentTime = ms / 1000;
          }}
          expanded={expanded}
        />
      )}
    </dialog>
  );
}
