import { CopyError } from './copy-error.js';
import { useModalFocusShard } from '../primitives/modal/focus-shards.js';
import { NotificationScopeContext } from './notification-context.js';
import {
  useContext,
  useCallback,
  useId,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { useLocale } from '../locale-context.js';
import type { Locale } from '../locale.js';
import { Notification } from '../primitives/notification/notification.js';
import type { NotificationItem } from '../primitives/notification/types.js';
import enUS from './locales/en-US.js';
import zhCN from './locales/zh-CN.js';

/** Present an existing failure without adding a row to the owning view. */
export function ErrorNotification({
  title,
  children,
  identity = children,
  actions,
  error,
  fromRequest,
  onDismiss,
  locale: localeProp,
}: {
  title: ReactNode;
  children?: ReactNode;
  identity?: unknown;
  actions?: ReactNode;
  error?: unknown;
  fromRequest?: boolean;
  onDismiss?: () => void;
  locale?: Locale;
}) {
  const locale = useLocale(localeProp);
  const text = { 'en-US': enUS, 'zh-CN': zhCN }[locale];
  const id = useId();
  const [dismissed, setDismissed] = useState<{ identity: unknown }>();
  const owner = useContext(NotificationScopeContext);
  const hidden =
    (!fromRequest && owner?.errors.wasReported(error)) ||
    (dismissed && Object.is(dismissed.identity, identity));
  const dismiss = useCallback(() => {
    setDismissed({ identity });
    onDismiss?.();
  }, [identity, onDismiss]);
  const buttons = useMemo(
    () => (
      <>
        <CopyError error={error} locale={locale} />
        {actions}
      </>
    ),
    [error, locale, actions],
  );
  const notices: NotificationItem[] = hidden
    ? []
    : [
        {
          id,
          type: 'error',
          title,
          content: children ?? text.unavailable,
          btn: buttons,
        },
      ];
  const modal = useModalFocusShard();
  const container =
    owner?.locale === locale && owner.modal === modal
      ? owner.container
      : undefined;
  return (
    <Notification
      notices={notices}
      locale={locale}
      onRemove={dismiss}
      container={container}
    />
  );
}

/** The Client already parses transport errors; UI keeps only their readable reason. */
export function errorMessage(error: unknown): string | undefined {
  const message =
    error instanceof Error
      ? error.message
      : typeof error === 'string'
        ? error
        : undefined;
  return message?.trim() ? message : undefined;
}
