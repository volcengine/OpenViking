import { opaqueIdentity } from '../../shared/monitoring/clock.js';
import { useMediaMonitoring } from './use-media-monitoring.js';
import { useRenderedMonitoring } from '../../shared/monitoring/context.js';
import type { FileViewerState } from '@ark-design/ui/file-viewer/core';
import { useExposure } from '../../shared/analytics/use-exposure.js';
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import type {
  FilePreviewFileTab,
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
import { FilePreviewNativeImage } from './file-preview-native-image.js';
import { FilePreviewVideoLayout } from './file-preview-video-layout.js';

export { mediaKind } from './media-kind.js';

/** Native images/video do not initialize FileViewer or its media adapters. */
export function FilePreviewMedia({
  source,
  kind,
  videoDetails,
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
  videoDetails?: FilePreviewFileTab['videoDetails'];
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
  const video = useRef<HTMLVideoElement>(null);
  const [playhead, setPlayhead] = useState<{
    owner: typeof owner;
    ms: number;
  }>();
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
  const ready = () => {
    setStatus({ owner, loaded: true });
    onStateChange?.('ready');
  };
  const details = kind === 'video' && url ? videoDetails : undefined;
  const media = (toggle?: ReactNode) => (
    <div
      ref={exposure}
      className={
        details
          ? 'ov-file-preview__media ov-file-preview__media--video-details'
          : 'ov-file-preview__media'
      }
      aria-busy={!loaded}
    >
      {!loaded && (slots?.loadingFallback ?? <LoadingState text={text} />)}
      {url &&
        (kind === 'image' ? (
          <FilePreviewNativeImage
            attempt={attempt}
            identity={identity}
            reports={reports}
            fileName={source.fileName}
            url={url}
            text={text}
            onReady={ready}
            onFail={fail}
          />
        ) : (
          // eslint-disable-next-line jsx-a11y/media-has-caption -- Caller-owned raw videos have no caption track in the source contract.
          <video
            ref={video}
            key={`${url}:${attempt}`}
            src={url}
            controls
            controlsList={details ? 'nodownload nofullscreen' : 'nodownload'}
            preload="metadata"
            aria-label={source.fileName}
            onLoadedMetadata={ready}
            onTimeUpdate={
              details
                ? (event) =>
                    setPlayhead({
                      owner,
                      ms: event.currentTarget.currentTime * 1000,
                    })
                : undefined
            }
            onError={() => fail()}
          />
        ))}
      {toggle}
    </div>
  );
  return (
    <FilePreviewImageFrame>
      {kind === 'video' && url ? (
        <FilePreviewVideoLayout
          key={url}
          details={details}
          fileName={source.fileName}
          text={text}
          ready={Boolean(loaded)}
          timeMs={playhead?.owner === owner ? playhead.ms : 0}
          player={video}
        >
          {media}
        </FilePreviewVideoLayout>
      ) : (
        media()
      )}
    </FilePreviewImageFrame>
  );
}
