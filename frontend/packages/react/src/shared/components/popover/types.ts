import type { OverlayPlacement } from '../overlay/types.js';
import type {
  AriaRole,
  CSSProperties,
  ReactElement,
  ReactNode,
  RefObject,
} from 'react';

/**
 * 弹出层的方位，与提示共用一套取值。空间不足时自动翻到对侧。
 * Where the popover sits relative to its trigger, sharing the tooltip's values.
 * It flips to the opposite side when there is not enough room.
 */
export type PopoverPlacement = OverlayPlacement;

/**
 * 触发方式。`hover` 悬停与聚焦都展示，适合展示型内容；`click` 点击切换，适合
 * 放按钮、菜单这类可交互的内容。
 *
 * How the popover opens. `hover` shows it on hover and focus, for display
 * content; `click` toggles it, for interactive content such as buttons or menus.
 */
export type PopoverTrigger = 'hover' | 'click';

/**
 * 弹出层的属性。
 *
 * 与提示的区别是视觉与语义：提示是深色底的一句话，弹出层是白底卡片，用来放
 * 结构化信息或可交互内容。两者共用同一套 portal 与定位，触发元素都必须是能
 * 接受 `ref` 的单个元素。
 *
 * Props of the popover.
 *
 * It differs from the tooltip in look and semantics: a tooltip is one dark line,
 * a popover is a white card for structured or interactive content. Both share the
 * same portal and positioning, and the trigger must be a single element that
 * accepts a `ref`.
 */
export interface PopoverProps {
  /** 触发元素，必须是能接受 ref 的单个元素。 The trigger; a single element that accepts a ref. */
  children: ReactElement;
  /** 浮层内容。 Surface content. */
  content: ReactNode;
  /** 方位，默认 `right`——内容在行尾展开，不遮挡左侧。 Placement, `right` by default. */
  placement?: PopoverPlacement;
  /** 可选的定位元素，触发事件仍在 children 上。 Optional anchor; children still handle the trigger. */
  anchorRef?: RefObject<HTMLElement | null>;
  /** 触发方式，默认 `hover`。 Trigger mode, `hover` by default. */
  trigger?: PopoverTrigger;
  /** 悬停后多久展示，单位毫秒。延迟只作用于 `hover`。 Hover delay in ms; `hover` only. */
  delay?: number;
  /** Controlled visibility; omitted values use instance-local state. */
  open?: boolean;
  /** Surface semantics; defaults to dialog for click and note for hover. */
  role?: AriaRole;
  /** 为真时不展示浮层。 Suppresses the surface; the trigger still renders. */
  disabled?: boolean;
  /** 浮层容器的额外类名。 Extra class names for the surface. */
  className?: string;
  /** 浮层容器的内联样式，可覆盖定位以外的样式。 Inline styles for the surface. */
  style?: CSSProperties;
  /** 展示状态变化时触发。 Fires when the surface opens or closes. */
  onOpenChange?: (open: boolean) => void;
}
