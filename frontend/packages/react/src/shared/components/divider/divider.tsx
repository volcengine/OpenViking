import type { CSSProperties } from 'react';

import type { DividerProps } from './types.js';

export type { DividerOrientation, DividerProps } from './types.js';

/**
 * 一条分隔线。并排的控件之间用竖线，堆叠的分组之间用横线。
 *
 * 尺寸用内联样式给：`length` 与 `thickness` 都是运行时值，写进类名只能固定成
 * 几个预设，而调用方要的往往是「和旁边按钮一样高」这种随上下文变化的长度。
 * 颜色默认取主题基线的边框色，宿主换主题时自动跟随。
 *
 * A single divider: vertical between side-by-side controls, horizontal between
 * stacked groups.
 *
 * Sizing is inline because `length` and `thickness` are runtime values; baking them
 * into class names would fix them to a few presets, while a caller usually wants a
 * length that follows its context, such as "as tall as the button beside it". The
 * colour defaults to the baseline border, so it follows a host theme change.
 */
export function Divider({
  orientation = 'vertical',
  length,
  thickness = 1,
  color,
  className,
  style,
  ...rest
}: DividerProps) {
  const vertical = orientation === 'vertical';

  const size: CSSProperties = vertical
    ? {
        flex: 'none',
        width: thickness,
        height: length,
      }
    : {
        flex: 'none',
        width: length ?? '100%',
        height: thickness,
      };

  return (
    <div
      {...rest}
      role="separator"
      aria-orientation={orientation}
      className={['ov-divider', className].filter(Boolean).join(' ')}
      style={{
        background:
          color ??
          'var(--ov-ui-color-border-2, var(--ov-ui-border-2-default, #e1e4f2))',
        ...size,
        ...style,
      }}
    />
  );
}
