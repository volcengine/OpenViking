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
}: {
  text: string;
  labels: VikingBotLabels;
  className: string;
  onCopy?: (text: string) => void;
  icon?: (copied: boolean) => ReactNode;
}) {
  const [copiedText, setCopiedText] = useState<string>();
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  // Effect necessity: release the feedback timer when the row unmounts.
  useEffect(() => () => clearTimeout(timer.current), []);
  const copied = copiedText === text;
  const label = copied ? labels.copied : labels.copy;
  const Icon = copied ? Check : Copy;
  const copy = async () => {
    if (onCopy) onCopy(text);
    else if (!(await copyToClipboard(text))) return;
    setCopiedText(text);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopiedText(undefined), 1500);
  };
  return (
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
  );
}
