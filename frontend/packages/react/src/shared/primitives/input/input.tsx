/**
 * Adapted from Arco Design Input (MIT), commit 4e087a0fe7d5ae2f222fbce4a9518ba047b01772.
 * https://github.com/arco-design/arco-design/tree/4e087a0fe7d5ae2f222fbce4a9518ba047b01772/components/Input
 * Copyright (c) 2021 Bytedance, Inc. and its affiliates. See LICENSE.arco-design.
 * Native value/events/ref and affixes only. No password toggles, groups, clear buttons,
 * word counters, auto-width/auto-size, value truncation or ConfigProvider.
 * Visual rules come from the console's theme-ve-o-aml-design@0.0.36 skin.
 */
import {
  forwardRef,
  type InputHTMLAttributes,
  type ReactNode,
  type TextareaHTMLAttributes,
} from 'react';

export interface InputProps extends Omit<
  InputHTMLAttributes<HTMLInputElement>,
  'size' | 'prefix' | 'type'
> {
  type?:
    | 'text'
    | 'search'
    | 'email'
    | 'url'
    | 'tel'
    | 'password'
    | 'number'
    | 'time';
  size?: 'mini' | 'small' | 'default' | 'large';
  prefix?: ReactNode;
  suffix?: ReactNode;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { size = 'default', prefix, suffix, className, style, disabled, ...rest },
  ref,
) {
  return (
    <span
      role="presentation"
      className={[
        'ov-input',
        `ov-input--${size}`,
        disabled && 'ov-input--disabled',
        className,
      ]
        .filter(Boolean)
        .join(' ')}
      style={style}
      onMouseDown={(event) => {
        if (event.target !== event.currentTarget.querySelector('input'))
          event.preventDefault();
      }}
      onClick={(event) => {
        const input = event.currentTarget.querySelector('input');
        if (!disabled && !event.defaultPrevented && event.target !== input)
          input?.focus();
      }}
    >
      {prefix != null ? (
        <span className="ov-input__affix" aria-hidden="true">
          {prefix}
        </span>
      ) : null}
      <input
        {...rest}
        ref={ref}
        disabled={disabled}
        className="ov-input__element"
      />
      {suffix != null ? (
        <span className="ov-input__affix" aria-hidden="true">
          {suffix}
        </span>
      ) : null}
    </span>
  );
});

export const TextArea = forwardRef<
  HTMLTextAreaElement,
  TextareaHTMLAttributes<HTMLTextAreaElement>
>(function TextArea({ className, ...rest }, ref) {
  return (
    <textarea
      {...rest}
      ref={ref}
      className={['ov-textarea', className].filter(Boolean).join(' ')}
    />
  );
});
