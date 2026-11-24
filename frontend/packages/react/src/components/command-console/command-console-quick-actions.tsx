import { Tooltip } from '../../shared/primitives/tooltip/index.js';
import type { pickCommandConsoleCommands } from './command-console-empty.js';

export function CommandConsoleQuickActions({
  commands,
  label,
  disabled,
  onPick,
}: {
  commands: ReturnType<typeof pickCommandConsoleCommands>;
  label: string;
  disabled: boolean;
  onPick: (value: string) => void;
}) {
  return (
    <div
      className="ov-command-console__quick-actions"
      role="group"
      aria-label={label}
    >
      {commands.map((command) => (
        <Tooltip
          key={command.name}
          content={command.description}
          placement="top"
          disabled={disabled}
          className="ov-command-console__quick-tooltip"
        >
          <button
            className="ov-command-console__quick-action"
            type="button"
            disabled={disabled}
            onClick={() => onPick(command.value)}
          >
            {command.compactLabel ?? command.name}
          </button>
        </Tooltip>
      ))}
    </div>
  );
}
