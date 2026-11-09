import type { TuiCommandHelpContent } from './types.js';
import type { ResolvedTuiLabels } from './tui-labels.js';

export interface TuiCommandHelpProps {
  content: TuiCommandHelpContent;
  onPick?: (command: string) => void;
  labels: ResolvedTuiLabels;
}

/** 命令帮助：按分组列出命令，点击填入输入框。 */
export function TuiCommandHelp({
  content,
  onPick,
  labels,
}: TuiCommandHelpProps) {
  return (
    <div className="ov-tui__help">
      <div className="ov-tui__help-lead">{labels.availableCommands}</div>
      {content.sections.map((section) => (
        <section key={section.key} className="ov-tui__help-section">
          <div className="ov-tui__help-section-label">{section.label}</div>
          <div className="ov-tui__help-items">
            {section.items.map((item) => (
              <button
                key={item.key}
                type="button"
                className="ov-tui__help-item"
                disabled={!onPick}
                onClick={() => onPick?.(item.name)}
              >
                <span className="ov-tui__help-name">{item.name}</span>
                {item.description ? (
                  <span className="ov-tui__help-desc">{item.description}</span>
                ) : null}
              </button>
            ))}
          </div>
        </section>
      ))}
      <div className="ov-tui__help-footer">{labels.helpHint}</div>
    </div>
  );
}
