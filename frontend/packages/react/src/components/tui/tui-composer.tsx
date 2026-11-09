import { useId, useRef } from 'react';
import type { RefObject } from 'react';

import { getTuiInlineCompletion } from './command-completion.js';
import { useTuiComposerKeyboard } from './use-tui-composer-keyboard.js';
import { TuiInputMirror } from './tui-input-mirror.js';
import type { ResolvedTuiLabels } from './tui-labels.js';
import type { TuiCompletion } from './use-tui-completion.js';

export interface TuiComposerProps {
  anchorRef: RefObject<HTMLDivElement | null>;
  assistId: string;
  onFocus: () => void;
  onDismiss: (value: string) => void;
  onRequestPalette: () => void;
  value: string;
  history: readonly string[];
  labels: ResolvedTuiLabels;
  completion: TuiCompletion;
  disabled: boolean;
  readOnly: boolean;
  canSubmit: boolean;
  activeIndex: number;
  onActiveIndexChange: (index: number | ((current: number) => number)) => void;
  onValueChange?: (value: string) => void;
  onSubmit?: (command: string) => void;
  onOpenUri?: (uri: string) => void;
}

/** 命令输入区：受控输入、键盘补全与提交意图。 */
export function TuiComposer({
  anchorRef,
  assistId,
  onFocus,
  onDismiss,
  onRequestPalette,
  value,
  history,
  labels,
  completion,
  disabled,
  readOnly,
  canSubmit,
  activeIndex,
  onActiveIndexChange,
  onValueChange,
  onSubmit,
  onOpenUri,
}: TuiComposerProps) {
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const inputId = useId();
  const mirrorRef = useRef<HTMLSpanElement>(null);
  const showSuggestions =
    completion.showSuggestions && completion.options.length > 0;
  const preview =
    completion.options[activeIndex] ??
    (completion.query.keyword ? completion.options[0] : undefined);
  const inline = completion.enabled
    ? getTuiInlineCompletion(
        value,
        completion.query,
        preview,
        completion.showSuggestions,
      )
    : undefined;
  const keyboard = useTuiComposerKeyboard({
    inputRef,
    value,
    history,
    completion,
    inline,
    disabled,
    readOnly,
    canSubmit,
    activeIndex,
    onActiveIndexChange,
    onValueChange,
    onSubmit,
    onDismiss,
    onRequestPalette,
  });

  return (
    <div
      ref={anchorRef}
      className="ov-tui__composer"
      data-disabled={disabled || undefined}
    >
      <div className="ov-tui__input-row" data-value={`${value}\n`}>
        <label className="ov-tui__input-label" htmlFor={inputId}>
          {labels.placeholder}
        </label>
        <TuiInputMirror
          value={value}
          input={completion.activeInput}
          inline={inline}
          colorInput={completion.query.colorInput}
          onOpenUri={onOpenUri}
          onScroll={(event) => {
            if (inputRef.current) {
              inputRef.current.scrollTop = event.currentTarget.scrollTop;
              inputRef.current.scrollLeft = event.currentTarget.scrollLeft;
            }
          }}
          mirrorRef={mirrorRef}
          hintId={`${inputId}-hint`}
        />
        <textarea
          id={inputId}
          name="command"
          ref={inputRef}
          className="ov-tui__input"
          rows={1}
          value={value}
          placeholder={labels.placeholder}
          readOnly={readOnly}
          disabled={disabled}
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
          enterKeyHint="send"
          onFocus={onFocus}
          aria-describedby={`${inputId}-shortcuts ${inputId}-hint`}
          aria-autocomplete={inline ? 'both' : 'list'}
          aria-haspopup="listbox"
          aria-controls={showSuggestions ? assistId : undefined}
          aria-activedescendant={
            showSuggestions && activeIndex >= 0
              ? `${assistId}-${activeIndex}`
              : undefined
          }
          onChange={(event) => keyboard.change(event.target.value)}
          onScroll={(event) => {
            if (mirrorRef.current) {
              mirrorRef.current.scrollTop = event.currentTarget.scrollTop;
              mirrorRef.current.scrollLeft = event.currentTarget.scrollLeft;
            }
          }}
          onKeyDown={keyboard.handleKeyDown}
        />
      </div>
      <div className="ov-tui__shortcuts" id={`${inputId}-shortcuts`}>
        <span className="ov-tui__shortcut">
          <kbd>/</kbd>
          {labels.allCommands}
        </span>
        <span className="ov-tui__shortcut">
          <kbd>↑</kbd>
          {labels.historyCommands}
        </span>
        <span className="ov-tui__shortcut">
          <kbd>↵</kbd>
          {labels.send}
        </span>
      </div>
      {disabled ? (
        <div className="ov-tui__unavailable" role="status">
          {labels.unavailableMessage}
        </div>
      ) : null}
    </div>
  );
}
