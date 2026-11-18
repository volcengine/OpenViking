import { useContext, useId } from 'react';
import { NotificationScopeContext } from '../../shared/feedback/notification-context.js';
import type { Locale } from '../../shared/locale.js';
import { Notification } from '../../shared/primitives/notification/notification.js';
import enUS from './locales/wiki-save.en-US.js';
import zhCN from './locales/wiki-save.zh-CN.js';

export function WikiSaveNotification({
  saving,
  locale,
}: {
  saving: boolean;
  locale: Locale;
}) {
  const owner = useContext(NotificationScopeContext);
  const id = useId();
  const text = { 'en-US': enUS, 'zh-CN': zhCN }[locale];
  if (!owner?.active) return null;
  return (
    <Notification
      locale={locale}
      container={owner.container}
      notices={
        saving
          ? [{ id, type: 'loading', duration: 0, closable: false, ...text }]
          : []
      }
      onRemove={() => {}}
    />
  );
}
