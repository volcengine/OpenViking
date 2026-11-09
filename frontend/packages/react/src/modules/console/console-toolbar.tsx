import { useState } from 'react';
import type { ReactNode } from 'react';
import type { TuiRecord } from '../../components/tui/index.js';
import { ConsoleIcon } from '../../icons/console-icons.js';
import { Tooltip } from '../../shared/components/tooltip/index.js';
import { ConsoleHistory } from './console-history.js';
import type { ConsoleMode } from './types.js';
import type { consoleLabels } from './labels.js';

export function ConsoleToolbar({
  mode,
  panelId,
  locale,
  labels,
  records,
  isExecuting,
  onModeChange,
  onClear,
  onRecall,
  children,
  conversationActions,
}: {
  mode: ConsoleMode;
  panelId: string;
  locale: 'en' | 'zh';
  labels: ReturnType<typeof consoleLabels>;
  records: readonly TuiRecord[];
  isExecuting: boolean;
  onModeChange: (mode: ConsoleMode) => void;
  onClear: () => void;
  onRecall: (command: string) => void;
  children?: ReactNode;
  conversationActions?: ReactNode;
}) {
  const [sessionVersion, setSessionVersion] = useState(0);
  return (
    <div className="ov-console__toolbar">
      <div className="ov-console__modes" role="group" aria-label={labels.mode}>
        {(['conversation', 'command'] as const).map((value) => (
          <button
            className="ov-console__mode"
            type="button"
            key={value}
            aria-label={labels[value]}
            title={labels[value]}
            aria-pressed={mode === value}
            aria-controls={`${panelId}-${value}`}
            onClick={() => onModeChange(value)}
          >
            <ConsoleIcon name={value} />
            {mode === value ? <span>{labels[value]}</span> : null}
          </button>
        ))}
      </div>
      {children ? (
        <div className="ov-console__toolbar-slot">{children}</div>
      ) : null}
      {mode === 'command' ? (
        <div className="ov-console__actions">
          <ConsoleHistory
            key={sessionVersion}
            records={records}
            isExecuting={isExecuting}
            locale={locale}
            labels={labels}
            onRecall={onRecall}
          />
          <Tooltip
            content={labels.clear}
            className="ov-console__action-tooltip"
          >
            <button
              className="ov-console__action"
              type="button"
              aria-label={labels.clear}
              onClick={() => {
                setSessionVersion((version) => version + 1);
                onClear();
              }}
            >
              <ConsoleIcon name="newSession" />
            </button>
          </Tooltip>
        </div>
      ) : (
        conversationActions
      )}
    </div>
  );
}
