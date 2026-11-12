/**
 * Adapted from Arco Design Checkbox (MIT), commit 4e087a0fe7d5ae2f222fbce4a9518ba047b01772.
 * https://github.com/arco-design/arco-design/tree/4e087a0fe7d5ae2f222fbce4a9518ba047b01772/components/Checkbox
 * Copyright (c) 2021 Bytedance, Inc. and its affiliates. See LICENSE.arco-design.
 * Keeps native input semantics, controlled/uncontrolled value, indeterminate and a
 * replaceable icon; omits groups, ConfigProvider, RTL, icon-hover ripples and the
 * function-as-children render prop. Visual rules come from the console's
 * theme-ve-o-aml-design skin.
 */
import { useState } from 'react';
import type { ChangeEvent, LabelHTMLAttributes, ReactNode } from 'react';

/**
 * 属性落在 label 上而不是 input 上：调用方给的 `className`、`title`、`data-*` 和
 * 事件都作用于整行，这也与 Arco 的行为一致。
 *
 * Props land on the label rather than the input: a caller's `className`, `title`,
 * `data-*` and handlers all address the whole row, matching Arco.
 */
export interface CheckboxProps extends Omit<
  LabelHTMLAttributes<HTMLLabelElement>,
  'onChange'
> {
  /** 受控选中态。Controlled checked state. */
  checked?: boolean;
  /** 非受控初始选中态。Initial checked state when uncontrolled. */
  defaultChecked?: boolean;
  /** 半选态，仅影响图标，不改变 `checked`。Indeterminate state; affects the icon only. */
  indeterminate?: boolean;
  /** 禁用。Disables the control. */
  disabled?: boolean;
  /** 自定义勾选图标。Replaces the check icon. */
  icon?: ReactNode;
  /** 选中态变化。Called with the next checked state. */
  onChange?: (checked: boolean, event: ChangeEvent<HTMLInputElement>) => void;
  children?: ReactNode;
}

/**
 * 复选框。原生 input 承担状态与键盘行为，外观由一层遮罩绘制——这样点击 label、
 * 空格键切换、屏幕阅读器读到的都还是真正的复选框。
 *
 * A checkbox. The native input owns state and keyboard behaviour while a mask draws
 * the appearance, so clicking the label, toggling with Space and what a screen reader
 * announces all still go through a real checkbox.
 */
export function Checkbox({
  checked,
  defaultChecked,
  indeterminate = false,
  icon,
  onChange,
  children,
  className,
  disabled,
  ...rest
}: CheckboxProps) {
  const [internal, setInternal] = useState(defaultChecked ?? false);
  const isControlled = checked !== undefined;
  const isChecked = isControlled ? checked : internal;

  const handleChange = (event: ChangeEvent<HTMLInputElement>) => {
    if (!isControlled) setInternal(event.target.checked);
    onChange?.(event.target.checked, event);
  };

  return (
    <label
      {...rest}
      className={[
        'ov-checkbox',
        isChecked && 'ov-checkbox--checked',
        indeterminate && 'ov-checkbox--indeterminate',
        disabled && 'ov-checkbox--disabled',
        className,
      ]
        .filter(Boolean)
        .join(' ')}
      aria-disabled={disabled || undefined}
    >
      <input
        type="checkbox"
        disabled={disabled}
        checked={isChecked}
        onChange={handleChange}
      />
      <span className="ov-checkbox__mask" aria-hidden="true">
        {icon ?? (
          <svg viewBox="0 0 1024 1024" fill="currentColor" focusable="false">
            <path d="M877.45 206.1a64.73 64.73 0 0 0-95.15 4.01L380.73 685.47 235.23 533.49a64.73 64.73 0 0 0-92.43-1.04l-45.83 45.83a64.73 64.73 0 0 0-.9 90.62l239.62 250.1a64.73 64.73 0 0 0 71.2 15.15 64.34 64.34 0 0 0 35.08-21.23l36.25-42.72 40.52-40.78-3.37-3.49 411.4-486.75a64.73 64.73 0 0 0-3.88-87.64l-45.31-45.44z" />
          </svg>
        )}
      </span>
      {children === undefined ? null : (
        <span className="ov-checkbox__text">{children}</span>
      )}
    </label>
  );
}
