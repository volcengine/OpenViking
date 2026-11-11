import { useMemo } from 'react';
import type { KeyboardEvent, RefObject } from 'react';
import {
  getTuiCommandInputText,
  matchTuiCommandInput,
} from './command-input.js';
import type { TuiInlineCompletion } from './command-completion.js';
import type { TuiCompletion } from './use-tui-completion.js';

interface KeyboardOptions {
  inputRef: RefObject<HTMLTextAreaElement | null>;
  value: string;
  history: readonly string[];
  completion: TuiCompletion;
  inline: TuiInlineCompletion | undefined;
  disabled: boolean;
  readOnly: boolean;
  canSubmit: boolean;
  activeIndex: number;
  onActiveIndexChange: (index: number | ((current: number) => number)) => void;
  onValueChange?: (value: string) => void;
  onSubmit?: (command: string) => void;
  onDismiss: (value: string) => void;
  onRequestPalette: () => void;
}

/** 编辑、补全和历史互斥处理；普通 Tab 留在编辑器，Shift+Tab 可移出。 */
export function useTuiComposerKeyboard({
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
}: KeyboardOptions) {
  // Keyboard drafts belong to this history, including its empty/reset state.
  const historyKey = JSON.stringify(history);
  const historyRef = useMemo(
    () => ({ current: { historyKey, offset: -1, draft: '', value: '' } }),
    [historyKey],
  );
  const change = (next: string) => {
    historyRef.current.offset = -1;
    onValueChange?.(next);
  };
  const accept = (next: string) => {
    change(next);
    inputRef.current?.focus({ preventScroll: true });
  };
  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229)
      return;
    const input = event.currentTarget;
    const atEnd =
      input.selectionStart === input.selectionEnd &&
      input.selectionEnd === value.length;
    if (event.key === 'Tab') {
      if (event.shiftKey || event.ctrlKey || event.altKey || event.metaKey)
        return;
      // Tab 属于命令输入，即使没有候选也不交给浏览器移动焦点和滚动页面。
      event.preventDefault();
      if (!atEnd) return;
    }
    const options = completion.options;
    const listOpen = completion.showSuggestions;
    if (event.key === 'Escape' && (inline || listOpen)) {
      event.preventDefault();
      onDismiss(value);
      return;
    }
    if (!disabled && !readOnly) {
      if (event.key === 'Tab' && inline?.kind === 'prefix') {
        event.preventDefault();
        accept(inline.value);
        return;
      }
      if (
        event.key === ' ' &&
        atEnd &&
        inline?.kind === 'prefix' &&
        !event.shiftKey &&
        !event.ctrlKey &&
        !event.altKey &&
        !event.metaKey
      ) {
        event.preventDefault();
        accept(`${inline.value} `);
        return;
      }
      if (event.key === 'Tab' && completion.query.canOpenPalette && !listOpen) {
        event.preventDefault();
        onRequestPalette();
        return;
      }
      if (listOpen && options.length) {
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
          event.preventDefault();
          const delta = event.key === 'ArrowDown' ? 1 : -1;
          onActiveIndexChange((index) =>
            index < 0
              ? delta > 0
                ? 0
                : options.length - 1
              : (index + delta + options.length) % options.length,
          );
          return;
        }
        const spaceCommand =
          event.key === ' ' && atEnd
            ? matchTuiCommandInput(value, options)?.command
            : undefined;
        if (
          event.key === 'Tab' ||
          (event.key === 'Enter' && !event.shiftKey) ||
          spaceCommand
        ) {
          event.preventDefault();
          const picked = spaceCommand ?? options[activeIndex];
          if (picked) {
            accept(`${getTuiCommandInputText(picked)} `);
          } else if (event.key === 'Tab' && inline) {
            accept(inline.value);
          }
          return;
        }
      }
      if (event.key === 'Tab' && inline) {
        event.preventDefault();
        accept(inline.value);
        return;
      }
    }
    const previous = historyRef.current;
    const browsing = previous.offset >= 0 && previous.value === value;
    if (
      !disabled &&
      !readOnly &&
      history.length > 0 &&
      input.selectionStart === input.selectionEnd &&
      !event.shiftKey &&
      ((event.key === 'ArrowUp' &&
        !value.slice(0, input.selectionStart).includes('\n')) ||
        (event.key === 'ArrowDown' &&
          browsing &&
          !value.slice(input.selectionEnd).includes('\n')))
    ) {
      event.preventDefault();
      const offset = Math.max(
        -1,
        Math.min(
          history.length - 1,
          (browsing ? previous.offset : -1) +
            (event.key === 'ArrowUp' ? 1 : -1),
        ),
      );
      const draft = browsing ? previous.draft : value;
      const next = offset < 0 ? draft : history[history.length - 1 - offset]!;
      historyRef.current = { historyKey, offset, draft, value: next };
      onValueChange?.(next);
      onDismiss(next);
      inputRef.current?.focus({ preventScroll: true });
      return;
    }
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      if (value.trim() && !disabled && !readOnly && canSubmit)
        onSubmit?.(value.trim());
    }
  };
  return { change, handleKeyDown };
}
