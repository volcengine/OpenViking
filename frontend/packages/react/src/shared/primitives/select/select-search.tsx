import { useRef, type KeyboardEvent, type RefObject } from 'react';
import { Input } from '../input/input.js';

export function SelectSearch({
  inputRef,
  listboxId,
  label,
  clearLabel,
  placeholder,
  query,
  onQueryChange,
  onKeyDown,
}: {
  inputRef: RefObject<HTMLInputElement | null>;
  listboxId: string;
  label: string;
  clearLabel: string;
  placeholder: string;
  query: string;
  onQueryChange: (query: string) => void;
  onKeyDown: (event: KeyboardEvent<HTMLElement>) => void;
}) {
  const clearRef = useRef<HTMLButtonElement | null>(null);
  return (
    <search className="ov-select__search" aria-label={label}>
      <Input
        ref={inputRef}
        type="search"
        className="ov-select__search-input"
        aria-label={label}
        aria-controls={listboxId}
        placeholder={placeholder}
        autoComplete="off"
        value={query}
        onChange={(event) => onQueryChange(event.currentTarget.value)}
        onKeyDown={(event) => {
          if (
            !event.defaultPrevented &&
            !event.nativeEvent.isComposing &&
            event.key === 'Tab' &&
            !event.shiftKey &&
            query
          ) {
            event.preventDefault();
            clearRef.current?.focus();
          } else onKeyDown(event);
        }}
      />
      {query ? (
        <button
          ref={clearRef}
          type="button"
          className="ov-select__clear"
          aria-label={clearLabel}
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => {
            onQueryChange('');
            inputRef.current?.focus();
          }}
          onKeyDown={(event) => {
            if (event.defaultPrevented || event.nativeEvent.isComposing) return;
            if (event.key === 'Tab' && event.shiftKey) {
              event.preventDefault();
              inputRef.current?.focus();
            } else if (event.key !== 'Enter' && event.key !== ' ')
              onKeyDown(event);
          }}
        >
          <svg aria-hidden="true" width="12" height="12" viewBox="0 0 16 16">
            <path
              d="m4 4 8 8m0-8-8 8"
              stroke="currentColor"
              strokeWidth="1.5"
            />
          </svg>
        </button>
      ) : null}
    </search>
  );
}
