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

export function mediaKind(
  source: FilePreviewSource,
): 'image' | 'video' | undefined {
  const type =
    source.fileType?.toLowerCase() || source.file?.type.toLowerCase() || '';
  if (type === 'image' || type.startsWith('image/')) return 'image';
  if (type === 'video' || type.startsWith('video/')) return 'video';
  const extension = (
    source.fileType ||
    source.fileName.split('.').pop() ||
    ''
  ).toLowerCase();
  if (/^(?:png|jpe?g|gif|webp|avif|bmp|ico|svg)$/.test(extension))
    return 'image';
  if (/^(?:mp4|webm|mov)$/.test(extension)) return 'video';
  return undefined;
}

/** Native images/video do not initialize FileViewer or its media adapters. */
export function FilePreviewMedia({
  source,
  kind,
  text,
  slots,
}: {
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
  }>();
  const [attempt, setAttempt] = useState(0);
  const dialog = useRef<HTMLDialogElement>(null);
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
  if (failed)
    return (
      slots?.errorFallback ?? (
        <LoadFailedState
          text={text}
          onRetry={() => {
            setStatus(undefined);
            setAttempt((value) => value + 1);
          }}
        />
      )
    );
  if (!blob && !url)
    return slots?.unsupportedFallback ?? <UnsupportedState text={text} />;
  const loaded = status?.owner === owner && status.loaded;
  const fail = () => setStatus({ owner, failed: true });
  const ready = () => setStatus({ owner, loaded: true });
  return (
    <FilePreviewImageFrame>
      <div className="ov-file-preview__media" aria-busy={!loaded}>
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
                onClick={() => dialog.current?.showModal()}
              >
                <img
                  key={`${url}:${attempt}`}
                  data-ov-preview-image
                  src={url}
                  alt={source.fileName}
                  onLoad={ready}
                  onError={fail}
                />
              </button>
              <dialog
                ref={dialog}
                className="ov-file-preview__image-dialog"
                aria-label={source.fileName}
              >
                <img src={url} alt={source.fileName} onError={fail} />
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
              onError={fail}
            />
          )
        ) : null}
      </div>
    </FilePreviewImageFrame>
  );
}
