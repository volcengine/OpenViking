import { ArrowRightIcon } from '../../icons/index.js';
import type { TuiCommandDefinition } from '../../components/tui/index.js';
import type { consoleLabels } from './labels.js';

const quickCommands = ['help', 'ls', 'find', 'read', 'status'];

export function ConsoleEmpty({
  commands,
  labels,
  onSelect,
}: {
  commands: readonly TuiCommandDefinition[];
  labels: ReturnType<typeof consoleLabels>;
  onSelect: (command: string) => void;
}) {
  return (
    <div className="ov-console__empty">
      <div className="ov-console__hero">
        <h2 className="ov-console__title">{labels.commandTitle}</h2>
        <p className="ov-console__subtitle">{labels.commandDescription}</p>
      </div>
      <div className="ov-console__quick-start">
        <p className="ov-console__quick-label">{labels.quickStart}</p>
        <ul className="ov-console__quick-list">
          {quickCommands
            .flatMap((name) =>
              commands.filter((command) => command.name === name),
            )
            .map((command) => (
              <li key={command.name}>
                <button
                  className="ov-console__quick-action"
                  type="button"
                  onClick={() =>
                    onSelect(`${command.inputText ?? command.name} `)
                  }
                >
                  <ArrowRightIcon width={14} height={14} focusable="false" />
                  <span className="ov-console__quick-content">
                    <span className="ov-console__quick-name">
                      {command.name}
                    </span>
                    <span className="ov-console__quick-description">
                      {command.description}
                    </span>
                  </span>
                </button>
              </li>
            ))}
        </ul>
      </div>
    </div>
  );
}
