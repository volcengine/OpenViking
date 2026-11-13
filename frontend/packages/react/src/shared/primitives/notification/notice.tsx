/** Adapted from Arco Design _class/notice (MIT), commit
 * 4e087a0fe7d5ae2f222fbce4a9518ba047b01772. See LICENSE.arco-design.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import CloseIcon from '../../../../../icons/svg/viking-bot/remove.svg?react';
import type { NotificationItem } from './types.js';
import { StatusIcon } from './status-icon.js';

export function Notice({
  notice,
  closeLabel,
  onRemove,
  active,
}: {
  active: boolean;
  notice: NotificationItem;
  closeLabel: string;
  onRemove: (id: string) => void;
}) {
  const {
    id,
    type,
    title,
    content,
    icon,
    showIcon = true,
    closable = true,
    closeIcon,
    className,
    style,
    duration = 3000,
    btn,
  } = notice;
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const previousFocus = useRef<Element | null>(null);
  const setRoot = useCallback((node: HTMLDivElement | null) => {
    root.current = node;
    if (node) previousFocus.current = node.ownerDocument.activeElement;
  }, []);
  // Effect necessity: synchronize the browser auto-dismiss timer with notice updates,
  // hover and keyboard focus. Cleanup cancels the old timer on update/unmount.
  useEffect(() => {
    if (!active || duration <= 0 || hovered || focused) return;
    const timer = window.setTimeout(() => onRemove(id), duration);
    return () => window.clearTimeout(timer);
  }, [notice, duration, hovered, focused, id, onRemove, active]);
  const close = () => {
    const element = root.current;
    if (element?.contains(document.activeElement)) {
      const buttons = element
        .closest('.ov-notification-stack')
        ?.querySelectorAll<HTMLButtonElement>('.ov-notification__close');
      const next = Array.from(buttons ?? []).find(
        (button) => !element.contains(button),
      );
      if (next) next.focus();
      else if (
        previousFocus.current instanceof HTMLElement &&
        previousFocus.current.isConnected
      )
        previousFocus.current.focus();
    }
    onRemove(id);
  };
  return (
    <div
      ref={setRoot}
      className={[
        'ov-notification',
        `ov-notification--${type}`,
        closable && 'ov-notification--closable',
        className,
      ]
        .filter(Boolean)
        .join(' ')}
      style={style}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onFocusCapture={() => setFocused(true)}
      onBlurCapture={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget))
          setFocused(false);
      }}
    >
      {showIcon && (icon != null || type !== 'normal') ? (
        <span className="ov-notification__icon" aria-hidden="true">
          {icon ?? <StatusIcon type={type} />}
        </span>
      ) : null}
      <div className="ov-notification__body">
        {title != null ? (
          <div className="ov-notification__title">{title}</div>
        ) : null}
        <div className="ov-notification__content">{content}</div>
        {btn != null ? (
          <div className="ov-notification__actions">{btn}</div>
        ) : null}
      </div>
      {closable ? (
        <button
          type="button"
          className="ov-notification__close"
          aria-label={closeLabel}
          onClick={close}
        >
          {closeIcon ?? <CloseIcon width={16} height={16} />}
        </button>
      ) : null}
    </div>
  );
}
