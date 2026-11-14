/**
 * Adapted from Arco Design's Modal (MIT), limited to Drawer-equivalent features.
 * https://github.com/arco-design/arco-design/tree/main/components/Modal
 * Copyright (c) 2021 Bytedance, Inc. and its affiliates.
 * License: ../../../../LICENSE.arco-design (included in the published package).
 */
import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from 'react';
import type { RefObject } from 'react';
import { createPortal } from 'react-dom';
import FocusLock from 'react-focus-lock';
import { CSSTransition } from 'react-transition-group';
import CloseIcon from '../../../../../icons/svg/viking-bot/remove.svg?react';
import { useLocale } from '../../locale-context.js';
import { resolveLocale } from '../../locale.js';
import { lockBodyScroll } from '../overlay/lock-body-scroll.js';
import { usePortalTheme } from '../tooltip/use-portal-theme.js';
import { ModalFocusShardContext } from './focus-shards.js';
import type { RegisterFocusShard } from './focus-shards.js';
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
  afterClose,
  children,
  closeLabel,
  closeIcon,
  maskClosable = true,
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

  // 浮层 portal 到 body 后不在弹窗的 DOM 子树里，`FocusLock` 的 Tab 序列看不到
  // 它。把浮层节点登记成 shard，`FocusLock` 就会把它算进自己的焦点范围，Tab 能
  // 进去也能出来。用 state 而不是 ref：shard 列表变了要重渲染，`FocusLock` 才会
  // 拿到新的 `shards` 属性。
  //
  // A portaled surface is not in the dialog's DOM subtree, so `FocusLock`'s tab
  // sequence cannot see it. Registering the node as a shard puts it inside the lock's
  // focus scope, so Tab enters and leaves it. State, not a ref: the lock only picks up
  // a changed `shards` prop when it re-renders.
  const [shards, setShards] = useState<Array<RefObject<HTMLElement | null>>>(
    [],
  );
  const registerFocusShard = useCallback<RegisterFocusShard>((shard) => {
    setShards((current: Array<RefObject<HTMLElement | null>>) =>
      current.includes(shard) ? current : [...current, shard],
    );
    return () =>
      setShards((current: Array<RefObject<HTMLElement | null>>) =>
        current.filter((item) => item !== shard),
      );
  }, []);
  const shardContext = useMemo(() => registerFocusShard, [registerFocusShard]);

  // useEffect 必要性：Portal 提交后才可锁定所属 document 的 body；
  // 锁属于浏览器外部资源，需一直保留到退出动画卸载 Portal，再释放本层令牌。
  // 与 Drawer 共用所有者计数，最后一个浮层释放时才恢复宿主原样式。
  useEffect(() => {
    if (layer) return lockBodyScroll(layer.ownerDocument.body);
  }, [layer]);

  if (!open && !present) return null;
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
          onClick={maskClosable ? onClose : undefined}
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
        onExited={() => {
          setPresent(false);
          afterClose?.();
        }}
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
            shards={shards}
          >
            <ModalFocusShardContext.Provider value={shardContext}>
              <button
                type="button"
                className="ov-modal__close"
                aria-label={label}
                disabled={!onClose}
                onClick={onClose}
              >
                {closeIcon ?? (
                  <CloseIcon width={16} height={16} aria-hidden="true" />
                )}
              </button>
              <div className="ov-modal__body">{children}</div>
            </ModalFocusShardContext.Provider>
          </FocusLock>
        </div>
      </CSSTransition>
    </div>,
    document.body,
  );
}
