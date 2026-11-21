import { useEffect, useRef } from 'react';
import { Empty } from '../../shared/primitives/empty/empty.js';
import type { RefObject } from 'react';
import type { ResolvedCommandConsoleLabels } from './command-console-labels.js';
import { getCommandConsoleCommandInputText } from './command-input.js';
import { CommandConsoleParamHint } from './command-console-param-hint.js';
import { useCommandConsoleAssistPosition } from './use-command-console-assist-position.js';
import type { CommandConsoleCompletion } from './use-command-console-completion.js';

export interface CommandConsoleAssistProps {
  anchorRef: RefObject<HTMLDivElement | null>;
  assistId: string;
  labels: ResolvedCommandConsoleLabels;
  completion: CommandConsoleCompletion;
  activeIndex: number;
  onActiveIndexChange: (index: number) => void;
  onPick: (value: string) => void;
  onDismiss: (value: string) => void;
}

function Highlighted({ text, keyword }: { text: string; keyword: string }) {
  const normalized = keyword.trim().toLowerCase();
  const index = normalized ? text.toLowerCase().indexOf(normalized) : -1;
  if (index < 0) return <>{text}</>;
  return (
    <>
      {text.slice(0, index)}
      <mark className="ov-command-console__mark">
        {text.slice(index, index + normalized.length)}
      </mark>
      {text.slice(index + normalized.length)}
    </>
  );
}

/** 候选浮层和联动详情；现代浏览器使用顶层 Popover，保留宿主主题继承。 */
export function CommandConsoleAssist({
  anchorRef,
  assistId,
  labels,
  completion,
  activeIndex,
  onActiveIndexChange,
  onPick,
  onDismiss,
}: CommandConsoleAssistProps) {
  const options = completion.options;
  const keyword = completion.query.keyword;
  const selected = options[activeIndex];
  const command = selected;
  const open = completion.showSuggestions;
  const panelRef = useCommandConsoleAssistPosition(anchorRef, open, onDismiss);
  const listRef = useRef<HTMLDivElement>(null);

  // useEffect 必要性：选中行提交到 DOM 后，调整浏览器列表的实际滚动位置。
  // 候选或键盘序号变化时保持选中项可见；同步滚动不产生订阅，无需清理。
  useEffect(() => {
    const list = listRef.current;
    const option = list?.querySelector<HTMLElement>('[aria-selected="true"]');
    if (!list || !option) return;
    const listBox = list.getBoundingClientRect();
    const optionBox = option.getBoundingClientRect();
    if (optionBox.top < listBox.top)
      list.scrollTop -= listBox.top - optionBox.top;
    if (optionBox.bottom > listBox.bottom)
      list.scrollTop += optionBox.bottom - listBox.bottom;
  }, [activeIndex, options]);

  if (!open) return null;
  return (
    <div
      ref={panelRef}
      className="ov-command-console__assist"
      popover="manual"
      data-details-only={!options.length || undefined}
    >
      {command ? (
        <div className="ov-command-console__assist-detail">
          <CommandConsoleParamHint
            command={command}
            labels={labels}
            onPick={onPick}
          />
        </div>
      ) : null}
      {options.length ? (
        <div
          className="ov-command-console__suggestions"
          ref={listRef}
          role="listbox"
          id={assistId}
          aria-label={labels.suggestions}
        >
          {completion.suggestions.map((section) => (
            <div
              key={section.group.key}
              className="ov-command-console__suggestion-group"
            >
              <div className="ov-command-console__suggestion-label">
                {section.group.label}
              </div>
              {section.commands.map((item) => {
                const index = options.indexOf(item);
                return (
                  <button
                    key={item.name}
                    id={`${assistId}-${index}`}
                    type="button"
                    role="option"
                    aria-selected={item === selected}
                    className="ov-command-console__suggestion"
                    onMouseEnter={() => onActiveIndexChange(index)}
                    onFocus={() => onActiveIndexChange(index)}
                    onMouseDown={(event) => event.preventDefault()}
                    onClick={() =>
                      onPick(`${getCommandConsoleCommandInputText(item)} `)
                    }
                  >
                    <code className="ov-command-console__suggestion-name">
                      <Highlighted text={item.name} keyword={keyword} />
                    </code>
                    <span
                      className="ov-command-console__suggestion-desc"
                      title={item.summary ?? item.description}
                    >
                      <Highlighted
                        text={item.summary ?? item.description}
                        keyword={keyword}
                      />
                    </span>
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      ) : !command ? (
        <Empty
          size="compact"
          illustration="search"
          title={labels.noSuggestions}
          className="ov-command-console__no-suggestions"
        />
      ) : null}
    </div>
  );
}
