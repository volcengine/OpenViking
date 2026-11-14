import type { CommandConsoleCommandHelpContent } from './types.js';
import type { ResolvedCommandConsoleLabels } from './command-console-labels.js';

export interface CommandConsoleCommandHelpProps {
  content: CommandConsoleCommandHelpContent;
  onPick?: (command: string) => void;
  labels: ResolvedCommandConsoleLabels;
}

/** 命令帮助：按分组列出命令，点击填入输入框。 */
export function CommandConsoleCommandHelp({
  content,
  onPick,
  labels,
}: CommandConsoleCommandHelpProps) {
  return (
    <div className="ov-command-console__help">
      <div className="ov-command-console__help-lead">
        {labels.availableCommands}
      </div>
      {content.sections.map((section) => (
        <section key={section.key} className="ov-command-console__help-section">
          <div className="ov-command-console__help-section-label">
            {section.label}
          </div>
          <div className="ov-command-console__help-items">
            {section.items.map((item) => (
              <button
                key={item.key}
                type="button"
                className="ov-command-console__help-item"
                disabled={!onPick}
                onClick={() => onPick?.(item.name)}
              >
                <span className="ov-command-console__help-name">
                  {item.name}
                </span>
                {item.description ? (
                  <span className="ov-command-console__help-desc">
                    {item.description}
                  </span>
                ) : null}
              </button>
            ))}
          </div>
        </section>
      ))}
      <div className="ov-command-console__help-footer">{labels.helpHint}</div>
    </div>
  );
}
