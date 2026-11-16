import { useImageDialogMonitoring } from './use-image-dialog-monitoring.js';
import { opaqueIdentity } from '../../shared/monitoring/clock.js';
import { useMediaMonitoring } from './use-media-monitoring.js';
import { useRenderedMonitoring } from '../../shared/monitoring/context.js';
import type { FileViewerState } from '@ark-design/ui/file-viewer/core';
import { useExposure } from '../../shared/analytics/use-exposure.js';
import { Button } from '../../shared/primitives/button/button.js';
import { useEffect, useMemo, useRef, useState } from 'react';
import type {
  FilePreviewFileTabSlots,
  FilePreviewLabels,
  FilePreviewSource,
} from './types.js';
import {
  LoadFailedState,
  LoadingState,
  UnsupportedState,
} from './file-preview-state.js';
import { FilePreviewImageFrame } from './file-preview-image-frame.js';

export { mediaKind } from './media-kind.js';

/** Native images/video do not initialize FileViewer or its media adapters. */
export function FilePreviewMedia({
  source,
  kind,
  text,
  slots,
  reports = true,
  onStateChange,
}: {
  reports?: boolean;
  onStateChange?: (
    state: FileViewerState,
    error?: unknown,
    reason?: 'retry',
  ) => void;
  source: FilePreviewSource;
  kind: 'image' | 'video';
  text: Required<FilePreviewLabels>;
  slots?: FilePreviewFileTabSlots;
}) {
  const blob = useMemo(
    () =>
      typeof source.content === 'string'
        ? new Blob([source.content], {
            type:
              kind === 'image' &&
              /svg/i.test(source.fileType || source.fileName)
                ? 'image/svg+xml'
                : source.file?.type,
          })
        : source.file,
    [source.content, source.file, source.fileType, source.fileName, kind],
  );
  const owner = useMemo(() => ({ blob, url: source.url }), [blob, source.url]);
  const [resolved, setResolved] = useState<{
    owner: typeof owner;
    url: string;
  }>();
  const [status, setStatus] = useState<{
    owner: typeof owner;
    failed?: boolean;
    loaded?: boolean;
    error?: unknown;
  }>();
  const [attempt, setAttempt] = useState(0);
  const dialog = useRef<HTMLDialogElement>(null);
  const enabled = useRenderedMonitoring();
  // Blob URLs belong to this committed source; release them on replacement/unmount.
  // URL sources are used directly and never revoked by the component.
  useEffect(() => {
    if (!blob) return;
    const url = URL.createObjectURL(blob);
    setResolved({ owner, url });
    return () => URL.revokeObjectURL(url);
  }, [blob, owner]);
  const url = blob
    ? resolved?.owner === owner
      ? resolved.url
      : undefined
    : source.url;
  const failed = status?.owner === owner && status.failed;
  const retry = () => {
    onStateChange?.('loading', undefined, 'retry');
    setStatus(undefined);
    setAttempt((value) => value + 1);
  };
  const loaded = status?.owner === owner && status.loaded;
  const exposure = useExposure(
    'file_preview_content_show',
    owner,
    undefined,
    Boolean(loaded && !failed),
  );
  const sourceIdentity = useMemo(() => opaqueIdentity(owner), [owner]);
  const identity = useMemo(
    () => opaqueIdentity(owner, attempt),
    [owner, attempt],
  );
  useMediaMonitoring(
    kind,
    identity,
    !!url && reports,
    !!loaded,
    status?.owner === owner ? status.error : undefined,
    { sourceIdentity, generation: attempt, bound: !!url },
  );
  const imageDialog = useImageDialogMonitoring(dialog, identity, reports);
  if (failed)
    return typeof slots?.errorFallback === 'function'
      ? slots.errorFallback(retry)
      : (slots?.errorFallback ?? (
          <LoadFailedState text={text} onRetry={retry} />
        ));
  if (!blob && !url)
    return slots?.unsupportedFallback ?? <UnsupportedState text={text} />;
  const fail = (reportResource = true) => {
    const error =
      enabled && reportResource
        ? new Error('Media resource could not be loaded')
        : undefined;
    setStatus({ owner, failed: true, error });
    onStateChange?.('error', error);
  };
  const open = () => {
    const closed = !dialog.current?.open;
    dialog.current?.showModal();
    if (closed) imageDialog.open();
  };
  const ready = () => {
    setStatus({ owner, loaded: true });
    onStateChange?.('ready');
  };
  return (
    <FilePreviewImageFrame>
      <div
        ref={exposure}
        className="ov-file-preview__media"
        aria-busy={!loaded}
      >
        {!loaded
          ? (slots?.loadingFallback ?? <LoadingState text={text} />)
          : null}
        {url ? (
          kind === 'image' ? (
            <>
              <button
                className="ov-file-preview__image-button"
                type="button"
                aria-label={`${text.previewImage}: ${source.fileName}`}
                onClick={open}
              >
                <img
                  key={`${url}:${attempt}`}
                  data-ov-preview-image
                  src={url}
                  alt={source.fileName}
                  onLoad={ready}
                  onError={() => fail()}
                />
              </button>
              <dialog
                ref={dialog}
                onClose={() => imageDialog.close()}
                className="ov-file-preview__image-dialog"
                aria-label={source.fileName}
              >
                <img
                  ref={imageDialog.image}
                  src={url}
                  alt={source.fileName}
                  onLoad={() => imageDialog.load()}
                  onError={() => {
                    if (!dialog.current?.open) return;
                    fail(false);
                    imageDialog.fail();
                  }}
                />
                <Button size="small" onClick={() => dialog.current?.close()}>
                  {text.closePreview}
                </Button>
              </dialog>
            </>
          ) : (
            // eslint-disable-next-line jsx-a11y/media-has-caption -- Caller-owned raw videos have no caption track in the source contract.
            <video
              key={`${url}:${attempt}`}
              src={url}
              controls
              controlsList="nodownload"
              preload="metadata"
              aria-label={source.fileName}
              onLoadedMetadata={ready}
              onError={() => fail()}
            />
          )
        ) : null}
      </div>
    </FilePreviewImageFrame>
  );
}
