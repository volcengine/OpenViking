/**
 * Adapted from Arco Design Radio (MIT), commit 4e087a0fe7d5ae2f222fbce4a9518ba047b01772.
 * https://github.com/arco-design/arco-design/tree/4e087a0fe7d5ae2f222fbce4a9518ba047b01772/components/Radio
 * Copyright (c) 2021 Bytedance, Inc. and its affiliates. See LICENSE.arco-design.
 * Keeps native input semantics, controlled/uncontrolled value and the label wrapper;
 * omits groups, the button variant, ConfigProvider, RTL and the icon-hover ripple.
 * Visual rules come from the console's theme-ve-o-aml-design skin.
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
export interface RadioProps extends Omit<
  LabelHTMLAttributes<HTMLLabelElement>,
  'onChange'
> {
  /** 受控选中态。Controlled checked state. */
  checked?: boolean;
  /** 非受控初始选中态。Initial checked state when uncontrolled. */
  defaultChecked?: boolean;
  /** 同组内的取值，原样回传给 `onChange`。Value within the group, passed back to `onChange`. */
  value?: string;
  /** 同组名称；同一 `name` 下的实例由浏览器保证互斥。Group name; instances sharing it are mutually exclusive. */
  name?: string;
  /** 禁用。Disables the control. */
  disabled?: boolean;
  /** 选中态变化。Called when this radio becomes selected. */
  onChange?: (checked: boolean, event: ChangeEvent<HTMLInputElement>) => void;
  children?: ReactNode;
}

/**
 * 单选框。同一 `name` 下的多个实例由浏览器保证互斥——不需要外层的 Group 组件，
 * 也就没有 `Radio.Group` 这种嵌套导出。
 *
 * A radio. Instances sharing a `name` are mutually exclusive by browser behaviour, so no
 * wrapping Group component is needed and there is no `Radio.Group` nested export.
 */
export function Radio({
  checked,
  defaultChecked,
  value,
  name,
  onChange,
  children,
  className,
  disabled,
  ...rest
}: RadioProps) {
  const [internal, setInternal] = useState(defaultChecked ?? false);
  const isControlled = checked !== undefined;
  const isChecked = isControlled ? checked : internal;

  const handleChange = (event: ChangeEvent<HTMLInputElement>) => {
    if (disabled) return;
    if (!isControlled) setInternal(true);
    onChange?.(true, event);
  };

  return (
    <label
      {...rest}
      className={[
        'ov-radio',
        isChecked && 'ov-radio--checked',
        disabled && 'ov-radio--disabled',
        className,
      ]
        .filter(Boolean)
        .join(' ')}
      aria-disabled={disabled || undefined}
    >
      <input
        type="radio"
        name={name}
        value={value ?? ''}
        disabled={disabled}
        checked={isChecked}
        onChange={handleChange}
      />
      <span className="ov-radio__mask" aria-hidden="true" />
      {children === undefined ? null : (
        <span className="ov-radio__text">{children}</span>
      )}
    </label>
  );
}
