import { useRef, useState } from 'react';
import { Button } from '../../shared/primitives/button/button.js';
import type { FilePreviewFileTab, FilePreviewLabels } from './types.js';

type VideoDetails = NonNullable<FilePreviewFileTab['videoDetails']>;
type Frame = VideoDetails['frames'][number];

export function VideoFrames({
  details,
  text,
}: {
  details: VideoDetails;
  text: Required<FilePreviewLabels>;
}) {
  const [selected, setSelected] = useState<Frame>();
  const [failures, setFailures] = useState<ReadonlyMap<string, string>>(
    new Map(),
  );
  const dialog = useRef<HTMLDialogElement>(null);
  const failed = (frame: Frame) =>
    frame.failed || (!!frame.src && failures.get(frame.id) === frame.src);
  function fail(frame: Frame) {
    const { src } = frame;
    if (!src) return;
    setFailures((previous) => new Map(previous).set(frame.id, src));
    if (selected?.id === frame.id && selected.src === frame.src) {
      dialog.current?.close();
      setSelected(undefined);
    }
    details.onFrameError?.(frame.id, src);
  }
  if (!details.frames.length)
    return <p className="ov-file-preview__video-empty">{text.videoNoFrames}</p>;

  return (
    <>
      {details.framesLoading && (
        <p className="ov-file-preview__video-empty" role="status">
          {text.loading}
        </p>
      )}
      <ul
        className="ov-file-preview__video-frames"
        aria-busy={details.framesLoading}
      >
        {details.frames.map((frame) => (
          <li key={frame.id}>
            {frame.src && !failed(frame) ? (
              <button
                type="button"
                aria-label={`${text.previewImage}: ${frame.name}`}
                onClick={() => {
                  setSelected(frame);
                  dialog.current?.showModal();
                }}
              >
                <img
                  key={frame.src}
                  src={frame.src}
                  alt=""
                  loading="lazy"
                  onError={() => fail(frame)}
                />
              </button>
            ) : null}
            <span
              className="ov-file-preview__video-frame-name"
              title={frame.name}
            >
              {frame.name}
            </span>
          </li>
        ))}
      </ul>
      {!details.framesLoading && details.frames.some(failed) && (
        <div className="ov-file-preview__video-empty">
          {text.loadFailed}{' '}
          <Button
            size="small"
            onClick={() => {
              setFailures(new Map());
              details.onLoadFrames?.();
            }}
          >
            {text.retry}
          </Button>
        </div>
      )}
      <dialog
        className="ov-file-preview__image-dialog ov-file-preview__frame-dialog"
        ref={dialog}
        aria-label={selected?.name ?? text.previewImage}
        onClose={() => setSelected(undefined)}
      >
        {selected?.src && (
          <img
            key={selected.src}
            src={selected.src}
            alt={selected.name}
            onError={() => fail(selected)}
          />
        )}
        <Button size="small" onClick={() => dialog.current?.close()}>
          {text.closePreview}
        </Button>
      </dialog>
    </>
  );
}
