import { ConsoleModeSwitch } from './console-mode-switch.js';
import type { Locale } from '../../shared/locale.js';
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
  locale: Locale;
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
      <ConsoleModeSwitch
        mode={mode}
        panelId={panelId}
        labels={labels}
        onModeChange={onModeChange}
      />
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
