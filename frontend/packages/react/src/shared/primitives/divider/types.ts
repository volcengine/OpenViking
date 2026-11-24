import type { CSSProperties, HTMLAttributes } from 'react';

/**
 * 分隔线的方向。竖线用于并排的控件之间，横线用于堆叠的分组之间。
 * Direction of the divider: vertical between side-by-side controls, horizontal
 * between stacked groups.
 */
export type DividerOrientation = 'vertical' | 'horizontal';

/**
 * 分隔线的属性。
 *
 * 它只是一个 `role="separator"` 的元素，不占交互焦点，也不读屏幕阅读器以外的
 * 内容。长度由 `length` 决定，颜色默认取主题基线里的边框色。
 *
 * Props of the divider.
 *
 * It is just a `role="separator"` element: not focusable, and it carries no content
 * for a screen reader. `length` sets its extent, and the colour defaults to the
 * baseline border.
 */
export interface DividerProps extends Omit<
  HTMLAttributes<HTMLDivElement>,
  'children'
> {
  /** 方向，默认竖线。Orientation; vertical by default. */
  orientation?: DividerOrientation;
  /**
   * 线的长度：竖线是高度，横线是宽度。省略时由父级布局决定，横线默认铺满。
   * Extent of the line: height for a vertical one, width for a horizontal one.
   * Omitted leaves it to the parent layout; a horizontal one fills by default.
   */
  length?: number | string;
  /** 线的粗细，缺省为 1px。Thickness, 1px by default. */
  thickness?: number | string;
  /** 线的颜色，缺省取主题基线的边框色。Colour; defaults to the baseline border. */
  color?: string;
  /** 内联样式，可覆盖上面的值。Inline styles, overriding the values above. */
  style?: CSSProperties;
}
