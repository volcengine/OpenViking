import { useRef } from 'react';
import { useImageDialogMonitoring } from './use-image-dialog-monitoring.js';
import { Button } from '../../shared/primitives/button/button.js';
import type { FilePreviewLabels } from './types.js';

export function FilePreviewNativeImage({
  attempt,
  identity,
  reports,
  fileName,
  url,
  text,
  onReady,
  onFail,
}: {
  attempt: number;
  identity: object;
  reports: boolean;
  fileName: string;
  url: string;
  text: Required<FilePreviewLabels>;
  onReady: () => void;
  onFail: (reportResource?: boolean) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const imageDialog = useImageDialogMonitoring(dialog, identity, reports);
  return (
    <>
      <button
        className="ov-file-preview__image-button"
        type="button"
        aria-label={`${text.previewImage}: ${fileName}`}
        onClick={() => {
          const closed = !dialog.current?.open;
          dialog.current?.showModal();
          if (closed) imageDialog.open();
        }}
      >
        <img
          key={`${url}:${attempt}`}
          data-ov-preview-image
          src={url}
          alt={fileName}
          onLoad={onReady}
          onError={() => onFail()}
        />
      </button>
      <dialog
        ref={dialog}
        onClose={() => imageDialog.close()}
        className="ov-file-preview__image-dialog"
        aria-label={fileName}
      >
        <img
          ref={imageDialog.image}
          src={url}
          alt={fileName}
          onLoad={() => imageDialog.load()}
          onError={() => {
            if (!dialog.current?.open) return;
            onFail(false);
            imageDialog.fail();
          }}
        />
        <Button size="small" onClick={() => dialog.current?.close()}>
          {text.closePreview}
        </Button>
      </dialog>
    </>
  );
}
