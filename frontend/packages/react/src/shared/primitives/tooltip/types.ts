import type { CSSProperties, ReactElement, ReactNode } from 'react';

import type { OverlayPlacement } from '../overlay/types.js';

/**
 * 提示相对触发元素的方位，与浮层共用一套取值。空间不足时自动翻到对侧。
 * Where the hint sits relative to its trigger, sharing the overlay's values. It
 * flips to the opposite side when there is not enough room.
 */
export type TooltipPlacement = OverlayPlacement;

/**
 * 触发方式。`hover` 悬停与聚焦都展示，适合按钮提示；`click` 点击切换，
 * 适合放可交互的内容。
 *
 * How the surface opens. `hover` shows it on hover and focus, which suits button
 * hints; `click` toggles it, for content the user interacts with.
 */
export type TooltipTrigger = 'hover' | 'click';

export interface TooltipProps {
  /** 触发元素，必须是能接受 ref 的单个元素。 The trigger; a single element that accepts a ref. */
  children: ReactElement;
  /** 浮层内容。 Surface content. */
  content: ReactNode;
  /** 方位，默认 `top`。 Placement, `top` by default. */
  placement?: TooltipPlacement;
  /** 触发方式，默认 `hover`。 Trigger mode, `hover` by default. */
  trigger?: TooltipTrigger;
  /** 悬停后多久展示，单位毫秒。延迟只作用于 `hover`。 Hover delay in ms; `hover` only. */
  delay?: number;
  /** 为真时不展示浮层。当为真，触发元素仍正常渲染。 Suppresses the surface; the trigger still renders. */
  disabled?: boolean;
  /** 浮层容器的额外类名。 Extra class names for the surface. */
  className?: string;
  /** 浮层容器的内联样式，可覆盖定位以外的样式。 Inline styles for the surface. */
  style?: CSSProperties;
  /** 展示状态变化时触发。 Fires when the surface opens or closes. */
  onOpenChange?: (open: boolean) => void;
  /** 可替换文案。 Replaceable copy. */
  labels?: TooltipLabels;
}

export interface TooltipLabels {
  /** `click` 触发时关闭按钮的无障碍名称。 Accessible name of the close button under `click`. */
  close?: string;
}
