import { forwardRef, useContext } from 'react';
import { SelectContext } from './select-context.js';
import type { SelectOptionProps } from './types.js';

export const SelectOption = forwardRef<HTMLButtonElement, SelectOptionProps>(
  function SelectOption(
    { value, label: _label, className, disabled, onClick, children, ...props },
    ref,
  ) {
    const context = useContext(SelectContext);
    const selected = context?.values.includes(value) ?? false;
    return (
      <button
        {...props}
        ref={ref}
        type="button"
        role="option"
        aria-selected={selected}
        disabled={disabled || context?.disabled}
        tabIndex={-1}
        className={['ov-select__option', className].filter(Boolean).join(' ')}
        onClick={(event) => {
          onClick?.(event);
          if (!event.defaultPrevented) context?.choose(value);
        }}
      >
        <span className="ov-select__option-content">{children}</span>
        <svg
          className="ov-select__check"
          aria-hidden="true"
          width="14"
          height="14"
          viewBox="0 0 16 16"
          fill="none"
        >
          {selected ? (
            <path
              d="M3 8.5 6.5 12 13 4.5"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          ) : null}
        </svg>
      </button>
    );
  },
);
