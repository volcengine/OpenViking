import type { HTMLAttributes } from 'react';

/**
 * 面板在分隔条左侧或上方用 `1`，在右侧或下方用 `-1`。
 * Use `1` for a pane to the left of or above the divider, `-1` otherwise.
 */
export type ResizeDirection = 1 | -1;

/**
 * 分隔条的属性。
 *
 * 可聚焦的分隔条。拖动或按方向键调整面板尺寸，调用方持有尺寸。
 *
 * Props of the divider.
 *
 * A focusable separator that reports a clamped pane size on drag or arrow-key input.
 */
export interface ResizableHandleProps extends Omit<
  HTMLAttributes<HTMLDivElement>,
  'role' | 'children' | 'defaultValue'
> {
  /** 无障碍名称，例如「调整目录树宽度」。 Accessible name, such as "Resize the directory tree". */
  label: string;
  /** 最小尺寸，单位像素。 Minimum size in pixels. */
  min: number;
  /** 最大尺寸，单位像素。 Maximum size in pixels. */
  max: number;
  /** 当前尺寸，单位像素。 Current size in pixels. */
  value: number;
  /** 分隔线朝向，默认为竖线。 Orientation of the dividing line, vertical by default. */
  orientation?: 'horizontal' | 'vertical';
  /** 方向键每次调整的像素数，缺省 16。 Pixels per arrow-key press, 16 by default. */
  step?: number;
  /** 拖动方向，缺省 `1`。 Drag direction, `1` by default. */
  direction?: ResizeDirection;
  /**
   * 拖动或按键产生新尺寸时触发，参数已夹到 `[min, max]`。省略时分隔条只作为
   * 装饰线存在，不可拖动也不可聚焦。
   *
   * Fires with a new size already clamped to `[min, max]`. Omitting it leaves the
   * divider as a decorative rule: not draggable and not focusable.
   */
  onResize?: (size: number) => void;
}
