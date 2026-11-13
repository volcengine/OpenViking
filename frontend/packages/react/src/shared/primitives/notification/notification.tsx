/** Arco Design Notification top-right stack (MIT); see LICENSE.arco-design. */
import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { TransitionGroup, CSSTransition } from 'react-transition-group';
import { useLocale } from '../../locale-context.js';
import { resolveLocale } from '../../locale.js';
import { usePortalTheme } from '../tooltip/use-portal-theme.js';
import { Notice } from './notice.js';
import type { NotificationItem, NotificationOptions } from './types.js';
import enUS from './locales/en-US.js';
import zhCN from './locales/zh-CN.js';

interface Props extends NotificationOptions {
  notices: NotificationItem[];
  onRemove: (id: string) => void;
}

export function Notification({
  notices,
  onRemove,
  locale: localeProp,
  labels,
}: Props) {
  const locale = useLocale(localeProp);
  const text = resolveLocale({ 'en-US': enUS, 'zh-CN': zhCN }, locale, labels);
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const anchor = useRef<HTMLSpanElement>(null);
  const [surface, setSurface] = useState<HTMLDivElement | null>(null);
  usePortalTheme(Boolean(surface), anchor, surface);
  // Effect necessity: synchronize the browser's top layer after the portal DOM commits.
  // React cannot show/hide a popover declaratively; removing the surface hides it.
  // Browsers without Popover retain the fixed-position portal as the fallback.
  useEffect(() => {
    if (!surface || typeof surface.showPopover !== 'function') return;
    surface.showPopover();
    return () => {
      if (surface.isConnected) surface.hidePopover();
    };
  }, [surface]);
  return (
    <>
      <span ref={anchor} hidden />
      {!mounted || typeof document === 'undefined'
        ? null
        : createPortal(
            <div
              ref={setSurface}
              className="ov-notification-stack"
              popover="manual"
              role="region"
              aria-label={text.region}
              lang={locale}
            >
              <div
                className="ov-notification-stack__list"
                aria-live="polite"
                aria-relevant="additions text"
              >
                <TransitionGroup component={null}>
                  {notices.map((notice) => (
                    <NotificationTransition
                      key={notice.id}
                      notice={notice}
                      closeLabel={text.close}
                      onRemove={onRemove}
                    />
                  ))}
                </TransitionGroup>
              </div>
            </div>,
            document.body,
          )}
    </>
  );
}

function NotificationTransition({
  notice,
  closeLabel,
  onRemove,
  in: visible,
  onExited,
}: {
  notice: NotificationItem;
  closeLabel: string;
  onRemove: (id: string) => void;
  in?: boolean;
  onExited?: () => void;
}) {
  const nodeRef = useRef<HTMLDivElement>(null);
  return (
    <CSSTransition
      in={visible}
      nodeRef={nodeRef}
      timeout={200}
      classNames="ov-notification-motion"
      onExited={() => {
        onExited?.();
        notice.onClose?.();
      }}
    >
      <div ref={nodeRef} className="ov-notification-stack__item">
        <Notice
          notice={notice}
          closeLabel={closeLabel}
          onRemove={onRemove}
          active={visible ?? true}
        />
      </div>
    </CSSTransition>
  );
}
