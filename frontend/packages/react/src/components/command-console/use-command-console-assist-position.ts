import { useEffect, useId, useRef } from 'react';
import type { RefObject } from 'react';

/** 顶层浮层保留组件的主题继承；不支持 Popover 的浏览器回退为内联面板。 */
export function useCommandConsoleAssistPosition(
  anchorRef: RefObject<HTMLDivElement | null>,
  open: boolean,
  onDismiss: (value: string) => void,
) {
  const panelRef = useRef<HTMLDivElement>(null);
  const anchorName = `--ov-command-console-${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`;

  // useEffect 必要性：浮层需订阅浏览器布局、滚动与视口变化，不能由 React 输入值推导坐标。
  // open 后 DOM 才可测量；卸载或关闭时隐藏浮层并清理观察器及事件，避免跨实例残留。
  useEffect(() => {
    const panel = panelRef.current;
    const anchor = anchorRef.current;
    if (!open || !panel || !anchor) return;
    const dismiss = () =>
      onDismiss(anchor.querySelector('textarea')?.value ?? '');
    const outside = (event: PointerEvent) => {
      if (
        event.target instanceof Node &&
        !panel.contains(event.target) &&
        !anchor.contains(event.target)
      )
        dismiss();
    };
    const escape = (event: KeyboardEvent) => {
      if (event.isComposing || event.keyCode === 229) return;
      if (
        event.key === 'Escape' &&
        (anchor.contains(document.activeElement) ||
          panel.contains(document.activeElement))
      ) {
        event.preventDefault();
        dismiss();
      }
    };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    if (!('popover' in HTMLElement.prototype)) {
      panel.removeAttribute('popover');
      panel.dataset.inline = '';
      return () => {
        document.removeEventListener('pointerdown', outside);
        document.removeEventListener('keydown', escape);
      };
    }
    const nativeAnchors = CSS.supports(
      'position-area',
      'block-start span-inline-end',
    );
    const previousAnchor = anchor.style.getPropertyValue('anchor-name');
    anchor.style.setProperty('anchor-name', anchorName);
    panel.style.setProperty('position-anchor', anchorName);
    panel.dataset.floating = '';

    const place = () => {
      const rect = anchor.getBoundingClientRect();
      const above = Math.max(0, rect.top - 20);
      const below = Math.max(0, window.innerHeight - rect.bottom - 20);
      const onTop = above >= 160 || above >= below;
      const available = Math.max(80, onTop ? above : below);
      const width = Math.min(rect.width, window.innerWidth - 24);
      const left = Math.max(
        12,
        Math.min(rect.left, window.innerWidth - width - 12),
      );
      panel.style.width = `${width}px`;
      panel.style.setProperty(
        '--ov-ui-command-console-assist-height',
        `${Math.min(420, available)}px`,
      );
      const right = window.innerWidth - left - width;
      panel.dataset.side =
        left >= 334 ? 'left' : right >= 334 ? 'right' : 'stack';
      panel.style.setProperty(
        '--ov-ui-command-console-assist-detail-width',
        `${Math.min(400, (left >= 334 ? left : right) - 14)}px`,
      );
      panel.dataset.placement = onTop ? 'above' : 'below';
      if (nativeAnchors) {
        panel.style.setProperty(
          'position-area',
          `${onTop ? 'block-start' : 'block-end'} span-inline-end`,
        );
      } else {
        panel.style.left = `${left}px`;
        panel.style.top = onTop ? 'auto' : `${rect.bottom + 8}px`;
        panel.style.bottom = onTop
          ? `${window.innerHeight - rect.top + 8}px`
          : 'auto';
      }
    };
    place();
    panel.showPopover();
    const observer = new ResizeObserver(place);
    observer.observe(anchor);
    observer.observe(panel);
    window.addEventListener('resize', place);
    window.addEventListener('scroll', place, true);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', place);
      window.removeEventListener('scroll', place, true);
      document.removeEventListener('pointerdown', outside);
      document.removeEventListener('keydown', escape);
      anchor.style.setProperty('anchor-name', previousAnchor);
      if (panel.matches(':popover-open')) panel.hidePopover();
    };
  }, [anchorRef, anchorName, open, onDismiss]);
  return panelRef;
}
