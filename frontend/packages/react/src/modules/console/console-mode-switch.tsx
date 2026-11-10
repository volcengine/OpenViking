import { ConsoleIcon } from '../../icons/console-icons.js';
import type { ConsoleMode } from './types.js';

/** Shared mode controls for Console and hosts that own command execution. */
export function ConsoleModeSwitch({
  mode,
  onModeChange,
  labels,
  showConversation = true,
  panelId,
}: {
  mode: ConsoleMode;
  onModeChange: (mode: ConsoleMode) => void;
  labels: { mode: string; conversation: string; command: string };
  showConversation?: boolean;
  panelId?: string;
}) {
  const modes: readonly ConsoleMode[] = showConversation
    ? ['conversation', 'command']
    : ['command'];
  return (
    <div className="ov-console__modes" role="group" aria-label={labels.mode}>
      {modes.map((value) => (
        <button
          className="ov-console__mode"
          type="button"
          key={value}
          aria-label={labels[value]}
          title={labels[value]}
          aria-pressed={mode === value}
          aria-controls={panelId ? `${panelId}-${value}` : undefined}
          onClick={() => onModeChange(value)}
        >
          <ConsoleIcon name={value} />
          {mode === value ? <span>{labels[value]}</span> : null}
        </button>
      ))}
    </div>
  );
}
