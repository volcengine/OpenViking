import {
  cloneElement,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from 'react';
import { createPortal } from 'react-dom';

import { useOverlayPosition } from '../overlay/use-overlay-position.js';
import type { TooltipLabels, TooltipProps } from './types.js';

export type {
  TooltipLabels,
  TooltipPlacement,
  TooltipProps,
  TooltipTrigger,
} from './types.js';

const DEFAULT_LABELS: Required<TooltipLabels> = { close: 'Close' };

/**
 * 把外部 ref 与内部 ref 合并成一个回调 ref。
 *
 * 结果按外部 ref 缓存：每次渲染都新建函数会让 React 先 `ref(null)` 再
 * `ref(node)`，触发额外的重挂与重渲染。
 *
 * Merges the caller's ref with the internal one into a single callback ref.
 *
 * The result is memoised per caller ref: a fresh function every render makes React
 * call `ref(null)` then `ref(node)` again, causing extra remounts and renders.
 */
function useMergedRef(
  ref: React.Ref<HTMLElement> | undefined,
  target: React.RefObject<HTMLElement | null>,
) {
  const merged = useCallback(
    (node: HTMLElement | null) => {
      target.current = node;
      if (typeof ref === 'function') ref(node);
      else if (ref) ref.current = node;
    },
    [ref, target],
  );
  return merged;
}

/**
 * 浮层：给触发元素挂一段提示或一小块可交互内容。
 *
 * 浮层渲染到 `document.body`，因此不会被祖先的 `overflow: hidden` 裁掉——
 * 文件预览的面包屑、文件树的行都处在这样的容器里。定位用 fixed 视口坐标，
 * 空间不足时翻到对侧。
 *
 * 触发元素必须是能接受 `ref` 的单个 React 元素；组件把事件处理与
 * `aria-describedby` 合并进去，不额外包一层 DOM。
 *
 * A surface that attaches a hint or a small piece of interactive content to a
 * trigger.
 *
 * It renders into `document.body`, so an ancestor's `overflow: hidden` cannot clip
 * it — the file preview's breadcrumb and the file tree's rows both sit inside such
 * containers. Positioning uses fixed viewport coordinates and flips sides when
 * space runs out.
 *
 * The trigger must be a single React element that accepts a `ref`; the component
 * merges its handlers and `aria-describedby` into it rather than wrapping it in
 * another DOM node.
 */
export function Tooltip({
  children,
  content,
  placement = 'top',
  trigger = 'hover',
  delay = 300,
  disabled = false,
  className,
  style,
  onOpenChange,
  labels,
}: TooltipProps) {
  const text = { ...DEFAULT_LABELS, ...labels };
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLElement | null>(null);
  const timerRef = useRef<number | undefined>(undefined);
  // 用 state 而不是 ref 持有浮层节点：定位 effect 要在节点挂载后重跑，
  // ref 变化不会触发重渲染，effect 便看不到它。
  const [surfaceNode, setSurfaceNode] = useState<HTMLDivElement | null>(null);
  const id = useId();
  const position = useOverlayPosition(open, triggerRef, surfaceNode, placement);

  const change = useCallback(
    (next: boolean) => {
      setOpen(next);
      onOpenChange?.(next);
    },
    [onOpenChange],
  );

  const clearTimer = useCallback(() => {
    if (timerRef.current !== undefined) {
      window.clearTimeout(timerRef.current);
      timerRef.current = undefined;
    }
  }, []);

  // useEffect 必要性：浏览器延迟打开/关闭定时器可能晚于组件卸载；
  // 只有卸载清理能释放最后一次事件创建的 timer，避免过期回调。
  useEffect(() => clearTimer, [clearTimer]);

  // useEffect 必要性：宿主可在事件外禁用触发器，需同步撤销浏览器 timer 和 Portal；
  // props 提交后清理旧 timer 并收起浮层，无额外订阅需要释放。
  useEffect(() => {
    if (disabled) {
      clearTimer();
      setOpen(false);
    }
  }, [disabled, clearTimer]);

  // useEffect 必要性：点击浮层响应外部按下，两种浮层都响应 Esc，这是文档级
  // 事件，只有挂到 document 才能捕获；浮层渲染在 portal 里，DOM 上不是触发
  // 元素的子孙，靠冒泡判断不了「点在外部」。关闭或卸载时移除监听。
  useEffect(() => {
    if (!open) return;

    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (
        surfaceNode?.contains(target) ||
        triggerRef.current?.contains(target)
      ) {
        return;
      }
      change(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        clearTimer();
        change(false);
      }
    };

    if (trigger === 'click')
      document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      if (trigger === 'click')
        document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [change, clearTimer, open, surfaceNode, trigger]);

  const show = () => {
    if (disabled) return;
    if (trigger === 'click') {
      change(!open);
      return;
    }
    clearTimer();
    timerRef.current = window.setTimeout(() => {
      change(true);
      timerRef.current = undefined;
    }, delay);
  };

  const hide = () => {
    if (trigger === 'click') return;
    clearTimer();
    timerRef.current = window.setTimeout(() => {
      change(false);
      timerRef.current = undefined;
    }, 120);
  };

  const child = children as React.ReactElement<Record<string, unknown>>;
  const childProps = child.props;
  const childRef = (child as unknown as { ref?: React.Ref<HTMLElement> }).ref;
  const mergedRef = useMergedRef(childRef, triggerRef);

  const triggerElement = cloneElement(child, {
    ref: mergedRef,
    'aria-describedby':
      childProps['aria-describedby'] ??
      (open && trigger === 'hover' && content !== childProps['aria-label']
        ? id
        : undefined),
    'aria-expanded': trigger === 'click' ? open : childProps['aria-expanded'],
    onMouseEnter: (event: React.MouseEvent) => {
      (
        childProps.onMouseEnter as ((e: React.MouseEvent) => void) | undefined
      )?.(event);
      // 只有悬停形态在鼠标进入时打开。点击形态的 `show` 是 toggle，鼠标移入
      // 会先打开、紧接着的点击又把它关掉，菜单看起来「点不开」。
      //
      // Only the hover shape opens on mouse enter. Under the click shape `show`
      // toggles, so hovering opens it and the click that follows closes it again
      // — the menu appears not to open.
      if (trigger === 'hover') show();
    },
    onMouseLeave: (event: React.MouseEvent) => {
      (
        childProps.onMouseLeave as ((e: React.MouseEvent) => void) | undefined
      )?.(event);
      // 同上：点击形态的关闭由点击或 Esc 决定，鼠标移开不应该收起菜单。
      //
      // Same here: the click shape closes on click or Escape, not on mouse leave.
      if (trigger === 'hover') hide();
    },
    onFocus: (event: React.FocusEvent) => {
      (childProps.onFocus as ((e: React.FocusEvent) => void) | undefined)?.(
        event,
      );
      if (trigger === 'hover') show();
    },
    onBlur: (event: React.FocusEvent) => {
      (childProps.onBlur as ((e: React.FocusEvent) => void) | undefined)?.(
        event,
      );
      hide();
    },
    onClick: (event: React.MouseEvent) => {
      (childProps.onClick as ((e: React.MouseEvent) => void) | undefined)?.(
        event,
      );
      if (trigger === 'click') show();
    },
  });

  const surface = open
    ? createPortal(
        <div
          ref={setSurfaceNode}
          id={id}
          role={trigger === 'hover' ? 'tooltip' : 'dialog'}
          aria-label={trigger === 'click' ? text.close : undefined}
          className={['ov-tooltip', className].filter(Boolean).join(' ')}
          data-placement={position.placement}
          style={{ top: position.top, left: position.left, ...style }}
          onMouseEnter={trigger === 'hover' ? clearTimer : undefined}
          onMouseLeave={trigger === 'hover' ? hide : undefined}
        >
          {content}
        </div>,
        document.body,
      )
    : null;

  return (
    <>
      {triggerElement}
      {surface}
    </>
  );
}
