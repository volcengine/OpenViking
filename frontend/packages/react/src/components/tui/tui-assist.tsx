import type { ResolvedTuiLabels } from './tui-labels.js';
import { TuiParamHint } from './tui-param-hint.js';
import type { TuiCompletion } from './use-tui-completion.js';

export interface TuiAssistProps {
  value: string;
  labels: ResolvedTuiLabels;
  completion: TuiCompletion;
  activeIndex: number;
  onActiveIndexChange: (index: number) => void;
  onPick: (value: string) => void;
}

function Highlighted({ text, keyword }: { text: string; keyword: string }) {
  const normalized = keyword.trim().toLowerCase();
  const index = normalized ? text.toLowerCase().indexOf(normalized) : -1;
  if (index < 0) return <>{text}</>;

  return (
    <>
      {text.slice(0, index)}
      <mark className="ov-tui__mark">
        {text.slice(index, index + normalized.length)}
      </mark>
      {text.slice(index + normalized.length)}
    </>
  );
}

/**
 * 输入辅助面板：命令名候选、无匹配提示或参数帮助。它吸附在记录区底部，
 * 不改变输入区位置，也不会溢出组件边界。
 *
 * Input assistance: command-name suggestions, the no-match message or the
 * parameter panel. It docks at the bottom of the record area, so the composer
 * never moves and the panel cannot overflow the component.
 */
export function TuiAssist({
  value,
  labels,
  completion,
  activeIndex,
  onActiveIndexChange,
  onPick,
}: TuiAssistProps) {
  const options = completion.options;
  const keyword = value.replace(/^\s*\//, '').split(/\s/)[0] ?? '';

  if (completion.showParamHint && completion.activeCommand) {
    return (
      <div className="ov-tui__assist">
        <TuiParamHint
          command={completion.activeCommand}
          labels={labels}
          onPick={onPick}
        />
      </div>
    );
  }

  if (options.length > 0) {
    return (
      <div className="ov-tui__assist">
        <div
          className="ov-tui__suggestions"
          role="listbox"
          aria-label={labels.suggestions}
        >
          {completion.suggestions.map((section) => (
            <div key={section.group.key} className="ov-tui__suggestion-group">
              <div className="ov-tui__suggestion-label">
                {section.group.label}
              </div>
              {section.commands.map((command) => {
                const index = options.indexOf(command);
                return (
                  <button
                    key={command.name}
                    type="button"
                    role="option"
                    aria-selected={index === activeIndex}
                    className="ov-tui__suggestion"
                    onMouseEnter={() => onActiveIndexChange(index)}
                    onMouseDown={(event) => event.preventDefault()}
                    onClick={() => onPick(`/${command.name} `)}
                  >
                    <code className="ov-tui__suggestion-name">
                      <Highlighted
                        text={`/${command.name}`}
                        keyword={keyword}
                      />
                    </code>
                    <span className="ov-tui__suggestion-desc">
                      {command.description}
                    </span>
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (completion.showTypingHint) {
    return (
      <div className="ov-tui__assist">
        <div className="ov-tui__no-suggestions" role="status">
          {labels.noSuggestions}
        </div>
      </div>
    );
  }

  return null;
}
