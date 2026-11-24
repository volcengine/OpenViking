import { useRef } from 'react';
import type { FileBrowserWorkflowLabels } from './preview-workflow-types.js';
export function PreviewHistoryTabs({
  id,
  value,
  text,
  onChange,
}: {
  id: string;
  value: 'preview' | 'source';
  text: FileBrowserWorkflowLabels;
  onChange: (value: 'preview' | 'source') => void;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const modes = ['preview', 'source'] as const;
  return (
    <div
      className="ov-file-history__segmented"
      role="tablist"
      aria-label={text.historyView}
    >
      {modes.map((mode, index) => (
        <button
          key={mode}
          ref={(node) => {
            refs.current[index] = node;
          }}
          type="button"
          role="tab"
          id={`${id}-${mode}`}
          aria-controls={`${id}-panel`}
          aria-selected={value === mode}
          tabIndex={value === mode ? 0 : -1}
          className={
            value === mode ? 'ov-file-history__segment-active' : undefined
          }
          onClick={() => onChange(mode)}
          onKeyDown={(event) => {
            const next =
              event.key === 'Home'
                ? 0
                : event.key === 'End'
                  ? 1
                  : ['ArrowLeft', 'ArrowRight'].includes(event.key)
                    ? 1 - index
                    : undefined;
            if (next !== undefined) {
              event.preventDefault();
              onChange(modes[next]!);
              refs.current[next]?.focus();
            }
          }}
        >
          {mode === 'preview' ? text.historyPreview : text.historyMarkdown}
        </button>
      ))}
    </div>
  );
}
