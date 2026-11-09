import type { HTMLAttributes } from 'react';

/**
 * 拖动方向：向右拖动让面板变宽为 `1`，变窄为 `-1`。
 *
 * 面板在分隔条左侧时用 `1`，在右侧时用 `-1`，否则往右拖反而变小。
 *
 * Drag direction: `1` widens the panel as the pointer moves right, `-1` narrows
 * it. A panel left of the divider uses `1`, one on its right uses `-1`; the
 * wrong sign makes a rightward drag shrink the panel.
 */
export type ResizeDirection = 1 | -1;

/**
 * 分隔条的属性。
 *
 * 它是一个 `role="separator"` 的可聚焦控件：拖动改宽，左右方向键也能改，屏幕
 * 阅读器通过 `aria-valuenow` 读到当前宽度。宽度本身由调用方持有，这里只上报
 * 夹取后的新值——面板多宽属于布局，不属于分隔条。
 *
 * Props of the divider.
 *
 * It is a focusable `role="separator"` control: drag to resize, or use the left
 * and right arrow keys, with a screen reader reading the current width from
 * `aria-valuenow`. The width itself belongs to the caller; this only reports the
 * clamped new value, because how wide a panel is is layout, not a divider's own
 * state.
 */
export interface ResizableHandleProps extends Omit<
  HTMLAttributes<HTMLDivElement>,
  'role' | 'children' | 'defaultValue'
> {
  /** 无障碍名称，例如「调整目录树宽度」。 Accessible name, such as "Resize the directory tree". */
  label: string;
  /** 最小宽度，单位像素。 Minimum width in pixels. */
  min: number;
  /** 最大宽度，单位像素。 Maximum width in pixels. */
  max: number;
  /** 当前宽度，单位像素。 Current width in pixels. */
  value: number;
  /** 方向键每次调整的像素数，缺省 16。 Pixels per arrow-key press, 16 by default. */
  step?: number;
  /** 拖动方向，缺省 `1`。 Drag direction, `1` by default. */
  direction?: ResizeDirection;
  /**
   * 拖动或按键产生新宽度时触发，参数已夹到 `[min, max]`。省略时分隔条只作为
   * 装饰线存在，不可拖动也不可聚焦。
   *
   * Fires with a new width already clamped to `[min, max]`. Omitting it leaves the
   * divider as a decorative rule: not draggable and not focusable.
   */
  onResize?: (width: number) => void;
}
