import { useEffect, useRef, useState } from 'react';
import { OvClientError } from '../../client/errors.js';
import type { Locale } from '../locale.js';
import { Button } from '../primitives/button/button.js';
import { useToast } from '../primitives/toast/use-toast.js';
import enUS from './locales/en-US.js';
import zhCN from './locales/zh-CN.js';

export function CopyError({
  error,
  locale,
}: {
  error?: unknown;
  locale: Locale;
}) {
  const text = { 'en-US': enUS, 'zh-CN': zhCN }[locale];
  const [toast, holder] = useToast({ locale });
  const [status, setStatus] = useState('');
  const lastMessage = useRef('');
  const active = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  // Effect necessity: clipboard permission can resolve after this view has closed,
  // and its feedback timer is a browser resource created by the click handler.
  // Cleanup ignores late results and cancels that timer on unmount.
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      clearTimeout(timer.current);
    };
  }, []);
  const feedback = (message: string) => {
    const next = message === lastMessage.current ? `${message}\u00a0` : message;
    lastMessage.current = next;
    return next;
  };
  const copy = async (button: HTMLButtonElement) => {
    clearTimeout(timer.current);
    setStatus('');
    const notice = button.closest('.ov-notification');
    const read = (selector: string) => {
      const element = notice?.querySelector<HTMLElement>(selector);
      return element?.innerText ?? element?.textContent ?? '';
    };
    const parts = [
      read('.ov-notification__title'),
      read('.ov-notification__content'),
    ];
    if (error instanceof OvClientError) {
      parts.push(`${text.code}: ${error.code}`);
      if (error.statusCode !== undefined)
        parts.push(`${text.statusCode}: ${error.statusCode}`);
      if (error.requestId) parts.push(`${text.requestId}: ${error.requestId}`);
    }
    try {
      await navigator.clipboard.writeText(
        parts.filter((part) => part.trim()).join('\n'),
      );
      if (!active.current) return;
      toast.clear();
      setStatus(feedback(text.copied));
      timer.current = setTimeout(() => setStatus(''), 2000);
    } catch {
      if (active.current)
        toast.error({ id: 'copy-error', content: feedback(text.copyFailed) });
    }
  };
  return (
    <span className="ov-error-notification__copy">
      {holder}
      <Button
        size="small"
        type="text"
        onClick={(event) => void copy(event.currentTarget)}
      >
        {text.copyError}
      </Button>
      <span className="ov-error-notification__copy-status" role="status">
        {status}
      </span>
    </span>
  );
}
