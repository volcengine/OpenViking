import { useEffect, useRef } from 'react';
import type { KeyboardEvent } from 'react';

import type { ResolvedTuiLabels } from './tui-labels.js';
import type { TuiCompletion } from './use-tui-completion.js';

export interface TuiComposerProps {
  value: string;
  labels: ResolvedTuiLabels;
  completion: TuiCompletion;
  disabled: boolean;
  readOnly: boolean;
  canSubmit: boolean;
  activeIndex: number;
  onActiveIndexChange: (index: number | ((current: number) => number)) => void;
  onValueChange?: (value: string) => void;
  onSubmit?: (command: string) => void;
}

/** 命令输入区：受控输入、键盘补全与发送。辅助面板在记录区底部展示。 */
export function TuiComposer({
  value,
  labels,
  completion,
  disabled,
  readOnly,
  canSubmit,
  activeIndex,
  onActiveIndexChange,
  onValueChange,
  onSubmit,
}: TuiComposerProps) {
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const options = completion.options;
  const keyword = value.replace(/^\s*\//, '').split(/\s/)[0] ?? '';
  const showSuggestions = options.length > 0;

  useEffect(() => {
    onActiveIndexChange(0);
  }, [keyword, onActiveIndexChange]);

  const fill = (next: string) => {
    onValueChange?.(next);
    inputRef.current?.focus();
  };

  const submit = () => {
    const command = value.trim();
    if (!command || disabled || !canSubmit) return;
    onSubmit?.(command);
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (showSuggestions) {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        const delta = event.key === 'ArrowDown' ? 1 : -1;
        onActiveIndexChange(
          (index) => (index + delta + options.length) % options.length,
        );
        return;
      }
      if (event.key === 'Tab' || event.key === 'Enter') {
        const picked = options[activeIndex];
        if (picked) {
          event.preventDefault();
          fill(`/${picked.name} `);
        }
        return;
      }
    }

    if (
      event.key === 'Enter' &&
      !event.shiftKey &&
      !event.nativeEvent.isComposing
    ) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <div className="ov-tui__composer">
      <div className="ov-tui__input-row">
        <span className="ov-tui__prompt" aria-hidden="true">
          &gt;_
        </span>
        <textarea
          ref={inputRef}
          className="ov-tui__input"
          rows={1}
          value={value}
          placeholder={labels.placeholder}
          readOnly={readOnly || disabled}
          aria-label={labels.placeholder}
          onChange={(event) => onValueChange?.(event.target.value)}
          onKeyDown={handleKeyDown}
        />
        <button
          type="button"
          className="ov-tui__send"
          disabled={disabled || !canSubmit || !value.trim()}
          onClick={submit}
        >
          {labels.send}
        </button>
      </div>
      {disabled ? (
        <div className="ov-tui__unavailable" role="status">
          {labels.unavailableMessage}
        </div>
      ) : null}
    </div>
  );
}
