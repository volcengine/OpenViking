import { InputFrame } from '../input/input-frame.js';
import type { RefCallback } from 'react';
import { optionText } from './collection.js';
import { useTagCount } from './use-tag-count.js';
import type { SelectProps, SelectTriggerState, SelectValue } from './types.js';
import en from './locales/en-US.js';
import zh from './locales/zh-CN.js';

export function SelectTrigger<V extends SelectValue>({
  state,
  size,
  variant,
  multiple,
  disabled,
  renderValue,
  maxTagCount = 2,
  locale,
  onRemove,
  frameRef,
}: Pick<
  SelectProps<V>,
  'size' | 'variant' | 'multiple' | 'disabled' | 'renderValue' | 'maxTagCount'
> & {
  state: SelectTriggerState<V>;
  locale: 'en-US' | 'zh-CN';
  onRemove: (value: V) => void;
  frameRef: RefCallback<HTMLElement>;
}) {
  const { triggerRef, triggerProps, content, selectedOptions } = state;
  const { className, style, ...attributes } = triggerProps;
  const { tagRef, count } = useTagCount(selectedOptions.length, maxTagCount);
  const visible = selectedOptions.slice(0, count);
  const hidden = selectedOptions.slice(visible.length);
  const tags =
    variant !== 'simple' &&
    multiple &&
    !renderValue &&
    selectedOptions.length > 0;
  const button = (
    <button
      {...attributes}
      ref={triggerRef}
      type="button"
      className={[
        'ov-select__trigger',
        variant === 'simple' && 'ov-select',
        variant === 'simple' && 'ov-select--simple',
        variant === 'simple' && className,
        tags && 'ov-select__trigger--tags',
      ]
        .filter(Boolean)
        .join(' ')}
      style={variant === 'simple' ? style : undefined}
    >
      <span
        className={tags ? 'ov-select__accessible-value' : 'ov-select__value'}
      >
        {content}
      </span>
      <svg
        className="ov-select__arrow"
        aria-hidden="true"
        width="14"
        height="14"
        viewBox="0 0 16 16"
        fill="none"
      >
        <path
          d="m4 6 4 4 4-4"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </button>
  );
  if (variant === 'simple') return button;
  const text = locale === 'zh-CN' ? zh : en;
  return (
    <InputFrame
      ref={frameRef}
      size={size}
      disabled={disabled}
      data-open={state.open || undefined}
      className={['ov-select', tags && 'ov-select--tags', className]
        .filter(Boolean)
        .join(' ')}
      style={style}
      onClick={(event) => {
        if (!(event.target as Element).closest('button'))
          (
            event.currentTarget.querySelector(
              '.ov-select__trigger',
            ) as HTMLButtonElement
          )?.click();
      }}
    >
      {button}
      {tags ? (
        <span className="ov-select__tags" ref={tagRef}>
          {visible.map((option) => (
            <span key={option.value} className="ov-select__tag">
              <span>{option.label}</span>
              <button
                type="button"
                disabled={disabled || option.disabled}
                aria-label={text.remove(
                  optionText(option.label) || String(option.value),
                )}
                onClick={() => onRemove(option.value)}
              >
                <svg
                  aria-hidden="true"
                  width="12"
                  height="12"
                  viewBox="0 0 16 16"
                >
                  <path
                    d="m4 4 8 8m0-8-8 8"
                    stroke="currentColor"
                    strokeWidth="1.5"
                  />
                </svg>
              </button>
            </span>
          ))}
          {hidden.length ? (
            <span
              className="ov-select__tag ov-select__tag-count"
              aria-label={text.more(hidden.length)}
              title={hidden
                .map((option) => optionText(option.label))
                .join(', ')}
            >
              +{hidden.length}
            </span>
          ) : null}
        </span>
      ) : null}
    </InputFrame>
  );
}
