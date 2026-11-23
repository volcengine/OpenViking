/** Shared Arco Message/Notification stack (MIT); see LICENSE.arco-design. */
import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { TransitionGroup, CSSTransition } from 'react-transition-group';
import { usePortalTheme } from '../tooltip/use-portal-theme.js';
import { useModalFocusShard } from '../modal/focus-shards.js';
import { Notice } from './notice.js';
import type { Ref } from 'react';
import type { NoticeItem, NoticeLabels } from './types.js';
import type { Locale } from '../../locale.js';

interface Props {
  kind: 'notification' | 'toast';
  locale: Locale;
  labels: NoticeLabels;
  notices: NoticeItem[];
  onRemove: (id: string) => void;
  container?: HTMLElement | null;
  contentRef?: Ref<HTMLDivElement>;
}

export function NoticeStack({
  kind,
  notices,
  onRemove,
  locale,
  labels,
  container,
  contentRef,
}: Props) {
  const anchor = useRef<HTMLSpanElement>(null);
  const [surface, setSurface] = useState<HTMLDivElement | null>(null);
  usePortalTheme(Boolean(surface) && !contentRef, anchor, surface);
  const registerFocusShard = useModalFocusShard();
  // Effect necessity: the body portal sits outside its owning Modal's DOM subtree.
  // Register the committed surface with FocusLock so its close/actions are reachable;
  // cleanup unregisters when the surface or modal owner changes, or on unmount.
  useEffect(() => {
    if (surface && container === undefined)
      return registerFocusShard?.({ current: surface });
  }, [surface, registerFocusShard, container]);
  // Effect necessity: synchronize the browser's top layer after the portal DOM commits.
  // React cannot show/hide a popover declaratively; removing the surface hides it.
  // Browsers without Popover retain the fixed-position portal as the fallback.
  useEffect(() => {
    if (
      !surface ||
      container !== undefined ||
      typeof surface.showPopover !== 'function'
    )
      return;
    surface.showPopover();
    return () => {
      if (surface.isConnected) surface.hidePopover();
    };
  }, [surface, container]);
  const items = (
    <TransitionGroup component={null}>
      {notices.map((notice) => (
        <NoticeTransition
          kind={kind}
          key={notice.id}
          notice={notice}
          closeLabel={labels.close}
          onRemove={onRemove}
        />
      ))}
    </TransitionGroup>
  );
  if (container !== undefined)
    return (
      <>
        <span ref={anchor} hidden />
        {container
          ? createPortal(
              <div
                ref={setSurface}
                className={`ov-${kind}-stack__contribution`}
              >
                {items}
              </div>,
              container,
            )
          : null}
      </>
    );
  return (
    <>
      {!contentRef && <span ref={anchor} hidden />}
      {createPortal(
        <div
          ref={setSurface}
          className={`ov-${kind}-stack`}
          popover="manual"
          role="region"
          aria-label={labels.region}
          lang={locale}
        >
          <div
            ref={contentRef}
            className={`ov-${kind}-stack__list`}
            aria-live="polite"
            aria-relevant="additions text"
          >
            {items}
          </div>
        </div>,
        document.body,
      )}
    </>
  );
}

function NoticeTransition({
  kind,
  notice,
  closeLabel,
  onRemove,
  in: visible,
  onExited,
}: {
  kind: 'notification' | 'toast';
  notice: NoticeItem;
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
      classNames={`ov-${kind}-motion`}
      onExited={() => {
        onExited?.();
        notice.onClose?.();
      }}
    >
      <div ref={nodeRef} className={`ov-${kind}-stack__item`}>
        <Notice
          kind={kind}
          notice={notice}
          closeLabel={closeLabel}
          onRemove={onRemove}
          active={visible ?? true}
        />
      </div>
    </CSSTransition>
  );
}
