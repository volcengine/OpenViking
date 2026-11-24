/** Adapted from Arco Design _class/notice (MIT), commit
 * 4e087a0fe7d5ae2f222fbce4a9518ba047b01772. See LICENSE.arco-design.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import CloseIcon from '../../../../../icons/svg/viking-bot/remove.svg?react';
import type { NoticeItem } from './types.js';
import { StatusIcon } from './status-icon.js';

export function Notice({
  notice,
  closeLabel,
  onRemove,
  active,
  kind,
}: {
  active: boolean;
  kind: 'notification' | 'toast';
  notice: NoticeItem;
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
    onDismiss,
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
  const dismiss = useCallback(() => {
    onDismiss?.();
    onRemove(id);
  }, [onDismiss, onRemove, id]);
  // Effect necessity: synchronize the browser auto-dismiss timer with notice updates,
  // hover and keyboard focus; elapsed time cannot be handled by click events.
  // Cleanup cancels the old timer on update/unmount.
  useEffect(() => {
    if (!active || duration <= 0 || hovered || focused) return;
    const timer = window.setTimeout(dismiss, duration);
    return () => window.clearTimeout(timer);
  }, [notice, duration, hovered, focused, dismiss, active]);
  const close = () => {
    const element = root.current;
    if (element?.contains(document.activeElement)) {
      const buttons = element
        .closest(`.ov-${kind}-stack`)
        ?.querySelectorAll<HTMLButtonElement>(`.ov-${kind}__close`);
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
    dismiss();
  };
  return (
    <div
      ref={setRoot}
      className={[
        `ov-${kind}`,
        `ov-${kind}--${type}`,
        closable && `ov-${kind}--closable`,
        className,
      ]
        .filter(Boolean)
        .join(' ')}
      style={style}
      aria-busy={type === 'loading' || undefined}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onFocusCapture={() => setFocused(true)}
      onBlurCapture={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget))
          setFocused(false);
      }}
    >
      {showIcon && (icon != null || type !== 'normal') ? (
        <span className={`ov-${kind}__icon`} aria-hidden="true">
          {icon ?? <StatusIcon type={type} />}
        </span>
      ) : null}
      <div className={`ov-${kind}__body`}>
        {title != null ? (
          <div className={`ov-${kind}__title`}>{title}</div>
        ) : null}
        <div className={`ov-${kind}__content`}>{content}</div>
        {btn != null ? (
          <div className={`ov-${kind}__actions`}>{btn}</div>
        ) : null}
      </div>
      {closable ? (
        <button
          type="button"
          className={`ov-${kind}__close`}
          aria-label={closeLabel}
          onClick={close}
        >
          {closeIcon ?? <CloseIcon width={16} height={16} />}
        </button>
      ) : null}
    </div>
  );
}
