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
    const roots: Node[] = [];
    const hosts: Element[] = [];
    let root = anchor.getRootNode();
    while (true) {
      roots.push(root);
      if (root.nodeType !== Node.DOCUMENT_FRAGMENT_NODE) break;
      const host = (root as ShadowRoot).host;
      hosts.push(host);
      root = host.getRootNode();
    }
    const outside = (event: Event) => {
      const path = event.composedPath();
      if (!path.includes(panel) && !path.includes(anchor)) dismiss();
    };
    const seen = roots.map(() => new WeakSet<Event>());
    const outsideListeners = roots.map((eventRoot, index) => {
      const listener = (event: Event) => {
        seen[index]!.add(event);
        const innerHost = index ? hosts[index - 1] : null;
        if (innerHost && event.composedPath().includes(innerHost)) {
          // Closed roots hide their internals from outer listeners. Wait until
          // propagation finishes: an inner root marks the event as seen, while
          // a direct press on host whitespace has no inner listener to do so.
          queueMicrotask(() => {
            if (!seen[index - 1]!.has(event)) dismiss();
          });
          return;
        }
        outside(event);
      };
      eventRoot.addEventListener('pointerdown', listener, true);
      return listener;
    });
    const escape = (event: KeyboardEvent) => {
      if (
        event.defaultPrevented ||
        event.isComposing ||
        event.keyCode === 229 ||
        event.key !== 'Escape'
      )
        return;
      event.preventDefault();
      dismiss();
    };
    // Capture presses at every root boundary. Each closed root can inspect the
    // section of the composed path hidden from its parent root.
    anchor.ownerDocument.addEventListener('keydown', escape);
    if (!('popover' in HTMLElement.prototype)) {
      panel.removeAttribute('popover');
      panel.dataset.inline = '';
      return () => {
        roots.forEach((eventRoot, index) =>
          eventRoot.removeEventListener(
            'pointerdown',
            outsideListeners[index]!,
            true,
          ),
        );
        anchor.ownerDocument.removeEventListener('keydown', escape);
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
      const right = window.innerWidth - left - width;
      const side = left >= 334 ? 'left' : right >= 334 ? 'right' : 'stack';
      panel.dataset.side = side;
      // 详情与列表上下堆叠时共用一份高度，上限放宽到 640，列表才不会只剩几行。
      panel.style.setProperty(
        '--ov-ui-command-console-assist-height',
        `${Math.min(side === 'stack' ? 640 : 420, available)}px`,
      );
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
      roots.forEach((eventRoot, index) =>
        eventRoot.removeEventListener(
          'pointerdown',
          outsideListeners[index]!,
          true,
        ),
      );
      anchor.ownerDocument.removeEventListener('keydown', escape);
      anchor.style.setProperty('anchor-name', previousAnchor);
      if (panel.matches(':popover-open')) panel.hidePopover();
    };
  }, [anchorRef, anchorName, open, onDismiss]);
  return panelRef;
}
