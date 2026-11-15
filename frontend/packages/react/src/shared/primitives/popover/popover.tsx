import {
  cloneElement,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  version,
} from 'react';
import { createPortal } from 'react-dom';

import { useModalFocusShard } from '../modal/focus-shards.js';
import { useOverlayPosition } from '../overlay/use-overlay-position.js';
import type { PopoverProps } from './types.js';

export type {
  PopoverPlacement,
  PopoverProps,
  PopoverTrigger,
} from './types.js';

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
  return useCallback(
    (node: HTMLElement | null) => {
      target.current = node;
      if (typeof ref === 'function') ref(node);
      else if (ref) ref.current = node;
    },
    [ref, target],
  );
}

/**
 * 弹出层：挂一段结构化信息或一小块可交互内容。
 *
 * 渲染到 `document.body`，因此不会被祖先的 `overflow: hidden` 裁掉——文件树的
 * 行就处在这样的容器里。定位用 fixed 视口坐标，空间不足时翻到对侧。
 *
 * 与 `Tooltip` 分开，是因为两者视觉与语义都不同：提示是深色的一句话，弹出层是
 * 白底卡片。共用的部分（portal、定位、主题变量同步）留在 `overlay/`。
 *
 * A surface holding structured information or a small piece of interactive
 * content.
 *
 * It renders into `document.body`, so an ancestor's `overflow: hidden` cannot clip
 * it — the file tree's rows sit inside exactly such a container. Positioning uses
 * fixed viewport coordinates and flips sides when space runs out.
 *
 * It is separate from `Tooltip` because the two differ in look and semantics: a
 * tooltip is one dark line, a popover is a white card. What they share — the
 * portal, positioning and theme-variable syncing — lives in `overlay/`.
 */
