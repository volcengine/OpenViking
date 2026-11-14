import { forwardRef, type HTMLAttributes } from 'react';

/** Shared visual shell for text inputs and rich selection controls. */
export const InputFrame = forwardRef<
  HTMLSpanElement,
  HTMLAttributes<HTMLSpanElement> & {
    size?: 'mini' | 'small' | 'default' | 'large';
    disabled?: boolean;
  }
>(function InputFrame(
  { size = 'default', disabled, className, ...props },
  ref,
) {
  return (
    <span
      {...props}
      ref={ref}
      className={[
        'ov-input',
        `ov-input--${size}`,
        disabled && 'ov-input--disabled',
        className,
      ]
        .filter(Boolean)
        .join(' ')}
    />
  );
});
