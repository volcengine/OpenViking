import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Button } from '../../shared/primitives/button/button.js';
import { Tooltip } from '../../shared/primitives/tooltip/index.js';
import Copy from '../../../../icons/svg/viking-bot/copy.svg?react';
import Check from '../../../../icons/svg/viking-bot/check.svg?react';
import type { VikingBotLabels } from './model.js';

async function copyToClipboard(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // Embedded pages may not have permission to use the async clipboard.
    const field = document.createElement('textarea');
    field.value = text;
    field.readOnly = true;
    field.style.cssText = 'position:fixed;inset:0 auto auto 0;opacity:0';
    const focused = document.activeElement;
    document.body.appendChild(field);
    field.select();
    try {
      return document.execCommand('copy');
    } catch {
      return false;
    } finally {
      field.remove();
      if (focused instanceof HTMLElement)
        focused.focus({ preventScroll: true });
    }
  }
}

export function CopyAction({
  text,
  labels,
  className,
  onCopy,
  icon,
  failureLabel,
}: {
  text: string | (() => string);
  labels: VikingBotLabels;
  className: string;
  onCopy?: (text: string) => void | Promise<void>;
  icon?: (copied: boolean) => ReactNode;
  failureLabel?: string;
}) {
  const [feedback, setFeedback] = useState<{
    source: typeof text;
    success: boolean;
  }>();
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const attempt = useRef(0);
  const owner = useRef({ active: true });
  // Effect necessity: the clipboard may settle after this row unmounts.
  // Event handlers cannot observe unmount; invalidate pending copies and clear
  // the browser feedback timer on cleanup, including Strict Mode reconnects.
  useEffect(() => {
    const current = { active: true };
    owner.current = current;
    return () => {
      current.active = false;
      clearTimeout(timer.current);
    };
  }, []);
  const result = feedback?.source === text ? feedback : undefined;
  const copied = result?.success === true;
  const label = result
    ? result.success
      ? labels.copied
      : (failureLabel ?? labels.copy)
    : labels.copy;
  const Icon = copied ? Check : Copy;
  const copy = async () => {
    const token = ++attempt.current;
    const current = owner.current;
    let success = false;
    try {
      const value = typeof text === 'function' ? text() : text;
      if (onCopy) {
        await onCopy(value);
        success = true;
      } else success = await copyToClipboard(value);
    } catch {
      /* Copy failures keep the action available for retry. */
    }
    if (!current.active || token !== attempt.current) return;
    setFeedback({ source: text, success });
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setFeedback(undefined), 1500);
  };
  return (
    <>
      <Tooltip content={label}>
        <Button
          size="mini"
          type="text"
          iconOnly
          htmlType="button"
          className={className}
          aria-label={label}
          data-copied={copied || undefined}
          onClick={() => void copy()}
        >
          {icon ? icon(copied) : <Icon aria-hidden="true" focusable="false" />}
        </Button>
      </Tooltip>
      {failureLabel ? (
        <span className="ov-viking-bot__copy-status" role="status">
          {result ? label : ''}
        </span>
      ) : null}
    </>
  );
}