export function Popover({
  children,
  content,
  placement = 'right',
  alignment = 'center',
  anchorRef,
  trigger = 'hover',
  clickToClose = true,
  delay = 300,
  open: controlledOpen,
  role,
  disabled = false,
  className,
  style,
  onOpenChange,
}: PopoverProps) {
  const [localOpen, setOpen] = useState(false);
  const open = !disabled && (controlledOpen ?? localOpen);
  const triggerRef = useRef<HTMLElement | null>(null);
  const timerRef = useRef<number | undefined>(undefined);
  // 用 state 而不是 ref 持有浮层节点：定位 effect 要在节点挂载后重跑，
  // ref 变化不会触发重渲染，effect 便看不到它。
  const [surfaceNode, setSurfaceNode] = useState<HTMLDivElement | null>(null);
  const id = useId();
  const registerFocusShard = useModalFocusShard();
  // 同一个 ref 要同时给定位逻辑（state）与焦点 shard（ref）。写成内联回调会让
  // React 每次渲染先 `ref(null)` 再 `ref(node)`，`setSurfaceNode` 因此反复拿到新
  // 值，渲染永不收敛。用 `useCallback` 固定下来。
  //
  // One ref serves both the positioning state and the focus shard. An inline callback
  // would make React call `ref(null)` then `ref(node)` every render, so
  // `setSurfaceNode` keeps receiving a fresh value and rendering never settles; a
  // memoised callback keeps it stable.
  const surfaceRef = useRef<HTMLDivElement | null>(null);
  const attachSurface = useCallback((node: HTMLDivElement | null) => {
    surfaceRef.current = node;
    setSurfaceNode(node);
  }, []);
  const position = useOverlayPosition(
    open,
    anchorRef ?? triggerRef,
    surfaceNode,
    placement,
    alignment,
  );

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

  // 卸载时清掉未触发的定时器。 Clear a pending timer on unmount.
  useEffect(() => clearTimer, [clearTimer]);

  // 浮层 portal 到 body 后不在所属 Modal 的 DOM 子树里，`FocusLock` 的 Tab 序列
  // 看不到它。打开时登记成 shard，Tab 才能进入；关闭或卸载时注销。
  //
  // The surface portals into `body`, outside the owning Modal's DOM subtree, so
  // `FocusLock`'s tab sequence cannot reach it. Registering it while open makes Tab
  // enter; closing or unmounting unregisters it.
  useEffect(() => {
    if (!open || !registerFocusShard) return;
    return registerFocusShard(surfaceRef);
  }, [open, registerFocusShard]);

  // `disabled` 变真时立即收起，避免浮层留在屏幕上。
  // Collapse as soon as `disabled` turns true.
  useEffect(() => {
    if (disabled) {
      clearTimer();
      setOpen(false);
    }
  }, [disabled, clearTimer]);

  // useEffect 必要性：`click` 触发时要响应浮层外的按下与 Esc，这是文档级
  // 事件，只有挂到 document 才能捕获；浮层渲染在 portal 里，DOM 上不是触发
  // 元素的子孙，靠冒泡判断不了「点在外部」。关闭或卸载时移除监听。
  useEffect(() => {
    if (!open || trigger !== 'click') return;

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
      if (event.key === 'Escape') change(false);
    };

    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [change, open, surfaceNode, trigger]);

  const show = () => {
    if (disabled) return;
    if (trigger === 'click') {
      if (!open || clickToClose) change(!open);
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
    change(false);
  };

  const child = children as React.ReactElement<Record<string, unknown>>;
  const childProps = child.props;
  const childRef = (
    Number.parseInt(version, 10) >= 19
      ? childProps.ref
      : (child as unknown as { ref?: React.Ref<HTMLElement> }).ref
  ) as React.Ref<HTMLElement> | undefined;
  const mergedRef = useMergedRef(childRef, triggerRef);

  // 触发元素声明过 `aria-expanded` 就一律不动它：文件树的行用它表达「目录展开
  // 与否」，浮层可见性只是另一件事。覆盖会让每个挂了浮层的目录行都报
  // `aria-expanded=false`；文件行更糟——它们显式传了 `undefined`，按值判断会
  // 被当成「没声明」而凭空多出一个属性，读屏器拿到的树结构因此是错的。
  //
  // 判据是「声明过」而不是「值不为 undefined」：文件行传的正是 `undefined`，
  // 那表示「这一行没有展开状态」，而不是「请帮我补一个」。只有完全没声明该属性
  // 的触发元素，才由浮层用可见性填上。
  //
  // A trigger that declares `aria-expanded` keeps it untouched: a file tree row uses
  // it for "directory expanded or not", which is unrelated to whether a popover is
  // open. Overwriting reports `aria-expanded=false` on every directory row with a
  // popover, and file rows are worse — they pass `undefined` explicitly, so a
  // value-based check would treat that as "not declared" and invent the attribute,
  // leaving assistive technology with the wrong tree structure.
  //
  // The test is "declared", not "value is not undefined": a file row passes
  // `undefined`, which means "this row has no expansion state", not "please supply
  // one". Only a trigger that never declares the attribute gets the visibility value.
  const declaresExpanded = 'aria-expanded' in childProps;
  const triggerElement = cloneElement(child, {
    ref: mergedRef,
    ...(declaresExpanded ? {} : { 'aria-expanded': open }),
    onMouseEnter: (event: React.MouseEvent) => {
      (
        childProps.onMouseEnter as ((e: React.MouseEvent) => void) | undefined
      )?.(event);
      // 只有悬停形态在鼠标进入时打开。点击形态的 `show` 是 toggle，鼠标移入
      // 会先打开、紧接着的点击又把它关掉，菜单看起来「点不开」。
      if (trigger === 'hover') show();
    },
    onMouseLeave: (event: React.MouseEvent) => {
      (
        childProps.onMouseLeave as ((e: React.MouseEvent) => void) | undefined
      )?.(event);
      // 点击形态的关闭由点击或 Esc 决定，鼠标移开不应该收起菜单。
      if (trigger === 'hover') hide();
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
          ref={attachSurface}
          id={id}
          role={role ?? (trigger === 'click' ? 'dialog' : 'note')}
          className={['ov-popover', className].filter(Boolean).join(' ')}
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
