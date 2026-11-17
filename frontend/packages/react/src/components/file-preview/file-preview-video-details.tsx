import { useId, useRef, useState } from 'react';
import { VideoFrames } from './frames.js';
import type { FilePreviewFileTab, FilePreviewLabels } from './types.js';

type VideoDetails = NonNullable<FilePreviewFileTab['videoDetails']>;

function formatTime(ms: number) {
  const total = Math.floor(ms / 1000);
  return [Math.floor(total / 60), total % 60]
    .map((part) => String(part).padStart(2, '0'))
    .join(':');
}

export function FilePreviewVideoDetails({
  details,
  text,
  timeMs,
  onSeek,
}: {
  details: VideoDetails;
  text: Required<FilePreviewLabels>;
  timeMs: number;
  onSeek: (ms: number) => void;
}) {
  const id = useId();
  const [tab, setTab] = useState<'transcript' | 'frames'>('transcript');
  const buttons = useRef<Array<HTMLButtonElement | null>>([]);
  const active = details.segments.findIndex(
    (segment) => segment.startMs <= timeMs && timeMs < segment.endMs,
  );
  const items = [
    { key: 'transcript', label: text.videoTranscript },
    { key: 'frames', label: text.videoFrames(details.frames.length) },
  ] as const;

  function select(key: typeof tab) {
    setTab(key);
    if (key === 'frames') details.onLoadFrames?.();
  }

  return (
    <div className="ov-file-preview__video-details">
      <div
        className="ov-file-preview__video-tabs"
        role="tablist"
        aria-label={text.previewMode}
      >
        {items.map(({ key, label }, index) => (
          <button
            key={key}
            ref={(node) => {
              buttons.current[index] = node;
            }}
            type="button"
            role="tab"
            id={`${id}-${key}`}
            aria-controls={`${id}-${key}-panel`}
            aria-selected={tab === key}
            tabIndex={tab === key ? 0 : -1}
            onClick={() => select(key)}
            onKeyDown={(event) => {
              const next =
                event.key === 'Home'
                  ? 0
                  : event.key === 'End'
                    ? 1
                    : event.key === 'ArrowLeft' || event.key === 'ArrowRight'
                      ? 1 - index
                      : undefined;
              if (next === undefined) return;
              event.preventDefault();
              select(next === 0 ? 'transcript' : 'frames');
              buttons.current[next]?.focus();
            }}
          >
            {label}
          </button>
        ))}
      </div>
      <div
        id={`${id}-transcript-panel`}
        role="tabpanel"
        aria-labelledby={`${id}-transcript`}
        hidden={tab !== 'transcript'}
      >
        {details.segments.length ? (
          <ol className="ov-file-preview__video-transcript">
            {details.segments.map((segment, index) => (
              <li key={index}>
                <button
                  type="button"
                  aria-current={active === index || undefined}
                  onClick={() => onSeek(segment.startMs)}
                >
                  <span>
                    {segment.speaker || text.videoSpeaker} ·{' '}
                    {formatTime(segment.startMs)}–{formatTime(segment.endMs)}
                  </span>
                  {segment.text}
                </button>
              </li>
            ))}
          </ol>
        ) : (
          <p className="ov-file-preview__video-empty">
            {text.videoNoTranscript}
          </p>
        )}
      </div>
      <div
        id={`${id}-frames-panel`}
        role="tabpanel"
        aria-labelledby={`${id}-frames`}
        hidden={tab !== 'frames'}
      >
        {tab === 'frames' && <VideoFrames details={details} text={text} />}
      </div>
    </div>
  );
}
