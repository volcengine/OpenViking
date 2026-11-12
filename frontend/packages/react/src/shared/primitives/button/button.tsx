/**
 * Adapted from Arco Design Button (MIT), commit 4e087a0fe7d5ae2f222fbce4a9518ba047b01772.
 * https://github.com/arco-design/arco-design/tree/4e087a0fe7d5ae2f222fbce4a9518ba047b01772/components/Button
 * Copyright (c) 2021 Bytedance, Inc. and its affiliates. See LICENSE.arco-design.
 * Keeps native events/ref, variants, sizes, icons and loading; omits links,
 * groups, status palettes, Chinese-character spacing and ConfigProvider coupling.
 * Visual rules come from the console's theme-ve-o-aml-design@0.0.36 skin.
 */
import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react';

export interface ButtonProps extends Omit<
  ButtonHTMLAttributes<HTMLButtonElement>,
  'type'
> {
  type?: 'default' | 'primary' | 'secondary' | 'outline' | 'text';
  htmlType?: 'button' | 'submit' | 'reset';
  size?: 'mini' | 'small' | 'default' | 'large';
  shape?: 'square' | 'circle';
  icon?: ReactNode;
  iconOnly?: boolean;
  loading?: boolean;
  long?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  function Button(
    {
      type = 'default',
      htmlType = 'button',
      size = 'default',
      shape = 'square',
      icon,
      iconOnly,
      loading,
      long,
      disabled,
      className,
      children,
      onClick,
      ...rest
    },
    ref,
  ) {
    const inactive =
      disabled ||
      loading ||
      rest['aria-disabled'] === true ||
      rest['aria-disabled'] === 'true';
    const variant = type === 'default' ? 'secondary' : type;
    const onlyIcon = iconOnly || (icon != null && children == null);
    return (
      <button
        {...rest}
        ref={ref}
        type={htmlType}
        disabled={disabled || loading}
        aria-busy={loading || rest['aria-busy']}
        className={[
          'ov-button',
          `ov-button--${variant}`,
          `ov-button--${size}`,
          `ov-button--${shape}`,
          onlyIcon && 'ov-button--icon-only',
          disabled && 'ov-button--disabled',
          loading && 'ov-button--loading',
          long && 'ov-button--long',
          className,
        ]
          .filter(Boolean)
          .join(' ')}
        onClick={(event) => {
          if (inactive) {
            event.preventDefault();
            return;
          }
          onClick?.(event);
        }}
      >
        {loading || icon != null ? (
          <span className="ov-button__icon" aria-hidden="true">
            {loading ? <span className="ov-button__spinner" /> : icon}
          </span>
        ) : null}
        {children}
      </button>
    );
  },
);
