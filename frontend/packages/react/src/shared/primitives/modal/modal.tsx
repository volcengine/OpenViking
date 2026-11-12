/**
 * Adapted from Arco Design's Modal (MIT), limited to Drawer-equivalent features.
 * https://github.com/arco-design/arco-design/tree/main/components/Modal
 * Copyright (c) 2021 Bytedance, Inc. and its affiliates.
 * License: ../../../../LICENSE.arco-design (included in the published package).
 */
import { useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import FocusLock from 'react-focus-lock';
import { CSSTransition } from 'react-transition-group';
import CloseIcon from '../../../../../icons/svg/viking-bot/remove.svg?react';
import { useLocale } from '../../locale-context.js';
import { resolveLocale } from '../../locale.js';
import { lockBodyScroll } from '../overlay/lock-body-scroll.js';
import { usePortalTheme } from '../tooltip/use-portal-theme.js';
import type { ModalProps } from './types.js';
import enUS from './locales/en-US.js';
import zhCN from './locales/zh-CN.js';

const MOTION = {
  appear: 'ov-modal--enter',
  appearActive: 'ov-modal--enter-active',
  enter: 'ov-modal--enter',
  enterActive: 'ov-modal--enter-active',
  exitActive: 'ov-modal--exit',
};

export function Modal({
  open,
  title,
  triggerRef,
  id,
  className,
  locale: localeProp,
  onClose,
  children,
  closeLabel,
}: ModalProps) {
  const locale = useLocale(localeProp);
  const text = resolveLocale({ 'en-US': enUS, 'zh-CN': zhCN }, locale);
  const label = closeLabel ?? text.closeLabel;
  const generatedId = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  const maskRef = useRef<HTMLButtonElement>(null);
  const [present, setPresent] = useState(open);
  const [layer, setLayer] = useState<HTMLDivElement | null>(null);
  usePortalTheme(Boolean(layer), triggerRef, layer);

  // useEffect 必要性：Portal 提交后才可锁定所属 document 的 body；
  // 锁属于浏览器外部资源，需一直保留到退出动画卸载 Portal，再释放本层令牌。
  // 与 Drawer 共用所有者计数，最后一个浮层释放时才恢复宿主原样式。
  useEffect(() => {
    if (layer) return lockBodyScroll(layer.ownerDocument.body);
  }, [layer]);

  if (typeof document === 'undefined' || (!open && !present)) return null;
  return createPortal(
    <div
      ref={setLayer}
      role="presentation"
      lang={locale}
      className={['ov-modal', className].filter(Boolean).join(' ')}
      onKeyDown={(event) => {
        if (event.key !== 'Escape' || event.defaultPrevented) return;
        if (
          !(event.target instanceof Node) ||
          !event.currentTarget.contains(event.target)
        )
          return;
        event.preventDefault();
        event.stopPropagation();
        onClose?.();
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
          className="ov-modal__mask"
          aria-label={label}
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
          id={id ?? generatedId}
          role="dialog"
          aria-modal="true"
          aria-label={title}
          className="ov-modal__panel"
        >
          <FocusLock
            className="ov-modal__focus-lock"
            returnFocus={{ preventScroll: true }}
            crossFrame={false}
          >
            <button
              type="button"
              className="ov-modal__close"
              aria-label={label}
              disabled={!onClose}
              onClick={onClose}
            >
              <CloseIcon width={16} height={16} aria-hidden="true" />
            </button>
            <div className="ov-modal__body">{children}</div>
          </FocusLock>
        </div>
      </CSSTransition>
    </div>,
    document.body,
  );
}
