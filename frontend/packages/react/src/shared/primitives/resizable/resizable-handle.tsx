import { useRef, useState } from 'react';
import type { KeyboardEvent, PointerEvent } from 'react';

import { clampResizableSize } from './clamp.js';
import type { ResizableHandleProps } from './types.js';

export type { ResizableHandleProps, ResizeDirection } from './types.js';

/**
 * 一条可拖拽的分隔条，用来改相邻面板的尺寸。
 *
 * 尺寸是受控的：组件只上报夹取后的新值。拖动期间用
 * 指针捕获接住后续事件，因此不需要往 `window` 上挂监听，也不留全局副作用
 * ——`preventDefault` 已经挡住拖动引起的文字选中。方向键也能调整尺寸，键盘用户
 * 不必依赖指针。
 *
 * 省略 `onResize` 时退化成一根装饰线。
 *
 * A draggable divider that resizes the neighbouring panel.
 *
 * The pane size is controlled: this reports the clamped new value. Pointer capture
 * receives the rest of the drag, so
 * nothing is bound to `window` and no global state is touched — `preventDefault`
 * already stops the drag from selecting text. The arrow keys resize too, so a
 * keyboard user never needs a pointer.
 *
 * Without `onResize` it degrades to a decorative rule: not draggable, not
 * focusable.
 */
export function ResizableHandle({
  label,
  min,
  max,
  value,
  orientation = 'vertical',
  step = 16,
  direction = 1,
  onResize,
  className,
  ...rest
}: ResizableHandleProps) {
  const dragRef = useRef<{
    pointerId: number;
    position: number;
    size: number;
  } | null>(null);
  const [dragging, setDragging] = useState(false);
  const horizontal = orientation === 'horizontal';

  const endDrag = (event: PointerEvent<HTMLDivElement>) => {
    if (!dragRef.current) return;
    dragRef.current = null;
    setDragging(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  };

  const handlePointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (!onResize) return;
    // 只认主键：右键与中键不开始拖动。
    // Primary button only: a right or middle click must not start a drag.
    if (event.button !== 0) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = {
      pointerId: event.pointerId,
      position: horizontal ? event.clientY : event.clientX,
      size: value,
    };
    setDragging(true);
  };

  const handlePointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag || !onResize || drag.pointerId !== event.pointerId) return;
    const position = horizontal ? event.clientY : event.clientX;
    const size = drag.size + (position - drag.position) * direction;
    onResize(clampResizableSize(size, min, max));
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!onResize) return;
    const decrease = horizontal ? 'ArrowUp' : 'ArrowLeft';
    const increase = horizontal ? 'ArrowDown' : 'ArrowRight';
    if (event.key !== decrease && event.key !== increase) return;
    event.preventDefault();
    const delta = (event.key === increase ? step : -step) * direction;
    onResize(clampResizableSize(value + delta, min, max));
  };

  return (
    <div
      {...rest}
      role="separator"
      // 可聚焦的分隔条是 ARIA 认可的键盘控件，因此这里必须显式给 tabIndex；
      // 没有 `onResize` 时它只是装饰线，不该进 Tab 顺序。
      //
      // A focusable separator is an ARIA keyboard control, so the explicit
      // tabIndex is required; without `onResize` it is decoration and must stay
      // out of the tab order.
      tabIndex={onResize ? 0 : undefined}
      aria-label={label}
      aria-orientation={orientation}
      aria-valuemin={min}
      aria-valuemax={max}
      aria-valuenow={Math.round(value)}
      data-dragging={dragging || undefined}
      className={['ov-resizable-handle', className].filter(Boolean).join(' ')}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onKeyDown={handleKeyDown}
    >
      <span aria-hidden="true" className="ov-resizable-handle__line" />
    </div>
  );
}
