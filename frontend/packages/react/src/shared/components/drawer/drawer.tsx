/**
 * Adapted from Arco Design's Drawer (MIT), limited to the right-side panel.
 * https://github.com/arco-design/arco-design/tree/main/components/Drawer
 * Copyright (c) 2021 Bytedance, Inc. and its affiliates.
 * License: ../../../../LICENSE.arco-design (included in the published package).
 */
import { useEffect, useRef, useState } from 'react';
import type { ReactNode, RefObject } from 'react';
import { createPortal } from 'react-dom';
import FocusLock from 'react-focus-lock';
import { CSSTransition } from 'react-transition-group';

import { lockBodyScroll } from '../overlay/lock-body-scroll.js';
import CloseIcon from '../../../../../icons/svg/viking-bot/remove.svg?react';
import { usePortalTheme } from '../tooltip/use-portal-theme.js';

export interface DrawerProps {
  id: string;
  open: boolean;
  title: string;
  closeLabel: string;
  triggerRef: RefObject<HTMLElement | null>;
  children: ReactNode;
  className?: string;
  lang?: string;
  onClose: () => void;
}

const ENTER = 'ov-drawer--enter';
const ENTER_ACTIVE = 'ov-drawer--enter-active';
const MOTION = {
  appear: ENTER,
  appearActive: ENTER_ACTIVE,
  enter: ENTER,
  enterActive: ENTER_ACTIVE,
  exitActive: 'ov-drawer--exit',
};

export function Drawer({
  id,
  open,
  title,
  closeLabel,
  triggerRef,
  children,
  className,
  lang,
  onClose,
}: DrawerProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const maskRef = useRef<HTMLButtonElement>(null);
  const [present, setPresent] = useState(open);
  const [layer, setLayer] = useState<HTMLDivElement | null>(null);
  usePortalTheme(Boolean(layer), triggerRef, layer);

  // useEffect 必要性：Portal 存在期间持有浏览器 body 的滚动锁；仅随 open 处理会
  // 在退出动画结束前解锁。Portal 移除时释放自己的锁，最后一个持有者恢复宿主样式。
  useEffect(() => {
    if (layer) return lockBodyScroll(layer.ownerDocument.body);
  }, [layer]);

  if (typeof document === 'undefined' || (!open && !present)) return null;
  return createPortal(
    <div
      ref={setLayer}
      role="presentation"
      className={['ov-drawer', className].filter(Boolean).join(' ')}
      lang={lang}
      onKeyDown={(event) => {
        if (event.key !== 'Escape' || event.defaultPrevented) return;
        if (
          !(event.target instanceof Node) ||
          !event.currentTarget.contains(event.target)
        )
          return;
        event.preventDefault();
        event.stopPropagation();
        onClose();
      }}
    >
      <CSSTransition
        nodeRef={maskRef}
        in={open}
        timeout={300}
        classNames={MOTION}
        appear
        mountOnEnter
        unmountOnExit
      >
        <button
          ref={maskRef}
          type="button"
          className="ov-drawer__mask"
          aria-label={closeLabel}
          aria-hidden="true"
          tabIndex={-1}
          onClick={onClose}
        />
      </CSSTransition>
      <CSSTransition
        nodeRef={panelRef}
        in={open}
        timeout={300}
        classNames={MOTION}
        appear
        mountOnEnter
        unmountOnExit
        onEnter={() => setPresent(true)}
        onExited={() => setPresent(false)}
      >
        <div
          ref={panelRef}
          id={id}
          role="dialog"
          aria-modal="true"
          aria-label={title}
          className="ov-drawer__panel"
        >
          <FocusLock
            className="ov-drawer__focus-lock"
            returnFocus={{ preventScroll: true }}
            crossFrame={false}
          >
            <button
              type="button"
              className="ov-drawer__close"
              aria-label={closeLabel}
              onClick={onClose}
            >
              <CloseIcon width={16} height={16} aria-hidden="true" />
            </button>
            <div className="ov-drawer__body">{children}</div>
          </FocusLock>
        </div>
      </CSSTransition>
    </div>,
    document.body,
  );
}
