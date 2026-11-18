import { useId, useLayoutEffect, useRef, useState } from 'react';
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
  expanded,
}: {
  details: VideoDetails;
  text: Required<FilePreviewLabels>;
  timeMs: number;
  onSeek: (ms: number) => void;
  expanded: boolean;
}) {
  const id = useId();
  const [selectedTab, setTab] = useState<'transcript' | 'frames'>('transcript');
  const tab = expanded ? 'transcript' : selectedTab;
  const transcript = useRef<HTMLDivElement>(null);
  const activeEntry = useRef<HTMLLIElement>(null);
  const buttons = useRef<Array<HTMLButtonElement | null>>([]);
  const active = details.segments.findIndex(
    (segment) => segment.startMs <= timeMs && timeMs < segment.endMs,
  );
  useLayoutEffect(() => {
    const panel = transcript.current;
    const entry = activeEntry.current;
    if (tab !== 'transcript' || !panel || !entry) return;

    // Scroll only the transcript panel so playback never moves the host page.
    const { top, bottom } = panel.getBoundingClientRect();
    const bounds = entry.getBoundingClientRect();
    if (bounds.top < top || bounds.height > panel.clientHeight) {
      panel.scrollTop += bounds.top - top;
    } else if (bounds.bottom > bottom) {
      panel.scrollTop += bounds.bottom - bottom;
    }
  }, [active, details.segments, expanded, tab]);

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
      {expanded ? (
        <h2 id={`${id}-transcript`} className="ov-file-preview__video-heading">
          {text.videoTranscript}
        </h2>
      ) : (
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
      )}
      <div
        ref={transcript}
        className="ov-file-preview__video-panel"
        id={`${id}-transcript-panel`}
        role={expanded ? 'region' : 'tabpanel'}
        aria-labelledby={`${id}-transcript`}
        hidden={tab !== 'transcript'}
      >
        {details.segments.length ? (
          <ol className="ov-file-preview__video-transcript">
            {details.segments.map((segment, index) => (
              <li key={index} ref={active === index ? activeEntry : undefined}>
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
        className="ov-file-preview__video-panel"
        id={`${id}-frames-panel`}
        role="tabpanel"
        aria-labelledby={`${id}-frames`}
        hidden={tab !== 'frames'}
      >
        {selectedTab === 'frames' && (
          <VideoFrames details={details} text={text} />
        )}
      </div>
    </div>
  );
}
