/** Adapted from Arco Design Switch 2.66.18 (MIT). Copyright (c) 2021 Bytedance,
 * Inc. and its affiliates. See LICENSE.arco-design. Retains the native button,
 * checked/defaultChecked, size, loading and change semantics used by our controls.
 * https://github.com/arco-design/arco-design/tree/2.66.18/components/Switch
 */
import {
  forwardRef,
  useState,
  type ButtonHTMLAttributes,
  type MouseEvent,
} from 'react';
import { LoadingIcon } from '../loading-icon/loading-icon.js';
export interface SwitchProps extends Omit<
  ButtonHTMLAttributes<HTMLButtonElement>,
  'onChange'
> {
  checked?: boolean;
  defaultChecked?: boolean;
  size?: 'small' | 'medium';
  loading?: boolean;
  onChange?: (checked: boolean, event: MouseEvent<HTMLButtonElement>) => void;
}
export const Switch = forwardRef<HTMLButtonElement, SwitchProps>(
  function Switch(
    {
      checked,
      defaultChecked = false,
      size = 'medium',
      loading = false,
      disabled,
      className,
      onChange,
      onClick,
      ...props
    },
    ref,
  ) {
    const [local, setLocal] = useState(defaultChecked);
    const value = checked ?? local;
    return (
      <button
        {...props}
        ref={ref}
        type="button"
        role="switch"
        aria-checked={value}
        aria-busy={loading || undefined}
        disabled={disabled}
        tabIndex={loading ? -1 : props.tabIndex}
        className={[
          'ov-switch',
          `ov-switch--${size}`,
          value && 'ov-switch--checked',
          className,
        ]
          .filter(Boolean)
          .join(' ')}
        onClick={(event) => {
          if (loading) return;
          onClick?.(event);
          if (checked === undefined) setLocal(!value);
          onChange?.(!value, event);
        }}
      >
        <span className="ov-switch__dot">
          {loading ? <LoadingIcon /> : null}
        </span>
      </button>
    );
  },
);
